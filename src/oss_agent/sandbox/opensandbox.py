"""
OpenSandbox-inspired local runtime + SandboxRunner adapter.

Remote OpenSandbox servers can be plugged in later via ``OpenSandboxRuntime``
Protocol methods; this module ships an offline-safe local engine that reuses
Fake/Podman isolation policies (default-deny egress, secret scrubbing, jail).
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from oss_agent.config import AppConfig, SandboxConfig
from oss_agent.protocols import SandboxResult
from oss_agent.sandbox.env import assert_no_secrets, scrub_env
from oss_agent.sandbox.fake import FakeSandboxRunner
from oss_agent.sandbox.network import EgressLog
from oss_agent.sandbox.vault import CredentialVault

if TYPE_CHECKING:
    from oss_agent.sandbox.session import SandboxSession


class OpenSandboxRuntime:
    """
    Local execution backend for OpenSandbox-style sessions.

    Prefers Podman when ``prefer_podman`` and podman is on PATH; otherwise Fake.
    """

    def __init__(
        self,
        *,
        prefer_podman: bool = False,
        memory_mb: int = 2048,
        cpus: float = 2.0,
        image: str = "docker.io/library/python:3.12-slim",
    ) -> None:
        self.prefer_podman = prefer_podman
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.image = image
        self.egress = EgressLog()
        self._fake = FakeSandboxRunner()
        self._podman = None
        if prefer_podman and shutil.which("podman") is not None:
            from oss_agent.sandbox.podman import PodmanSandboxRunner

            self._podman = PodmanSandboxRunner(image=image, memory_mb=memory_mb, cpus=cpus)

    async def execute(
        self,
        *,
        session: SandboxSession,
        task_id: str,
        command: Sequence[str],
        env: Mapping[str, str],
        timeout_sec: int,
    ) -> SandboxResult:
        allow = session.network_allowlist
        if self._podman is not None:
            result = await self._podman.run(
                task_id=task_id,
                workdir=session.workdir,
                command=command,
                env=env,
                network_allowlist=allow,
                timeout_sec=timeout_sec,
            )
        else:
            # Share egress log with the session for auditability.
            self._fake.egress = session.egress
            result = await self._fake.run(
                task_id=task_id,
                workdir=session.workdir,
                command=command,
                env=env,
                network_allowlist=allow,
                timeout_sec=timeout_sec,
            )
        # Merge fake egress into session if podman path (podman logs via allowlist note).
        if self._podman is not None:
            for entry in self.egress.entries:
                session.egress.entries.append(entry)
        return result


class OpenSandboxRunner:
    """
    ``SandboxRunner`` Protocol implementation backed by OpenSandbox sessions.

    Each ``run`` call creates an ephemeral session, executes the command, then
    destroys the session (create → commands.run → destroy).
    """

    def __init__(
        self,
        *,
        config: AppConfig | SandboxConfig | None = None,
        vault: CredentialVault | None = None,
        prefer_podman: bool | None = None,
    ) -> None:
        if isinstance(config, AppConfig):
            sb = config.sandbox
        elif isinstance(config, SandboxConfig):
            sb = config
        else:
            sb = SandboxConfig()
        self.config = sb
        self.vault = vault or CredentialVault()
        use_podman = (
            prefer_podman
            if prefer_podman is not None
            else sb.backend in {"opensandbox", "podman"} and sb.prefer_podman
        )
        self.runtime = OpenSandboxRuntime(
            prefer_podman=bool(use_podman),
            memory_mb=sb.memory_mb,
            cpus=sb.cpus,
            image=sb.image,
        )
        self.last_session_id: str | None = None

    async def run(
        self,
        *,
        task_id: str,
        workdir: Path,
        command: Sequence[str],
        env: Mapping[str, str],
        network_allowlist: Sequence[str],
        timeout_sec: int,
    ) -> SandboxResult:
        from oss_agent.sandbox.session import SandboxSession

        cleaned = scrub_env({**dict(env), **self.vault.sandbox_safe_env()})
        assert_no_secrets(cleaned)
        session = await SandboxSession.create(
            self.config.image,
            workdir=workdir,
            network_allowlist=network_allowlist or self.config.network_allowlist,
            vault=self.vault,
            runner=self.runtime,
            meta={"task_id": task_id},
        )
        self.last_session_id = session.id
        try:
            execution = await session.run_command(
                command,
                env=cleaned,
                timeout_sec=timeout_sec,
                task_id=task_id,
            )
            diff = None
            diff_path = workdir / ".oss_agent_diff.patch"
            if diff_path.is_file():
                diff = diff_path.read_text(encoding="utf-8")
            return SandboxResult(
                execution.exit_code,
                execution.stdout,
                execution.stderr,
                diff,
                execution.logs_path,
                execution.duration_sec,
            )
        finally:
            await session.destroy()


def make_sandbox_runner(config: AppConfig | None = None) -> OpenSandboxRunner | FakeSandboxRunner:
    """Factory: OpenSandbox by default; fake when backend=fake."""
    cfg = config or AppConfig()
    if cfg.sandbox.backend == "fake":
        return FakeSandboxRunner()
    return OpenSandboxRunner(config=cfg)
