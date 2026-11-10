"""
OpenSandbox-inspired sandbox lifecycle: create → commands/files → destroy.

Does not vendor OpenSandbox; mirrors its session/command/file API shape so we
can later point a remote adapter at a real OpenSandbox server.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from oss_agent.protocols import SandboxResult
from oss_agent.sandbox.env import assert_no_secrets, scrub_env
from oss_agent.sandbox.network import EgressLog
from oss_agent.sandbox.vault import CredentialVault

SandboxStatus = Literal["creating", "running", "paused", "destroyed", "error"]


@dataclass
class CommandExecution:
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    logs_path: Path | None = None


@dataclass
class WriteEntry:
    path: str
    data: str
    mode: int = 0o644


@dataclass
class SandboxSession:
    """One ephemeral agent working environment (OpenSandbox ``Sandbox`` analogue)."""

    id: str
    image: str
    workdir: Path
    status: SandboxStatus = "creating"
    network_allowlist: list[str] = field(default_factory=list)
    egress: EgressLog = field(default_factory=EgressLog)
    vault: CredentialVault = field(default_factory=CredentialVault)
    created_at: float = field(default_factory=time.monotonic)
    meta: dict[str, str] = field(default_factory=dict)
    _runner: object | None = field(default=None, repr=False)

    @classmethod
    async def create(
        cls,
        image: str = "local/python:3.12",
        *,
        workdir: Path,
        network_allowlist: Sequence[str] | None = None,
        vault: CredentialVault | None = None,
        runner: object | None = None,
        meta: Mapping[str, str] | None = None,
    ) -> SandboxSession:
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        session = cls(
            id=f"sbx_{uuid.uuid4().hex[:12]}",
            image=image,
            workdir=workdir,
            status="running",
            network_allowlist=list(network_allowlist or []),
            vault=vault or CredentialVault(),
            meta=dict(meta or {}),
            _runner=runner,
        )
        return session

    async def run_command(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_sec: int = 120,
        task_id: str | None = None,
    ) -> CommandExecution:
        self._require_running()
        from oss_agent.sandbox.opensandbox import OpenSandboxRuntime

        runtime = self._runner
        if runtime is None:
            runtime = OpenSandboxRuntime()
            self._runner = runtime
        assert isinstance(runtime, OpenSandboxRuntime)
        cleaned = scrub_env({**(env or {}), **self.vault.sandbox_safe_env()})
        assert_no_secrets(cleaned)
        result: SandboxResult = await runtime.execute(
            session=self,
            task_id=task_id or self.id,
            command=command,
            env=cleaned,
            timeout_sec=timeout_sec,
        )
        return CommandExecution(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_sec=result.duration_sec,
            logs_path=result.log_path,
        )

    async def write_files(self, entries: Sequence[WriteEntry]) -> None:
        self._require_running()
        for entry in entries:
            target = self._jail_path(entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.data, encoding="utf-8")
            target.chmod(entry.mode & 0o777)

    async def read_file(self, path: str) -> str:
        self._require_running()
        target = self._jail_path(path)
        return target.read_text(encoding="utf-8")

    async def pause(self) -> None:
        """Checkpoint hook (OpenSandbox pause/resume). Local runtime marks paused."""
        self._require_running()
        self.status = "paused"

    async def resume(self) -> None:
        if self.status != "paused":
            msg = f"cannot resume from {self.status}"
            raise RuntimeError(msg)
        self.status = "running"

    async def destroy(self) -> None:
        self.status = "destroyed"
        self.vault.clear()

    def _require_running(self) -> None:
        if self.status != "running":
            msg = f"sandbox {self.id} is {self.status}"
            raise RuntimeError(msg)

    def _jail_path(self, rel: str) -> Path:
        """Resolve path inside workdir; reject host escapes."""
        raw = Path(rel)
        if raw.is_absolute():
            # Map absolute sandbox paths under /work → workdir.
            parts = raw.parts
            if len(parts) >= 2 and parts[1] == "work":
                candidate = self.workdir.joinpath(*parts[2:])
            else:
                msg = f"escape_blocked:host_path:{rel}"
                raise PermissionError(msg)
        else:
            candidate = (self.workdir / raw).resolve()
        try:
            candidate.relative_to(self.workdir.resolve())
        except ValueError as exc:
            msg = f"escape_blocked:host_path:{rel}"
            raise PermissionError(msg) from exc
        return candidate
