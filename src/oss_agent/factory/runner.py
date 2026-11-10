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
from oss_agent.orchestrator.engine import Orchestrator
from oss_agent.orchestrator.states import TaskState
from oss_agent.patching.draft import apply_minimal_fix, build_pr_draft
from oss_agent.patching.verify import run_local_verification
from oss_agent.protocols import OutwardActionDraft, UntrustedText
from oss_agent.quality.analyzer import analyze_patch_quality
from oss_agent.research import ResearchBrief, ResearchEngine
from oss_agent.rules.engine import DefaultRulesEngine
from oss_agent.sandbox import make_sandbox_runner
from oss_agent.sandbox.repro import reproduce_issue
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


async def run_oss_factory_demo(root: Path, work: Path, session: Session) -> FactoryRunResult:
    """
    Create a factory, intake a fixture issue as a work order, and run the
    oss-contribution workflow (n8n-style graph) through Strix quality → PR draft.
    """
    fixtures = root / "fixtures"
    repo = fixtures / "projects" / "friendly_tiny"
    rules = ingest_project_rules("fixtures/friendly_tiny", repo)
    assert_ai_allowed(rules)
    issues = json.loads((fixtures / "issues" / "friendly_tiny_issues.json").read_text())
    ranked = rank_issues("proj-friendly", issues)
    chosen = next(c for c in ranked if c.number == 3)

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

    cfg = AppConfig()
    audit = FileAndDbAuditLog(work / "audit.jsonl", session)
    orch = Orchestrator(session, cfg, audit)
    gw = GitHubGatewayService(session, cfg, audit)
    runner = make_sandbox_runner(cfg)
    task = orch.create_task(
        project_id="proj-friendly",
        issue_id=None,
        state=TaskState.REPRODUCING,
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
    repro_dir: Path | None = None
    patch_diff = ""
    changed_files: list[str] = []
    verify_log = ""
    tests_verified = False
    art_ids: list[str] = []
    analysis: ContributionAnalysisReport | None = None
    research_engine = ResearchEngine(cfg)
    issues_path = fixtures / "issues" / "friendly_tiny_issues.json"
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
            passed = True
            detail = {"score": chosen.score}

        elif step == "reproduce":
            repro = await reproduce_issue(
                runner,
                task_id=task.id,
                source_repo=repo,
                issue=UntrustedText(source=chosen.body.source, body=chosen.body.body),
            )
            passed = repro.repro_failed_as_expected
            repro_dir = repro.workdir / "repo"
            detail = {"evidence_hash": repro.evidence_hash}
            orch.transition(task.id, TaskState.DESIGNING, reason="factory-repro")

        elif step == "implement":
            assert repro_dir is not None
            orch.transition(task.id, TaskState.IMPLEMENTING, reason="factory-implement")
            patch = apply_minimal_fix(
                repro_dir,
                module_relpath="src/friendly_tiny/__init__.py",
                old="return a + b + 1",
                new="return a + b",
            )
            patch_diff = patch.diff
            changed_files = list(patch.files_changed)
            passed = bool(patch.diff)
            detail = {"files": patch.files_changed}

        elif step == "verify":
            assert repro_dir is not None
            orch.transition(task.id, TaskState.SELF_REVIEW, reason="factory-verify")
            verify = await run_local_verification(runner, task_id=task.id, workdir=repro_dir)
            passed = verify.ok
            tests_verified = verify.ok
            verify_log = verify.log
            art_log = Artifact(
                id=f"art_{uuid.uuid4().hex[:12]}",
                task_id=task.id,
                type="test_log",
                path=str(work / "verify.log"),
                content_hash=verify.content_hash,
                from_sandbox=True,
            )
            (work / "verify.log").write_text(verify.log, encoding="utf-8")
            art_patch = Artifact(
                id=f"art_{uuid.uuid4().hex[:12]}",
                task_id=task.id,
                type="patch",
                path=str(repro_dir / ".oss_agent_diff.patch"),
                content_hash=verify.content_hash,
                from_sandbox=True,
            )
            session.add_all([art_log, art_patch])
            session.flush()
            art_ids = [art_log.id, art_patch.id]
            detail = {"verify_ok": verify.ok}

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
            pr_payload = build_pr_draft(
                title="fix off-by-one in add()",
                issue_number=3,
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
                passed = True
                detail = {"pr_draft_id": pr_draft_id}
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
    if pr_draft_id and steps_passed == expected_steps and wf_exec.status == "success":
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
    )


def _jsonish(value: object) -> bool:
    return isinstance(value, str | int | float | bool | type(None) | list | dict)


def mark_execution_running(execution: WorkOrderExecution) -> None:
    """Hook for external runners; status already running on start."""
    _ = execution
