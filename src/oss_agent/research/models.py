"""Research brief models — Khoj retrieval + last30days-style scored sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

SourceKind = Literal[
    "local_corpus",  # Khoj-inspired personal/project docs
    "github_issues",
    "github_codeowners",
    "contributing",
    "ai_policy",
    "memory",
    "fixture_signal",
]


@dataclass(frozen=True)
class ResearchHit:
    source: SourceKind
    title: str
    snippet: str
    uri: str = ""
    # last30days-style engagement / people-signal proxy (higher = stronger).
    engagement_score: float = 0.0
    # Recency weight inside the research window (1.0 = fresh).
    recency_score: float = 1.0
    confirmed: bool = False

    @property
    def rank_score(self) -> float:
        return self.engagement_score * 0.7 + self.recency_score * 0.3


@dataclass
class ResearchBrief:
    """Durable research artifact required before outward actions."""

    id: str
    query: str
    action_kind: str
    hits: list[ResearchHit] = field(default_factory=list)
    summary: str = ""
    window_days: int = 30
    tool: str = "oss-agent-khoj-last30days"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    passed: bool = True
    block_reason: str | None = None

    def top_hits(self, n: int = 5) -> list[ResearchHit]:
        return sorted(self.hits, key=lambda h: h.rank_score, reverse=True)[:n]

    def to_markdown(self) -> str:
        lines = [
            "# Project research brief",
            "",
            f"**Query:** {self.query}",
            f"**Action:** {self.action_kind}",
            f"**Window:** last {self.window_days} days (engagement-scored)",
            f"**Tool:** {self.tool}",
            f"**Passed:** {self.passed}",
            "",
            "## Summary",
            "",
            self.summary or "_No summary._",
            "",
            "## Top sources",
            "",
        ]
        if not self.hits:
            lines.append("_No hits._")
        for h in self.top_hits():
            lines.append(
                f"- **[{h.source}]** {h.title} "
                f"(rank={h.rank_score:.2f}, engagement={h.engagement_score:.1f})"
            )
            if h.snippet:
                lines.append(f"  - {h.snippet[:200]}")
            if h.uri:
                lines.append(f"  - `{h.uri}`")
        if self.block_reason:
            lines.extend(["", f"**Block:** {self.block_reason}"])
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "action_kind": self.action_kind,
            "summary": self.summary,
            "window_days": self.window_days,
            "tool": self.tool,
            "created_at": self.created_at,
            "passed": self.passed,
            "block_reason": self.block_reason,
            "hits": [
                {
                    "source": h.source,
                    "title": h.title,
                    "snippet": h.snippet,
                    "uri": h.uri,
                    "engagement_score": h.engagement_score,
                    "recency_score": h.recency_score,
                    "rank_score": h.rank_score,
                }
                for h in self.hits
            ],
        }
