"""High-level offline pipeline: scout -> repro -> patch -> PR draft -> review."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from oss_agent.audit.log import FileAndDbAuditLog
from oss_agent.config import AppConfig
from oss_agent.db.models import Artifact, Project
from oss_agent.db.session import create_all, make_engine, session_scope
from oss_agent.gateway.service import GitHubGatewayService
from oss_agent.orchestrator.engine import Orchestrator
from oss_agent.orchestrator.states import TaskState
from oss_agent.patching.draft import apply_minimal_fix, build_pr_draft
from oss_agent.patching.verify import run_local_verification
from oss_agent.protocols import OutwardActionDraft, UntrustedText
from oss_agent.research import research_before_action
from oss_agent.review.loop import handle_review_thread
from oss_agent.rules.engine import DefaultRulesEngine
from oss_agent.sandbox import make_sandbox_runner
from oss_agent.sandbox.repro import reproduce_issue
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


async def run_fixture_pipeline(root: Path, work: Path) -> PipelineResult:
    fixtures = root / "fixtures"
    repo = fixtures / "projects" / "friendly_tiny"
    rules = ingest_project_rules("fixtures/friendly_tiny", repo)
    assert_ai_allowed(rules)
    issues = json.loads((fixtures / "issues" / "friendly_tiny_issues.json").read_text())
    ranked = rank_issues("proj-friendly", issues)
    chosen = next(c for c in ranked if c.number == 3)

    engine = make_engine(f"sqlite:///{work / 'demo.db'}")
    create_all(engine)
    with session_scope(engine) as session:
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
        cfg = AppConfig()
        runner = make_sandbox_runner(cfg)
        audit = FileAndDbAuditLog(work / "audit.jsonl", session)
        orch = Orchestrator(session, cfg, audit)
        gw = GitHubGatewayService(session, cfg, audit)
        task = orch.create_task(
            project_id="proj-friendly",
            issue_id=None,
            state=TaskState.REPRODUCING,
        )

        repro = await reproduce_issue(
            runner,
            task_id=task.id,
            source_repo=repo,
            issue=UntrustedText(source=chosen.body.source, body=chosen.body.body),
        )
        assert repro.repro_failed_as_expected

        orch.transition(task.id, TaskState.DESIGNING, reason="repro-ok")
        orch.transition(task.id, TaskState.IMPLEMENTING, reason="design-trivial")

        patch = apply_minimal_fix(
            repro.workdir / "repo",
            module_relpath="src/friendly_tiny/__init__.py",
            old="return a + b + 1",
            new="return a + b",
        )
        verify = await run_local_verification(
            runner, task_id=task.id, workdir=repro.workdir / "repo"
        )
        orch.transition(task.id, TaskState.SELF_REVIEW, reason="patch-ready")
        if not verify.ok:
            return PipelineResult(
                shortlist_top=ranked[0].number,
                reproduced=True,
                verify_ok=False,
                pr_draft_id=None,
                pr_errors=["verify_failed"],
                review_classes=[],
                flake_detected=False,
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

        pr_payload = build_pr_draft(
            title="fix off-by-one in add()",
            issue_number=3,
            rules=rules,
            evidence_artifact_ids=[art_log.id, art_patch.id],
            test_log_excerpt=verify.log,
            diff_excerpt=patch.diff,
        )
        brief = research_before_action(
            action_kind="pr",
            query=f"{chosen.title}\n{chosen.body.body}",
            project_root=repo,
            issues_path=fixtures / "issues" / "friendly_tiny_issues.json",
            config=cfg,
        )
        pr_payload = {**pr_payload, "research_brief_id": brief.id}
        rules_engine = DefaultRulesEngine()
        errors = rules_engine.validate_pr_draft(pr_payload, rules)
        orch.transition(task.id, TaskState.AWAITING_PR_APPROVAL, reason="self-review-pass")
        draft_id = None
        if not errors and brief.passed:
            draft_id = gw.enqueue(
                OutwardActionDraft(
                    kind="pr",
                    task_id=task.id,
                    payload=pr_payload,
                    evidence_artifact_ids=[art_log.id, art_patch.id],
                )
            )

        review = handle_review_thread(
            [
                "nit: rename variable for clarity",
                "This test looks flaky on CI when the load is high",
                "Why did you choose this approach?",
            ],
            ci_log="flaky failure in tests/test_add.py intermittently",
            known_flakes=["tests/test_add.py"],
        )
        return PipelineResult(
            shortlist_top=ranked[0].number,
            reproduced=True,
            verify_ok=verify.ok,
            pr_draft_id=draft_id,
            pr_errors=errors,
            review_classes=[i["class"] for i in review.items],
            flake_detected=review.flake_detected,
        )
