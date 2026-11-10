"""Diagnose a minimal repair from a captured check log and the worktree.

The log is data. Issue text and model prose are not inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from oss_agent.learn.models import is_one_off_typo
from oss_agent.review.classify import detect_flake

_ASSERT = re.compile(r"assert\s+(\d+)\s*==\s*(\d+)")
_WHERE = re.compile(r"where\s+\d+\s*=\s*([A-Za-z_]\w*)\s*\(")
_CALL = re.compile(r"assert\s+([A-Za-z_]\w*)\s*\(")
_PHRASE = re.compile(r"still says\s+['\"]?(.+?)['\"]?\s*$", re.IGNORECASE | re.MULTILINE)
_DEF = re.compile(r"^\s*def\s+(\w+)\s*\(")
_MAX_DELTA = 32
_MIN_PHRASE = 8


@dataclass(frozen=True)
class RepairPlan:
    signature: str
    module_relpath: str
    old: str
    new: str
    rule: str
    avoid_files: tuple[str, ...]
    flake: bool
    evidence_excerpt: str
    title_hint: str = ""

    def with_patterns(self, old: str, new: str) -> RepairPlan:
        return RepairPlan(
            signature=self.signature,
            module_relpath=self.module_relpath,
            old=old,
            new=new,
            rule=self.rule,
            avoid_files=self.avoid_files,
            flake=self.flake,
            evidence_excerpt=self.evidence_excerpt,
            title_hint=self.title_hint,
        )


def diagnose_failure(
    log: str,
    workdir: Path,
    *,
    known_flakes: list[str] | None = None,
) -> RepairPlan | None:
    """Return a repair derived from this log, or None when the log is not actionable."""
    known = known_flakes or []
    excerpt = log.strip()[:500]
    if detect_flake(log, known):
        return RepairPlan(
            signature=f"flake:{_slug(excerpt[:80])}",
            module_relpath="",
            old="",
            new="",
            rule="Do not retry a flake. Wait for a real failure before changing code.",
            avoid_files=(),
            flake=True,
            evidence_excerpt=excerpt,
        )
    if is_one_off_typo(log):
        return None
    phrase = _phrase_plan(log, workdir, excerpt)
    if phrase is not None:
        return phrase
    return _arithmetic_plan(log, workdir, excerpt)


def _phrase_plan(log: str, workdir: Path, excerpt: str) -> RepairPlan | None:
    match = _PHRASE.search(log)
    if match is None:
        return None
    phrase = match.group(1).strip().strip(".").strip()
    if len(phrase) < _MIN_PHRASE:
        return None
    found = _find_phrase_line(workdir, phrase)
    if found is None:
        return None
    relpath, line, func = found
    indent = re.match(r"\s*", line)
    prefix = indent.group(0) if indent else ""
    new = f'{prefix}"""Return the result of {func}."""'
    if new == line:
        return None
    signature = f"phrase:{_slug(phrase)}"
    return RepairPlan(
        signature=signature,
        module_relpath=relpath,
        old=line,
        new=new,
        rule=(
            f"When a captured check says {phrase!r}, replace that wording in {relpath}. "
            f"Keep the {func}() docstring factual. Do not copy the flagged phrase forward."
        ),
        avoid_files=(),
        flake=False,
        evidence_excerpt=excerpt,
        title_hint=f"docs: correct {func} docstring",
    )


def _arithmetic_plan(log: str, workdir: Path, excerpt: str) -> RepairPlan | None:
    parsed = _first_arithmetic(log)
    if parsed is None:
        return None
    actual, expected, func = parsed
    delta = actual - expected
    if delta == 0 or abs(delta) > _MAX_DELTA:
        return None
    found = _find_return_line(workdir, func, delta)
    if found is None:
        return None
    relpath, line = found
    needle = re.compile(rf" \+ {delta}(?!\d)" if delta > 0 else rf" - {-delta}(?!\d)")
    new = needle.sub("", line, count=1)
    if new == line:
        return None
    shown = f"+{delta}" if delta > 0 else str(delta)
    signature = f"assert:{func or 'unknown'}:delta:{shown}"
    return RepairPlan(
        signature=signature,
        module_relpath=relpath,
        old=line,
        new=new,
        rule=(
            f"In {relpath}, {func or 'the function'}() failed with actual={actual} "
            f"expected={expected} (delta {shown}). Replace `{line.strip()}` with "
            f"`{new.strip()}`. Do not edit the test to match the bug."
        ),
        avoid_files=(),
        flake=False,
        evidence_excerpt=excerpt,
        title_hint=f"fix: {func or 'function'} delta {shown}",
    )


def _first_arithmetic(log: str) -> tuple[int, int, str] | None:
    match = _ASSERT.search(log)
    if match is None:
        return None
    start = max(0, match.start() - 400)
    window = log[start : match.end() + 400]
    where = _WHERE.search(window)
    call = _CALL.search(window)
    if where is not None:
        func = where.group(1)
    elif call is not None:
        func = call.group(1)
    else:
        func = ""
    return int(match.group(1)), int(match.group(2)), func


def _find_phrase_line(workdir: Path, phrase: str) -> tuple[str, str, str] | None:
    hits: list[tuple[str, str, str]] = []
    for path in _sources(workdir):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if phrase not in line:
                continue
            func = _function_above(lines, index)
            hits.append((path.relative_to(workdir).as_posix(), line, func))
    if not hits:
        return None
    hits.sort(key=lambda item: (0 if item[0].startswith("src/") else 1, len(item[0])))
    return hits[0]


def _find_return_line(workdir: Path, func: str, delta: int) -> tuple[str, str] | None:
    needle = re.compile(rf" \+ {delta}(?!\d)" if delta > 0 else rf" - {-delta}(?!\d)")
    hits: list[tuple[str, str]] = []
    for path in _sources(workdir):
        text = path.read_text(encoding="utf-8")
        line = _return_in_text(text, func, needle)
        if line is None:
            continue
        hits.append((path.relative_to(workdir).as_posix(), line))
    if len(hits) != 1:
        return None
    return hits[0]


def _return_in_text(text: str, func: str, needle: re.Pattern[str]) -> str | None:
    active = func == ""
    found: str | None = None
    for line in text.splitlines():
        defined = _DEF.match(line)
        if defined:
            active = func == "" or defined.group(1) == func
            continue
        if active and "return" in line and needle.search(line):
            if found is not None:
                return None
            found = line
    return found


def _function_above(lines: list[str], index: int) -> str:
    for cursor in range(index, -1, -1):
        defined = _DEF.match(lines[cursor])
        if defined:
            return defined.group(1)
    return "function"


def _sources(workdir: Path) -> list[Path]:
    files: list[Path] = []
    if not workdir.is_dir():
        return files
    for path in sorted(workdir.rglob("*.py")):
        parts = set(path.parts)
        if "__pycache__" in parts or "tests" in parts or path.name.startswith("test_"):
            continue
        files.append(path)
    return files


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug[:80] or "unknown"
