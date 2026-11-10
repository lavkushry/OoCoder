"""Ingest CONTRIBUTING / AI policy / templates into ProjectRules."""

from __future__ import annotations

from pathlib import Path

from oss_agent.protocols import ProjectRules
from oss_agent.rules.engine import DefaultRulesEngine

_RULE_FILES = (
    ("CONTRIBUTING.md", "contributing"),
    ("CONTRIBUTING", "contributing"),
    ("CODE_OF_CONDUCT.md", "code_of_conduct"),
    (".github/PULL_REQUEST_TEMPLATE.md", "pr_template"),
    ("PULL_REQUEST_TEMPLATE.md", "pr_template"),
    ("OWNERS", "owners"),
    ("CODEOWNERS", "owners"),
    (".github/CODEOWNERS", "owners"),
    ("AI_POLICY.md", "ai_policy_text"),
    ("docs/AI_POLICY.md", "ai_policy_text"),
    (".github/AI_POLICY.md", "ai_policy_text"),
)


def _read(root: Path, rel: str) -> str | None:
    path = root / rel
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def ingest_project_rules(
    repo: str,
    root: Path,
    *,
    engine: DefaultRulesEngine | None = None,
) -> ProjectRules:
    """Parse local checkout docs into structured rules. Refuses unclear/banned via status."""
    engine = engine or DefaultRulesEngine()
    fields: dict[str, str | None] = {
        "contributing": None,
        "code_of_conduct": None,
        "pr_template": None,
        "owners": None,
        "ai_policy_text": None,
        "cla_or_dco": None,
    }
    extra: dict[str, str] = {}
    for rel, key in _RULE_FILES:
        text = _read(root, rel)
        if text is None:
            continue
        if key in fields and fields[key] is None:
            fields[key] = text
        else:
            extra[rel] = text

    # Also scan CONTRIBUTING for AI / DCO / CLA mentions if no dedicated AI policy file.
    contributing = fields["contributing"] or ""
    if fields["ai_policy_text"] is None and contributing and "ai" in contributing.lower():
        fields["ai_policy_text"] = contributing
    if "DCO" in contributing or "Developer Certificate of Origin" in contributing:
        fields["cla_or_dco"] = "DCO"
    if "CLA" in contributing or "Contributor License Agreement" in contributing:
        fields["cla_or_dco"] = fields["cla_or_dco"] or "CLA"

    status = engine.classify_ai_policy(fields["ai_policy_text"])
    return ProjectRules(
        repo=repo,
        contributing=fields["contributing"],
        code_of_conduct=fields["code_of_conduct"],
        pr_template=fields["pr_template"],
        owners=fields["owners"],
        ai_policy_text=fields["ai_policy_text"],
        ai_policy_status=status,
        cla_or_dco=fields["cla_or_dco"],
        extra=extra,
    )


def assert_ai_allowed(rules: ProjectRules) -> None:
    if rules.ai_policy_status != "allowed":
        msg = f"refusing project {rules.repo}: ai_policy_status={rules.ai_policy_status}"
        raise PermissionError(msg)
