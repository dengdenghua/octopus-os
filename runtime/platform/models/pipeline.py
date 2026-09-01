from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .execution import Step
from .primitives import (
    ArmId,
    CostEntry,
    SkillId,
    TaskId,
    new_id,
    now_utc,
)

# ─── STAGE 1 · INGEST ───────────────────────────────────────

IntentType = Literal[
    "query",
    "task",
    "event",
    "command",
    "plan",
    "refactor",
    "debug",
    "design",
    "chitchat",
]

Modality = Literal["text", "file", "image", "audio", "webhook"]

PrivacyClass = Literal["public", "internal", "confidential", "personal"]


class AmbientSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=now_utc)
    ttl_s: int | None = None


class ParsedIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: UUID = Field(default_factory=new_id)
    raw: str
    intent_type: IntentType
    normalized_goal: str = Field(..., min_length=1)
    modalities: list[Modality] = Field(default_factory=lambda: ["text"])
    user_context: dict[str, Any] = Field(default_factory=dict)
    signals: list[AmbientSignal] = Field(default_factory=list)
    privacy: PrivacyClass = "internal"
    flags: dict[str, bool] = Field(default_factory=dict)  # e.g. {"deep": True}
    ts: datetime = Field(default_factory=now_utc)


RoutePath = Literal["reflex", "deliberative"]


class RouteDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: RoutePath
    reflex_rule_id: str | None = None
    reflex_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = ""


# ─── STAGE 3 · PLAN ─────────────────────────────────────────

NodeKind = Literal[
    "sucker",  # Implementation note.
    "subgraph",  # Implementation note.
    "validator",  # Implementation note.
    "branch",  # Implementation note.
    "merger",  # Implementation note.
    "arm",  # Implementation note.
]

EdgeKind = Literal["normal", "branch", "chromatophore"]


class TaskNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str = Field(..., min_length=1)
    kind: NodeKind = "sucker"
    skill_ref: SkillId | None = None
    args_template: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    failure_retry: int = 0
    # Some nodes are probes whose failure is useful input for later repair
    # nodes (for example, a test command that is expected to fail before a
    # fix).  Keep the failed Step as evidence, but do not turn that expected
    # observation into a graph-level terminal.
    continue_on_failure: bool = False
    timeout_ms: int = 30_000


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_node: str
    to_node: str
    kind: EdgeKind = "normal"
    condition: str | None = None


class BudgetSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    tokens: int = Field(..., gt=0)
    usd: float = Field(..., gt=0.0)
    latency_ms: int = Field(default=600_000, gt=0)


class TaskGraph(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: TaskId = Field(default_factory=lambda: TaskId(new_id()))
    nodes: list[TaskNode] = Field(..., min_length=1)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    budget: BudgetSpec
    strategy: str = "default"
    task_type: str = "general"
    recipe_hash: str | None = None  # Implementation note.
    planner_usage: dict[str, int] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=now_utc)

    @model_validator(mode="after")
    def _validate_edge_refs(self) -> TaskGraph:
        node_ids = {n.node_id for n in self.nodes}
        for edge in self.edges:
            if edge.from_node not in node_ids:
                raise ValueError(
                    f"edge.from_node '{edge.from_node}' not found in nodes "
                    f"(valid: {sorted(node_ids)})"
                )
            if edge.to_node not in node_ids:
                raise ValueError(
                    f"edge.to_node '{edge.to_node}' not found in nodes (valid: {sorted(node_ids)})"
                )
        return self

    @model_validator(mode="after")
    def _validate_no_cycles(self) -> TaskGraph:
        if not self.edges:
            return self  # Implementation note.

        node_ids = {n.node_id for n in self.nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        for edge in self.edges:
            adj[edge.from_node].append(edge.to_node)
            in_degree[edge.to_node] = in_degree.get(edge.to_node, 0) + 1

        # Kahn's algorithm
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            node = queue.pop()
            visited += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited < len(node_ids):
            raise ValueError(
                f"TaskGraph contains a cycle "
                f"({visited}/{len(node_ids)} nodes visited in topological sort)"
            )
        return self


# ─── STAGE 4 · DISPATCH ────────────────────────────────────


class ContextPacketRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    packet_id: UUID
    budget_tokens: int
    quotas_used: dict[str, float] = Field(default_factory=dict)


class ArmAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    arm_id: ArmId
    subgraph: TaskGraph
    context_ref: ContextPacketRef
    deadline: datetime
    # Exclusive resources this assignment must hold while running (ADR-010).
    # Empty (the default) ⇒ no BoidsArbitrator claim ⇒ behaviour unchanged.
    # URIs follow the convention in ADR-010 (e.g. ``device:desktop``,
    # ``file:/abs/path``, ``api:openai``; ``<uri>:read`` for shared readers).
    exclusive_resources: list[str] = Field(default_factory=list)


ArmResultStatus = Literal[
    "success",
    "partial",
    "failed",
    "circuit_broken",
    "immune_reject",
    "budget_exceeded",
]


class ArmResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    arm_id: ArmId
    task_id: TaskId
    status: ArmResultStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    trajectory_id: UUID | None = None
    cost: CostEntry = Field(default_factory=CostEntry)
    reason: str = ""
    steps: list[Step] = Field(default_factory=list)


# ─── STAGE 6 · SYNTHESIZE ───────────────────────────────────


class FinalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    content: str
    sources: list[ArmResult] = Field(default_factory=list)
    degraded: bool = False
    cost_total: CostEntry = Field(default_factory=CostEntry)
    ts: datetime = Field(default_factory=now_utc)
