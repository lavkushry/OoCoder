"""Reproduction workflow: baseline tests then issue-focused failing test evidence."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from oss_agent.protocols import SandboxRunner, UntrustedText


@dataclass(frozen=True)
class ReproductionResult:
    baseline_ok: bool
    baseline_log: str
    repro_failed_as_expected: bool
    repro_log: str
    workdir: Path
    evidence_hash: str


async def reproduce_issue(
    runner: SandboxRunner,
    *,
    task_id: str,
    source_repo: Path,
    issue: UntrustedText,
    network_allowlist: list[str] | None = None,
    timeout_sec: int = 120,
) -> ReproductionResult:
    """
    Copy fixture/repo into an ephemeral workdir, run baseline tests, then a
    known failing assertion path for the fixture bug.

    Issue body is UntrustedText and is only written to a data file — never exec'd.
    """
    work_root = Path("/tmp/oss-agent-sandbox") / task_id
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)
    dest = work_root / "repo"
    shutil.copytree(source_repo, dest, dirs_exist_ok=True)

    # Persist untrusted issue as data only.
    issue_path = dest / ".oss_agent_issue.txt"
    issue_path.write_text(
        f"source={issue.source}\n<<<UNTRUSTED>>>\n{issue.body}\n<<<END>>>\n",
        encoding="utf-8",
    )

    allow = network_allowlist or []
    py = sys.executable
    run_env = {
        "PYTHONPATH": "src",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(work_root),
    }
    baseline = await runner.run(
        task_id=f"{task_id}-baseline",
        workdir=dest,
        command=[py, "-m", "pytest", "-q"],
        env=run_env,
        network_allowlist=allow,
        timeout_sec=timeout_sec,
    )
    # For the friendly_tiny fixture, baseline is expected to FAIL (bug present).
    # "Baseline" here means "run the project's tests as-is and capture output".
    baseline_log = baseline.stdout + "\n" + baseline.stderr

    repro = await runner.run(
        task_id=f"{task_id}-repro",
        workdir=dest,
        command=[py, "-m", "pytest", "-q", "tests/test_add.py"],
        env=run_env,
        network_allowlist=allow,
        timeout_sec=timeout_sec,
    )
    repro_log = repro.stdout + "\n" + repro.stderr
    evidence = baseline_log + "\n---\n" + repro_log
    digest = hashlib.sha256(evidence.encode()).hexdigest()
    (work_root / "evidence.log").write_text(evidence, encoding="utf-8")

    return ReproductionResult(
        baseline_ok=baseline.exit_code == 0,
        baseline_log=baseline_log,
        repro_failed_as_expected=repro.exit_code != 0,
        repro_log=repro_log,
        workdir=work_root,
        evidence_hash=digest,
    )
