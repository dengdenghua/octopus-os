from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi


def _node_status_from_events(
    task_id: str,
    node_id: str,
    events: list[Any],
) -> dict[str, Any]:
    from runtime.memory.journal.journal import (
        NodeStartedEvent,
        StepEvent,
    )

    status = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: float | None = None
    error: str | None = None
    output_preview: str | None = None

    for evt in events:
        if not hasattr(evt, "task_id") or evt.task_id != task_id:
            continue
        if isinstance(evt, NodeStartedEvent):
            if getattr(evt, "node_id", None) == node_id:
                status = "running"
                started_at = evt.ts.isoformat() if evt.ts else None
        elif isinstance(evt, StepEvent):
            step = getattr(evt, "step", None)
            if step is not None and hasattr(step, "node_id"):  # noqa: SIM102
                if step.node_id == node_id:
                    if getattr(step, "error", None):
                        status = "failed"
                        error = str(step.error)[:200]
                    else:
                        status = "completed"
                    completed_at = evt.ts.isoformat() if evt.ts else None
                    if started_at and evt.ts:
                        try:
                            from datetime import datetime

                            s = datetime.fromisoformat(started_at)
                            if completed_at is not None:
                                e = datetime.fromisoformat(completed_at)
                                duration_ms = (e - s).total_seconds() * 1000
                        except (ValueError, TypeError):  # noqa: BLE001 — duration computation skipped on bad timestamps
                            pass
                    out = getattr(step, "output", None)
                    if out is not None:
                        text = str(out)
                        output_preview = text[:300] + "..." if len(text) > 300 else text

    return {
        "node_id": node_id,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "error": error,
        "output_preview": output_preview,
    }


def _graph_to_dag_visual(graph: Any, node_statuses: list[dict]) -> dict[str, Any]:
    nodes = []
    for n in graph.nodes:
        ns = next(
            (s for s in node_statuses if s["node_id"] == n.node_id),
            {"node_id": n.node_id, "status": "pending"},
        )
        nodes.append(
            {
                "id": n.node_id,
                "kind": n.kind,
                "skill_ref": str(n.skill_ref) if n.skill_ref else None,
                "status": ns.get("status", "pending"),
                "started_at": ns.get("started_at"),
                "completed_at": ns.get("completed_at"),
                "duration_ms": ns.get("duration_ms"),
                "error": ns.get("error"),
                "output_preview": ns.get("output_preview"),
            }
        )
    edges = []
    for e in graph.edges:
        edges.append(
            {
                "from": e.from_node,
                "to": e.to_node,
                "kind": e.kind,
                "condition": e.condition,
            }
        )
    return {
        "task_id": str(graph.task_id),
        "strategy": graph.strategy,
        "task_type": graph.task_type,
        "budget": {
            "tokens": graph.budget.tokens,
            "usd": graph.budget.usd,
            "latency_ms": graph.budget.latency_ms,
        },
        "nodes": nodes,
        "edges": edges,
        "ts": graph.ts.isoformat() if graph.ts else None,
    }


def create_dag_debugger_router(
    *,
    journal: Any,
    planner: Any = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["dag-debugger"])

    @router.get("/api/dag/task/{task_id}")
    def get_task_dag(task_id: str) -> dict[str, Any]:
        events = journal.read_by_task(task_id)
        if not events:
            raise HTTPException(404, f"no events found for task {task_id}")

        graph = None
        for evt in events:
            if evt.event_type == "task_started":
                graph = getattr(evt, "graph", None) or getattr(evt, "task_graph", None)
                break

        if graph is None:
            from runtime.memory.journal.journal import TrajectoryEvent

            for evt in events:
                if isinstance(evt, TrajectoryEvent):
                    traj = getattr(evt, "trajectory", None)
                    if traj is not None and hasattr(traj, "graph"):
                        graph = traj.graph
                        break

        if graph is None:
            return {
                "task_id": task_id,
                "nodes": [],
                "edges": [],
                "status": "unknown",
                "message": "TaskGraph not found in journal events",
            }

        node_ids = [n.node_id for n in graph.nodes] if hasattr(graph, "nodes") else []
        node_statuses = [_node_status_from_events(task_id, nid, events) for nid in node_ids]
        return _graph_to_dag_visual(graph, node_statuses)

    @router.get("/api/dag/task/{task_id}/timeline")
    def get_task_timeline(
        task_id: str,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        events = journal.read_by_task(task_id)
        if not events:
            raise HTTPException(404, f"no events found for task {task_id}")

        timeline: list[dict[str, Any]] = []
        for evt in events[:limit]:
            entry: dict[str, Any] = {
                "event_type": evt.event_type,
                "ts": evt.ts.isoformat() if evt.ts else None,
                "arm_id": str(evt.arm_id) if evt.arm_id else None,
                "agent_id": evt.agent_id,
            }
            if evt.event_type == "node_started":
                entry["node_id"] = getattr(evt, "node_id", "")
                entry["skill_ref"] = getattr(evt, "skill_ref", "")
                entry["node_index"] = getattr(evt, "node_index", 0)
            elif evt.event_type == "step":
                step = getattr(evt, "step", None)
                if step:
                    entry["node_id"] = getattr(step, "node_id", "")
                    entry["skill"] = str(getattr(step, "skill_ref", ""))
                    entry["success"] = not bool(getattr(step, "error", None))
            elif evt.event_type == "task_checkpoint":
                entry["nodes_completed"] = getattr(evt, "nodes_completed", 0)
                entry["total_nodes"] = getattr(evt, "total_nodes", 0)
                entry["tokens_spent"] = getattr(evt, "tokens_spent", 0)
                entry["usd_spent"] = getattr(evt, "usd_spent", 0.0)
            timeline.append(entry)

        return {
            "task_id": task_id,
            "event_count": len(events),
            "timeline": timeline,
        }

    @router.get("/api/dag/active")
    def list_active_tasks() -> dict[str, Any]:
        from runtime.memory.journal.journal import (
            TaskStartedEvent,
            TrajectoryEvent,
        )

        all_events = journal.read_all()
        started_tasks: dict[str, dict[str, Any]] = {}
        completed_tasks: set[str] = set()

        for evt in all_events:
            if isinstance(evt, TaskStartedEvent) and evt.task_id:
                started_tasks[str(evt.task_id)] = {
                    "task_id": str(evt.task_id),
                    "strategy": getattr(evt, "strategy", ""),
                    "task_type": getattr(evt, "task_type", ""),
                    "total_nodes": getattr(evt, "total_nodes", 0),
                    "started_at": evt.ts.isoformat() if evt.ts else None,
                    "status": "running",
                }
            elif isinstance(evt, TrajectoryEvent) and evt.task_id:
                completed_tasks.add(str(evt.task_id))

        active = []
        for tid, info in started_tasks.items():
            if tid not in completed_tasks:
                active.append(info)
            else:
                info["status"] = "completed"

        return {
            "active_count": len(active),
            "tasks": active,
        }

    @router.get("/api/dag/stats")
    def dag_stats() -> dict[str, Any]:
        from runtime.memory.journal.journal import (
            StepEvent,
            TaskStartedEvent,
            TrajectoryEvent,
        )

        all_events = journal.read_all()
        total_tasks = 0
        completed_tasks = 0
        failed_tasks = 0
        total_steps = 0
        failed_steps = 0
        total_tokens = 0
        total_usd = 0.0
        durations: list[float] = []
        task_starts: dict[str, float] = {}

        for evt in all_events:
            if isinstance(evt, TaskStartedEvent) and evt.task_id:
                total_tasks += 1
                if evt.ts:
                    task_starts[str(evt.task_id)] = evt.ts.timestamp()
            elif isinstance(evt, TrajectoryEvent) and evt.task_id:
                completed_tasks += 1
                tid = str(evt.task_id)
                if tid in task_starts and evt.ts:
                    durations.append(evt.ts.timestamp() - task_starts[tid])
                traj = getattr(evt, "trajectory", None)
                if traj is not None:
                    outcome = getattr(traj, "outcome", "")
                    if outcome in ("failure", "error", "timeout"):
                        failed_tasks += 1
                    total_tokens += getattr(traj, "tokens_spent", 0) or 0
                    total_usd += getattr(traj, "usd_spent", 0.0) or 0.0
            elif isinstance(evt, StepEvent):
                total_steps += 1
                step = getattr(evt, "step", None)
                if step and getattr(step, "error", None):
                    failed_steps += 1

        avg_duration = sum(durations) / len(durations) if durations else 0.0
        success_rate = (
            (completed_tasks - failed_tasks) / completed_tasks if completed_tasks else 0.0
        )

        return {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "success_rate": round(success_rate, 3),
            "total_steps": total_steps,
            "failed_steps": failed_steps,
            "step_success_rate": round((total_steps - failed_steps) / total_steps, 3)
            if total_steps
            else 0.0,
            "total_tokens": total_tokens,
            "total_usd": round(total_usd, 4),
            "avg_duration_seconds": round(avg_duration, 2),
        }

    @router.post("/api/dag/dry-run")
    def dry_run_graph(body: dict[str, Any]) -> dict[str, Any]:
        from runtime.platform.models.pipeline import (
            BudgetSpec,
            TaskGraph,
            TaskNode,
            WorkflowEdge,
        )

        try:
            nodes_data = body.get("nodes", [])
            edges_data = body.get("edges", [])
            budget_data = body.get("budget", {"tokens": 20000, "usd": 0.5, "latency_ms": 600000})

            nodes = [
                TaskNode(
                    node_id=n.get("node_id", f"n{i}"),
                    kind=n.get("kind", "sucker"),
                    skill_ref=n.get("skill_ref"),
                    args_template=n.get("args_template", {}),
                )
                for i, n in enumerate(nodes_data)
            ]
            edges = [
                WorkflowEdge(
                    from_node=e.get("from_node", e.get("from", "")),
                    to_node=e.get("to_node", e.get("to", "")),
                    kind=e.get("kind", "normal"),
                    condition=e.get("condition"),
                )
                for e in edges_data
            ]
            budget = BudgetSpec(**budget_data)

            graph = TaskGraph(
                nodes=nodes,
                edges=edges,
                budget=budget,
                strategy=body.get("strategy", "default"),
                task_type=body.get("task_type", "general"),
            )
        except Exception as exc:
            raise HTTPException(400, f"invalid graph definition: {exc}") from exc

        from runtime.core.graph_runtime.runtime import _topo_layers

        layers = _topo_layers(graph.nodes, graph.edges)
        max_parallelism = max(len(layer) for layer in layers) if layers else 1
        critical_path_len = len(layers)

        return {
            "valid": True,
            "task_id": str(graph.task_id),
            "topology": {
                "layers": [[graph.nodes[i].node_id for i in layer] for layer in layers],
                "max_parallelism": max_parallelism,
                "critical_path_length": critical_path_len,
                "total_nodes": len(nodes),
                "total_edges": len(edges),
            },
            "budget": budget_data,
            "estimated_min_steps": critical_path_len,
        }

    return router
