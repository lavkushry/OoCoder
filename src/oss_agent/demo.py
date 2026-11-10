"""Offline end-to-end demo via SuperPlane-style factory board."""

from __future__ import annotations

import asyncio
from pathlib import Path

from oss_agent.db.session import create_all, make_engine, session_scope
from oss_agent.factory.board import group_by_lane
from oss_agent.factory.runner import run_oss_factory_demo
from oss_agent.factory.service import FactoryService
from oss_agent.heal.proof import run_heal_learn_proof


def run_offline_demo() -> None:
    root = Path(__file__).resolve().parents[2]
    work = root / "data" / "demo"
    work.mkdir(parents=True, exist_ok=True)
    # Fresh DB each demo run so factory keys/projects do not collide.
    db_path = work / "factory.db"
    if db_path.exists():
        db_path.unlink()
    engine = make_engine(f"sqlite:///{db_path}")
    create_all(engine)

    async def _run() -> None:
        with session_scope(engine) as session:
            result = await run_oss_factory_demo(root, work, session)
            svc = FactoryService(session)
            orders = svc.list_orders(result.factory_id)
            board = group_by_lane(orders)
            print("=== oss-agent factory demo ===")
            print("(research → n8n → Paperclip → SuperPlane → Strix)")
            print(f"factory_id={result.factory_id}")
            print(f"work_order_id={result.work_order_id}")
            print(f"workflow={result.workflow_execution_id} status={result.workflow_status}")
            print(f"analysis={result.analysis_recommendation}")
            print(f"chosen_issue=#{result.chosen_issue}")
            print(f"discussion_action_id={result.discussion_action_id}")
            print(f"steps_passed={result.steps_passed}")
            print(f"pr_draft_id={result.pr_draft_id}")
            print(f"merged={result.merged} task_state={result.task_state}")
            print(f"final_state={result.final_state} result={result.result}")
            print("Board lanes:")
            for lane, items in board.items():
                titles = ", ".join(f"#{o.number} {o.title}" for o in items) or "(empty)"
                print(f"  {lane}: {titles}")
            expected = [
                "project_research",
                "analyze",
                "await_analysis_approval",
                "intake",
                "discuss",
                "await_discuss_approval",
                "reproduce",
                "implement",
                "verify",
                "quality_analysis",
                "await_approval",
                "review_iterate",
                "await_merge",
            ]
            ok = (
                result.pr_draft_id
                and result.discussion_action_id
                and result.merged
                and result.task_state == "MERGED"
                and result.final_state == "closed"
                and result.result == "completed"
                and result.chosen_issue == 3
                and result.analysis_recommendation == "go"
                and result.workflow_status == "success"
                and result.steps_passed == expected
            )
            if not ok:
                raise SystemExit(1)
            proof = await run_heal_learn_proof(
                session,
                source_repo=root / "fixtures" / "projects" / "friendly_tiny",
                issues_path=root / "fixtures" / "issues" / "friendly_tiny_issues.json",
                work=work / "heal-learn",
            )
            learned = (
                proof.run_a.healed
                and not proof.run_a.first_verify_ok
                and proof.run_a.repair_attempts == 1
                and proof.run_b.first_verify_ok
                and proof.run_b.repair_attempts == 0
                and proof.lesson_id in proof.run_b.applied_lesson_ids
            )
            print(
                "heal_learn "
                f"run_a_attempts={proof.run_a.repair_attempts} "
                f"signature={proof.lesson_signature} "
                f"run_b_attempts={proof.run_b.repair_attempts} "
                f"lesson={proof.lesson_id}"
            )
            if not learned:
                raise SystemExit(1)
            print("demo OK — fixture loop merged after sponsor approval (no live GitHub write)")
            print("demo OK — run B avoided the failure by applying the stored lesson")

    asyncio.run(_run())
