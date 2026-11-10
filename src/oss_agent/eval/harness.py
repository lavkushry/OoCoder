"""Replay agent against local fixtures; write docs/EVAL_REPORT.md."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from oss_agent.pipeline import run_fixture_pipeline
from oss_agent.scout.policy import ingest_project_rules
from oss_agent.scout.ranking import rank_issues


async def _run(root: Path, out: Path) -> dict[str, float]:
    work = root / "data" / "eval"
    work.mkdir(parents=True, exist_ok=True)
    # Repeatable offline replay: drop the previous sqlite file before insert.
    db_path = work / "demo.db"
    if db_path.exists():
        db_path.unlink()
    fixtures = root / "fixtures"
    issues = json.loads((fixtures / "issues" / "friendly_tiny_issues.json").read_text())
    ranked = rank_issues("proj", issues)
    # Gold: issue 1 and 3 are good shortlist; 2 is not.
    top2 = {c.number for c in ranked[:2]}
    gold = {1, 3}
    precision = len(top2 & gold) / max(len(top2), 1)

    banned = ingest_project_rules("banned", fixtures / "projects" / "banned_ai")
    policy_ok = 1.0 if banned.ai_policy_status == "banned" else 0.0

    result = await run_fixture_pipeline(root, work)
    repro = 1.0 if result.reproduced else 0.0
    draft_quality = 1.0 if result.pr_draft_id and not result.pr_errors else 0.0
    merged = 1.0 if result.merged and result.discussion_disclosed else 0.0

    metrics: dict[str, float] = {
        "shortlist_precision_at_2": precision,
        "policy_ban_detected": policy_ok,
        "reproduction_success": repro,
        "draft_quality": draft_quality,
        "flake_handling": 1.0 if result.flake_detected else 0.0,
        "fixture_merge": merged,
    }
    lines = [
        "# Eval report",
        "",
        "Offline replay against `fixtures/`.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    for k, v in metrics.items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("docs/EVAL_REPORT.md"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    metrics = asyncio.run(_run(root, args.out))
    print(metrics)
    if (
        metrics["shortlist_precision_at_2"] < 1.0
        or metrics["reproduction_success"] < 1.0
        or metrics["fixture_merge"] < 1.0
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
