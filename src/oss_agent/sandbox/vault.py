"""
OpenSandbox-inspired Credential Vault.

Host holds real credentials. Sandbox workloads never receive them — only opaque
handles that the host-side gateway may resolve outside the sandbox boundary.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VaultEntry:
    handle: str
    name: str
    # Real secret stays on the host process only; never serialized into sandbox env.
    _secret: str = field(repr=False)
    meta: dict[str, Any] = field(default_factory=dict)


class CredentialVault:
    """In-process vault. Secrets never cross into SandboxSession.env."""

    def __init__(self) -> None:
        self._entries: dict[str, VaultEntry] = {}

    def put(self, name: str, secret: str, *, meta: dict[str, Any] | None = None) -> str:
        handle = (
            "vault_" + hashlib.sha256(f"{name}:{secrets.token_hex(8)}".encode()).hexdigest()[:16]
        )
        self._entries[handle] = VaultEntry(
            handle=handle, name=name, _secret=secret, meta=meta or {}
        )
        return handle

    def resolve(self, handle: str) -> str | None:
        """Host-only resolution. Must not be called from sandbox command env builders."""
        entry = self._entries.get(handle)
        return None if entry is None else entry._secret

    def describe(self, handle: str) -> dict[str, Any] | None:
        entry = self._entries.get(handle)
        if entry is None:
            return None
        return {"handle": entry.handle, "name": entry.name, "meta": dict(entry.meta)}

    def sandbox_safe_env(self) -> dict[str, str]:
        """Env fragment safe to inject: handles only, never secret values."""
        out: dict[str, str] = {}
        for entry in self._entries.values():
            # Opaque handle — not the secret. Sandbox cannot redeem it.
            out[f"OSS_VAULT_HANDLE_{entry.name.upper()}"] = entry.handle
        return out

    def clear(self) -> None:
        self._entries.clear()
