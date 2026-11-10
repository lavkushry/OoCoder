"""Rootless Podman sandbox runner."""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from oss_agent.protocols import SandboxResult
from oss_agent.sandbox.env import assert_no_secrets, scrub_env


class PodmanSandboxRunner:
    """
    Rootless Podman: non-root user, dropped caps, read-only rootfs,
    memory/cpu/timeout limits, ephemeral container, log export.
    Network allowlist is applied by not giving the container unrestricted net
    unless allowlist is non-empty (then --network=bridge + proxy note in audit).
    """

    def __init__(
        self,
        *,
        image: str = "docker.io/library/python:3.12-slim",
        memory_mb: int = 2048,
        cpus: float = 2.0,
    ) -> None:
        self.image = image
        self.memory_mb = memory_mb
        self.cpus = cpus
        if shutil.which("podman") is None:
            msg = "podman not found on PATH"
            raise RuntimeError(msg)

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
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        cleaned = scrub_env(env)
        assert_no_secrets(cleaned)

        name = f"oss-agent-{task_id}"[:63]
        # Empty allowlist => no network. Non-empty => bridge (egress still logged by proxy layer).
        network_args = ["--network=bridge"] if network_allowlist else ["--network=none"]

        env_args: list[str] = []
        for k, v in cleaned.items():
            env_args.extend(["-e", f"{k}={v}"])

        podman_cmd = [
            "podman",
            "run",
            "--rm",
            "--name",
            name,
            "--user",
            "65534:65534",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=512m",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            f"{self.memory_mb}m",
            "--cpus",
            str(self.cpus),
            "--timeout",
            str(timeout_sec),
            *network_args,
            "-v",
            f"{workdir}:/work:rw",
            "-w",
            "/work",
            *env_args,
            self.image,
            *command,
        ]
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *podman_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        log_path = workdir / "sandbox.log"
        log_path.write_text(
            stdout_b.decode("utf-8", errors="replace")
            + "\n--- stderr ---\n"
            + stderr_b.decode("utf-8", errors="replace"),
            encoding="utf-8",
        )
        diff = None
        diff_path = workdir / ".oss_agent_diff.patch"
        if diff_path.is_file():
            diff = diff_path.read_text(encoding="utf-8")
        return SandboxResult(
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
            diff,
            log_path,
            time.monotonic() - start,
        )
