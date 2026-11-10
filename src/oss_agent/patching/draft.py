"""Produce minimal diffs and PR draft payloads from sandbox evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from oss_agent.protocols import ProjectRules


@dataclass(frozen=True)
class PatchResult:
    diff: str
    files_changed: list[str]
    content_hash: str
    workdir: Path


def apply_minimal_fix(repo_dir: Path, *, module_relpath: str, old: str, new: str) -> PatchResult:
    """Apply a surgical text replacement and return a unified diff string."""
    target = repo_dir / module_relpath
    original = target.read_text(encoding="utf-8")
    if old not in original:
        msg = f"old snippet not found in {module_relpath}"
        raise ValueError(msg)
    updated = original.replace(old, new, 1)
    target.write_text(updated, encoding="utf-8")
    diff = _unified_diff(module_relpath, original, updated)
    (repo_dir / ".oss_agent_diff.patch").write_text(diff, encoding="utf-8")
    digest = hashlib.sha256(diff.encode()).hexdigest()
    return PatchResult(
        diff=diff,
        files_changed=[module_relpath],
        content_hash=digest,
        workdir=repo_dir,
    )


def unified_diff(path: str, before: str, after: str) -> str:
    """Unified diff for a repair. Callers persist this as sandbox-captured evidence."""
    return _unified_diff(path, before, after)


def _unified_diff(path: str, before: str, after: str) -> str:
    import difflib

    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(lines)


def build_pr_draft(
    *,
    title: str,
    issue_number: int,
    rules: ProjectRules,
    evidence_artifact_ids: list[str],
    test_log_excerpt: str,
    diff_excerpt: str,
) -> dict[str, object]:
    template = rules.pr_template or "## Summary\n\n## Test plan\n"
    body = template
    if "## Summary" in body:
        body = body.replace(
            "## Summary",
            f"## Summary\n\nFixes #{issue_number}.\n\nMinimal change with tests.\n",
            1,
        )
    else:
        body = f"## Summary\n\nFixes #{issue_number}.\n\n" + body
    if "## Test plan" in body:
        body = body.replace(
            "## Test plan",
            "## Test plan\n\n```\n" + test_log_excerpt[-2000:] + "\n```\n",
            1,
        )
    body += "\n### Diff (sandbox-captured)\n```diff\n" + diff_excerpt[:4000] + "\n```\n"
    return {
        "title": title,
        "body": body,
        "issue": issue_number,
        "evidence_artifact_ids": evidence_artifact_ids,
    }
