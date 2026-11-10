"""Choose the coding brain without calling a model API or reading a secret.

Cursor Agent local runtime is used when a real Cursor credential is present.
Grok is the fallback only when that credential is absent. Selection never
prints or returns the key, and the default runtimes do not open a socket.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BrainName = Literal["cursor", "grok", "fixture"]
RuntimeName = Literal["local", "none"]

_PLACEHOLDER = re.compile(r"^(x+|changeme|replace.?me|todo)$", re.IGNORECASE)
_PLACEHOLDER_VALUES = frozenset({"…", "...", "YOUR_KEY", "your-key"})


@dataclass(frozen=True)
class CodingBrainPlan:
    """Which brain would run. ``api_called`` stays false for selection."""

    brain: BrainName
    runtime: RuntimeName
    api_called: bool = False


@dataclass(frozen=True)
class CodingTurn:
    brain: BrainName
    runtime: RuntimeName
    status: Literal["not_executed", "finished"]
    api_called: bool
    summary: str


def _placeholder(value: str | None) -> bool:
    if value is None:
        return True
    text = value.strip()
    if not text or text in _PLACEHOLDER_VALUES:
        return True
    return _PLACEHOLDER.fullmatch(text) is not None


def _cursor_present(env: Mapping[str, str], *, key_file_exists: bool | None) -> bool:
    file_name = env.get("CURSOR_API_KEY_FILE", "").strip()
    if file_name and not _placeholder(file_name):
        exists = Path(file_name).is_file() if key_file_exists is None else key_file_exists
        if exists:
            return True
    for name in ("CURSOR_SERVICE_ACCOUNT_KEY", "CURSOR_API_KEY"):
        if not _placeholder(env.get(name)):
            return True
    return False


def _grok_present(env: Mapping[str, str]) -> bool:
    return any(not _placeholder(env.get(name)) for name in ("GROK_API_KEY", "XAI_API_KEY"))


def select_coding_brain(
    env: Mapping[str, str] | None = None,
    *,
    key_file_exists: bool | None = None,
) -> CodingBrainPlan:
    """Return cursor, else grok, else the offline fixture brain.

    Passing ``env`` isolates the check from the process environment. A key file
    is treated as present only when ``key_file_exists`` is true or the path is
    an existing file. The file contents are never read.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    if _cursor_present(source, key_file_exists=key_file_exists):
        return CodingBrainPlan(brain="cursor", runtime="local", api_called=False)
    if _grok_present(source):
        return CodingBrainPlan(brain="grok", runtime="local", api_called=False)
    return CodingBrainPlan(brain="fixture", runtime="none", api_called=False)


def host_note() -> str:
    """Say whether this process is a Linux host. Do not claim a host that is not."""
    if sys.platform.startswith("linux"):
        return (
            "This process is Linux. A live Cursor Agent run still needs a credential, "
            "an allowlisted repository, and sponsor approval before any outward action."
        )
    return f"This process is {sys.platform}. A live Linux Cursor Agent run was not executed."


class CursorLocalRuntime:
    """Local Cursor Agent coding brain. The default turn does not call the API."""

    async def implement(self, *, cwd: Path, prompt: str) -> CodingTurn:
        _ = (cwd, prompt)
        return CodingTurn(
            brain="cursor",
            runtime="local",
            status="not_executed",
            api_called=False,
            summary=("Cursor local runtime was selected and not invoked. " + host_note()),
        )


class GrokFallbackRuntime:
    """Used only after select_coding_brain chooses grok (no Cursor credential)."""

    async def implement(self, *, cwd: Path, prompt: str) -> CodingTurn:
        _ = (cwd, prompt)
        return CodingTurn(
            brain="grok",
            runtime="local",
            status="not_executed",
            api_called=False,
            summary=(
                "Grok fallback was selected because no Cursor credential is present. "
                "No API call was made."
            ),
        )
