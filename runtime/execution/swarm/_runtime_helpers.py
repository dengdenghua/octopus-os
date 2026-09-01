from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from runtime.execution.swarm.models import (
    AgentHandoff,
    SwarmEvent,
    SwarmPhase,
    SwarmPhaseReport,
    WorkContract,
)
from runtime.platform.models import (
    ArmAssignment,
    ArmId,
    ArmResult,
    ContextPacketRef,
    TaskGraph,
    TaskId,
    new_id,
    now_utc,
)

if TYPE_CHECKING:
    from runtime.execution.arms.base import Worker


@dataclass(frozen=True)
class _PreparedLayer:
    phase: SwarmPhase
    pairs: list[tuple[ArmAssignment, Worker]]
    unmatched: list[tuple[ArmAssignment, ArmResult]]


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _make_context_ref(graph: TaskGraph) -> ContextPacketRef:
    return ContextPacketRef(
        packet_id=new_id(),
        budget_tokens=graph.budget.tokens,
    )


def _make_deadline(graph: TaskGraph) -> datetime:
    return now_utc() + timedelta(milliseconds=graph.budget.latency_ms)


def _node_sort_key(node_id: str) -> tuple[int, str]:
    """Prefer numeric ``n12`` ordering over raw lexicographic sort."""
    if node_id.startswith("n") and node_id[1:].isdigit():
        return (int(node_id[1:]), node_id)
    return (10**9, node_id)


def _split_per_node(graph: TaskGraph) -> list[ArmAssignment]:
    if graph.edges:
        raise ValueError(
            f"per_node requires edgeless graph (got {len(graph.edges)} edges) · "
            "use split_strategy='topo_layers' to respect dependencies"
        )
    assignments: list[ArmAssignment] = []
    for node in graph.nodes:
        sub_graph = TaskGraph(
            nodes=[node],
            edges=[],
            budget=graph.budget,
            task_type=graph.task_type,
        )
        assignments.append(
            ArmAssignment(
                arm_id=ArmId("unassigned"),  # Implementation note.
                subgraph=sub_graph,
                context_ref=_make_context_ref(graph),
                deadline=_make_deadline(graph),
            )
        )
    return assignments


def _split_single(graph: TaskGraph) -> list[ArmAssignment]:
    return [
        ArmAssignment(
            arm_id=ArmId("unassigned"),
            subgraph=graph,
            context_ref=_make_context_ref(graph),
            deadline=_make_deadline(graph),
        )
    ]


def _split_topo_layers(graph: TaskGraph) -> list[list[ArmAssignment]]:
    node_by_id = {n.node_id: n for n in graph.nodes}
    if len(node_by_id) != len(graph.nodes):
        raise ValueError("duplicate node_id in graph")

    indeg: dict[str, int] = {nid: 0 for nid in node_by_id}
    children: dict[str, list[str]] = {nid: [] for nid in node_by_id}
    for e in graph.edges:
        if e.from_node not in node_by_id or e.to_node not in node_by_id:
            raise ValueError(f"edge references unknown node: {e}")
        indeg[e.to_node] += 1
        children[e.from_node].append(e.to_node)

    layers: list[list[ArmAssignment]] = []
    remaining = dict(indeg)
    while remaining:
        ready = [nid for nid, d in remaining.items() if d == 0]
        if not ready:
            raise ValueError(f"cycle in TaskGraph (unresolved nodes: {sorted(remaining)})")
        ready.sort()
        layer: list[ArmAssignment] = []
        for nid in ready:
            sub_graph = TaskGraph(
                nodes=[node_by_id[nid]],
                edges=[],
                budget=graph.budget,
                task_type=graph.task_type,
            )
            layer.append(
                ArmAssignment(
                    arm_id=ArmId("unassigned"),
                    subgraph=sub_graph,
                    context_ref=_make_context_ref(graph),
                    deadline=_make_deadline(graph),
                )
            )
            del remaining[nid]
            for child in children[nid]:
                if child in remaining:
                    remaining[child] -= 1
        layers.append(layer)
    return layers


def _assignment_node_ids(assignment: ArmAssignment) -> list[str]:
    return [node.node_id for node in assignment.subgraph.nodes]


def _assignment_role(assignment: ArmAssignment) -> str:
    skills = [
        str(node.skill_ref) for node in assignment.subgraph.nodes if node.skill_ref is not None
    ]
    if not skills:
        return assignment.subgraph.task_type
    if len(set(skills)) == 1:
        return skills[0]
    return " + ".join(skills)


def _make_contract(
    *,
    assignment_id: str,
    assignment: ArmAssignment,
    agent_id: str,
    all_node_ids: list[str],
    graph: TaskGraph,
) -> WorkContract:
    node_ids = _assignment_node_ids(assignment)
    depends_on = [
        edge.from_node
        for edge in graph.edges
        if edge.to_node in node_ids and edge.from_node not in node_ids
    ]
    return WorkContract(
        contract_id=assignment_id,
        agent_id=agent_id,
        role=_assignment_role(assignment),
        node_ids=node_ids,
        depends_on=depends_on,
        owned_scope=[f"node:{node_id}" for node_id in node_ids],
        forbidden_scope=[f"node:{node_id}" for node_id in all_node_ids if node_id not in node_ids],
        success_criteria=[f"Complete node {node_id}" for node_id in node_ids]
        + ["Return an ArmResult with status=success"],
    )


def _agent_assigned_events(
    task_id: TaskId,
    phase_index: int,
    pairs: list[tuple[ArmAssignment, Worker]],
) -> list[SwarmEvent]:
    events: list[SwarmEvent] = []
    for assignment, arm in pairs:
        agent_id = str(getattr(arm, "arm_id", ArmId("unknown")))
        node_ids = _assignment_node_ids(assignment)
        events.append(
            SwarmEvent(
                type="agent_assigned",
                lane="agent",
                task_id=task_id,
                phase_index=phase_index,
                agent_id=agent_id,
                node_ids=node_ids,
                payload={"role": _assignment_role(assignment)},
            )
        )
    return events


def _agent_started_events(
    task_id: TaskId,
    phase_index: int,
    pairs: list[tuple[ArmAssignment, Worker]],
) -> list[SwarmEvent]:
    events: list[SwarmEvent] = []
    for assignment, arm in pairs:
        agent_id = str(getattr(arm, "arm_id", ArmId("unknown")))
        events.append(
            SwarmEvent(
                type="agent_started",
                lane="timeline",
                task_id=task_id,
                phase_index=phase_index,
                agent_id=agent_id,
                node_ids=_assignment_node_ids(assignment),
                payload={"role": _assignment_role(assignment)},
            )
        )
    return events


def _agent_finished_events(
    task_id: TaskId,
    phase_index: int,
    results: list[ArmResult],
) -> list[SwarmEvent]:
    events: list[SwarmEvent] = []
    for result in results:
        agent_id = str(result.arm_id)
        events.append(
            SwarmEvent(
                type="agent_finished",
                lane="timeline",
                task_id=task_id,
                phase_index=phase_index,
                agent_id=agent_id,
                payload={
                    "arm_task_id": str(result.task_id),
                    "status": result.status,
                    "reason": result.reason,
                    "cost_usd": result.cost.usd,
                },
            )
        )
    return events


def _agent_handoffs(
    task_id: TaskId,
    phase_index: int,
    assignment_results: list[tuple[ArmAssignment, ArmResult]],
) -> list[AgentHandoff]:
    handoffs: list[AgentHandoff] = []
    for assignment, result in assignment_results:
        handoffs.append(
            AgentHandoff(
                agent_id=str(result.arm_id),
                task_id=task_id,
                phase_index=phase_index,
                node_ids=_result_node_ids(result) or _assignment_node_ids(assignment),
                status=result.status,
                summary=_result_summary(result),
                artifacts=_result_artifacts(result),
                cost_usd=result.cost.usd,
                reason=result.reason,
            )
        )
    return handoffs


def _phase_report(
    *,
    phase: SwarmPhase,
    results: list[ArmResult],
    handoffs: list[AgentHandoff],
    wall_ms: float,
) -> SwarmPhaseReport:
    succeeded = sum(1 for result in results if result.status == "success")
    failed = sum(1 for result in results if result.status != "success")
    if not results:
        status: Literal["success", "partial", "failed", "empty"] = "empty"
    elif succeeded == len(results):
        status = "success"
    elif succeeded == 0:
        status = "failed"
    else:
        status = "partial"

    return SwarmPhaseReport(
        phase_index=phase.phase_index,
        node_ids=phase.node_ids,
        assignment_count=len(phase.assignment_ids),
        handoff_count=len(handoffs),
        succeeded=succeeded,
        failed=failed,
        status=status,
        wall_ms=wall_ms,
        cost_usd=sum(result.cost.usd for result in results),
    )


def _result_node_ids(result: ArmResult) -> list[str]:
    if result.steps:
        node_ids: list[str] = []
        for step in result.steps:
            if step.node_id not in node_ids:
                node_ids.append(step.node_id)
        return node_ids
    raw = result.outputs.get("node_ids") or result.outputs.get("task_ids")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    raw = result.outputs.get("node_id") or result.outputs.get("task_id")
    if raw:
        return [str(raw)]
    return []


def _result_summary(result: ArmResult) -> str:
    for key in ("summary", "handoff", "result", "message"):
        value = result.outputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if result.reason:
        return result.reason
    if result.status == "success":
        return "completed"
    return result.status


def _result_artifacts(result: ArmResult) -> list[str]:
    artifacts: list[str] = []
    for key in ("artifacts", "files", "modified_files", "deliverables"):
        value = result.outputs.get(key)
        if isinstance(value, str) and value:
            artifacts.append(value)
        elif isinstance(value, list):
            artifacts.extend(str(item) for item in value if item)
    for step in result.steps:
        artifacts.extend(step.result.files_modified)
    return list(dict.fromkeys(artifacts))
