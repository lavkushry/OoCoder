"""
Diff-scoped code quality / security analysis inspired by Strix.

Strix runs AI pentesters with PoC validation. This module provides the offline
deterministic gate used inside oss-agent: static review of the contribution
diff with Strix-compatible severity + SARIF. An optional external Strix CLI
adapter can be plugged in later via QualityAnalyzer Protocol.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from oss_agent.config import AppConfig, QualityConfig
from oss_agent.quality.findings import SEVERITY_RANK, Finding, QualityReport, Severity

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "secret-aws-key",
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "CWE-798",
    ),
    (
        "secret-generic-token",
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
        "CWE-798",
    ),
    (
        "secret-private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "CWE-321",
    ),
]

_DANGEROUS_PATTERNS: list[tuple[str, re.Pattern[str], Severity, str, str]] = [
    (
        "dangerous-eval",
        re.compile(r"\beval\s*\("),
        "high",
        "CWE-95",
        "Avoid eval(); use safe parsers or explicit allowlists.",
    ),
    (
        "dangerous-exec",
        re.compile(r"\bexec\s*\("),
        "high",
        "CWE-95",
        "Avoid exec() on untrusted input.",
    ),
    (
        "dangerous-pickle",
        re.compile(r"\bpickle\.loads?\s*\("),
        "high",
        "CWE-502",
        "Do not unpickle untrusted data.",
    ),
    (
        "dangerous-shell-true",
        re.compile(r"subprocess\.[a-zA-Z_]+\([^)]*shell\s*=\s*True"),
        "high",
        "CWE-78",
        "Use shell=False and argument lists.",
    ),
    (
        "dangerous-os-system",
        re.compile(r"\bos\.system\s*\("),
        "high",
        "CWE-78",
        "Prefer subprocess with explicit args.",
    ),
    (
        "sql-format-injection",
        re.compile(r"(?i)(execute|executemany)\s*\(\s*(f['\"]|['\"].*%[sd].*['\"]\.format)"),
        "high",
        "CWE-89",
        "Use parameterized queries.",
    ),
]


@runtime_checkable
class QualityAnalyzer(Protocol):
    def analyze(
        self,
        *,
        workdir: Path,
        diff: str,
        changed_files: Sequence[str],
        tests_verified: bool = False,
    ) -> QualityReport: ...


def _fid(rule: str, path: str, line: int, snippet: str) -> str:
    raw = f"{rule}:{path}:{line}:{snippet}"
    return "find_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _iter_added_lines(diff: str) -> list[tuple[str, int, str]]:
    """Return (path, line_no, text) for added lines in a unified diff."""
    path = ""
    new_line = 0
    out: list[tuple[str, int, str]] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if line.startswith("@@"):
            # @@ -a,b +c,d @@
            m = re.search(r"\+(\d+)", line)
            new_line = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.append((path, new_line, line[1:]))
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif (
            not line.startswith("\\")
            and path
            and not line.startswith("+")
            and not line.startswith("-")
        ):
            new_line += 1
    return out


def analyze_patch_quality(
    *,
    workdir: Path,
    diff: str,
    changed_files: Sequence[str],
    config: AppConfig | None = None,
    tests_verified: bool = False,
) -> QualityReport:
    cfg = (config or AppConfig()).quality
    findings: list[Finding] = []
    files = list(changed_files)

    # Size / scope quality gates (contribution hygiene).
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    if len(files) > cfg.max_files_changed:
        findings.append(
            Finding(
                id=_fid("quality-too-many-files", "", 0, str(len(files))),
                title="Diff touches too many files",
                severity="medium",
                rule_id="quality-too-many-files",
                message=(f"Changed {len(files)} files; max allowed is {cfg.max_files_changed}."),
                remediation="Split into smaller focused pull requests.",
            )
        )
    if len(added) > cfg.max_lines_changed:
        findings.append(
            Finding(
                id=_fid("quality-too-many-lines", "", 0, str(len(added))),
                title="Diff is too large",
                severity="medium",
                rule_id="quality-too-many-lines",
                message=f"Added {len(added)} lines; max allowed is {cfg.max_lines_changed}.",
                remediation="Keep contributions minimal and reviewable.",
            )
        )

    code_changed = any(not p.startswith("tests/") and not p.endswith(".md") for p in files)
    tests_changed = any("test" in p.lower() for p in files)
    # Strix/OSS ethos: prefer tests with code. Sandbox-verified existing tests count.
    if code_changed and not tests_changed and cfg.require_tests_with_code and not tests_verified:
        findings.append(
            Finding(
                id=_fid("quality-missing-tests", "", 0, ",".join(files)),
                title="Code change without tests",
                severity="medium",
                rule_id="quality-missing-tests",
                message="Source files changed but no test files were updated.",
                remediation="Add or update tests that fail before the fix.",
            )
        )

    for path, line_no, text in _iter_added_lines(diff):
        for rule, pat, cwe in _SECRET_PATTERNS:
            if pat.search(text):
                findings.append(
                    Finding(
                        id=_fid(rule, path, line_no, text[:40]),
                        title="Possible secret committed",
                        severity="critical",
                        rule_id=rule,
                        message="Added line matches a secret/credential pattern.",
                        file_path=path or None,
                        start_line=line_no or None,
                        cwe=cwe,
                        remediation="Remove the secret and rotate credentials.",
                        evidence=text.strip()[:120],
                        confirmed=False,
                    )
                )
        for rule, pat, severity, cwe, fix in _DANGEROUS_PATTERNS:
            if pat.search(text):
                findings.append(
                    Finding(
                        id=_fid(rule, path, line_no, text[:40]),
                        title="Dangerous API usage",
                        severity=severity,
                        rule_id=rule,
                        message=f"Added code matches {rule}.",
                        file_path=path or None,
                        start_line=line_no or None,
                        cwe=cwe,
                        remediation=fix,
                        evidence=text.strip()[:120],
                        confirmed=False,
                    )
                )

    # Optional: scan full file contents for secrets if paths exist on disk.
    for rel in files:
        fp = workdir / rel
        if not fp.is_file():
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, text in enumerate(content.splitlines(), start=1):
            for rule, pat, cwe in _SECRET_PATTERNS:
                if pat.search(text):
                    findings.append(
                        Finding(
                            id=_fid(rule + "-file", rel, i, text[:40]),
                            title="Possible secret in changed file",
                            severity="critical",
                            rule_id=rule,
                            message="Changed file contains a secret-like pattern.",
                            file_path=rel,
                            start_line=i,
                            cwe=cwe,
                            remediation="Remove the secret and rotate credentials.",
                            evidence=text.strip()[:120],
                        )
                    )

    # Deduplicate by id
    uniq: dict[str, Finding] = {f.id: f for f in findings}
    report = QualityReport(
        findings=sorted(
            uniq.values(),
            key=lambda f: (-SEVERITY_RANK[f.severity], f.rule_id, f.file_path or ""),
        ),
        files_scanned=files,
    )
    report.passed, report.block_reason = _gate(report, cfg)
    return report


def _gate(report: QualityReport, cfg: QualityConfig) -> tuple[bool, str | None]:
    threshold = SEVERITY_RANK[cfg.block_on_severity]
    blockers = [f for f in report.findings if SEVERITY_RANK[f.severity] >= threshold]
    if not blockers:
        return True, None
    top = blockers[0]
    return False, f"{len(blockers)} finding(s) at/above {cfg.block_on_severity}: {top.rule_id}"


class StaticQualityAnalyzer:
    """Default offline analyzer (no Docker / LLM)."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig()

    def analyze(
        self,
        *,
        workdir: Path,
        diff: str,
        changed_files: Sequence[str],
        tests_verified: bool = False,
    ) -> QualityReport:
        return analyze_patch_quality(
            workdir=workdir,
            diff=diff,
            changed_files=changed_files,
            config=self.config,
            tests_verified=tests_verified,
        )
