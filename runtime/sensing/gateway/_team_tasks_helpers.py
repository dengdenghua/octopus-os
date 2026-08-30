"""Module-level helpers for the persistent team tasks router.

Holds the type aliases, constants, and pure helper functions that the
``team_tasks_router`` factory uses. Keeping them here lets the router
module stay focused on routing and lifecycle wiring.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.safety.organization import (
    AgentSpec,
    CoordinationProtocol,
    Role,
    TeamTopology,
)
from runtime.sensing.gateway._team_tasks_models import TeamTaskWire

try:
    from fastapi import HTTPException

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    HTTPException = None  # type: ignore[assignment,misc]

_LOG = logging.getLogger("echo.team_tasks")

TeamEventBroadcaster = Callable[[str, dict[str, Any]], Awaitable[None] | None]
TaskProjection = Callable[[str, dict[str, Any]], None]
RunnerFactory = Callable[..., Any]
RoomMembershipResolver = Callable[[str], list[str]]
RoomParticipantResolver = Callable[..., dict[str, Any] | None]

# Bounds resource exhaustion via /run. Each running task spawns a
# daemon thread with a 15-min timeout; without this cap a single
# authenticated user could fan out unbounded threads.
_MAX_CONCURRENT_RUNS = 16

# sop_template flows into a filesystem lookup (load_meta_skill →
# meta_skills_dir / f"{name}.yaml"). Restrict to a flat slug to
# defend against path traversal in depth, even though the dir
# helper is itself confined.
_SOP_TEMPLATE_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")

_RUNNER_ROLE_ORDER: tuple[Role, ...] = (
    Role.PLANNER,
    Role.RESEARCHER,
    Role.GENERATOR,
    Role.CRITIC,
    Role.SYNTHESIZER,
    Role.EVALUATOR,
)

_DEFAULT_AGENT_BY_ROLE: dict[Role, str] = {
    Role.PLANNER: "planner",
    Role.RESEARCHER: "researcher",
    Role.GENERATOR: "implementer",
    Role.CRITIC: "reviewer",
    Role.SYNTHESIZER: "synthesizer",
    Role.EVALUATOR: "evaluator",
}

_ROLE_KEYWORDS: dict[Role, tuple[str, ...]] = {
    Role.PLANNER: (
        "plan",
        "planner",
        "scope",
        "outline",
        "decompose",
        "breakdown",
        "requirements",
        "ask_user",
        "question",
        "clarify",
        "roadmap",
    ),
    Role.RESEARCHER: (
        "research",
        "search",
        "fetch",
        "source",
        "market",
        "data",
        "news",
        "trend",
        "competitor",
        "collect",
        "investigate",
        "crawl",
        "web",
    ),
    Role.CRITIC: (
        "critic",
        "review",
        "audit",
        "risk",
        "verify",
        "check",
        "compliance",
        "safety",
        "vulnerability",
        "postmortem",
    ),
    Role.EVALUATOR: (
        "evaluate",
        "evaluator",
        "score",
        "quality",
        "test",
        "benchmark",
        "validator",
        "rank",
        "rubric",
    ),
    Role.SYNTHESIZER: (
        "synth",
        "synthesize",
        "summarize",
        "report",
        "brief",
        "memo",
        "final",
        "deliver",
        "write",
        "compose",
        "export",
        "publish",
    ),
    Role.GENERATOR: (
        "generate",
        "generator",
        "build",
        "implement",
        "draft",
        "create",
        "code",
        "produce",
        "make",
    ),
}


def _prepare_team_run(task: TeamTaskWire) -> dict[str, Any]:
    from runtime.memory.skills_lib.meta_skill import (
        compile_to_task_graph,
        load_meta_skill,
        match_meta_skill,
    )

    task_input = _task_input_text(task)
    meta = None
    explicit_template = task.sop_template.strip()
    if explicit_template:
        meta = load_meta_skill(explicit_template)
        if meta is None:
            raise ValueError(f"meta-skill not found: {explicit_template}")
    else:
        meta = match_meta_skill(task_input)

    task_graph: dict[str, Any] | None = None
    if meta is not None:
        graph = compile_to_task_graph(meta, user_input=_task_user_input(task))
        task_graph = _task_graph_summary(graph)
        topology = _topology_from_task_graph(task, meta, graph)
    else:
        topology = _fallback_topology(task)

    context = {
        "room_id": task.room_id,
        "team_id": task.room_id,
        "team_task_id": task.id,
        "team_task": task.model_dump(),
        "meta_skill": getattr(meta, "name", None),
        "task_graph": task_graph,
    }
    return {
        "task_input": task_input,
        "context": context,
        "meta_skill": getattr(meta, "name", None),
        "task_graph": task_graph,
        "topology": topology,
    }


def _team_task_process_timeline(task: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = task.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    process_events = [item for item in metadata.get("process_events", []) if isinstance(item, dict)]
    artifacts = [item for item in task.get("produced_artifacts", []) if isinstance(item, dict)]
    nodes: list[dict[str, Any]] = []
    created_at = str(task.get("created_at") or "")
    updated_at = str(task.get("updated_at") or "")
    if created_at:
        nodes.append(
            _team_timeline_node(
                node_id="task-created",
                lane="workflow",
                kind="task_created",
                ts=created_at,
                title="Task created",
                status="pending",
                severity="info",
                summary=str(task.get("title") or ""),
            )
        )
    started_at = str(task.get("started_at") or "")
    if started_at:
        nodes.append(
            _team_timeline_node(
                node_id="run-started",
                lane="workflow",
                kind="run_started",
                ts=started_at,
                title="Run started",
                status="running",
                severity="info",
                summary=_runner_summary(metadata.get("runner")),
            )
        )
    for idx, event in enumerate(process_events):
        nodes.append(_team_process_event_node(event, idx))
    for idx, artifact in enumerate(artifacts):
        nodes.append(
            _team_timeline_node(
                node_id=f"artifact-{artifact.get('id') or idx}",
                lane="artifact",
                kind=str(artifact.get("type") or "artifact"),
                ts=str(artifact.get("created_at") or task.get("completed_at") or updated_at),
                title=str(artifact.get("title") or "Produced artifact"),
                status="ok" if artifact.get("ok", True) is not False else "failed",
                severity="info" if artifact.get("ok", True) is not False else "high",
                summary=str(artifact.get("content") or "")[:500],
                data={key: value for key, value in artifact.items() if key not in {"content"}},
            )
        )
    completed_at = str(task.get("completed_at") or "")
    status = str(task.get("status") or "")
    if completed_at:
        nodes.append(
            _team_timeline_node(
                node_id="run-completed",
                lane="workflow",
                kind=f"run_{status or 'completed'}",
                ts=completed_at,
                title=f"Run {status or 'completed'}",
                status=status or "done",
                severity="high"
                if status == "failed"
                else "medium"
                if status == "cancelled"
                else "info",
                summary=str(metadata.get("error") or ""),
            )
        )
    nodes = sorted(nodes, key=_team_timeline_sort_key)
    assignees = [item for item in task.get("assignees", []) if isinstance(item, dict)]
    return {
        "schema": "echo.team_task_process_timeline.v1",
        "task_id": task.get("id"),
        "room_id": task.get("room_id"),
        "overview": {
            "title": task.get("title") or "",
            "description": task.get("description") or "",
            "status": status,
            "created_by": task.get("created_by"),
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
            "updated_at": task.get("updated_at"),
            "runner": metadata.get("runner") if isinstance(metadata.get("runner"), dict) else {},
            "event_count": len(process_events),
            "artifact_count": len(artifacts),
            "assignee_count": len(assignees),
        },
        "assignees": assignees,
        "artifacts": [
            {key: value for key, value in artifact.items() if key != "content"}
            for artifact in artifacts
        ],
        "timeline": nodes,
        "safety": {
            "raw_messages_included": False,
            "artifact_content_truncated": True,
            "process_events_persisted": True,
            "process_event_limit": 300,
        },
    }


def _team_process_event_node(event: dict[str, Any], index: int) -> dict[str, Any]:
    event_type = str(event.get("type") or "runner_event")
    role = str(event.get("role") or "")
    status = str(event.get("status") or "")
    agent_id = str(event.get("agent_id") or "")
    if event_type == "team_role_start":
        title = f"Role started: {role or agent_id or 'agent'}"
        lane = "agent"
    elif event_type == "team_role_end":
        title = f"Role finished: {role or agent_id or 'agent'}"
        lane = "agent"
    else:
        title = event_type.replace("_", " ")
        lane = "timeline"
    severity = "high" if status in {"error", "failed", "failure"} else "info"
    return _team_timeline_node(
        node_id=f"process-event-{index}",
        lane=lane,
        kind=event_type,
        ts=str(event.get("ts") or ""),
        title=title,
        status=status or "ok",
        severity=severity,
        summary=str(event.get("error") or event.get("output") or "")[:500],
        data={
            "role": role,
            "agent_id": agent_id,
            "event": event.get("event") if isinstance(event.get("event"), dict) else {},
        },
    )


def _team_timeline_node(
    *,
    node_id: str,
    lane: str,
    kind: str,
    ts: str,
    title: str,
    status: str,
    severity: str,
    summary: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "lane": lane,
        "kind": kind,
        "ts": ts,
        "title": title,
        "status": status,
        "severity": severity,
        "summary": summary,
        "data": data or {},
    }


def _team_timeline_sort_key(node: dict[str, Any]) -> tuple[str, str]:
    return (str(node.get("ts") or ""), str(node.get("id") or ""))


def _runner_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    engine = value.get("engine") or value.get("topology_name") or value.get("topology")
    status = value.get("status")
    return " ".join(str(item) for item in (engine, status) if item)


def _task_input_text(task: TeamTaskWire) -> str:
    lines = [task.title.strip()]
    if task.description.strip():
        lines.extend(["", task.description.strip()])
    if task.sop_template.strip():
        lines.extend(["", f"SOP template: {task.sop_template.strip()}"])
    output_contract = _task_output_contract_text(task)
    if output_contract:
        lines.extend(["", output_contract])
    return "\n".join(lines).strip()


def _task_output_contract_text(task: TeamTaskWire) -> str | None:
    contract = task.metadata.get("output_contract")
    if not isinstance(contract, dict):
        return None
    name = str(contract.get("name") or "output_contract").strip()
    lines = [f"Output contract: {name}"]
    instructions = contract.get("instructions")
    if isinstance(instructions, list):
        for instruction in instructions:
            text = str(instruction or "").strip()
            if text:
                lines.append(f"- {text}")
    schema = contract.get("schema")
    if isinstance(schema, dict) and schema:
        lines.extend(
            [
                "- JSON schema shape:",
                json.dumps(schema, ensure_ascii=False, indent=2),
            ]
        )
    return "\n".join(lines)


def _task_user_input(task: TeamTaskWire) -> dict[str, Any]:
    text = _task_input_text(task)
    return {
        "task": text,
        "title": task.title,
        "description": task.description,
        "topic": task.title,
        "target": task.title,
        "goal": task.title,
        "purpose": task.description or task.title,
        "seed": text,
        "room_id": task.room_id,
        "task_id": task.id,
        "assignees": [a.model_dump() for a in task.assignees],
    }


def _topology_from_task_graph(
    task: TeamTaskWire,
    meta: Any,
    graph: Any,
) -> TeamTopology:
    node_roles: list[dict[str, str]] = []
    roles: list[Role] = []
    for node in getattr(graph, "nodes", []) or []:
        role = _role_for_graph_node(node)
        node_roles.append(
            {
                "node_id": str(getattr(node, "node_id", "")),
                "skill_ref": str(getattr(node, "skill_ref", "") or ""),
                "role": str(role),
            }
        )
        if role not in roles:
            roles.append(role)

    if not any(role in roles for role in (Role.PLANNER, Role.GENERATOR)):
        roles.insert(0, Role.PLANNER)
    if not any(
        role in roles
        for role in (
            Role.SYNTHESIZER,
            Role.GENERATOR,
            Role.EVALUATOR,
        )
    ):
        roles.append(Role.SYNTHESIZER)

    roles = _ordered_unique_roles(roles)
    topology_name = _slugify(
        f"task-{getattr(meta, 'name', None) or task.title}",
        fallback=f"team-task-{task.id}",
    )
    return TeamTopology(
        name=topology_name,
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents=_agents_for_roles(task, roles),
        task_bucket=str(getattr(graph, "task_type", "") or "team-task"),
        metadata={
            "source": "team_tasks_router",
            "team_task_id": task.id,
            "room_id": task.room_id,
            "meta_skill": getattr(meta, "name", None),
            "node_role_projection": node_roles,
        },
    )


def _fallback_topology(task: TeamTaskWire) -> TeamTopology:
    roles = [Role.PLANNER, Role.SYNTHESIZER]
    return TeamTopology(
        name=_slugify(f"task-{task.title}", fallback=f"team-task-{task.id}"),
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents=_agents_for_roles(task, roles),
        task_bucket="team-task:freeform",
        metadata={
            "source": "team_tasks_router",
            "team_task_id": task.id,
            "room_id": task.room_id,
            "meta_skill": None,
            "fallback": True,
        },
    )


def _role_for_graph_node(node: Any) -> Role:
    haystack = " ".join(
        str(part or "").lower()
        for part in (
            getattr(node, "node_id", ""),
            getattr(node, "skill_ref", ""),
            getattr(node, "kind", ""),
        )
    )
    for role in (
        Role.PLANNER,
        Role.RESEARCHER,
        Role.CRITIC,
        Role.EVALUATOR,
        Role.SYNTHESIZER,
        Role.GENERATOR,
    ):
        if any(keyword in haystack for keyword in _ROLE_KEYWORDS[role]):
            return role
    if str(getattr(node, "kind", "") or "") == "validator":
        return Role.EVALUATOR
    if str(getattr(node, "kind", "") or "") == "merger":
        return Role.SYNTHESIZER
    return Role.GENERATOR


def _ordered_unique_roles(roles: list[Role]) -> list[Role]:
    role_set = set(roles)
    return [role for role in _RUNNER_ROLE_ORDER if role in role_set]


def _agents_for_roles(
    task: TeamTaskWire,
    roles: list[Role],
) -> dict[Role, AgentSpec]:
    agent_refs = [
        assignee.ref.strip()
        for assignee in task.assignees
        if assignee.kind.strip().lower() == "agent" and assignee.ref.strip()
    ]
    role_specific_refs: dict[Role, str] = {}
    generic_refs: list[str] = []
    for ref in agent_refs:
        matched_role = _role_for_agent_ref(ref)
        if matched_role is None:
            generic_refs.append(ref)
        else:
            role_specific_refs[matched_role] = ref
    out: dict[Role, AgentSpec] = {}
    for index, role in enumerate(roles):
        if role in role_specific_refs:
            agent_id = role_specific_refs[role]
        elif generic_refs:
            agent_id = generic_refs[index % len(generic_refs)]
        else:
            agent_id = _DEFAULT_AGENT_BY_ROLE[role]
        out[role] = AgentSpec(
            agent_id=agent_id,
            system_addendum=_system_addendum_for_role(task, role),
        )
    return out


def _role_for_agent_ref(ref: str) -> Role | None:
    normalized = ref.strip().lower()
    for role, default_agent_id in _DEFAULT_AGENT_BY_ROLE.items():
        if normalized in {str(role).lower(), default_agent_id.lower()}:
            return role
    return None


def _system_addendum_for_role(task: TeamTaskWire, role: Role) -> str | None:
    if role != Role.SYNTHESIZER:
        return None
    contract = task.metadata.get("output_contract")
    if not isinstance(contract, dict):
        return None
    name = str(contract.get("name") or "output_contract").strip()
    schema = contract.get("schema")
    schema_text = (
        json.dumps(schema, ensure_ascii=False, indent=2)
        if isinstance(schema, dict) and schema
        else "{}"
    )
    return "\n".join(
        [
            f"You are the final synthesizer for output contract `{name}`.",
            "Return a useful concise summary, then include exactly one fenced json block.",
            "The fenced json block must be parseable JSON and must match this shape:",
            schema_text,
            "Use empty arrays when there is no concrete supported item.",
            "Do not stop at a plan; convert concrete role outputs into the JSON fields.",
        ]
    )


def _slugify(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-_")
    return (slug or fallback)[:80]


def _task_graph_summary(graph: Any) -> dict[str, Any]:
    nodes = []
    for node in getattr(graph, "nodes", []) or []:
        nodes.append(
            {
                "node_id": str(getattr(node, "node_id", "")),
                "kind": str(getattr(node, "kind", "") or ""),
                "skill_ref": str(getattr(node, "skill_ref", "") or ""),
                "timeout_ms": getattr(node, "timeout_ms", None),
                "failure_retry": getattr(node, "failure_retry", None),
            }
        )
    edges = []
    for edge in getattr(graph, "edges", []) or []:
        edges.append(
            {
                "from_node": str(getattr(edge, "from_node", "")),
                "to_node": str(getattr(edge, "to_node", "")),
                "kind": str(getattr(edge, "kind", "") or ""),
                "condition": getattr(edge, "condition", None),
            }
        )
    return {
        "task_type": str(getattr(graph, "task_type", "") or ""),
        "strategy": str(getattr(graph, "strategy", "") or ""),
        "nodes": nodes,
        "edges": edges,
        "budget": _jsonable(getattr(graph, "budget", None)),
    }


def _runner_result_success(result: Any) -> bool:
    return bool(_result_value(result, "success", False))


def _runner_result_error(result: Any) -> str | None:
    error = _result_value(result, "error", None)
    if error is None:
        return None
    text = str(error).strip()
    return text or None


def _runner_metadata(result: Any, prepared: dict[str, Any]) -> dict[str, Any]:
    topology = prepared["topology"]
    return {
        "status": "done" if _runner_result_success(result) else "failed",
        "meta_skill": prepared.get("meta_skill"),
        "task_graph": prepared.get("task_graph"),
        "topology": topology.to_dict(),
        "topology_name": _result_value(result, "topology_name", topology.name),
        "topology_fingerprint": _result_value(
            result,
            "topology_fingerprint",
            topology.fingerprint,
        ),
        "task_bucket": _result_value(result, "task_bucket", topology.task_bucket),
        "iterations": _result_value(result, "iterations", None),
        "total_duration_ms": _result_value(result, "total_duration_ms", None),
        "quality_score": _result_value(result, "quality_score", None),
        "error": _runner_result_error(result),
        "role_outputs": _jsonable(_result_value(result, "role_outputs", [])),
    }


def _mobile_artifacts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One artifact per phone carrying its run output, so the team room can
    review what each device did."""
    artifacts: list[dict[str, Any]] = []
    for rec in records or []:
        tentacle_id = str(rec.get("tentacle_id") or "device")
        artifacts.append(
            {
                "id": f"artifact-{uuid4().hex[:12]}",
                "type": "mobile_run",
                "title": f"{tentacle_id} · {'完成' if rec.get('ok') else '未完成'}",
                "content": str(rec.get("output") or rec.get("error") or ""),
                "agent_id": f"mobile_{tentacle_id}",
                "device_id": tentacle_id,
                "ok": bool(rec.get("ok")),
                "error": rec.get("error"),
                "created_at": _now(),
            }
        )
    return artifacts


def _runner_artifacts(result: Any, prepared: dict[str, Any]) -> list[dict[str, Any]]:
    final_output = str(_result_value(result, "final_output", "") or "")
    if not final_output.strip():
        return []
    topology = prepared["topology"]
    return [
        {
            "id": f"artifact-{uuid4().hex[:12]}",
            "type": "team_runner_output",
            "title": "TeamRunner final output",
            "content": final_output,
            "meta_skill": prepared.get("meta_skill"),
            "topology": topology.name,
            "topology_fingerprint": topology.fingerprint,
            "role_outputs": _jsonable(_result_value(result, "role_outputs", [])),
            "created_at": _now(),
        }
    ]


def _result_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    try:
        return getattr(result, key)
    except Exception:  # noqa: BLE001
        return default


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (Role, CoordinationProtocol)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"))
        except TypeError:
            return _jsonable(value.model_dump())
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_VALID_STATUSES = {"pending", "running", "done", "failed", "cancelled"}


def _normalize_status(status: str | None) -> str:
    normalized = (status or "pending").strip().lower()
    if normalized not in _VALID_STATUSES:
        raise HTTPException(
            400,
            f"invalid status {status!r}; expected one of {sorted(_VALID_STATUSES)}",
        )
    return normalized


def _load_state(path: Path) -> dict[str, TeamTaskWire]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("failed to load team tasks from %s: %s", path, exc)
        return {}
    items = raw.get("tasks") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return {}
    out: dict[str, TeamTaskWire] = {}
    for item in items:
        if not isinstance(item, dict):
            _LOG.warning("skipping non-dict task entry: %s", type(item).__name__)
            continue
        try:
            task = TeamTaskWire.model_validate(item)
        except (ValueError, TypeError) as exc:
            _LOG.warning("skipping invalid task entry (id=%s): %s", item.get("id"), exc)
            continue
        out[task.id] = task
    return out


def _save_state(path: Path, tasks: dict[str, TeamTaskWire]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tasks": [t.model_dump() for t in tasks.values()],
        "updated_at": _now(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


__all__ = [
    "TeamEventBroadcaster",
    "TaskProjection",
    "RunnerFactory",
    "RoomMembershipResolver",
    "_MAX_CONCURRENT_RUNS",
    "_SOP_TEMPLATE_PATTERN",
    "_prepare_team_run",
    "_team_task_process_timeline",
    "_team_process_event_node",
    "_team_timeline_node",
    "_team_timeline_sort_key",
    "_runner_summary",
    "_task_input_text",
    "_task_output_contract_text",
    "_task_user_input",
    "_topology_from_task_graph",
    "_fallback_topology",
    "_role_for_graph_node",
    "_ordered_unique_roles",
    "_agents_for_roles",
    "_role_for_agent_ref",
    "_system_addendum_for_role",
    "_slugify",
    "_task_graph_summary",
    "_runner_result_success",
    "_runner_result_error",
    "_runner_metadata",
    "_mobile_artifacts",
    "_runner_artifacts",
    "_result_value",
    "_jsonable",
    "_now",
    "_normalize_status",
    "_load_state",
    "_save_state",
]
