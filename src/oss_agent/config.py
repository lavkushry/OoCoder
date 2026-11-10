"""Single validated configuration schema. No magic constants elsewhere."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ActionKind = Literal["claim", "comment", "pr", "reply", "push"]


class RateLimits(BaseModel):
    max_open_prs: int = Field(default=2, ge=0)
    max_comments_per_day: int = Field(default=5, ge=0)
    max_ci_retries: int = Field(default=2, ge=0)


class AutoApprove(BaseModel):
    """Per-action-type opt-in. All default off (human approval required)."""

    claim: bool = False
    comment: bool = False
    pr: bool = False
    reply: bool = False
    push: bool = False


class PolicyConfig(BaseModel):
    # Per-action-type opt-in auto-approve (all False by default).
    auto_approve: AutoApprove = Field(default_factory=AutoApprove)
    rate_limits: RateLimits = Field(default_factory=RateLimits)
    ai_disclosure: str = (
        "This contribution was prepared with AI assistance. "
        "A human sponsor reviewed and approved every outward action."
    )
    kill_switch: bool = False
    allowed_scope: list[str] = Field(default_factory=lambda: ["docs", "bugfix", "tests", "lint"])


class SandboxConfig(BaseModel):
    backend: Literal["podman", "fake"] = "podman"
    memory_mb: int = Field(default=2048, ge=256)
    cpus: float = Field(default=2.0, gt=0)
    timeout_sec: int = Field(default=1800, ge=30)
    network_allowlist: list[str] = Field(
        default_factory=lambda: [
            "github.com",
            "pypi.org",
            "files.pythonhosted.org",
            "registry.npmjs.org",
        ]
    )
    work_root: Path = Path("/tmp/oss-agent-sandbox")


class ModelConfig(BaseModel):
    main_model: str = "main"
    cheap_model: str = "cheap"
    provider: Literal["stub", "openai_compatible"] = "stub"
    base_url: str | None = None
    max_tokens_per_task: int = Field(default=200_000, ge=1000)


class GoalConfig(BaseModel):
    """Paperclip-style goal + budget gates applied before contribution."""

    company_goal: str = (
        "Contribute carefully to AI-friendly open-source projects with human approval."
    )
    contribution_goal: str = (
        "Ship small, well-tested docs/bug/test/lint fixes that maintainers can merge."
    )
    max_usd_per_task: float = Field(default=5.0, ge=0)
    usd_per_million_tokens: float = Field(default=5.0, ge=0)
    min_issue_score: float = Field(default=2.0)
    min_readiness: float = Field(default=0.7, ge=0, le=1)
    # Offline demo may auto-pass the analysis approval gate when recommendation=go.
    demo_auto_approve_analysis: bool = True


class QualityConfig(BaseModel):
    """Strix-inspired code quality / security gate on the contribution diff."""

    enabled: bool = True
    # Block PR drafting when any finding reaches this severity (inclusive).
    block_on_severity: Literal["critical", "high", "medium", "low", "info"] = "high"
    max_files_changed: int = Field(default=20, ge=1)
    max_lines_changed: int = Field(default=400, ge=1)
    require_tests_with_code: bool = True
    # Optional external Strix CLI when installed; default uses static analyzer.
    prefer_strix_cli: bool = False
    write_sarif: bool = True


class StateTimeouts(BaseModel):
    """Wall-clock timeouts in seconds per task state."""

    SCOUTING: int = 3600
    SHORTLISTED: int = 86_400
    AWAITING_CLAIM_APPROVAL: int = 259_200
    CLAIMED: int = 86_400
    REPRODUCING: int = 7200
    DESIGNING: int = 3600
    IMPLEMENTING: int = 7200
    SELF_REVIEW: int = 3600
    AWAITING_PR_APPROVAL: int = 259_200
    SUBMITTED: int = 604_800
    IN_REVIEW: int = 1_209_600
    CHANGES_REQUESTED: int = 604_800
    APPROVED: int = 259_200
    MERGED: int = 86_400
    ABANDONED: int = 0
    PAUSED: int = 0


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OSS_AGENT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/oss_agent.db"
    state_dir: Path = Path("./data")
    audit_dir: Path = Path("./data/audit")
    host: str = "127.0.0.1"
    port: int = 8787
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    goal: GoalConfig = Field(default_factory=GoalConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    timeouts: StateTimeouts = Field(default_factory=StateTimeouts)
    github_credentials_file: Path | None = None

    @field_validator("host")
    @classmethod
    def localhost_only(cls, v: str) -> str:
        if v not in {"127.0.0.1", "localhost", "::1"}:
            msg = "web UI must bind to localhost"
            raise ValueError(msg)
        return v


def load_config(path: Path | None = None) -> AppConfig:
    """Load config from optional YAML overlay, then environment."""
    if path is None:
        return AppConfig()
    import yaml

    raw = yaml.safe_load(path.read_text()) or {}
    return AppConfig(**raw)
