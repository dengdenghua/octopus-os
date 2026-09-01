from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .primitives import (
    ArmId,
    CostEntry,
    SkillId,
    TaskId,
    TrajectoryId,
    new_id,
    now_utc,
)


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: UUID = Field(default_factory=new_id)
    caller: str  # "arm:code_arm" / "cerebrum" / "spinal_cord"
    sucker_id: SkillId
    args: dict[str, Any] = Field(default_factory=dict)
    predicted_cost: CostEntry | None = None  # Implementation note.
    ts: datetime = Field(default_factory=now_utc)


ExecutionStatus = Literal[
    "success",
    "failed",
    "timeout",
    "sandbox_violation",
    "circuit_broken",
    "immune_reject",
]


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: UUID
    status: ExecutionStatus
    # Output is kept as the original runtime value (str from command-style
    # skills, dict/list from structured skills like `list_cwd`, number/bool
    # for simple yes/no tools). Journal JSON-encodes all of these; only
    # exotic objects get repr'd via `_safe_repr` at executor boundary.
    # Widening this from `str | None` fixed ~70 tests that construct
    # ExecutionResult with dict output.
    output: Any = None
    output_hash: str | None = None
    error_type: str | None = None
    stderr_tags: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    cost: CostEntry = Field(default_factory=CostEntry)
    files_modified: list[str] = Field(default_factory=list)
    network_egress_bytes: int = 0
    # Server-owned execution provenance. These fields are stamped by the
    # ToolExecutor from the exact handler object it captured and invoked;
    # callers must not infer trust from a registry name or skill metadata.
    trusted_execution: bool = False
    execution_source: str = ""
    # Server-stamped effect proof. Tool/plugin output cannot populate this
    # field: ToolExecutor overwrites it after every pre/post hook has run.
    # Older journal rows remain valid through the empty default and therefore
    # fail closed when a consumer requires a sealed receipt.
    effect_receipt: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=now_utc)

    @property
    def is_attack_like(self) -> bool:
        signals = [
            self.status == "sandbox_violation",
            self.exit_code in {124, 137, 139} if self.exit_code is not None else False,
            "shell_injection" in self.stderr_tags,
            "path_traversal" in self.stderr_tags,
            "prompt_injection" in self.stderr_tags,
        ]
        return sum(signals) >= 2


class Step(BaseModel):
    model_config = ConfigDict(frozen=True)

    step_id: int = Field(..., ge=0)
    node_id: str  # Implementation note.
    action: ToolCall
    result: ExecutionResult
    context_hash: str | None = None  # Implementation note.
    immune_verdict: str | None = None  # "allow" | "quarantine" | "reject"
    ts: datetime = Field(default_factory=now_utc)
    # Raw args_template BEFORE ``resolve_templates`` consumed the
    # ``{nX.key}`` refs. Captured so SkillForge can replay the
    # original template chain when building a composite meta-handler
    # (claim ② in the 2026-04 review). Without this, a forged
    # multi-step skill loses data dependencies across steps or
    # leaks user kwargs into steps that don't accept them.
    # Default ``{}`` keeps backward compat for Steps built directly
    # (tests, direct executor calls) and old journal rows.
    args_template: dict[str, Any] = Field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.result.status == "success"


class TrajectoryOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    user_rating: int | None = Field(default=None, ge=1, le=5)
    cost: CostEntry = Field(default_factory=CostEntry)
    degraded: bool = False
    # Terminal disposition distinguishes a *cancelled* / *paused* run from a
    # plain failure: ``completed`` | ``cancelled`` | ``paused`` |
    # ``blocked_on_user`` | ``failed`` | ``completed_with_warning`` |
    # ``partial``. Backward-compatible default keeps old journal rows valid.
    disposition: str = "completed"


class Trajectory(BaseModel):
    model_config = ConfigDict(frozen=True)

    trajectory_id: TrajectoryId = Field(default_factory=lambda: TrajectoryId(new_id()))
    task_id: TaskId
    # Conversation this run belongs to (when known). Lets a chat thread be
    # traced back to its trajectories — e.g. the REC button forging a skill
    # from "this conversation". Optional + backward-compatible.
    thread_id: str | None = None
    arm_id: ArmId
    strategy_id: str = "default"
    recipe_id: str | None = None
    steps: list[Step] = Field(default_factory=list)
    outcome: TrajectoryOutcome
    started_at: datetime = Field(default_factory=now_utc)
    completed_at: datetime = Field(default_factory=now_utc)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def failed_step_count(self) -> int:
        return sum(1 for s in self.steps if not s.success)
