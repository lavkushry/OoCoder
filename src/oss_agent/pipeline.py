"""Offline loop: choose, discuss, reproduce, fix, quality, PR, review/CI, merge."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig
from oss_agent.db.models import Action, Artifact, Project
from oss_agent.db.session import create_all, make_engine, session_scope
from oss_agent.gateway.service import GitHubGatewayService
from oss_agent.heal.loop import repair_budget
from oss_agent.learn.apply import apply_implementation, title_from_lessons
from oss_agent.learn.store import LessonStore, audit_lessons
from oss_agent.loop.monitor import checks_as_observations, monitor_until_merged
from oss_agent.loop.offline import (
    build_discussion_comment,
    load_contribution_fixture,
    plan_review_iteration,
    reply_payloads,
)
from oss_agent.models.coding import select_coding_brain
from oss_agent.orchestrator.engine import Orchestrator
from oss_agent.orchestrator.states import TaskState
from oss_agent.patching.draft import build_pr_draft
from oss_agent.patching.verify import run_local_verification
from oss_agent.protocols import OutwardActionDraft, UntrustedText
from oss_agent.quality.analyzer import analyze_patch_quality
from oss_agent.research import research_before_action
from oss_agent.rules.engine import DefaultRulesEngine
from oss_agent.sandbox import make_sandbox_runner
from oss_agent.sandbox.repro import reproduce_issue
from oss_agent.scout.choose import choose_allowlisted_issue
from oss_agent.scout.policy import assert_ai_allowed, ingest_project_rules
from oss_agent.scout.ranking import rank_issues


@dataclass
class PipelineResult:
    shortlist_top: int
    reproduced: bool
    verify_ok: bool
    pr_draft_id: str | None
    pr_errors: list[str]
    review_classes: list[str]
    flake_detected: bool
    chosen_issue: int = 0
    discussion_action_id: str | None = None
    discussion_disclosed: bool = False
    merged: bool = False
    task_state: str = ""


async def run_fixture_pipeline(root: Path, work: Path) -> PipelineResult:
    fixtures = root / "fixtures"
    repo = fixtures / "projects" / "friendly_tiny"
    rules = ingest_project_rules("fixtures/friendly_tiny", repo)
    assert_ai_allowed(rules)
    issues_path = fixtures / "issues" / "friendly_tiny_issues.json"
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    loop_fx = load_contribution_fixture(fixtures / "loop" / "friendly_tiny_contribution.json")
    cfg = AppConfig()
    ranked = rank_issues("proj-friendly", issues)

    engine = make_engine(f"sqlite:///{work / 'demo.db'}")
    create_all(engine)
    with session_scope(engine) as session:
        lesson_store = LessonStore(session)
        chosen = choose_allowlisted_issue(
            ranked,
            allowlisted_repos=set(loop_fx.allowlist),
            repo=loop_fx.repo,
            min_score=cfg.goal.min_issue_score,
            lessons=lesson_store.for_repo(loop_fx.repo),
        )
        session.add(
            Project(
                id="proj-friendly",
                repo="fixtures/friendly_tiny",
                policy_json={},
                ai_allowed=True,
                rate_limits={},
            )
        )
        session.flush()
        runner = make_sandbox_runner(cfg)
        audit = FileAndDbAuditLog(work / "audit.jsonl", session)
        orch = Orchestrator(session, cfg, audit)
        gw = GitHubGatewayService(session, cfg, audit)
        task = orch.create_task(
            project_id="proj-friendly",
            issue_id=None,
            state=TaskState.SCOUTING,
        )
        orch.transition(task.id, TaskState.SHORTLISTED, reason="issue-chosen")

        def _research(kind: str) -> str:
            brief = research_before_action(
                action_kind=kind,
                query=f"{chosen.title}\n{chosen.body.body}",
                project_root=repo,
                issues_path=issues_path,
                config=cfg,
            )
            if not brief.passed:
                msg = "research required before action"
                raise RuntimeError(msg)
            return brief.id

        comment_body = build_discussion_comment(chosen)
        if cfg.policy.ai_disclosure in comment_body:
            msg = "disclosure must be injected by the gateway"
            raise RuntimeError(msg)
        orch.transition(task.id, TaskState.AWAITING_CLAIM_APPROVAL, reason="discussion-draft")
        discussion_id = gw.enqueue(
            OutwardActionDraft(
                kind="comment",
                task_id=task.id,
                payload={
                    "body": comment_body,
                    "issue": chosen.number,
                    "research_brief_id": _research("comment"),
                },
            )
        )
        sponsor = loop_fx.sponsor.approved_by
        if not loop_fx.sponsor.approve_discussion:
            return _result(
                ranked[0].number,
                chosen.number,
                discussion_id,
                False,
                task.state,
            )
        await gw.send_approved(discussion_id, approved_by=sponsor)
        disclosed = _body_has_disclosure(
            session.get(Action, discussion_id),
            cfg.policy.ai_disclosure,
        )
        orch.transition(task.id, TaskState.CLAIMED, reason="sponsor-approved-discussion")
        orch.transition(task.id, TaskState.REPRODUCING, reason="claim-sent")

        repro = await reproduce_issue(
            runner,
            task_id=task.id,
            source_repo=repo,
            issue=UntrustedText(source=chosen.body.source, body=chosen.body.body),
            work_root=cfg.sandbox.work_root,
        )
        if not repro.repro_failed_as_expected:
            return _result(ranked[0].number, chosen.number, discussion_id, disclosed, task.state)

        orch.transition(task.id, TaskState.DESIGNING, reason="repro-ok")
        orch.transition(task.id, TaskState.IMPLEMENTING, reason="design-trivial")
        brain = select_coding_brain()
        audit.append(
            actor="orchestrator",
            event="coding_brain.selected",
            payload={
                "brain": brain.brain,
                "runtime": brain.runtime,
                "api_called": False,
            },
            task_id=task.id,
        )
        fix = loop_fx.initial_fix
        patch, impl_status = apply_implementation(
            repro.workdir / "repo",
            module_relpath=fix.module_relpath,
            old=fix.old,
            new=fix.new,
            lessons=lesson_store.for_repo(loop_fx.repo),
        )
        if patch is None:
            return _result(
                ranked[0].number,
                chosen.number,
                discussion_id,
                disclosed,
                task.state,
                reproduced=True,
                pr_errors=[impl_status],
            )
        verify = await run_local_verification(
            runner, task_id=task.id, workdir=repro.workdir / "repo"
        )
        orch.transition(task.id, TaskState.SELF_REVIEW, reason="patch-ready")
        if not verify.ok:
            return _result(
                ranked[0].number,
                chosen.number,
                discussion_id,
                disclosed,
                task.state,
                reproduced=True,
            )

        art_log = Artifact(
            id=f"art_{uuid.uuid4().hex[:12]}",
            task_id=task.id,
            type="test_log",
            path=str(repro.workdir / "evidence.log"),
            content_hash=verify.content_hash,
            from_sandbox=True,
        )
        art_patch = Artifact(
            id=f"art_{uuid.uuid4().hex[:12]}",
            task_id=task.id,
            type="patch",
            path=str((repro.workdir / "repo") / ".oss_agent_diff.patch"),
            content_hash=patch.content_hash,
            from_sandbox=True,
        )
        session.add_all([art_log, art_patch])
        session.flush()
        art_ids = [art_log.id, art_patch.id]

        quality = analyze_patch_quality(
            workdir=repro.workdir / "repo",
            diff=patch.diff,
            changed_files=patch.files_changed,
            config=cfg,
            tests_verified=True,
        )
        if not quality.passed:
            return _result(
                ranked[0].number,
                chosen.number,
                discussion_id,
                disclosed,
                task.state,
                reproduced=True,
                verify_ok=True,
                pr_errors=[quality.block_reason or "quality_blocked"],
            )

        orch.transition(task.id, TaskState.AWAITING_PR_APPROVAL, reason="self-review-pass")
        pr_payload = build_pr_draft(
            title=title_from_lessons(fix.title, lesson_store.for_repo(loop_fx.repo)),
            issue_number=chosen.number,
            rules=rules,
            evidence_artifact_ids=art_ids,
            test_log_excerpt=verify.log,
            diff_excerpt=patch.diff,
        )
        pr_payload = {**pr_payload, "research_brief_id": _research("pr")}
        errors = DefaultRulesEngine().validate_pr_draft(pr_payload, rules)
        draft_id = None
        if not errors and loop_fx.sponsor.approve_pr:
            draft_id = gw.enqueue(
                OutwardActionDraft(
                    kind="pr",
                    task_id=task.id,
                    payload=pr_payload,
                    evidence_artifact_ids=art_ids,
                )
            )
            await gw.send_approved(draft_id, approved_by=sponsor)
            orch.transition(task.id, TaskState.SUBMITTED, reason="sponsor-approved-pr")
            orch.transition(task.id, TaskState.IN_REVIEW, reason="fixture-pr-open")

        monitored = await monitor_until_merged(
            runner,
            task_id=f"{task.id}-monitor",
            workdir=repro.workdir / "repo",
            repo=loop_fx.repo,
            observations=checks_as_observations(loop_fx.ci_rounds),
            review_comments=loop_fx.review_comments,
            known_flakes=loop_fx.known_flakes,
            max_repairs=repair_budget(cfg.policy.rate_limits.max_ci_retries),
            lessons=lesson_store.for_repo(loop_fx.repo),
            issue_title=chosen.title,
            sponsor_approves_merge=False,
            accept_design=loop_fx.sponsor.accept_design,
        )
        lesson_store.upsert_many(monitored.lessons)
        audit_lessons(audit, monitored.lessons, task_id=task.id)
        plan = plan_review_iteration(
            comments=loop_fx.review_comments,
            ci_rounds=loop_fx.ci_rounds,
            known_flakes=loop_fx.known_flakes,
            max_ci_retries=cfg.policy.rate_limits.max_ci_retries,
            accept_design=loop_fx.sponsor.accept_design,
        )
        classes = [str(item["class"]) for item in plan.review.items]
        merged = False
        healed_ci = monitored.heal
        if (
            monitored.status == "stand_down"
            and monitored.stand_down_body
            and cfg.policy.ai_disclosure not in monitored.stand_down_body
        ):
            gw.enqueue(
                OutwardActionDraft(
                    kind="comment",
                    task_id=task.id,
                    payload={
                        "body": monitored.stand_down_body,
                        "issue": chosen.number,
                        "research_brief_id": _research("comment"),
                    },
                )
            )
        ready = (
            draft_id is not None
            and plan.repair
            and plan.ready_to_merge
            and healed_ci is not None
            and healed_ci.ok
        )
        if ready and healed_ci is not None:
            orch.transition(task.id, TaskState.CHANGES_REQUESTED, reason="ci-failure")
            orch.transition(task.id, TaskState.IMPLEMENTING, reason="heal")
            verify2_ok = healed_ci.ok
            verify2_log = healed_ci.last_log
            verify2_hash = healed_ci.last_hash
            patch2_diff = healed_ci.diff
            patch2_files = list(healed_ci.files_changed)
            patch2_hash = healed_ci.patch_hash or healed_ci.last_hash
            if (
                not verify2_ok
                and healed_ci.stand_down_body
                and cfg.policy.ai_disclosure not in healed_ci.stand_down_body
            ):
                gw.enqueue(
                    OutwardActionDraft(
                        kind="comment",
                        task_id=task.id,
                        payload={
                            "body": healed_ci.stand_down_body,
                            "issue": chosen.number,
                            "research_brief_id": _research("comment"),
                        },
                    )
                )
            if verify2_ok:
                quality2 = analyze_patch_quality(
                    workdir=repro.workdir / "repo",
                    diff=patch2_diff,
                    changed_files=patch2_files,
                    config=cfg,
                    tests_verified=True,
                )
            else:
                quality2 = None
            if (
                verify2_ok
                and quality2 is not None
                and quality2.passed
                and loop_fx.sponsor.approve_follow_up
            ):
                follow_log = Artifact(
                    id=f"art_{uuid.uuid4().hex[:12]}",
                    task_id=task.id,
                    type="test_log",
                    path=str(work / "verify-followup.log"),
                    content_hash=verify2_hash,
                    from_sandbox=True,
                )
                follow_patch = Artifact(
                    id=f"art_{uuid.uuid4().hex[:12]}",
                    task_id=task.id,
                    type="patch",
                    path=str((repro.workdir / "repo") / ".oss_agent_diff.patch"),
                    content_hash=patch2_hash,
                    from_sandbox=True,
                )
                (work / "verify-followup.log").write_text(verify2_log, encoding="utf-8")
                session.add_all([follow_log, follow_patch])
                session.flush()
                orch.transition(task.id, TaskState.SELF_REVIEW, reason="follow-up-verified")
                orch.transition(task.id, TaskState.AWAITING_PR_APPROVAL, reason="follow-up-ready")
                brief_id = _research("push")
                push_id = gw.enqueue(
                    OutwardActionDraft(
                        kind="push",
                        task_id=task.id,
                        payload={
                            "body": "Follow-up from sandbox evidence.\n\n```diff\n"
                            + patch2_diff[:4000]
                            + "\n```\n",
                            "issue": chosen.number,
                            "branch": f"oss-agent/issue-{chosen.number}",
                            "force": False,
                            "research_brief_id": brief_id,
                        },
                        evidence_artifact_ids=[follow_log.id, follow_patch.id],
                    )
                )
                await gw.send_approved(push_id, approved_by=sponsor)
                orch.transition(task.id, TaskState.SUBMITTED, reason="sponsor-approved-follow-up")
                orch.transition(task.id, TaskState.IN_REVIEW, reason="follow-up-submitted")
                if loop_fx.sponsor.approve_replies:
                    reply_brief = _research("reply")
                    for payload in reply_payloads(
                        plan,
                        issue_number=chosen.number,
                        research_brief_id=reply_brief,
                    ):
                        reply_id = gw.enqueue(
                            OutwardActionDraft(kind="reply", task_id=task.id, payload=payload)
                        )
                        await gw.send_approved(reply_id, approved_by=sponsor)
                if loop_fx.sponsor.approve_merge:
                    audit.append(
                        actor="sponsor",
                        event="fixture.merge_approved",
                        payload={
                            "approved_by": sponsor,
                            "mode": "fixture_state_transition",
                            "live_github_write": False,
                        },
                        task_id=task.id,
                    )
                    orch.transition(
                        task.id,
                        TaskState.APPROVED,
                        reason="fixture-maintainer-approved",
                    )
                    orch.transition(task.id, TaskState.MERGED, reason="sponsor-approved-merge")
                    merged = True

        return PipelineResult(
            shortlist_top=ranked[0].number,
            reproduced=True,
            verify_ok=verify.ok,
            pr_draft_id=draft_id,
            pr_errors=errors,
            review_classes=classes,
            flake_detected=plan.flake_detected,
            chosen_issue=chosen.number,
            discussion_action_id=discussion_id,
            discussion_disclosed=disclosed,
            merged=merged,
            task_state=task.state,
        )


def _body_has_disclosure(action: Action | None, disclosure: str) -> bool:
    if action is None:
        return False
    body = action.draft.get("body", action.draft.get("message", ""))
    return disclosure in str(body)


def _result(
    shortlist_top: int,
    chosen_issue: int,
    discussion_id: str | None,
    disclosed: bool,
    task_state: str,
    *,
    reproduced: bool = False,
    verify_ok: bool = False,
    pr_errors: list[str] | None = None,
) -> PipelineResult:
    return PipelineResult(
        shortlist_top=shortlist_top,
        reproduced=reproduced,
        verify_ok=verify_ok,
        pr_draft_id=None,
        pr_errors=pr_errors or [],
        review_classes=[],
        flake_detected=False,
        chosen_issue=chosen_issue,
        discussion_action_id=discussion_id,
        discussion_disclosed=disclosed,
        merged=False,
        task_state=task_state,
    )
