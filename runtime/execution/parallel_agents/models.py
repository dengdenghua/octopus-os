from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ParallelTaskStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "partial",
]


class DispatchTaskInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str
    subagent_name: str = "general-purpose"
    task_id: str | None = None  # Implementation note.
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0
    write_paths: list[str] = Field(default_factory=list)


class WorkContract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contract_id: str
    agent_id: str
    role: str
    task_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    owned_scope: list[str] = Field(default_factory=list)
    forbidden_scope: list[str] = Field(default_factory=list)
    write_paths: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)


class BatchPhase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phase_index: int
    task_ids: list[str] = Field(default_factory=list)
    parallel: bool = False


class BatchPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    batch_id: str
    strategy: str
    max_concurrency: int
    phases: list[BatchPhase] = Field(default_factory=list)
    contracts: list[WorkContract] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)


class DispatchRequest(BaseModel):
    """POST /api/agents/parallel/dispatch body."""

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    tasks: list[DispatchTaskInput]
    max_concurrency: int | None = None
    aggregation_strategy: str | None = None  # "concat" | "vote" | ... (stub)
    execution_mode: str | None = None  # "parallel" | "sequential"
    thread_id: str | None = None  # Implementation note.
    model_name: str | None = None  # Implementation note.


class SplitRequest(BaseModel):
    """POST /api/agents/parallel/split body."""

    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    task: str
    max_subtasks: int | None = None
    context: str | None = None
    model_name: str | None = None


class TaskResult(BaseModel):
    task_id: str
    batch_id: str
    description: str | None = None
    status: str  # Implementation note.
    result: str | None = None
    error: str | None = None
    started_at: str | None = None  # ISO8601
    completed_at: str | None = None  # ISO8601
    duration_seconds: float | None = None
    subagent_name: str = "general-purpose"
    work_contract: WorkContract | None = None
    worker_generation: int | None = None
    worker_state: str = "pending"
    replacement_generation: int | None = None
    late_result_ignored_at: str | None = None
    worker_isolation: str = "thread"
    worker_isolation_reason: str | None = None


class BatchStreamEvent(BaseModel):
    type: Literal["stage_change", "task_update", "tool_call", "batch_complete"]
    batch_id: str
    sequence: int | None = None
    created_at: str | None = None
    task_id: str | None = None
    lane: Literal["workflow", "agent", "computer", "timeline"] | None = None
    status: str | None = None
    subagent_name: str | None = None
    phase: str | None = None
    stage: str | None = None
    tool_name: str | None = None
    tool_input_preview: str | None = None
    tool_output_preview: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    payload: dict[str, object] = Field(default_factory=dict)
    progress: float | None = None
    message: str | None = None
    description: str | None = None
    result_preview: str | None = None
    duration_seconds: float | None = None
    error: str | None = None


class BatchResult(BaseModel):
    batch_id: str
    status: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    created_at: str | None = None
    completed_at: str | None = None
    results: list[TaskResult] = Field(default_factory=list)
    aggregated_content: str | None = None
    aggregation_strategy: str | None = None
    conflicts: list[str] = Field(default_factory=list)
    plan: BatchPlan | None = None
    event_log: list[BatchStreamEvent] = Field(default_factory=list)
    event_log_truncated: bool = False
    event_log_dropped_count: int = 0
    completion_receipt: dict[str, object] = Field(default_factory=dict)
    file_write_observability: dict[str, object] = Field(default_factory=dict)
    coordination_summary: dict[str, object] = Field(default_factory=dict)
    worker_observability: dict[str, object] = Field(default_factory=dict)


class BatchRecoveryTask(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    status: str
    subagent_name: str
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0
    write_paths: list[str] = Field(default_factory=list)
    description_preview: str | None = None
    result_preview: str | None = None
    error: str | None = None
    submitted_at: str | None = None
    cancel_requested_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    artifact_paths: list[str] = Field(default_factory=list)
    work_contract: WorkContract | None = None
    route_decision: dict[str, Any] = Field(default_factory=dict)
    worker_generation: int | None = None
    worker_state: str = "pending"
    replacement_generation: int | None = None
    late_result_ignored_at: str | None = None
    worker_isolation: str = "thread"
    worker_isolation_reason: str | None = None


class BatchRecoverySnapshot(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_: Literal["echo.parallel_batch_recovery_snapshot.v1"] = Field(
        default="echo.parallel_batch_recovery_snapshot.v1",
        alias="schema",
    )
    batch_id: str
    status: str
    terminal: bool
    resume_available: bool
    created_at: str | None = None
    completed_at: str | None = None
    task_count: int
    completed_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    running_tasks: int
    pending_tasks: int
    tasks: list[BatchRecoveryTask] = Field(default_factory=list)
    dag: dict[str, list[str]] = Field(default_factory=dict)
    plan: BatchPlan | None = None
    event_sequence: dict[str, object] = Field(default_factory=dict)
    artifact_paths: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    completion_receipt: dict[str, object] = Field(default_factory=dict)
    file_write_observability: dict[str, object] = Field(default_factory=dict)
    coordination_summary: dict[str, object] = Field(default_factory=dict)
    worker_observability: dict[str, object] = Field(default_factory=dict)
    recovery_hints: dict[str, object] = Field(default_factory=dict)
    safety: dict[str, object] = Field(default_factory=dict)


class OrchestratorStatus(BaseModel):
    active_count: int = 0
    pending_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0
    max_concurrency: int = 0
    batches: dict[str, str] = Field(default_factory=dict)  # batch_id → status
    worker_generation: int = 0
    worker_replacement_count: int = 0
    retired_worker_generation_count: int = 0


class SplitTask(BaseModel):
    task_id: str
    description: str
    subagent_name: str = "general-purpose"
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0


class SplitResult(BaseModel):
    tasks: list[SplitTask] = Field(default_factory=list)
    dag_levels: list[list[str]] = Field(default_factory=list)
    total_levels: int = 0
    is_parallelizable: bool = False
