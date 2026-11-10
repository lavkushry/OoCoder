"""M3: fixture issue -> approved-ready PR draft with sandbox evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from oss_agent.pipeline import run_fixture_pipeline
from oss_agent.rules.engine import DefaultRulesEngine
from oss_agent.scout.policy import ingest_project_rules


@pytest.mark.asyncio
async def test_fixture_to_pr_draft(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = await run_fixture_pipeline(root, tmp_path)
    assert result.reproduced is True
    assert result.verify_ok is True
    assert result.pr_draft_id is not None
    assert result.pr_errors == []


def test_rules_engine_blocks_incomplete_draft() -> None:
    root = Path(__file__).resolve().parents[1]
    rules = ingest_project_rules(
        "fixtures/friendly_tiny",
        root / "fixtures" / "projects" / "friendly_tiny",
    )
    engine = DefaultRulesEngine()
    errors = engine.validate_pr_draft({"title": "", "body": ""}, rules)
    assert "empty_title" in errors
    assert "missing_evidence" in errors
