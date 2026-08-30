"""Immutable public models emitted by the swarm runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.models import ArmResult, TaskId, now_utc

SplitStrategy = Literal["per_node", "single", "topo_layers"]
SwarmEventLane = Literal["workflow", "agent", "timeline"]


class WorkContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_id: str
    agent_id: str
    role: str
    node_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    owned_scope: list[str] = Field(default_factory=list)
    forbidden_scope: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)


class SwarmPhase(BaseModel):
    model_config = ConfigDict(frozen=True)

    phase_index: int = Field(ge=0)
    assignment_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    parallel: bool = False


class SwarmPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    strategy: SplitStrategy
    max_workers: int = Field(ge=1)
    phases: list[SwarmPhase] = Field(default_factory=list)
    contracts: list[WorkContract] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)


class SwarmEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    lane: SwarmEventLane
    task_id: TaskId | None = None
    phase_index: int | None = Field(default=None, ge=0)
    agent_id: str | None = None
    node_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=now_utc)


class AgentHandoff(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: str
    task_id: TaskId
    phase_index: int = Field(ge=0)
    node_ids: list[str] = Field(default_factory=list)
    status: str
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    cost_usd: float = Field(default=0.0, ge=0.0)
    reason: str = ""


class SwarmPhaseReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    phase_index: int = Field(ge=0)
    node_ids: list[str] = Field(default_factory=list)
    assignment_count: int = Field(default=0, ge=0)
    handoff_count: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    status: Literal["success", "partial", "failed", "empty"] = "empty"
    wall_ms: float = Field(default=0.0, ge=0.0)
    cost_usd: float = Field(default=0.0, ge=0.0)


class SwarmResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    arm_results: list[ArmResult] = Field(default_factory=list)
    parallelism_achieved: int = Field(default=0, ge=0)
    total_wall_ms: float = Field(default=0.0, ge=0.0)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    all_successful: bool = False
    plan: SwarmPlan | None = None
    events: list[SwarmEvent] = Field(default_factory=list)
    handoffs: list[AgentHandoff] = Field(default_factory=list)
    phase_reports: list[SwarmPhaseReport] = Field(default_factory=list)
    contract_validation_issues: list[str] = Field(default_factory=list)
    contract_validation_warnings: list[str] = Field(default_factory=list)
    completion_receipt: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AgentHandoff",
    "SplitStrategy",
    "SwarmEvent",
    "SwarmEventLane",
    "SwarmPhase",
    "SwarmPhaseReport",
    "SwarmPlan",
    "SwarmResult",
    "WorkContract",
]
