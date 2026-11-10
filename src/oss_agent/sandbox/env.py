"""Scrub secrets from sandbox environments (invariant 7)."""

from __future__ import annotations

import re
from collections.abc import Mapping

_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|private[_-]?key|ssh|credential)",
    re.I,
)


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    """Drop any env var that looks like a secret. Never forward host secrets."""
    clean: dict[str, str] = {}
    for key, value in env.items():
        if _SECRET_KEY.search(key):
            continue
        if key in {"SSH_AUTH_SOCK", "GIT_SSH_COMMAND", "CURSOR_API_KEY", "OPENAI_API_KEY"}:
            continue
        clean[key] = value
    # Minimal safe defaults. Fake/local runners may pass PATH explicitly.
    clean.setdefault("HOME", "/sandbox")
    clean.setdefault("LANG", "C.UTF-8")
    if "PATH" not in clean:
        clean["PATH"] = "/usr/bin:/bin"
    return clean


def assert_no_secrets(env: Mapping[str, str]) -> None:
    for key in env:
        if _SECRET_KEY.search(key) or key in {
            "SSH_AUTH_SOCK",
            "CURSOR_API_KEY",
            "OPENAI_API_KEY",
            "OSS_AGENT_GITHUB_APP_PRIVATE_KEY",
        }:
            msg = f"refusing to pass secret-like env into sandbox: {key}"
            raise PermissionError(msg)
