"""Invariant 1: only gateway may import gateway.credentials."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "oss_agent"
FORBIDDEN = ("oss_agent.gateway.credentials", "gateway.credentials")


def test_only_gateway_imports_credentials() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] == "gateway":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module in FORBIDDEN or node.module.endswith(".gateway.credentials"))
            ):
                offenders.append(f"{rel}: from {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN:
                        offenders.append(f"{rel}: import {alias.name}")
    assert offenders == [], offenders
