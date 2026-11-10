"""
Research engine combining:

- Khoj-inspired local corpus retrieval (project docs as second brain)
- last30days-inspired multi-source scoring (engagement + recency window)

Offline by default: fixtures / local files only. No network writes.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from oss_agent.config import AppConfig, ResearchConfig
from oss_agent.research.corpus import search_corpus
from oss_agent.research.models import ResearchBrief, ResearchHit


def _issue_hits(issues_path: Path | None, query: str) -> list[ResearchHit]:
    if issues_path is None or not issues_path.is_file():
        return []
    try:
        issues = json.loads(issues_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    q = {t for t in query.lower().split() if len(t) > 2}
    hits: list[ResearchHit] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        blob = f"{issue.get('title', '')} {issue.get('body', '')}".lower()
        overlap = sum(1 for t in q if t in blob) if q else 0
        comments = float(issue.get("comments") or 0)
        # last30days ethos: people engagement (comments) beats editor SEO.
        engagement = comments * 2.0 + overlap * 3.0
        if engagement <= 0 and overlap == 0:
            continue
        hits.append(
            ResearchHit(
                source="github_issues",
                title=f"#{issue.get('number')} {issue.get('title', '')}",
                snippet=str(issue.get("body", ""))[:200],
                uri=str(issue.get("html_url", "")),
                engagement_score=engagement,
                recency_score=0.9,
                confirmed=True,
            )
        )
    return hits


def _memory_hits(memory: dict[str, Any] | None) -> list[ResearchHit]:
    if not memory:
        return []
    hits: list[ResearchHit] = []
    for note in memory.get("style_notes") or []:
        hits.append(
            ResearchHit(
                source="memory",
                title="style note",
                snippet=str(note)[:200],
                engagement_score=3.0,
                recency_score=1.0,
                confirmed=True,
            )
        )
    for flake in memory.get("flake_tests") or []:
        hits.append(
            ResearchHit(
                source="memory",
                title="known flake",
                snippet=str(flake)[:200],
                engagement_score=6.0,
                recency_score=1.0,
                confirmed=True,
            )
        )
    objections = memory.get("common_objections") or []
    for obj in objections[:3]:
        hits.append(
            ResearchHit(
                source="memory",
                title="common objection",
                snippet=str(obj)[:200],
                engagement_score=5.0,
                recency_score=0.8,
                confirmed=True,
            )
        )
    return hits


def _synthesize(query: str, action_kind: str, hits: list[ResearchHit]) -> str:
    if not hits:
        return (
            f"No project research hits for action={action_kind!r} query={query!r}. "
            "Proceed only with extreme caution."
        )
    top = sorted(hits, key=lambda h: h.rank_score, reverse=True)[:3]
    parts = [
        f"Research before action `{action_kind}` for `{query}` "
        f"({len(hits)} sources, engagement-ranked):"
    ]
    for h in top:
        parts.append(f"- {h.source}: {h.title} — {h.snippet[:120]}")
    return "\n".join(parts)


def research_before_action(
    *,
    action_kind: str,
    query: str,
    project_root: Path,
    issues_path: Path | None = None,
    memory: dict[str, Any] | None = None,
    config: AppConfig | ResearchConfig | None = None,
) -> ResearchBrief:
    """
    Run project research before any action (workflow step or outward gateway write).

    Combines Khoj local-corpus retrieval with last30days-style engagement scoring.
    """
    if isinstance(config, AppConfig):
        cfg = config.research
    elif isinstance(config, ResearchConfig):
        cfg = config
    else:
        cfg = ResearchConfig()

    hits: list[ResearchHit] = []
    if cfg.use_local_corpus:
        hits.extend(search_corpus(project_root, query, limit=cfg.max_hits))
    if cfg.use_issue_signals:
        hits.extend(_issue_hits(issues_path, query))
    if cfg.use_project_memory:
        hits.extend(_memory_hits(memory))

    # Deduplicate by (source, title)
    uniq: dict[tuple[str, str], ResearchHit] = {}
    for h in hits:
        key = (h.source, h.title)
        prev = uniq.get(key)
        if prev is None or h.rank_score > prev.rank_score:
            uniq[key] = h
    ranked = sorted(uniq.values(), key=lambda h: h.rank_score, reverse=True)[: cfg.max_hits]

    brief = ResearchBrief(
        id=f"res_{uuid.uuid4().hex[:12]}",
        query=query,
        action_kind=action_kind,
        hits=ranked,
        summary=_synthesize(query, action_kind, ranked),
        window_days=cfg.window_days,
    )
    if cfg.require_min_hits > 0 and len(ranked) < cfg.require_min_hits:
        brief.passed = False
        brief.block_reason = (
            f"need at least {cfg.require_min_hits} research hit(s); got {len(ranked)}"
        )
    return brief


class ResearchEngine:
    """Stateful helper that caches the latest brief per action kind."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig()
        self._latest: dict[str, ResearchBrief] = {}

    def research(
        self,
        *,
        action_kind: str,
        query: str,
        project_root: Path,
        issues_path: Path | None = None,
        memory: dict[str, Any] | None = None,
    ) -> ResearchBrief:
        brief = research_before_action(
            action_kind=action_kind,
            query=query,
            project_root=project_root,
            issues_path=issues_path,
            memory=memory,
            config=self.config,
        )
        self._latest[action_kind] = brief
        self._latest["*"] = brief
        return brief

    def latest(self, action_kind: str | None = None) -> ResearchBrief | None:
        if action_kind:
            return self._latest.get(action_kind) or self._latest.get("*")
        return self._latest.get("*")
