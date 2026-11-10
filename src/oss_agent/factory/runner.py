"""Execute SuperPlane-style factory line steps via n8n-inspired workflow engine."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from oss_agent.analysis.contribution import (
    ContributionAnalysisReport,
    analyze_before_contribution,
)
from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig
from oss_agent.db.models import Artifact, Project, WorkOrder, WorkOrderExecution
from oss_agent.factory.lines import OSS_CONTRIBUTION_LINE_NAME, OSS_CONTRIBUTION_STEPS
from oss_agent.factory.service import FactoryService
from oss_agent.factory.states import CloseResult, WorkOrderState
from oss_agent.gateway.service import GitHubGatewayService
from oss_agent.heal.loop import repair_budget, verify_or_heal
from oss_agent.learn.apply import apply_implementation, lesson_rules, title_from_lessons
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
from oss_agent.protocols import OutwardActionDraft, UntrustedText
from oss_agent.quality.analyzer import analyze_patch_quality
from oss_agent.research import ResearchBrief, ResearchEngine
from oss_agent.rules.engine import DefaultRulesEngine
from oss_agent.sandbox import make_sandbox_runner
from oss_agent.sandbox.repro import reproduce_issue
from oss_agent.scout.choose import choose_allowlisted_issue
from oss_agent.scout.memory import ProjectMemory
from oss_agent.scout.policy import assert_ai_allowed, ingest_project_rules
from oss_agent.scout.ranking import rank_issues
from oss_agent.workflow import (
    OSS_CONTRIBUTION_WORKFLOW,
    NodeRunResult,
    WorkflowEngine,
    WorkflowItem,
)


@dataclass
class FactoryRunResult:
    factory_id: str
    work_order_id: str
    final_state: str
    result: str | None
    steps_passed: list[str]
    pr_draft_id: str | None
    analysis_recommendation: str | None = None
    workflow_execution_id: str | None = None
    workflow_status: str | None = None
    chosen_issue: int = 0
    discussion_action_id: str | None = None
    merged: bool = False
    task_state: str = ""


async def run_oss_factory_demo(root: Path, work: Path, session: Session) -> FactoryRunResult:
    """
    Create a factory, intake a fixture issue as a work order, and run the
    oss-contribution workflow (n8n-style graph) through Strix quality → PR draft.
    """
    fixtures = root / "fixtures"
    repo = fixtures / "projects" / "friendly_tiny"
    rules = ingest_project_rules("fixtures/friendly_tiny", repo)
    assert_ai_allowed(rules)
    issues_path = fixtures / "issues" / "friendly_tiny_issues.json"
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    loop_fx = load_contribution_fixture(fixtures / "loop" / "friendly_tiny_contribution.json")
    cfg = AppConfig()
    ranked = rank_issues("proj-friendly", issues)
    lesson_store = LessonStore(session)
    prior_lessons = lesson_store.for_repo(loop_fx.repo)
    chosen = choose_allowlisted_issue(
        ranked,
        allowlisted_repos=set(loop_fx.allowlist),
        repo=loop_fx.repo,
        min_score=cfg.goal.min_issue_score,
        lessons=prior_lessons,
    )

    svc = FactoryService(session)
    factory = svc.create_factory(
        key="oss-demo",
        name="OSS Contribution Factory",
        description="SuperPlane factory + n8n-style workflow engine",
    )
    order = svc.create_work_order(
        factory_id=factory.id,
        title=chosen.title,
        description=chosen.body.body,
        project_id="proj-friendly",
        source={"issue": {"number": chosen.number, "title": chosen.title}},
        created_by="intake",
    )
    svc.add_artifact(
        order.id,
        artifact_type="markdown",
        data={
            "body": (
                f"Intake score={chosen.score:.1f}\nReasons: {', '.join(chosen.score_reasons)}"
            ),
            "title": "intake assessment",
        },
        key=f"issue:{chosen.number}",
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

    audit = FileAndDbAuditLog(work / "audit.jsonl", session)
    orch = Orchestrator(session, cfg, audit)
    gw = GitHubGatewayService(session, cfg, audit)
    runner = make_sandbox_runner(cfg)
    task = orch.create_task(
        project_id="proj-friendly",
        issue_id=None,
        state=TaskState.SCOUTING,
    )

    engine = WorkflowEngine(OSS_CONTRIBUTION_WORKFLOW)
    wf_exec = engine.start(
        {
            "issue_number": chosen.number,
            "factory_id": factory.id,
            "work_order_id": order.id,
        }
    )
    svc.add_artifact(
        order.id,
        artifact_type="markdown",
        data={
            "title": "workflow execution",
            "body": (
                f"workflow={OSS_CONTRIBUTION_WORKFLOW.id}\n"
                f"execution={wf_exec.id}\n"
                f"engine=n8n-inspired"
            ),
            "workflow_id": OSS_CONTRIBUTION_WORKFLOW.id,
            "execution_id": wf_exec.id,
        },
        key=f"workflow:{wf_exec.id}",
    )

    execution: WorkOrderExecution | None = svc.dispatch(
        order.id, line_name=OSS_CONTRIBUTION_LINE_NAME
    )
    steps_passed: list[str] = []
    pr_draft_id: str | None = None
    discussion_action_id: str | None = None
    merged = False
    repro_dir: Path | None = None
    patch_diff = ""
    changed_files: list[str] = []
    verify_log = ""
    tests_verified = False
    art_ids: list[str] = []
    analysis: ContributionAnalysisReport | None = None
    research_engine = ResearchEngine(cfg)
    memory = ProjectMemory.from_rules_and_owners(
        "fixtures/friendly_tiny",
        (repo / "CODEOWNERS").read_text(encoding="utf-8")
        if (repo / "CODEOWNERS").is_file()
        else None,
    )
    latest_brief: ResearchBrief | None = None
    research_query = f"{chosen.title}\n{chosen.body.body}"

    while execution is not None:
        detail: dict[str, object] = {}
        wf_node = engine.current_node(wf_exec)
        if wf_node is None or wf_node.name != execution.step_name:
            detail = {
                "error": "workflow_factory_desync",
                "factory_step": execution.step_name,
                "workflow_node": None if wf_node is None else wf_node.name,
            }
            execution = svc.advance_or_fail(execution.id, passed=False, detail=detail)
            break

        step = execution.step_name
        passed = False
        gate_passed = True
        waiting = False

        # Research before every action (Khoj corpus + last30days-style scoring).
        if cfg.research.enabled and step != "project_research":
            latest_brief = research_engine.research(
                action_kind=f"step:{step}",
                query=research_query,
                project_root=repo,
                issues_path=issues_path,
                memory=memory.snapshot(),
            )
            if cfg.research.require_before_action and not latest_brief.passed:
                detail = {
                    "error": "research_gate_failed",
                    "research_brief_id": latest_brief.id,
                    "block_reason": latest_brief.block_reason,
                }
                item = WorkflowItem(
                    json={
                        "passed": False,
                        "step": step,
                        **{k: v for k, v in detail.items() if _jsonish(v)},
                    },
                )
                engine.complete(
                    wf_exec,
                    NodeRunResult(
                        success=False,
                        items=[item],
                        error="research_gate_failed",
                        detail=detail,
                    ),
                )
                execution = svc.advance_or_fail(execution.id, passed=False, detail=detail)
                break

        if step == "project_research":
            latest_brief = research_engine.research(
                action_kind="project_research",
                query=research_query,
                project_root=repo,
                issues_path=issues_path,
                memory=memory.snapshot(),
            )
            svc.add_artifact(
                order.id,
                artifact_type="markdown",
                data={
                    "title": "project research brief",
                    "body": latest_brief.to_markdown(),
                    **latest_brief.to_dict(),
                },
                key=f"research:{latest_brief.id}",
            )
            passed = True
            gate_passed = latest_brief.passed
            detail = {
                "research_brief_id": latest_brief.id,
                "hits": len(latest_brief.hits),
                "passed": latest_brief.passed,
            }

        elif step == "analyze":
            analysis = analyze_before_contribution(chosen, rules, cfg)
            svc.add_artifact(
                order.id,
                artifact_type="markdown",
                data={
                    "title": "pre-contribution analysis",
                    "body": analysis.to_markdown(),
                    "recommendation": analysis.recommendation,
                    "readiness_score": analysis.readiness_score,
                },
                key=f"analysis:{chosen.number}",
            )
            # Node ran; IF node routes on recommendation via gate_passed.
            passed = True
            gate_passed = analysis.recommendation == "go"
            detail = {
                "recommendation": analysis.recommendation,
                "readiness": analysis.readiness_score,
            }

        elif step == "await_analysis_approval":
            if analysis is None or analysis.recommendation != "go":
                passed = False
                gate_passed = False
                detail = {"error": "missing_or_blocked_analysis"}
            elif cfg.goal.demo_auto_approve_analysis:
                svc.add_artifact(
                    order.id,
                    artifact_type="markdown",
                    data={
                        "title": "analysis approval",
                        "body": "Auto-approved for offline demo (Paperclip gate).",
                        "approved_by": "demo",
                    },
                )
                passed = True
                detail = {"approved_by": "demo"}
            else:
                passed = False
                waiting = True
                detail = {"waiting": "sponsor_analysis_approval"}

        elif step == "intake":
            orch.transition(task.id, TaskState.SHORTLISTED, reason="issue-chosen")
            passed = True
            detail = {"score": chosen.score, "issue": chosen.number}

        elif step == "discuss":
            if latest_brief is None or not latest_brief.passed:
                passed = False
                detail = {"error": "research_required_before_comment"}
            else:
                comment_body = build_discussion_comment(chosen)
                if cfg.policy.ai_disclosure in comment_body:
                    passed = False
                    detail = {"error": "disclosure_must_come_from_gateway"}
                else:
                    orch.transition(
                        task.id,
                        TaskState.AWAITING_CLAIM_APPROVAL,
                        reason="discussion-draft",
                    )
                    discussion_action_id = gw.enqueue(
                        OutwardActionDraft(
                            kind="comment",
                            task_id=task.id,
                            payload={
                                "body": comment_body,
                                "issue": chosen.number,
                                "research_brief_id": latest_brief.id,
                            },
                        )
                    )
                    svc.add_artifact(
                        order.id,
                        artifact_type="markdown",
                        data={
                            "title": "discussion draft",
                            "body": comment_body,
                            "draft_id": discussion_action_id,
                        },
                        key=f"discuss:{chosen.number}",
                    )
                    passed = True
                    detail = {"discussion_action_id": discussion_action_id}

        elif step == "await_discuss_approval":
            if discussion_action_id is None:
                passed = False
                detail = {"error": "missing_discussion_draft"}
            elif not loop_fx.sponsor.approve_discussion:
                waiting = True
                passed = False
                detail = {"waiting": "sponsor_discussion_approval"}
            else:
                await gw.send_approved(
                    discussion_action_id,
                    approved_by=loop_fx.sponsor.approved_by,
                )
                orch.transition(task.id, TaskState.CLAIMED, reason="sponsor-approved-discussion")
                orch.transition(task.id, TaskState.REPRODUCING, reason="claim-sent")
                passed = True
                detail = {
                    "approved_by": loop_fx.sponsor.approved_by,
                    "live_github_write": False,
                }

        elif step == "reproduce":
            repro = await reproduce_issue(
                runner,
                task_id=task.id,
                source_repo=repo,
                issue=UntrustedText(source=chosen.body.source, body=chosen.body.body),
                work_root=cfg.sandbox.work_root,
            )
            passed = repro.repro_failed_as_expected
            repro_dir = repro.workdir / "repo"
            detail = {"evidence_hash": repro.evidence_hash}
            orch.transition(task.id, TaskState.DESIGNING, reason="factory-repro")

        elif step == "implement":
            assert repro_dir is not None
            orch.transition(task.id, TaskState.IMPLEMENTING, reason="factory-implement")
            impl_lessons = lesson_store.for_repo(loop_fx.repo)
            for note in lesson_rules(impl_lessons):
                memory.add_style_note(note)
            brain = select_coding_brain()
            fix = loop_fx.initial_fix
            patch, impl_status = apply_implementation(
                repro_dir,
                module_relpath=fix.module_relpath,
                old=fix.old,
                new=fix.new,
                lessons=impl_lessons,
            )
            if patch is None:
                passed = False
                detail = {"error": impl_status}
            else:
                patch_diff = patch.diff
                changed_files = list(patch.files_changed)
                passed = bool(patch.diff)
                detail = {
                    "files": patch.files_changed,
                    "implementation": impl_status,
                    "coding_brain": brain.brain,
                    "coding_runtime": brain.runtime,
                    "api_called": False,
                }

        elif step == "verify":
            assert repro_dir is not None
            orch.transition(task.id, TaskState.SELF_REVIEW, reason="factory-verify")
            healed = await verify_or_heal(
                runner,
                task_id=task.id,
                workdir=repro_dir,
                repo=loop_fx.repo,
                known_flakes=loop_fx.known_flakes,
                max_repairs=repair_budget(cfg.policy.rate_limits.max_ci_retries),
                lessons=lesson_store.for_repo(loop_fx.repo),
                issue_title=chosen.title,
            )
            lesson_store.upsert_many(healed.lessons)
            audit_lessons(audit, healed.lessons, task_id=task.id)
            if healed.ok and healed.diff:
                patch_diff = healed.diff
                changed_files = list(healed.files_changed)
            passed = healed.ok
            tests_verified = healed.ok
            verify_log = healed.last_log
            disclose = cfg.policy.ai_disclosure
            if (
                not healed.ok
                and healed.stand_down_body
                and latest_brief is not None
                and disclose not in healed.stand_down_body
            ):
                gw.enqueue(
                    OutwardActionDraft(
                        kind="comment",
                        task_id=task.id,
                        payload={
                            "body": healed.stand_down_body,
                            "issue": chosen.number,
                            "research_brief_id": latest_brief.id,
                        },
                    )
                )
            art_log = Artifact(
                id=f"art_{uuid.uuid4().hex[:12]}",
                task_id=task.id,
                type="test_log",
                path=str(work / "verify.log"),
                content_hash=healed.last_hash,
                from_sandbox=True,
            )
            (work / "verify.log").write_text(healed.last_log, encoding="utf-8")
            art_patch = Artifact(
                id=f"art_{uuid.uuid4().hex[:12]}",
                task_id=task.id,
                type="patch",
                path=str(repro_dir / ".oss_agent_diff.patch"),
                content_hash=healed.patch_hash or healed.last_hash,
                from_sandbox=True,
            )
            session.add_all([art_log, art_patch])
            session.flush()
            art_ids = [art_log.id, art_patch.id]
            detail = {
                "verify_ok": healed.ok,
                "reason": healed.reason,
                "repair_attempts": healed.repair_attempts,
            }

        elif step == "quality_analysis":
            assert repro_dir is not None
            if not cfg.quality.enabled:
                passed = True
                gate_passed = True
                detail = {"skipped": True}
            else:
                quality = analyze_patch_quality(
                    workdir=repro_dir,
                    diff=patch_diff,
                    changed_files=changed_files,
                    config=cfg,
                    tests_verified=tests_verified,
                )
                artifact_data: dict[str, object] = {
                    "title": "code quality / security analysis",
                    "body": quality.to_markdown(),
                    "passed": quality.passed,
                    "block_reason": quality.block_reason,
                    "tool": quality.tool,
                    "counts": quality.count_by_severity(),
                }
                if cfg.quality.write_sarif:
                    sarif_path = work / "quality.sarif.json"
                    sarif_path.write_text(
                        json.dumps(quality.to_sarif(), indent=2),
                        encoding="utf-8",
                    )
                    artifact_data["sarif_path"] = str(sarif_path)
                svc.add_artifact(
                    order.id,
                    artifact_type="markdown",
                    data=artifact_data,
                    key="quality:strix",
                )
                # Node ran; IF routes on quality.passed.
                passed = True
                gate_passed = quality.passed
                detail = {
                    "passed": quality.passed,
                    "block_reason": quality.block_reason,
                    "findings": len(quality.findings),
                }

        elif step == "await_approval":
            orch.transition(task.id, TaskState.AWAITING_PR_APPROVAL, reason="factory-pr")
            pr_title = title_from_lessons(
                loop_fx.initial_fix.title,
                lesson_store.for_repo(loop_fx.repo),
            )
            pr_payload = build_pr_draft(
                title=pr_title,
                issue_number=chosen.number,
                rules=rules,
                evidence_artifact_ids=art_ids,
                test_log_excerpt=verify_log,
                diff_excerpt=patch_diff,
            )
            # Fresh research immediately before the outward PR action.
            latest_brief = research_engine.research(
                action_kind="pr",
                query=research_query,
                project_root=repo,
                issues_path=issues_path,
                memory=memory.snapshot(),
            )
            if latest_brief.passed:
                pr_payload = {
                    **pr_payload,
                    "research_brief_id": latest_brief.id,
                    "research_summary": latest_brief.summary[:500],
                }
            errors = DefaultRulesEngine().validate_pr_draft(pr_payload, rules)
            if not latest_brief.passed:
                passed = False
                detail = {
                    "error": "research_required_before_pr",
                    "research_brief_id": latest_brief.id,
                    "block_reason": latest_brief.block_reason,
                }
            elif errors:
                passed = False
                detail = {"errors": errors}
            else:
                pr_draft_id = gw.enqueue(
                    OutwardActionDraft(
                        kind="pr",
                        task_id=task.id,
                        payload=pr_payload,
                        evidence_artifact_ids=art_ids,
                    )
                )
                svc.add_artifact(
                    order.id,
                    artifact_type="pr",
                    data={
                        "title": pr_payload["title"],
                        "draft_id": pr_draft_id,
                        "url": f"https://example.local/drafts/{pr_draft_id}",
                        "state": "draft",
                    },
                )
                if not loop_fx.sponsor.approve_pr:
                    waiting = True
                    passed = False
                    detail = {
                        "waiting": "sponsor_pr_approval",
                        "pr_draft_id": pr_draft_id,
                    }
                else:
                    await gw.send_approved(
                        pr_draft_id,
                        approved_by=loop_fx.sponsor.approved_by,
                    )
                    orch.transition(task.id, TaskState.SUBMITTED, reason="sponsor-approved-pr")
                    orch.transition(task.id, TaskState.IN_REVIEW, reason="fixture-pr-open")
                    passed = True
                    detail = {
                        "pr_draft_id": pr_draft_id,
                        "pr_sent": True,
                        "approved_by": loop_fx.sponsor.approved_by,
                        "live_github_write": False,
                    }

        elif step == "review_iterate":
            if latest_brief is None or not latest_brief.passed:
                passed = False
                detail = {"error": "research_required_before_review"}
            else:
                plan = plan_review_iteration(
                    comments=loop_fx.review_comments,
                    ci_rounds=loop_fx.ci_rounds,
                    known_flakes=loop_fx.known_flakes,
                    max_ci_retries=cfg.policy.rate_limits.max_ci_retries,
                    accept_design=loop_fx.sponsor.accept_design,
                )
                assert repro_dir is not None
                monitored = await monitor_until_merged(
                    runner,
                    task_id=f"{task.id}-monitor",
                    workdir=repro_dir,
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
                detail = {
                    "reason": plan.reason,
                    "repair": plan.repair,
                    "flake": plan.flake_detected,
                    "attempts": plan.attempts_used,
                    "monitor_status": monitored.status,
                    "monitor_reason": monitored.reason,
                    "observations": monitored.observations_consumed,
                    "repair_attempts": monitored.repair_attempts,
                }
                follow_ok = True
                healed_ci = monitored.heal
                disclose = cfg.policy.ai_disclosure
                if monitored.status == "stand_down":
                    if monitored.stand_down_body and disclose not in monitored.stand_down_body:
                        gw.enqueue(
                            OutwardActionDraft(
                                kind="comment",
                                task_id=task.id,
                                payload={
                                    "body": monitored.stand_down_body,
                                    "issue": chosen.number,
                                    "research_brief_id": latest_brief.id,
                                },
                            )
                        )
                    detail["stand_down"] = True
                    follow_ok = False
                    passed = True
                    gate_passed = False
                elif plan.repair:
                    if healed_ci is None or not healed_ci.ok:
                        follow_ok = False
                        passed = False
                        detail["error"] = monitored.reason
                        if monitored.stand_down_body and disclose not in monitored.stand_down_body:
                            gw.enqueue(
                                OutwardActionDraft(
                                    kind="comment",
                                    task_id=task.id,
                                    payload={
                                        "body": monitored.stand_down_body,
                                        "issue": chosen.number,
                                        "research_brief_id": latest_brief.id,
                                    },
                                )
                            )
                    else:
                        for note in lesson_rules(lesson_store.for_repo(loop_fx.repo)):
                            memory.add_style_note(note)
                        orch.transition(
                            task.id,
                            TaskState.CHANGES_REQUESTED,
                            reason="ci-failure",
                        )
                        orch.transition(task.id, TaskState.IMPLEMENTING, reason="heal")
                        detail["heal_reason"] = healed_ci.reason
                        detail["from_lesson"] = healed_ci.from_lesson
                        quality2 = analyze_patch_quality(
                            workdir=repro_dir,
                            diff=healed_ci.diff,
                            changed_files=list(healed_ci.files_changed),
                            config=cfg,
                            tests_verified=True,
                        )
                        follow_log = Artifact(
                            id=f"art_{uuid.uuid4().hex[:12]}",
                            task_id=task.id,
                            type="test_log",
                            path=str(work / "verify-followup.log"),
                            content_hash=healed_ci.last_hash,
                            from_sandbox=True,
                        )
                        follow_patch = Artifact(
                            id=f"art_{uuid.uuid4().hex[:12]}",
                            task_id=task.id,
                            type="patch",
                            path=str(repro_dir / ".oss_agent_diff.patch"),
                            content_hash=healed_ci.patch_hash or healed_ci.last_hash,
                            from_sandbox=True,
                        )
                        (work / "verify-followup.log").write_text(
                            healed_ci.last_log, encoding="utf-8"
                        )
                        session.add_all([follow_log, follow_patch])
                        session.flush()
                        if not quality2.passed:
                            follow_ok = False
                            passed = True
                            gate_passed = False
                            detail["block_reason"] = quality2.block_reason
                        elif not loop_fx.sponsor.approve_follow_up:
                            follow_ok = False
                            waiting = True
                            passed = False
                            detail["waiting"] = "sponsor_follow_up_approval"
                        else:
                            orch.transition(
                                task.id,
                                TaskState.SELF_REVIEW,
                                reason="follow-up-verified",
                            )
                            orch.transition(
                                task.id,
                                TaskState.AWAITING_PR_APPROVAL,
                                reason="follow-up-ready",
                            )
                            push_body = (
                                f"Follow-up for #{chosen.number} after review.\n\n"
                                "### Diff (sandbox-captured)\n```diff\n"
                                + healed_ci.diff[:4000]
                                + "\n```\n"
                            )
                            push_id = gw.enqueue(
                                OutwardActionDraft(
                                    kind="push",
                                    task_id=task.id,
                                    payload={
                                        "body": push_body,
                                        "issue": chosen.number,
                                        "branch": f"oss-agent/issue-{chosen.number}",
                                        "force": False,
                                        "research_brief_id": latest_brief.id,
                                    },
                                    evidence_artifact_ids=[follow_log.id, follow_patch.id],
                                )
                            )
                            await gw.send_approved(
                                push_id,
                                approved_by=loop_fx.sponsor.approved_by,
                            )
                            orch.transition(
                                task.id,
                                TaskState.SUBMITTED,
                                reason="sponsor-approved-follow-up",
                            )
                            orch.transition(
                                task.id,
                                TaskState.IN_REVIEW,
                                reason="follow-up-submitted",
                            )
                            detail["push_id"] = push_id
                if follow_ok and not waiting and not plan.ready_to_merge:
                    passed = True
                    gate_passed = False
                elif follow_ok and not waiting:
                    if plan.review.items and not loop_fx.sponsor.approve_replies:
                        waiting = True
                        passed = False
                        detail["waiting"] = "sponsor_reply_approval"
                    else:
                        reply_ids: list[str] = []
                        for payload in reply_payloads(
                            plan,
                            issue_number=chosen.number,
                            research_brief_id=latest_brief.id,
                        ):
                            reply_id = gw.enqueue(
                                OutwardActionDraft(
                                    kind="reply",
                                    task_id=task.id,
                                    payload=payload,
                                )
                            )
                            await gw.send_approved(
                                reply_id,
                                approved_by=loop_fx.sponsor.approved_by,
                            )
                            reply_ids.append(reply_id)
                        passed = True
                        gate_passed = plan.ready_to_merge
                        detail["replies"] = len(reply_ids)

        elif step == "await_merge":
            if not loop_fx.sponsor.approve_merge:
                waiting = True
                passed = False
                detail = {"waiting": "sponsor_merge_approval"}
            else:
                audit.append(
                    actor="sponsor",
                    event="fixture.merge_approved",
                    payload={
                        "approved_by": loop_fx.sponsor.approved_by,
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
                passed = True
                svc.add_artifact(
                    order.id,
                    artifact_type="markdown",
                    data={
                        "title": "fixture merge",
                        "body": (
                            "Sponsor approved the fixture merge transition. "
                            "No live GitHub write was performed."
                        ),
                        "approved_by": loop_fx.sponsor.approved_by,
                    },
                    key="merge:fixture",
                )
                detail = {
                    "merged": True,
                    "approved_by": loop_fx.sponsor.approved_by,
                    "live_github_write": False,
                }
        else:
            detail = {"error": f"unknown step {step}"}
            passed = False
            gate_passed = False

        item = WorkflowItem(
            json={
                "passed": bool(gate_passed and passed),
                "step": step,
                **{k: v for k, v in detail.items() if _jsonish(v)},
            }
        )
        if waiting:
            engine.complete(wf_exec, NodeRunResult(waiting=True, items=[item], detail=detail))
            execution = svc.advance_or_fail(execution.id, passed=False, detail=detail)
            break

        # App/wait nodes that must fail hard (not IF-gated).
        hard_fail = not passed and step not in {
            "project_research",
            "analyze",
            "quality_analysis",
        }
        if hard_fail:
            engine.complete(
                wf_exec,
                NodeRunResult(
                    success=False,
                    items=[item],
                    error=str(detail.get("error") or f"{step}_failed"),
                    detail=detail,
                ),
            )
            execution = svc.advance_or_fail(execution.id, passed=False, detail=detail)
            break

        engine.complete(
            wf_exec,
            NodeRunResult(success=True, items=[item], detail=detail),
        )
        line_ok = wf_exec.status != "error" and gate_passed and passed
        if line_ok:
            steps_passed.append(step)
        execution = svc.advance_or_fail(execution.id, passed=line_ok, detail=detail)

        if execution is not None and wf_exec.status == "running":
            nxt = engine.current_node(wf_exec)
            if nxt is None or nxt.name != execution.step_name:
                execution = svc.advance_or_fail(
                    execution.id,
                    passed=False,
                    detail={
                        "error": "workflow_factory_desync_after_advance",
                        "factory_step": execution.step_name,
                        "workflow_node": None if nxt is None else nxt.name,
                    },
                )
                break

    final = session.get(WorkOrder, order.id)
    assert final is not None
    expected_steps = [s["name"] for s in OSS_CONTRIBUTION_STEPS]
    if merged and pr_draft_id and steps_passed == expected_steps and wf_exec.status == "success":
        svc.update_status(
            order.id,
            state=WorkOrderState.CLOSED,
            result=CloseResult.COMPLETED,
            actor=loop_fx.sponsor.approved_by,
        )
        final = session.get(WorkOrder, order.id)
        assert final is not None
    elif wf_exec.status == "waiting":
        pass
    elif steps_passed != expected_steps or wf_exec.status != "success":
        svc.update_status(
            order.id,
            state=WorkOrderState.CLOSED,
            result=CloseResult.FAILED,
            actor="factory",
        )
        final = session.get(WorkOrder, order.id)
        assert final is not None

    return FactoryRunResult(
        factory_id=factory.id,
        work_order_id=order.id,
        final_state=final.state,
        result=final.result,
        steps_passed=steps_passed,
        pr_draft_id=pr_draft_id,
        analysis_recommendation=None if analysis is None else analysis.recommendation,
        workflow_execution_id=wf_exec.id,
        workflow_status=wf_exec.status,
        chosen_issue=chosen.number,
        discussion_action_id=discussion_action_id,
        merged=merged,
        task_state=task.state,
    )


def _jsonish(value: object) -> bool:
    return isinstance(value, str | int | float | bool | type(None) | list | dict)


def mark_execution_running(execution: WorkOrderExecution) -> None:
    """Hook for external runners; status already running on start."""
    _ = execution
