"""Local lint/test verification with sandbox-captured evidence only."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from oss_agent.protocols import SandboxRunner


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    log: str
    content_hash: str
    from_sandbox: bool = True


async def run_local_verification(
    runner: SandboxRunner,
    *,
    task_id: str,
    workdir: Path,
    timeout_sec: int = 120,
) -> VerifyResult:
    py = sys.executable
    result = await runner.run(
        task_id=f"{task_id}-verify",
        workdir=workdir,
        command=[py, "-m", "pytest", "-q"],
        env={
            "PYTHONPATH": "src",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(workdir),
        },
        network_allowlist=[],
        timeout_sec=timeout_sec,
    )
    log = result.stdout + "\n" + result.stderr
    digest = hashlib.sha256(log.encode()).hexdigest()
    return VerifyResult(ok=result.exit_code == 0, log=log, content_hash=digest, from_sandbox=True)
