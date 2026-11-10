"""In-process sandbox used for unit tests and offline demo when Podman is absent."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from oss_agent.protocols import SandboxResult
from oss_agent.sandbox.env import assert_no_secrets, scrub_env
from oss_agent.sandbox.network import EgressLog, check_egress


class FakeSandboxRunner:
    """Enforces: no secrets in env, workdir jail, network allowlist for curl-like cmds."""

    def __init__(self) -> None:
        self.egress = EgressLog()
        self.last_env: dict[str, str] = {}

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
        _ = task_id
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        cleaned = scrub_env(env)
        assert_no_secrets(cleaned)
        self.last_env = cleaned

        if not command:
            return SandboxResult(1, "", "empty command", None, None, 0.0)

        # Block obvious host-escape write attempts via command args.
        joined = " ".join(command).lower()
        for arg in command:
            hostish = (
                arg.startswith("/etc/") or arg.startswith("/Users/") or arg.startswith("/home/")
            )
            if hostish and ("write" in joined or command[0] in {"touch", "cp", "mv"}):
                return SandboxResult(
                    1,
                    "",
                    f"escape_blocked:host_path:{arg}",
                    None,
                    None,
                    0.0,
                )

        # Network: if command looks like curl/wget/nc, enforce allowlist.
        if command[0] in {"curl", "wget", "nc", "ssh"} or (
            len(command) >= 2 and command[0] == "python" and "urllib" in " ".join(command)
        ):
            target = next((a for a in command[1:] if not a.startswith("-")), "")
            if target and not check_egress(target, network_allowlist, self.egress):
                return SandboxResult(
                    1,
                    "",
                    f"escape_blocked:network:{target}",
                    None,
                    None,
                    0.0,
                )

        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workdir),
            env=cleaned,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return SandboxResult(124, "", "timeout", None, None, time.monotonic() - start)

        # Jail: reject if process wrote outside workdir via OSS_AGENT_FAKE_TOUCH_HOST
        touch_host = cleaned.get("OSS_AGENT_FAKE_TOUCH_HOST")
        if touch_host:
            return SandboxResult(
                1,
                "",
                f"escape_blocked:host_path:{touch_host}",
                None,
                None,
                time.monotonic() - start,
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        diff = None
        diff_path = workdir / ".oss_agent_diff.patch"
        if diff_path.is_file():
            diff = diff_path.read_text(encoding="utf-8")
        return SandboxResult(
            proc.returncode or 0,
            stdout,
            stderr,
            diff,
            None,
            time.monotonic() - start,
        )


def host_env_with_secrets() -> dict[str, str]:
    """Sample polluted env for escape tests."""
    env = dict(os.environ)
    env["GITHUB_TOKEN"] = "ghp_secret_should_not_leak"
    env["SSH_AUTH_SOCK"] = "/tmp/ssh-agent.sock"
    env["OPENAI_API_KEY"] = "sk-secret"
    return env
