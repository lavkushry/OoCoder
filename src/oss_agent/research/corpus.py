"""Khoj-inspired local corpus indexer — project docs as a personal knowledge base."""

from __future__ import annotations

import re
from pathlib import Path

from oss_agent.research.models import ResearchHit, SourceKind

_DOC_NAMES: dict[str, SourceKind] = {
    "CONTRIBUTING.md": "contributing",
    "AI_POLICY.md": "ai_policy",
    "CODEOWNERS": "github_codeowners",
    "README.md": "local_corpus",
    "PULL_REQUEST_TEMPLATE.md": "local_corpus",
}

_TEXT_SUFFIXES = {".md", ".txt", ".rst", ".toml", ".yml", ".yaml"}


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]{2,}", text.lower()) if t}


def _overlap_score(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    doc = _tokenize(text)
    if not doc:
        return 0.0
    hit = len(query_tokens & doc)
    return hit / len(query_tokens)


def index_project_corpus(project_root: Path) -> list[tuple[Path, SourceKind, str]]:
    """Collect text documents under a project tree (Khoj local docs analogue)."""
    docs: list[tuple[Path, SourceKind, str]] = []
    if not project_root.is_dir():
        return docs
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in _DOC_NAMES:
            kind = _DOC_NAMES[path.name]
        elif path.suffix.lower() in _TEXT_SUFFIXES and "test" not in path.parts:
            kind = "local_corpus"
        else:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            docs.append((path, kind, text))
    return docs


def search_corpus(
    project_root: Path,
    query: str,
    *,
    limit: int = 8,
) -> list[ResearchHit]:
    """Keyword retrieval over the project corpus (offline semantic-search stand-in)."""
    q = _tokenize(query)
    hits: list[ResearchHit] = []
    for path, kind, text in index_project_corpus(project_root):
        score = _overlap_score(q, text)
        if score <= 0 and kind not in {"contributing", "ai_policy", "github_codeowners"}:
            continue
        # Always surface policy / contributing docs for contribution research.
        engagement = 5.0 if kind in {"contributing", "ai_policy"} else score * 10.0
        if kind == "github_codeowners":
            engagement = max(engagement, 4.0)
        snippet = text.strip().splitlines()[0][:180] if text.strip() else ""
        if path.is_relative_to(project_root):
            uri = str(path.relative_to(project_root))
        else:
            uri = str(path)
        hits.append(
            ResearchHit(
                source=kind,
                title=path.name,
                snippet=snippet,
                uri=uri,
                engagement_score=engagement,
                recency_score=1.0,
                confirmed=True,
            )
        )
    hits.sort(key=lambda h: h.rank_score, reverse=True)
    return hits[:limit]
