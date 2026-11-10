"""Paperclip-inspired pre-contribution analysis."""

from __future__ import annotations

import json
from pathlib import Path

from oss_agent.analysis.contribution import analyze_before_contribution
from oss_agent.config import AppConfig, GoalConfig, PolicyConfig
from oss_agent.protocols import IssueCandidate, UntrustedText
from oss_agent.scout.policy import ingest_project_rules
from oss_agent.scout.ranking import rank_issues

ROOT = Path(__file__).resolve().parents[1]


def _friendly_candidate() -> tuple:
    rules = ingest_project_rules(
        "fixtures/friendly_tiny",
        ROOT / "fixtures/projects/friendly_tiny",
    )
    issues = json.loads((ROOT / "fixtures/issues/friendly_tiny_issues.json").read_text())
    ranked = rank_issues("proj", issues)
    chosen = next(c for c in ranked if c.number == 3)
    return rules, chosen


def test_analysis_recommends_go_for_tractable_bug() -> None:
    rules, chosen = _friendly_candidate()
    report = analyze_before_contribution(chosen, rules, AppConfig())
    assert report.recommendation == "go"
    assert report.requires_human_approval is True
    assert report.goal.aligned is True
    assert report.budget.within_budget is True
    assert report.governance.ai_policy_allowed is True
    assert "<<<UNTRUSTED_REPO_CONTENT" in report.to_markdown()


def test_analysis_blocks_banned_policy() -> None:
    rules = ingest_project_rules("banned", ROOT / "fixtures/projects/banned_ai")
    candidate = IssueCandidate(
        project_id="p",
        number=1,
        title="docs typo",
        body=UntrustedText(source="issue:1", body="fix typo"),
        labels=["documentation"],
        score=5.0,
        score_reasons=["helpful_labels:documentation"],
        url="https://example.local/1",
    )
    report = analyze_before_contribution(candidate, rules, AppConfig())
    assert report.recommendation == "block"
    assert "ai_policy_status=banned" in report.governance.blockers


def test_analysis_blocks_design_epic() -> None:
    rules, _ = _friendly_candidate()
    issues = json.loads((ROOT / "fixtures/issues/friendly_tiny_issues.json").read_text())
    ranked = rank_issues("proj", issues)
    epic = next(c for c in ranked if c.number == 2)
    report = analyze_before_contribution(epic, rules, AppConfig())
    assert report.recommendation in {"block", "revise"}


def test_analysis_respects_budget_gate() -> None:
    rules, chosen = _friendly_candidate()
    cfg = AppConfig(goal=GoalConfig(max_usd_per_task=0.0))
    report = analyze_before_contribution(chosen, rules, cfg)
    assert report.budget.within_budget is False
    assert report.recommendation == "block"


def test_kill_switch_blocks_analysis() -> None:
    rules, chosen = _friendly_candidate()
    cfg = AppConfig(policy=PolicyConfig(kill_switch=True))
    report = analyze_before_contribution(chosen, rules, cfg)
    assert report.recommendation == "block"
