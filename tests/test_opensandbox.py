"""OpenSandbox-inspired session lifecycle, vault, and runner tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from oss_agent.config import AppConfig, SandboxConfig
from oss_agent.sandbox import (
    CredentialVault,
    OpenSandboxRunner,
    SandboxSession,
    WriteEntry,
    make_sandbox_runner,
)
from oss_agent.sandbox.fake import FakeSandboxRunner


@pytest.mark.asyncio
async def test_session_lifecycle_commands_and_files(tmp_path: Path) -> None:
    session = await SandboxSession.create(
        "local/python:3.12",
        workdir=tmp_path / "sbx",
        network_allowlist=["pypi.org"],
    )
    assert session.status == "running"
    assert session.id.startswith("sbx_")

    await session.write_files([WriteEntry(path="hello.sh", data="echo hi\n", mode=0o755)])
    content = await session.read_file("hello.sh")
    assert "echo hi" in content

    exe = await session.run_command(["/bin/echo", "opensandbox-ok"], timeout_sec=30)
    assert exe.exit_code == 0
    assert "opensandbox-ok" in exe.stdout

    await session.pause()
    assert session.status == "paused"
    await session.resume()
    await session.destroy()
    assert session.status == "destroyed"


@pytest.mark.asyncio
async def test_session_file_jail_blocks_host_path(tmp_path: Path) -> None:
    session = await SandboxSession.create("local", workdir=tmp_path / "sbx")
    with pytest.raises(PermissionError, match="escape_blocked:host_path"):
        await session.write_files([WriteEntry(path="/etc/passwd", data="x")])
    await session.destroy()


def test_credential_vault_never_exposes_secret() -> None:
    vault = CredentialVault()
    handle = vault.put("github_token", "ghp_super_secret_value")
    assert handle.startswith("vault_")
    assert vault.resolve(handle) == "ghp_super_secret_value"
    safe = vault.sandbox_safe_env()
    assert "ghp_super_secret_value" not in safe.values()
    assert safe["OSS_VAULT_HANDLE_GITHUB_TOKEN"] == handle


@pytest.mark.asyncio
async def test_opensandbox_runner_protocol(tmp_path: Path) -> None:
    runner = OpenSandboxRunner(config=SandboxConfig(backend="opensandbox", prefer_podman=False))
    result = await runner.run(
        task_id="t1",
        workdir=tmp_path / "w",
        command=["/bin/echo", "ok"],
        env={"PATH": "/usr/bin:/bin"},
        network_allowlist=[],
        timeout_sec=30,
    )
    assert result.exit_code == 0
    assert "ok" in result.stdout
    assert runner.last_session_id is not None


@pytest.mark.asyncio
async def test_opensandbox_runner_blocks_network(tmp_path: Path) -> None:
    runner = OpenSandboxRunner()
    result = await runner.run(
        task_id="t2",
        workdir=tmp_path / "w",
        command=["curl", "https://evil.example"],
        env={"PATH": "/usr/bin:/bin"},
        network_allowlist=["github.com"],
        timeout_sec=10,
    )
    assert result.exit_code != 0
    assert "escape_blocked:network" in result.stderr


def test_make_sandbox_runner_respects_backend() -> None:
    fake = make_sandbox_runner(AppConfig(sandbox=SandboxConfig(backend="fake")))
    assert isinstance(fake, FakeSandboxRunner)
    osb = make_sandbox_runner(AppConfig(sandbox=SandboxConfig(backend="opensandbox")))
    assert isinstance(osb, OpenSandboxRunner)
