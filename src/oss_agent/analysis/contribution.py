"""
Before-contribution analysis, inspired by Paperclip control-plane ideas:

- Goal alignment (why this work exists)
- Budget / cost gate
- Governance checklist (AI policy, scope, kill switch)
- Readiness score and go / no-go recommendation
- Human approval required before contribution execution begins

Repo issue text is treated as UntrustedText data only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from oss_agent.config import AppConfig, GoalConfig
from oss_agent.protocols import IssueCandidate, ProjectRules, UntrustedText

Recommendation = Literal["go", "revise", "block"]


@dataclass(frozen=True)
class GoalAlignment:
    company_goal: str
    contribution_goal: str
    why_this_issue: str
    aligned: bool
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BudgetCheck:
    estimated_tokens: int
    max_tokens_per_task: int
    estimated_usd: float
    max_usd_per_task: float
    within_budget: bool


@dataclass(frozen=True)
class GovernanceCheck:
    ai_policy_allowed: bool
    kill_switch_clear: bool
    scope_allowed: bool
    scope_label: str
    blockers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContributionAnalysisReport:
    """Durable analysis artifact attached to a work order before contribution."""

    issue_number: int
    issue_title: str
    untrusted_issue: UntrustedText
    goal: GoalAlignment
    budget: BudgetCheck
    governance: GovernanceCheck
    readiness_score: float
    recommendation: Recommendation
    plan: list[str]
    risks: list[str]
    requires_human_approval: bool = True

    def to_markdown(self) -> str:
        lines = [
            "# Pre-contribution analysis (Paperclip-style)",
            "",
            f"**Recommendation:** `{self.recommendation}`",
            f"**Readiness:** {self.readiness_score:.2f}",
            f"**Requires human approval:** {self.requires_human_approval}",
            "",
            "## Goal alignment",
            f"- Company goal: {self.goal.company_goal}",
            f"- Contribution goal: {self.goal.contribution_goal}",
            f"- Why this issue: {self.goal.why_this_issue}",
            f"- Aligned: {self.goal.aligned}",
            "",
            "## Budget",
            (f"- Est. tokens {self.budget.estimated_tokens} / {self.budget.max_tokens_per_task}"),
            (f"- Est. USD {self.budget.estimated_usd:.2f} / {self.budget.max_usd_per_task:.2f}"),
            f"- Within budget: {self.budget.within_budget}",
            "",
            "## Governance",
            f"- AI policy allowed: {self.governance.ai_policy_allowed}",
            f"- Kill switch clear: {self.governance.kill_switch_clear}",
            f"- Scope ({self.governance.scope_label}): {self.governance.scope_allowed}",
        ]
        if self.governance.blockers:
            lines.append("- Blockers:")
            lines.extend(f"  - {b}" for b in self.governance.blockers)
        lines.extend(["", "## Plan"])
        lines.extend(f"1. {p}" for p in self.plan)
        lines.extend(["", "## Risks"])
        lines.extend(f"- {r}" for r in self.risks)
        lines.extend(
            [
                "",
                "## Untrusted issue (data only)",
                f'<<<UNTRUSTED_REPO_CONTENT source="{self.untrusted_issue.source}">>',
                self.untrusted_issue.body[:2000],
                "<<<END_UNTRUSTED_REPO_CONTENT>>>",
            ]
        )
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _infer_scope(candidate: IssueCandidate) -> str:
    labels = {lab.lower() for lab in candidate.labels}
    title = candidate.title.lower()
    body = candidate.body.body.lower()
    blob = f"{title}\n{body}\n{' '.join(labels)}"
    if any(k in blob for k in ("doc", "readme", "typo")):
        return "docs"
    if "lint" in blob or "format" in blob:
        return "lint"
    if "test" in blob:
        return "tests"
    if "bug" in labels or "fix" in blob or "off-by-one" in blob:
        return "bugfix"
    return "unknown"


def _estimate_tokens(candidate: IssueCandidate) -> int:
    # Cheap heuristic: context size + fixed implementation overhead.
    base = 8_000
    return base + min(len(candidate.body.body), 8_000) + 4_000 * max(1, len(candidate.labels))


def analyze_before_contribution(
    candidate: IssueCandidate,
    rules: ProjectRules,
    config: AppConfig,
    *,
    goal: GoalConfig | None = None,
) -> ContributionAnalysisReport:
    """Run Paperclip-style control-plane checks before any contribution work."""
    goal_cfg = goal or config.goal
    scope = _infer_scope(candidate)
    scope_allowed = scope in set(config.policy.allowed_scope)
    ai_ok = rules.ai_policy_status == "allowed"
    kill_clear = not config.policy.kill_switch

    blockers: list[str] = []
    if not ai_ok:
        blockers.append(f"ai_policy_status={rules.ai_policy_status}")
    if not kill_clear:
        blockers.append("kill_switch_engaged")
    if not scope_allowed:
        blockers.append(f"scope_not_allowed:{scope}")
    if any(r in candidate.score_reasons for r in ("already_assigned", "has_linked_pr")):
        blockers.append("issue_not_free")
    if "likely_design_scope" in candidate.score_reasons:
        blockers.append("design_scope")

    est_tokens = _estimate_tokens(candidate)
    est_usd = (est_tokens / 1_000_000.0) * goal_cfg.usd_per_million_tokens
    budget = BudgetCheck(
        estimated_tokens=est_tokens,
        max_tokens_per_task=config.models.max_tokens_per_task,
        estimated_usd=round(est_usd, 4),
        max_usd_per_task=goal_cfg.max_usd_per_task,
        within_budget=(
            est_tokens <= config.models.max_tokens_per_task and est_usd <= goal_cfg.max_usd_per_task
        ),
    )

    why = (
        f"Issue #{candidate.number} scored {candidate.score:.1f} "
        f"({', '.join(candidate.score_reasons[:3]) or 'no reasons'})"
    )
    aligned = (
        scope_allowed
        and ai_ok
        and candidate.score >= goal_cfg.min_issue_score
        and "likely_design_scope" not in candidate.score_reasons
    )
    goal_alignment = GoalAlignment(
        company_goal=goal_cfg.company_goal,
        contribution_goal=goal_cfg.contribution_goal,
        why_this_issue=why,
        aligned=aligned,
        notes=[] if aligned else ["weak_goal_fit"],
    )

    governance = GovernanceCheck(
        ai_policy_allowed=ai_ok,
        kill_switch_clear=kill_clear,
        scope_allowed=scope_allowed,
        scope_label=scope,
        blockers=blockers,
    )

    # Readiness: Paperclip-style composite before hiring the coding agent.
    readiness = 0.0
    readiness += 0.35 if goal_alignment.aligned else 0.0
    readiness += 0.25 if budget.within_budget else 0.0
    readiness += 0.25 if (ai_ok and kill_clear and scope_allowed) else 0.0
    readiness += 0.15 if candidate.score >= goal_cfg.min_issue_score else 0.0
    readiness = round(min(readiness, 1.0), 3)

    if blockers or not budget.within_budget:
        recommendation: Recommendation = "block"
    elif readiness < goal_cfg.min_readiness or not goal_alignment.aligned:
        recommendation = "revise"
    else:
        recommendation = "go"

    plan = [
        "Confirm goal alignment and sponsor approval of this analysis",
        "Reproduce the issue in the sandbox with a failing test",
        "Implement a minimal patch with tests",
        "Capture sandbox evidence and draft a PR for human approval",
    ]
    risks = list(blockers)
    if candidate.score < goal_cfg.min_issue_score:
        risks.append("low_shortlist_score")
    if len(candidate.body.body) > 4000:
        risks.append("large_context")

    return ContributionAnalysisReport(
        issue_number=candidate.number,
        issue_title=candidate.title,
        untrusted_issue=candidate.body,
        goal=goal_alignment,
        budget=budget,
        governance=governance,
        readiness_score=readiness,
        recommendation=recommendation,
        plan=plan,
        risks=risks or ["none_identified"],
        requires_human_approval=True,
    )
