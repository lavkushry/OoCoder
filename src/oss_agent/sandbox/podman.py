"""Rootless Podman sandbox runner for a Linux host.

The argv matches rootless Podman on Linux: ``--userns=keep-id`` so the
service user can write the workdir mount. Container uid 65534 cannot write
that mount. The engine default seccomp profile stays on. An empty allowlist
is ``--network=none``. This module does not start Podman on macOS.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from oss_agent.protocols import SandboxResult
from oss_agent.sandbox.env import assert_no_secrets, scrub_env


def linux_podman_available() -> bool:
    """True only on Linux when the podman binary is on PATH."""
    return sys.platform.startswith("linux") and shutil.which("podman") is not None


def linux_podman_argv(
    *,
    name: str,
    image: str,
    workdir: Path,
    command: Sequence[str],
    env: Mapping[str, str],
    network_allowlist: Sequence[str],
    timeout_sec: int,
    memory_mb: int,
    cpus: float,
    selinux_relabel: bool = False,
) -> list[str]:
    """Build a rootless Linux ``podman run`` command. Do not execute it here."""
    network = ["--network=none"] if not network_allowlist else ["--network=bridge"]
    mount = f"{workdir}:/work:rw"
    if selinux_relabel:
        mount += ",Z"
    env_args: list[str] = []
    for key, value in env.items():
        env_args.extend(["-e", f"{key}={value}"])
    return [
        "podman",
        "run",
        "--rm",
        "--name",
        name,
        "--userns=keep-id",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=512m",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        f"{memory_mb}m",
        "--cpus",
        str(cpus),
        "--timeout",
        str(timeout_sec),
        *network,
        "-v",
        mount,
        "-w",
        "/work",
        *env_args,
        image,
        *list(command),
    ]


class PodmanSandboxRunner:
    """
    Rootless Podman on Linux: keep-id user, dropped caps, read-only rootfs,
    memory/cpu/pids/timeout limits, ephemeral container, log export.

    An empty allowlist uses ``--network=none``. A non-empty allowlist uses
    the bridge network; it is not an egress proxy.
    """

    image: str
    memory_mb: int
    cpus: float
    selinux_relabel: bool

    def __init__(
        self,
        *,
        image: str = "docker.io/library/python:3.12-slim",
        memory_mb: int = 2048,
        cpus: float = 2.0,
        selinux_relabel: bool = False,
    ) -> None:
        if not sys.platform.startswith("linux"):
            msg = (
                f"Podman sandbox runs on Linux. This process is {sys.platform}. "
                "Offline runs use the fake backend. A live Linux Cursor Agent run was not started."
            )
            raise RuntimeError(msg)
        self.image = image
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.selinux_relabel = selinux_relabel
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
        podman_cmd = linux_podman_argv(
            name=name,
            image=self.image,
            workdir=workdir,
            command=command,
            env=cleaned,
            network_allowlist=network_allowlist,
            timeout_sec=timeout_sec,
            memory_mb=self.memory_mb,
            cpus=self.cpus,
            selinux_relabel=self.selinux_relabel,
        )
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
