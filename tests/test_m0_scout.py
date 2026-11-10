"""M0 acceptance: policy parser blocks banned AI; ranking works on fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss_agent.scout.memory import ProjectMemory
from oss_agent.scout.policy import assert_ai_allowed, ingest_project_rules
from oss_agent.scout.ranking import rank_issues

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_banned_ai_fixture_is_refused() -> None:
    rules = ingest_project_rules(
        "fixtures/banned_ai",
        FIXTURES / "projects" / "banned_ai",
    )
    assert rules.ai_policy_status == "banned"
    with pytest.raises(PermissionError, match="ai_policy_status=banned"):
        assert_ai_allowed(rules)


def test_friendly_project_is_allowed_and_builds_memory() -> None:
    root = FIXTURES / "projects" / "friendly_tiny"
    rules = ingest_project_rules("fixtures/friendly_tiny", root)
    assert rules.ai_policy_status == "allowed"
    assert_ai_allowed(rules)
    mem = ProjectMemory.from_rules_and_owners(rules.repo, rules.owners)
    assert "*" in mem.owners_map
    snap = mem.snapshot()
    assert snap["repo"] == "fixtures/friendly_tiny"


def test_ranking_prefers_tractable_docs_and_bugs() -> None:
    raw = json.loads((FIXTURES / "issues" / "friendly_tiny_issues.json").read_text())
    ranked = rank_issues("proj-friendly", raw)
    assert [c.number for c in ranked[:2]] == [1, 3] or ranked[0].number in {1, 3}
    assert ranked[-1].number == 2
    assert ranked[0].score > ranked[-1].score
    assert ranked[0].score_reasons
    # Untrusted wrapper present
    assert ranked[0].body.source.startswith("issue:")
