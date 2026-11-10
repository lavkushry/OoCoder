"""Load GitHub App credentials. Import only from gateway package code."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitHubWriteCredentials:
    app_id: str
    private_key_pem: str
    installation_id: str | None = None


def load_credentials(credentials_file: Path | None = None) -> GitHubWriteCredentials:
    """Read from root-owned file or environment. Never log values."""
    if credentials_file is not None:
        text = credentials_file.read_text()
        # Simple KEY=VALUE file; private key may be path reference.
        values: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
        key = values.get("GITHUB_APP_PRIVATE_KEY", "")
        if key.startswith("@"):
            key = Path(key[1:]).read_text()
        return GitHubWriteCredentials(
            app_id=values["GITHUB_APP_ID"],
            private_key_pem=key,
            installation_id=values.get("GITHUB_APP_INSTALLATION_ID"),
        )
    app_id = os.environ["OSS_AGENT_GITHUB_APP_ID"]
    key = os.environ["OSS_AGENT_GITHUB_APP_PRIVATE_KEY"]
    if key.startswith("@"):
        key = Path(key[1:]).read_text()
    return GitHubWriteCredentials(
        app_id=app_id,
        private_key_pem=key,
        installation_id=os.environ.get("OSS_AGENT_GITHUB_APP_INSTALLATION_ID"),
    )
