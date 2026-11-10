"""Finding model aligned with Strix severity + SARIF-friendly fields."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["critical", "high", "medium", "low", "info"]
SarifLevel = Literal["error", "warning", "note"]

SEVERITY_TO_SARIF: dict[Severity, SarifLevel] = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}

SEVERITY_TO_SCORE: dict[Severity, str] = {
    "critical": "9.5",
    "high": "8.0",
    "medium": "5.5",
    "low": "3.0",
    "info": "1.0",
}

SEVERITY_RANK: dict[Severity, int] = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    severity: Severity
    rule_id: str
    message: str
    file_path: str | None = None
    start_line: int | None = None
    cwe: str | None = None
    remediation: str | None = None
    # Strix ethos: static hits are unconfirmed until exploited/PoC'd.
    confirmed: bool = False
    evidence: str = ""

    def sarif_level(self) -> SarifLevel:
        return SEVERITY_TO_SARIF[self.severity]


@dataclass
class QualityReport:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    tool: str = "oss-agent-strix-inspired"
    passed: bool = True
    block_reason: str | None = None

    def highest_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return max(self.findings, key=lambda f: SEVERITY_RANK[f.severity]).severity

    def count_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {s: 0 for s in SEVERITY_RANK}
        for f in self.findings:
            counts[f.severity] += 1
        return counts

    def to_markdown(self) -> str:
        lines = [
            "# Code quality / security analysis (Strix-inspired)",
            "",
            f"**Tool:** {self.tool}",
            f"**Passed gate:** {self.passed}",
            f"**Files scanned:** {len(self.files_scanned)}",
        ]
        if self.block_reason:
            lines.append(f"**Block reason:** {self.block_reason}")
        lines.extend(["", "## Findings", ""])
        if not self.findings:
            lines.append("_No findings._")
        for f in self.findings:
            loc = f"{f.file_path}:{f.start_line}" if f.file_path else "(synthetic)"
            conf = "confirmed" if f.confirmed else "static_unconfirmed"
            lines.append(f"### [{f.severity.upper()}] {f.title} (`{f.rule_id}`)")
            lines.append(f"- Location: `{loc}`")
            lines.append(f"- Status: {conf}")
            if f.cwe:
                lines.append(f"- CWE: {f.cwe}")
            lines.append(f"- {f.message}")
            if f.remediation:
                lines.append(f"- Remediation: {f.remediation}")
            if f.evidence:
                lines.append(f"- Evidence: `{f.evidence[:200]}`")
            lines.append("")
        return "\n".join(lines)

    def to_sarif(self) -> dict[str, Any]:
        """Minimal SARIF 2.1.0 document (GitHub code-scanning compatible shape)."""
        rules: dict[str, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []
        for f in self.findings:
            if f.rule_id not in rules:
                rules[f.rule_id] = {
                    "id": f.rule_id,
                    "name": f.title,
                    "shortDescription": {"text": f.title},
                    "fullDescription": {"text": f.message},
                    "defaultConfiguration": {"level": f.sarif_level()},
                    "properties": {
                        "security-severity": SEVERITY_TO_SCORE[f.severity],
                        "tags": ["security"] if f.cwe else ["quality"],
                    },
                }
            location: dict[str, Any]
            if f.file_path:
                location = {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.file_path.replace("\\", "/")},
                        "region": {"startLine": f.start_line or 1},
                    }
                }
            else:
                location = {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "SECURITY.md"},
                    },
                    "properties": {"synthetic_location": True},
                }
            results.append(
                {
                    "ruleId": f.rule_id,
                    "level": f.sarif_level(),
                    "message": {"text": f.message},
                    "locations": [location],
                    "properties": {
                        "strix": {
                            "severity": f.severity,
                            "confirmed": f.confirmed,
                            "cwe": f.cwe,
                        }
                    },
                }
            )
        return {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": self.tool,
                            "informationUri": "https://github.com/usestrix/strix",
                            "rules": list(rules.values()),
                        }
                    },
                    "results": results,
                }
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "block_reason": self.block_reason,
            "files_scanned": self.files_scanned,
            "findings": [asdict(f) for f in self.findings],
            "counts": self.count_by_severity(),
        }
