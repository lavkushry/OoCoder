"""Rules engine: AI policy classification and PR draft validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from oss_agent.protocols import ActionKind, AiPolicyStatus, ProjectRules

_BANNED_PATTERNS = [
    re.compile(
        r"\bno\s+ai[- ]?(generated|assisted)?\s+(prs?|pull requests?|contributions?)\b",
        re.I,
    ),
    re.compile(
        r"\bai[- ]?(generated|assisted)\s+(prs?|contributions?)?\s+(are|is)\s+"
        r"(not\s+)?(banned|prohibited|forbidden)\b",
        re.I,
    ),
    re.compile(
        r"\b(ban|prohibit|forbid)\w*\s+.{0,40}\bai[- ]?(generated|assisted|prs?)\b",
        re.I,
    ),
    re.compile(r"\bwe do not accept\b.{0,40}\bai\b", re.I),
]

_ALLOWED_PATTERNS = [
    re.compile(
        r"\bai[- ]?(generated|assisted)\s+(prs?|contributions?)?\s+(are|is)\s+welcome\b",
        re.I,
    ),
    re.compile(r"\bai[- ]?assistance\s+is\s+allowed\b", re.I),
    re.compile(r"\bdisclose\b.{0,40}\bai[- ]?(assistance|generated|use)\b", re.I),
    re.compile(r"\bai[- ]?contributions?\s+(are|is)\s+accepted\b", re.I),
]


class DefaultRulesEngine:
    def classify_ai_policy(self, text: str | None) -> AiPolicyStatus:
        if text is None or not text.strip():
            return "unclear"
        for pat in _BANNED_PATTERNS:
            if pat.search(text):
                return "banned"
        for pat in _ALLOWED_PATTERNS:
            if pat.search(text):
                return "allowed"
        # Mentions AI but no clear allow/ban → unclear
        if re.search(r"\bai[- ]?(generated|assisted|tool|agent)\b", text, re.I):
            return "unclear"
        return "unclear"

    def validate_pr_draft(self, draft: Mapping[str, Any], rules: ProjectRules) -> list[str]:
        errors: list[str] = []
        if rules.ai_policy_status != "allowed":
            errors.append(f"ai_policy_status={rules.ai_policy_status}")
        body = str(draft.get("body", ""))
        if not body.strip():
            errors.append("empty_body")
        if rules.pr_template and "{{" not in rules.pr_template and len(body) < 40:
            errors.append("body_shorter_than_template_expectation")
        if not draft.get("evidence_artifact_ids"):
            errors.append("missing_evidence")
        title = str(draft.get("title", ""))
        if not title.strip():
            errors.append("empty_title")
        return errors

    def rate_limit_ok(self, project_id: str, kind: ActionKind) -> bool:
        # Gateway owns counters; default engine is permissive until wired to DB in M2.
        _ = (project_id, kind)
        return True
