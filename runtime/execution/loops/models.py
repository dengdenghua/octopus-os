from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_run_id() -> str:
    return uuid4().hex


def _normalize_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


class LoopRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Startup reconciliation (audit R-02): the process exited while the
    # run was in flight. Terminal (nothing is driving it after a
    # restart) but resumable — attempts are preserved.
    INTERRUPTED = "interrupted"


class LoopMode(StrEnum):
    CODE = "code"
    PLAN = "plan"
    SPEC = "spec"
    GOAL = "goal"


class LoopPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_attempts: int = Field(default=2, ge=1, le=10)
    max_iterations: int = Field(default=8, ge=1, le=50)
    goal_mode: bool = False
    max_tokens_budget: int = Field(default=100_000, ge=1, le=2_000_000)
    max_usd_budget: float = Field(default=1.0, ge=0.0, le=100.0)
    # Keep controller-launched work consistent with direct ReAct turns:
    # accounting budgets are telemetry unless the user explicitly opts into
    # an automatic pause. A controller entry point must not silently turn a
    # recoverable long task into a "budget near limit" interruption.
    budget_auto_pause: bool = False
    verifier_profile: str = "auto"
    auto_approve: bool = True
    sandbox_mode: str = "full"
    permission_mode: str = "bypassPermissions"
    execution_environment: str = "local"
    model: str | None = None


class VerifierFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    command: str = ""
    category: str = ""
    passed: bool = False
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    execution_policy: dict[str, Any] = Field(default_factory=dict)


class VerifierResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    profile: str
    kind: str = "unknown"
    failure_category: str = ""
    passed: bool = False
    findings: list[VerifierFinding] = Field(default_factory=list)
    checked_at: str = Field(default_factory=_now_iso)
    summary: str = ""


class LoopAttempt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    attempt_index: int = Field(ge=1)
    prompt: str
    started_at: str = Field(default_factory=_now_iso)
    completed_at: str | None = None
    status: str = "running"
    success: bool | None = None
    terminated_reason: str = ""
    final_answer: str = ""
    completion_receipt: dict[str, Any] = Field(default_factory=dict)
    completion_decision: dict[str, Any] = Field(default_factory=dict)
    effect_summary: dict[str, Any] = Field(default_factory=dict)
    verifier_result: VerifierResult | None = None
    error: str = ""


class LoopRun(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str = Field(default_factory=_new_run_id)
    tenant_id: str | None = None
    owner_id: str | None = None
    parent_run_id: str | None = None
    origin_run_id: str | None = None
    resume_checkpoint_id: str | None = None
    goal: str = Field(..., min_length=1)
    mode: LoopMode = LoopMode.CODE
    status: LoopRunStatus = LoopRunStatus.PENDING
    thread_id: str | None = None
    workspace_path: str | None = None
    policy: LoopPolicy = Field(default_factory=LoopPolicy)
    attempts: list[LoopAttempt] = Field(default_factory=list)
    last_verifier_result: VerifierResult | None = None
    last_review: dict[str, Any] | None = None
    last_review_queue_result: dict[str, Any] | None = None
    last_evolution_candidate_result: dict[str, Any] | None = None
    cancel_requested_at: str | None = None
    cancel_reason: str = ""
    last_error: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    started_at: str | None = None
    completed_at: str | None = None

    @field_validator(
        "owner_id",
        "tenant_id",
        "parent_run_id",
        "origin_run_id",
        "resume_checkpoint_id",
        "thread_id",
        "workspace_path",
        mode="before",
    )
    @classmethod
    def _normalize_optional_fields(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)


class CreateLoopRunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal: str = Field(..., min_length=1)
    mode: LoopMode = LoopMode.CODE
    thread_id: str | None = None
    workspace_path: str | None = None
    policy: LoopPolicy = Field(default_factory=LoopPolicy)
    execute: bool = False
    background: bool = False

    @field_validator("thread_id", "workspace_path", mode="before")
    @classmethod
    def _normalize_optionals(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)


class CancelLoopRunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)


class RestartLoopRunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal: str | None = None
    thread_id: str | None = None
    workspace_path: str | None = None
    policy: LoopPolicy | None = None
    execute: bool = False
    background: bool = False
    reuse_workspace: bool = True

    @field_validator("goal", "thread_id", "workspace_path", mode="before")
    @classmethod
    def _normalize_optionals(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)


class LoopRunListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    runs: list[LoopRun] = Field(default_factory=list)
    total: int = 0


class LoopRunRuntimeStateResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    parent_run_id: str | None = None
    origin_run_id: str | None = None
    resume_checkpoint_id: str | None = None
    status: LoopRunStatus
    is_running: bool = False
    attempt_count: int = 0
    last_error: str = ""
    workspace_path: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str
    review_available: bool = False
    cancel_requested: bool = False
    cancel_requested_at: str | None = None
    cancel_reason: str = ""
    task_run: dict[str, Any] = Field(default_factory=dict)
    task_lease_health: dict[str, Any] = Field(default_factory=dict)
    task_recovery: dict[str, Any] = Field(default_factory=dict)
    recovery_audit: dict[str, Any] = Field(default_factory=dict)


class LoopRunsOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total: int = 0
    active_dispatches: int = 0
    active_run_ids: list[str] = Field(default_factory=list)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_mode: dict[str, int] = Field(default_factory=dict)
    reviewed_runs: int = 0
    task_health: dict[str, Any] = Field(default_factory=dict)
    recovery_audit: dict[str, Any] = Field(default_factory=dict)
