"""Structured per-project memory (SQL-backed when session provided)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectMemory:
    repo: str
    owners_map: dict[str, list[str]] = field(default_factory=dict)
    label_taxonomy: list[str] = field(default_factory=list)
    style_notes: list[str] = field(default_factory=list)
    recent_merged_prs: list[dict[str, Any]] = field(default_factory=list)
    recent_rejected_prs: list[dict[str, Any]] = field(default_factory=list)
    common_objections: list[str] = field(default_factory=list)
    flake_tests: list[str] = field(default_factory=list)

    def upsert_owners(self, path_glob: str, owners: list[str]) -> None:
        self.owners_map[path_glob] = list(owners)

    def add_style_note(self, note: str) -> None:
        if note and note not in self.style_notes:
            self.style_notes.append(note)

    def snapshot(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "owners_map": dict(self.owners_map),
            "label_taxonomy": list(self.label_taxonomy),
            "style_notes": list(self.style_notes),
            "recent_merged_prs": list(self.recent_merged_prs),
            "recent_rejected_prs": list(self.recent_rejected_prs),
            "common_objections": list(self.common_objections),
            "flake_tests": list(self.flake_tests),
        }

    @classmethod
    def from_rules_and_owners(cls, repo: str, owners_text: str | None) -> ProjectMemory:
        mem = cls(repo=repo)
        if not owners_text:
            return mem
        for line in owners_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                mem.upsert_owners(parts[0], parts[1:])
        return mem
