"""Cross-module Protocols. Implementation comes in later milestones."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

AiPolicyStatus = Literal["allowed", "banned", "unclear"]
ActionKind = Literal["claim", "comment", "pr", "reply", "push"]
ArtifactType = Literal["repro", "patch", "test_log", "design_note", "lint_log", "baseline_log"]
ReviewClass = Literal["nit", "bug", "design", "question", "flake"]


@dataclass(frozen=True)
class UntrustedText:
    """Repo-sourced text that must never be treated as instructions."""

    source: str
    body: str


@dataclass(frozen=True)
class ProjectRules:
    repo: str
    contributing: str | None
    code_of_conduct: str | None
    pr_template: str | None
    owners: str | None
    ai_policy_text: str | None
    ai_policy_status: AiPolicyStatus
    cla_or_dco: str | None
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class IssueCandidate:
    project_id: str
    number: int
    title: str
    body: UntrustedText
    labels: Sequence[str]
    score: float
    score_reasons: Sequence[str]
    url: str


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    diff: str | None
    log_path: Path | None
    duration_sec: float


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str


@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: Sequence[Mapping[str, Any]]
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class OutwardActionDraft:
    kind: ActionKind
    task_id: str
    payload: Mapping[str, Any]
    evidence_artifact_ids: Sequence[str] = ()


@dataclass(frozen=True)
class GatewaySendResult:
    ok: bool
    remote_id: str | None
    error: str | None


@runtime_checkable
class ModelProvider(Protocol):
    async def complete(
        self,
        *,
        model: Literal["main", "cheap"],
        messages: Sequence[ModelMessage],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> ModelResponse: ...


@runtime_checkable
class SandboxRunner(Protocol):
    async def run(
        self,
        *,
        task_id: str,
        workdir: Path,
        command: Sequence[str],
        env: Mapping[str, str],
        network_allowlist: Sequence[str],
        timeout_sec: int,
    ) -> SandboxResult: ...


@runtime_checkable
class GitHubReader(Protocol):
    """Read-only GitHub access. Must not hold write credentials."""

    async def fetch_file(self, repo: str, path: str, ref: str = "HEAD") -> str | None: ...

    async def list_issues(
        self, repo: str, *, labels: Sequence[str] | None = None, limit: int = 50
    ) -> Sequence[Mapping[str, Any]]: ...

    async def get_issue(self, repo: str, number: int) -> Mapping[str, Any]: ...

    async def list_pr_comments(self, repo: str, number: int) -> Sequence[Mapping[str, Any]]: ...

    async def get_check_runs(self, repo: str, ref: str) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class GitHubGateway(Protocol):
    """Sole writer to GitHub. Enforces kill switch, policy, rates, disclosure."""

    async def enqueue(self, draft: OutwardActionDraft) -> str:
        """Persist draft row; return draft id. Does not send."""
        ...

    async def send_approved(self, draft_id: str, *, approved_by: str) -> GatewaySendResult: ...


@runtime_checkable
class AuditLog(Protocol):
    def append(
        self,
        *,
        actor: str,
        event: str,
        payload: Mapping[str, Any],
        task_id: str | None = None,
    ) -> None: ...


@runtime_checkable
class RulesEngine(Protocol):
    def classify_ai_policy(self, text: str | None) -> AiPolicyStatus: ...

    def validate_pr_draft(self, draft: Mapping[str, Any], rules: ProjectRules) -> list[str]: ...

    def rate_limit_ok(self, project_id: str, kind: ActionKind) -> bool: ...
