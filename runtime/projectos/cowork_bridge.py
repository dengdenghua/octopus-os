"""Attach and run a Project OS project on a custom cowork group.

The 4 roles (PM/Engineer/Research/QA) are only the *default* routing. When you
freely pull members into a cowork thread, those members become the project team:
this bridge turns the thread's roster into the agent pool and routes each task to
the best-fit member (nominate: relevance × past competence) instead of a fixed
role. Project membership is a persistent capability of the group; chat/cluster/
swarm remains an independent response strategy for conversation turns.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any
from uuid import uuid4

from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.nominate import CompetenceStore, suggest
from runtime.projectos.engine import (
    DEFAULT_RUN_MAX_TICKS,
    AgentAssigner,
    ProjectEngine,
    _default_assign,
    normalize_run_ticks,
    stub_decompose_tasks,
    stub_generate_milestones,
)
from runtime.projectos.model import Task
from runtime.projectos.store import ProjectStore


def roster_from_group(group_store: GroupStore, thread_id: str) -> list[tuple[str, str]]:
    """The group's participant agents as (agent_id, family) candidates. Humans and
    observers are excluded — they don't auto-execute tasks."""
    state = group_store.state(thread_id)
    return [
        (m.id, m.id)  # roster only knows ids; the id doubles as the domain token
        for m in state.roster
        if m.kind == "agent" and m.role == "participant" and not m.muted
    ]


def nominate_assigner(
    roster: list[tuple[str, str]], competence: CompetenceStore | None = None
) -> AgentAssigner:
    """An assigner that routes each task to the best-fit *group member*.

    Ranks the roster for the task's goal (keyword relevance, boosted by recorded
    competence); falls back to the first member, then to role routing if the
    group has no agents. Always prefers a real member over a fixed role."""

    def _assign(task: Task) -> str:
        if not roster:
            return _default_assign(task)
        ranked = suggest(task.goal, roster, competence)
        if ranked:
            return str(ranked[0]["agent_id"])
        return roster[0][0]  # no keyword match → still keep it in the group

    return _assign


def _compose_swarm_output(result: dict[str, Any], prompt: str) -> str:
    """Turn a group_fanout result into a project-task deliverable: the primary
    reply + supporting angles, labeled so the QA gate sees who said what."""
    raw_synthesis = result.get("synthesis")
    synthesis = dict(raw_synthesis) if isinstance(raw_synthesis, dict) else {}
    primary = str(synthesis.get("primary_reply") or "").strip()
    support = [
        r
        for r in (result.get("replies") or [])
        if isinstance(r, dict) and r.get("ok") and str(r.get("reply") or "").strip()
    ]
    if not primary and not support:
        return "[swarm] 无人回应"
    lines = [f"# 蜂群交付 · {prompt[:80]}", ""]
    if primary:
        lines.append(f"**主要观点（{synthesis.get('primary_agent_id') or '?'}）**\n{primary}")
        lines.append("")
    if len(support) > 1:
        lines.append("**支撑角度**")
        for r in support:
            who = str(r.get("display_name") or r.get("agent_id") or "?")
            body = str(r.get("reply") or "").strip()
            if body and r.get("agent_id") != synthesis.get("primary_agent_id"):
                lines.append(f"- {who}: {body[:800]}")
    return "\n".join(lines)


def _compose_cluster_output(result: Any, prompt: str) -> str:
    final = str(getattr(result, "final_output", "") or "").strip()
    if final:
        return f"# 集群交付\n{final}"
    return "[cluster] 团队流水线未产出可交付内容"


def team_execute_for_group(
    roster: list[tuple[str, str]],
    *,
    agent_caller: Callable[[str, str, int], dict[str, Any]] | None = None,
    subagent_runner: Callable[..., str] | None = None,
    debate_rounds: int = 2,
) -> Callable[[Task, dict[str, Any]], Any]:
    """ProjectEngine.run_task_team hook: execute a project task node as a team.

    - ``swarm`` (蜂群) → ``run_group_fanout`` over the roster, optional debate
      rounds, arbitration synthesis as the deliverable.
    - ``cluster`` (集群) → ``TeamRunner`` parallel pipeline: the roster becomes
      the researcher pool, the assigned agent becomes the synthesizer.

    This is the seam that lets 项目模式 reuse the cluster/swarm engines we
    optimized instead of running every project task single-agent.
    """
    members = [{"name": mid, "display_name": mid} for mid, _ in roster]
    ids = [mid for mid, _ in roster]

    def _call_agent(
        agent_id: str,
        prompt: str,
        timeout_s: int = 300,
        *,
        execution_context: dict[str, Any] | None = None,
        role_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if agent_caller is not None:
            return agent_caller(agent_id, prompt, timeout_s)
        from runtime.execution.subagents import call_subagent

        project_context = dict(execution_context or {})
        thread_id = str(project_context.get("thread_id") or "")
        actor = str(project_context.get("owner_id") or project_context.get("actor") or "")
        tenant_id = str(project_context.get("tenant_id") or "")
        project_id = str(project_context.get("project_id") or "")
        task_id = str(project_context.get("task_id") or "")
        workspace_path = project_context.get("workspace_path")
        inherited_metadata = project_context.get("runtime_session_metadata")
        runtime_session_metadata = (
            dict(inherited_metadata) if isinstance(inherited_metadata, dict) else {}
        )
        runtime_session_metadata.update(
            {
                "source": "projectos_team_task",
                "project_id": project_id,
                "task_id": task_id,
                "tenant_id": tenant_id,
            }
        )
        if isinstance(workspace_path, str) and workspace_path:
            runtime_session_metadata.setdefault("workspace_path", workspace_path)
        dispatch_context: dict[str, Any] = dict(role_context or {})
        dispatch_context.update(
            {
                "source": "projectos_team_task",
                "task_id": task_id,
                "projectos": project_context,
                "runtime_session_metadata": runtime_session_metadata,
            }
        )
        if thread_id:
            dispatch_context["thread_id"] = thread_id
        if actor:
            dispatch_context["actor"] = actor
        if tenant_id:
            dispatch_context["tenant_id"] = tenant_id
        if isinstance(workspace_path, str) and workspace_path:
            dispatch_context["workspace_path"] = workspace_path
        # Group fan-out and TeamRunner execute members on worker threads where
        # the parent ContextVar is intentionally absent.  Carry the authenticated
        # Project OS principal as an explicit Session so a production Coder can
        # pass the role runner's trusted-principal gate without treating ordinary
        # context identity fields as authorization.
        from runtime.platform.process.session import Session

        project_session = Session(
            actor=actor or None,
            thread_id=thread_id or None,
            conversation_id=thread_id or None,
            metadata=dict(runtime_session_metadata),
        )
        call_kwargs: dict[str, Any] = {
            "context": dispatch_context,
            "session": project_session,
            "timeout_s": timeout_s,
            "timeout_seconds": float(timeout_s),
        }
        if subagent_runner is not None:
            call_kwargs["runner"] = subagent_runner
        try:
            result = call_subagent(agent_id, prompt, **call_kwargs)
            return {
                "success": bool(result.get("success")),
                "output": str(result.get("output") or result.get("parsed") or ""),
                "error": result.get("error"),
            }
        except Exception as exc:  # noqa: BLE001 — isolate one member's failure
            return {
                "success": False,
                "output": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _run(task: Task, context: dict[str, Any]) -> Any:
        prompt = task.goal
        milestone_goal = context.get("milestone_goal")
        if milestone_goal:
            prompt = f"Milestone: {milestone_goal}\nTask: {task.goal}"
        execution_context = {**context, "task_id": task.id}
        if task.team_mode == "swarm":
            return _run_swarm(prompt, execution_context)
        return _run_cluster(task, prompt, execution_context)

    def _run_swarm(prompt: str, execution_context: dict[str, Any]) -> str:
        from runtime.execution.agents.group_fanout import run_group_fanout

        def _scoped_call(agent_id: str, prompt: str, timeout_s: int = 300):
            return _call_agent(
                agent_id,
                prompt,
                timeout_s,
                execution_context=execution_context,
            )

        n = max(1, len(members))
        result = run_group_fanout(
            prompt,
            members,
            agent_caller=_scoped_call,
            max_members=n,
            max_concurrency=min(32, n),
            scale_mode="safe",
            debate_rounds=debate_rounds,
        )
        return _compose_swarm_output(result, prompt)

    def _run_cluster(
        task: Task,
        prompt: str,
        execution_context: dict[str, Any],
    ) -> str:
        if not ids:
            raise RuntimeError("project cluster task needs at least one roster member")
        from runtime.safety.organization import (
            AgentSpec,
            CoordinationProtocol,
            Role,
            TeamTopology,
        )
        from runtime.safety.organization.team_runner import TeamRunner

        pool_id = ids[0]
        synth_id = task.assigned_agent or pool_id
        topology = TeamTopology(
            name="project-cluster",
            protocol=CoordinationProtocol.PARALLEL,
            agents={
                # The assigned (best-fit) member leads: plans the task, then
                # merges the pool into the deliverable. Same agent runs both
                # roles (plan → pool → synthesize), which is the "cluster" feel.
                Role.PLANNER: AgentSpec(agent_id=synth_id),
                # The whole roster is the researcher pool (one replica per member).
                Role.RESEARCHER: AgentSpec(agent_id=pool_id, parallel_replicas=len(ids)),
                Role.SYNTHESIZER: AgentSpec(agent_id=synth_id),
            },
        )

        def _role_caller(
            *,
            agent_id: str,
            prompt: str,
            context: dict[str, Any] | None = None,
            timeout_seconds: int | None = None,
            use_cheap_model: bool = False,
            event_emitter: Callable[[dict[str, Any]], None] | None = None,
        ) -> dict[str, Any]:
            role = (context or {}).get("team_role")
            idx = (context or {}).get("team_replica_index")
            actual = agent_id
            if role == "researcher" and isinstance(idx, int) and 1 <= idx <= len(ids):
                actual = ids[idx - 1]
            return _call_agent(
                actual,
                prompt,
                timeout_s=timeout_seconds or 300,
                execution_context=execution_context,
                role_context=context,
            )

        runner = TeamRunner(role_caller=_role_caller, timeout_seconds=900)
        return _compose_cluster_output(runner.run(topology, prompt), prompt)

    return _run


def engine_for_group(
    project_store: ProjectStore,
    group_store: GroupStore,
    thread_id: str,
    *,
    hooks: dict[str, Any] | None = None,
    competence: CompetenceStore | None = None,
    owner_id: str = "",
    tenant_id: str = "",
    subagent_runner: Callable[..., str] | None = None,
    require_execution_binding: bool = False,
) -> ProjectEngine:
    """A ProjectEngine whose task→agent routing uses the cowork thread's roster.

    ``hooks`` supplies generate/decompose/execute/qa (LLM in production, stubs in
    tests). The assigner is always the roster-aware one, so the custom group runs
    the project."""
    roster = roster_from_group(group_store, thread_id)
    kwargs = dict(hooks or {})
    kwargs.setdefault("generate_milestones", stub_generate_milestones)
    kwargs.setdefault("decompose_tasks", stub_decompose_tasks)
    kwargs["assign_agent"] = nominate_assigner(roster, competence)
    # 项目模式 × 集群/蜂群：有可执行成员时注入任务级团队执行器，让声明了
    # team_mode 的任务节点跑成蜂群/集群，而不是一律单 agent。
    if roster:
        kwargs["run_task_team"] = team_execute_for_group(
            roster,
            subagent_runner=subagent_runner,
        )
    return ProjectEngine(
        project_store,
        **kwargs,
        owner_id=owner_id,
        tenant_id=tenant_id,
        required_execution_thread_id=thread_id if require_execution_binding else "",
    )


def ensure_project_for_thread(
    project_store: ProjectStore,
    group_store: GroupStore,
    thread_id: str,
    *,
    name: str,
    goal: str,
    owner_id: str = "",
    tenant_id: str = "",
) -> str | None:
    """Create (if missing) a Project OS project bound to a cowork thread.

    Returns the project_id (existing or freshly planned), or ``None`` when the
    group has no participant agents to staff it. Used by the cowork mode switch
    so entering "project" mode always has a real project for the workbench 项目
    tab to render. Planning uses stub/deterministic hooks (no LLM call); actual
    execution stays user-triggered via Run/Tick so a mere mode switch never
    auto-runs a project.
    """
    if not roster_from_group(group_store, thread_id):
        return None
    result = run_project_from_group(
        project_store,
        group_store,
        thread_id,
        name=name or "当前项目",
        goal=goal or name or "当前目标",
        run=False,
        reuse_active=True,
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    if not result.get("ok", True):
        raise RuntimeError(str(result.get("message") or "project attach needs recovery"))
    raw_project = result.get("project")
    project = raw_project if isinstance(raw_project, dict) else {}
    project_id = str(project.get("id") or "").strip()
    if not project_id:
        raise RuntimeError("project attach returned no project id")
    return project_id


def full_project_state(project_store: ProjectStore, project_id: str) -> dict[str, Any] | None:
    """Return the complete Project OS read-model for API and realtime callers.

    Includes the derived PM console (``pm``) — milestone health, burndown,
    risks/blockers, next actions, assignments — plus a ``retro`` once the
    project reaches a terminal state. Published artifacts and recorded
    decisions are projected from the durable Project OS event stream rather
    than collaboration metadata.
    """
    project = project_store.get_project(project_id)
    if project is None:
        return None
    milestones = project_store.milestones_for(project_id)
    tasks_by_ms = {
        milestone.id: [
            _task_read_model(project.id, task)
            for task in project_store.tasks_for_milestone(milestone.id)
        ]
        for milestone in milestones
    }
    from runtime.projectos.pm import build_pm_report, build_retro

    pm = build_pm_report(project_store, project_id)
    retro = build_retro(project_store, project_id) if project.status in ("done", "failed") else None
    return {
        "project": project.to_dict(),
        "milestones": [milestone.to_dict() for milestone in milestones],
        "tasks": tasks_by_ms,
        "artifacts": project_store.artifacts_for_project(project_id),
        "decisions": project_store.decisions_for_project(project_id),
        "pm": pm,
        "retro": retro,
        "available_actions": _project_available_actions(project.status),
        "action_specs": _project_action_specs(project.id, project.status),
    }


def _project_available_actions(status: str) -> list[str]:
    if status == "blocked":
        return ["recover", "recover_and_run"]
    if status in {"planning", "running"}:
        return ["run", "tick"]
    if status == "done":
        return ["inspect", "report"]
    return ["inspect"]


def _project_action_specs(project_id: str, status: str) -> list[dict[str, Any]]:
    specs = {
        "recover": {
            "action": "recover",
            "label": "Recover",
            "api": {
                "method": "POST",
                "path": f"/api/projects/{project_id}/recover",
                "body": {"run": False},
            },
            "realtime_command": "/project recover",
        },
        "recover_and_run": {
            "action": "recover_and_run",
            "label": "Recover and run",
            "api": {
                "method": "POST",
                "path": f"/api/projects/{project_id}/recover",
                "body": {"run": True},
            },
            "realtime_command": "/project recover run",
        },
        "run": {
            "action": "run",
            "label": "Run",
            "api": {
                "method": "POST",
                "path": f"/api/projects/{project_id}/run",
                "body": {"max_ticks": DEFAULT_RUN_MAX_TICKS},
            },
        },
        "tick": {
            "action": "tick",
            "label": "Tick",
            "api": {"method": "POST", "path": f"/api/projects/{project_id}/tick"},
        },
        "inspect": {
            "action": "inspect",
            "label": "Inspect",
            "api": {"method": "GET", "path": f"/api/projects/{project_id}"},
        },
        "report": {
            "action": "report",
            "label": "Report",
            "api": {"method": "GET", "path": f"/api/projects/{project_id}/report"},
        },
    }
    return [specs[action] for action in _project_available_actions(status)]


def _task_read_model(project_id: str, task: Task) -> dict[str, Any]:
    raw = task.to_dict()
    raw["available_actions"] = _task_available_actions(task.status)
    raw["action_specs"] = _task_action_specs(project_id, task)
    return raw


def project_task_to_collaboration(
    collaboration_store: Any,
    *,
    session_id: str,
    room_id: str,
    project_id: str,
    milestone_id: str,
    task: Task | dict[str, Any],
    tenant_id: str = "",
    binding_generation: int | None = None,
) -> dict[str, Any] | None:
    """Project one authoritative Project OS task into collaboration storage.

    This bridge is deliberately one-way.  Callers must persist the Project OS
    task first; this function only builds the room/workbench read model and
    never invokes the Team Task write path.
    """

    upsert = getattr(collaboration_store, "upsert_project_task", None)
    if not callable(upsert):
        return None
    raw = task.to_dict() if isinstance(task, Task) else dict(task or {})
    assigned_agent = str(raw.get("assigned_agent") or "")
    assigned_role = str(raw.get("assigned_role") or "")
    return upsert(
        session_id=session_id,
        room_id=room_id,
        project_id=project_id,
        milestone_id=milestone_id,
        binding_generation=binding_generation,
        task={
            "id": raw.get("id"),
            "kind": "project",
            "title": raw.get("goal") or raw.get("id"),
            "description": raw.get("goal") or "",
            "status": raw.get("status") or "pending",
            "assignees": [
                item
                for item in (
                    {"name": assigned_agent, "role": "agent"},
                    {"name": assigned_role, "role": "role"},
                )
                if item["name"]
            ],
            "artifacts": (
                [{"kind": "project_task_output", "output": raw.get("output")}]
                if raw.get("output") not in (None, "", {}, [])
                else []
            ),
            "metadata": {
                "source": "projectos",
                "project_id": project_id,
                "tenant_id": tenant_id,
                "milestone_id": milestone_id,
                "task_type": raw.get("type"),
                "assigned_agent": assigned_agent,
                "assigned_role": assigned_role,
                "attempts": raw.get("attempts"),
                **(
                    {"source_message": raw.get("input", {}).get("source_message")}
                    if isinstance(raw.get("input"), dict)
                    and raw.get("input", {}).get("source_message")
                    else {}
                ),
            },
        },
    )


def _task_available_actions(status: str) -> list[str]:
    if status in {"failed", "rejected", "blocked"}:
        return ["reassign", "reset", "complete", "skip"]
    if status in {"pending", "ready"}:
        return ["reassign", "reset", "complete", "skip"]
    if status == "running":
        return ["inspect"]
    if status == "done":
        return ["reset"]
    return ["inspect"]


def _task_action_specs(project_id: str, task: Task) -> list[dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {
        "reassign": {
            "action": "reassign",
            "label": "Reassign",
            "api": {
                "method": "POST",
                "path": f"/api/projects/{project_id}/tasks/{task.id}/intervene",
                "body": {"action": "reassign", "assigned_agent": ""},
            },
            "realtime_command": f"/project task {task.id} reassign agent=<agent-id>",
            "requires": ["assigned_agent"],
        },
        "reset": {
            "action": "reset",
            "label": "Reset",
            "api": {
                "method": "POST",
                "path": f"/api/projects/{project_id}/tasks/{task.id}/intervene",
                "body": {"action": "reset", "cascade": True},
            },
            "realtime_command": f"/project task {task.id} reset",
        },
        "complete": {
            "action": "complete",
            "label": "Complete",
            "api": {
                "method": "POST",
                "path": f"/api/projects/{project_id}/tasks/{task.id}/intervene",
                "body": {"action": "complete", "output": ""},
            },
            "realtime_command": f'/project task {task.id} complete output="<result>"',
            "requires": ["output"],
        },
        "skip": {
            "action": "skip",
            "label": "Skip",
            "api": {
                "method": "POST",
                "path": f"/api/projects/{project_id}/tasks/{task.id}/intervene",
                "body": {"action": "skip", "reason": ""},
            },
            "realtime_command": f'/project task {task.id} skip reason="<reason>"',
        },
        "inspect": {
            "action": "inspect",
            "label": "Inspect",
        },
    }
    return [specs[action] for action in _task_available_actions(task.status)]


def project_run_trace(
    *,
    thread_id: str,
    roster: list[str],
    reused: bool,
    result: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Compact audit trace for Project OS runs over a cowork group."""
    raw_project = state.get("project")
    project = dict(raw_project) if isinstance(raw_project, dict) else {}
    raw_milestones = state.get("milestones")
    milestones = list(raw_milestones) if isinstance(raw_milestones, list) else []
    raw_tasks = state.get("tasks")
    tasks_by_ms = dict(raw_tasks) if isinstance(raw_tasks, dict) else {}
    raw_history = result.get("history")
    history = list(raw_history) if isinstance(raw_history, list) else []
    tick_events: list[dict[str, Any]] = []
    for index, tick in enumerate(history, start=1):
        if not isinstance(tick, dict):
            continue
        tick_events.append(
            {
                "tick": index,
                "project_status": tick.get("project_status"),
                "current_ms": tick.get("current_ms"),
                "events": [
                    str(event) for event in (tick.get("events") or []) if str(event or "").strip()
                ],
            }
        )

    milestone_summaries: list[dict[str, Any]] = []
    for milestone in milestones:
        if not isinstance(milestone, dict):
            continue
        ms_id = str(milestone.get("id") or "")
        tasks = tasks_by_ms.get(ms_id) if isinstance(tasks_by_ms, dict) else []
        tasks = tasks if isinstance(tasks, list) else []
        milestone_summaries.append(
            {
                "id": ms_id,
                "name": milestone.get("name"),
                "status": milestone.get("status"),
                "task_count": len(tasks),
                "done_task_count": sum(
                    1 for task in tasks if isinstance(task, dict) and task.get("status") == "done"
                ),
                "assignments": [
                    {
                        "task_id": task.get("id"),
                        "type": task.get("type"),
                        "status": task.get("status"),
                        "assigned_agent": task.get("assigned_agent"),
                        "available_actions": task.get("available_actions") or [],
                    }
                    for task in tasks
                    if isinstance(task, dict)
                ],
            }
        )

    return {
        "schema": "echo.projectos.run_trace.v1",
        "thread_id": thread_id,
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "project_status": result.get("final_status") or project.get("status"),
        "reused": reused,
        "roster": roster,
        "tick_count": result.get("ticks", len(tick_events)),
        "tick_events": tick_events,
        "milestones": milestone_summaries,
    }


def run_project_from_group(
    project_store: ProjectStore,
    group_store: GroupStore,
    thread_id: str,
    *,
    name: str,
    goal: str,
    hooks: dict[str, Any] | None = None,
    run: bool = False,
    max_ticks: int = DEFAULT_RUN_MAX_TICKS,
    competence: CompetenceStore | None = None,
    actor: str = "project-os",
    reuse_active: bool = False,
    owner_id: str = "",
    tenant_id: str = "",
    subagent_runner: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Attach a Project OS project to a cowork group and optionally run it.

    This is the shared contract for the HTTP `/api/projects/from-group/*` route
    and the legacy realtime project-mode turn. Binding the project does not
    mutate the group's response strategy; callers must opt into execution.
    """
    roster = [agent_id for agent_id, _ in roster_from_group(group_store, thread_id)]
    if not roster:
        raise ValueError("group has no participant agents to staff the project")

    response_mode = group_store.state(thread_id).mode
    engine = engine_for_group(
        project_store,
        group_store,
        thread_id,
        hooks=hooks,
        competence=competence,
        owner_id=owner_id,
        tenant_id=tenant_id,
        subagent_runner=subagent_runner,
        require_execution_binding=run,
    )
    previously_bound_project, binding_generation = project_store.binding_snapshot(thread_id)
    project = previously_bound_project if reuse_active else None
    # A thread owns at most one optional project capability.  Once attached,
    # retries return that same project even after it reaches a terminal state;
    # creating a new project requires an explicit detach first.
    reused = project is not None

    def _recovery_state(
        project_id: str,
        *,
        phase: str,
        error: Exception,
        winner_project_id: str = "",
    ) -> dict[str, Any]:
        preserved = project_store.get_project(project_id)
        if preserved is None:
            raise error
        recovery_recorded = False
        try:
            project_store.append_event(
                project_id,
                kind="project.group_attach_recovery_pending",
                payload={
                    "thread_id": thread_id,
                    "phase": phase,
                    "reason": type(error).__name__,
                    "winner_project_id": winner_project_id,
                },
            )
        except Exception:  # noqa: BLE001 - the preserved plan is still the recovery anchor
            recovery_recorded = False
        else:
            recovery_recorded = True
        try:
            preserved_state = full_project_state(project_store, project_id)
        except Exception:  # noqa: BLE001 - return the source row even if its read model is broken
            preserved_state = None
        canonical, current_generation = project_store.binding_snapshot(thread_id)
        detail = {
            "code": "PROJECT_ATTACH_RECOVERY_PENDING",
            "message": "project plan was preserved and needs an attach-only retry",
            "project_id": project_id,
            "thread_id": thread_id,
            "phase": phase,
            "winner_project_id": winner_project_id or (canonical.id if canonical else ""),
            "recovery_recorded": recovery_recorded,
            "recovery": {
                "method": "POST",
                "path": f"/api/projects/from-group/{thread_id}",
                "run": False,
            },
        }
        return {
            "ok": False,
            "error": "project_attach_recovery_pending",
            "message": detail["message"],
            "recovery_pending": True,
            "recovery": detail,
            "reused": False,
            "binding_generation": current_generation,
            **(preserved_state or {"project": preserved.to_dict()}),
        }

    def _finish(
        *,
        result: dict[str, Any],
        state: dict[str, Any],
        event_kind: str,
    ) -> dict[str, Any]:
        if project is None:
            raise RuntimeError("project disappeared before lifecycle audit")
        trace = project_run_trace(
            thread_id=thread_id,
            roster=roster,
            reused=reused,
            result=result,
            state=state,
        )
        # Idempotent attach reads must not manufacture duplicate lifecycle
        # events. Explicit execution still records every run request.
        if event_kind != "project.attached_from_group" or not reused:
            project_store.append_event(
                project.id,
                kind=event_kind,
                payload={
                    "thread_id": thread_id,
                    "actor": actor,
                    "response_mode": response_mode,
                    "roster": roster,
                    "reused": reused,
                    "run": run,
                    "max_ticks": normalize_run_ticks(max_ticks),
                    "trace": trace,
                },
            )
        return {
            "ok": True,
            "roster": roster,
            "result": result,
            "reused": reused,
            "binding_generation": binding_generation,
            "trace": trace,
            **state,
        }

    if not reused:
        candidate_id = f"P-{uuid4().hex[:8]}"
        try:
            candidate = engine.plan(name, goal, project_id=candidate_id)
        except Exception as plan_error:
            return _recovery_state(
                candidate_id,
                phase="plan_commit",
                error=plan_error,
            )
        attached_candidate = False
        try:
            if reuse_active:
                project, attached_candidate, binding_generation = (
                    project_store.bind_thread_if_absent_versioned(
                        thread_id,
                        candidate.id,
                    )
                )
                if not attached_candidate:
                    # The plan transaction is already public. Preserve the CAS
                    # loser's source rows and mark them as an auditable orphan
                    # instead of deleting concurrent external work.
                    with suppress(Exception):
                        project_store.append_event(
                            candidate.id,
                            kind="project.group_attach_orphaned",
                            payload={
                                "thread_id": thread_id,
                                "winner_project_id": project.id,
                                "reason": "binding_cas_lost",
                            },
                        )
                    reused = True
            else:
                project, binding_generation = project_store.bind_thread_versioned(
                    thread_id,
                    candidate.id,
                )
                attached_candidate = True
            attached_state = full_project_state(project_store, project.id)
            if attached_state is None:
                raise RuntimeError(f"project disappeared after planning: {project.id}")
            if not run:
                return _finish(
                    result={"final_status": project.status},
                    state=attached_state,
                    event_kind="project.attached_from_group",
                )
        except Exception as attach_error:
            canonical, _generation = project_store.binding_snapshot(thread_id)
            return _recovery_state(
                candidate.id,
                phase="attach",
                error=attach_error,
                winner_project_id=canonical.id if canonical is not None else "",
            )
    else:
        if project is None:  # pragma: no cover - ``reused`` proves this invariant
            raise RuntimeError("project disappeared before attach")
        attached_state = full_project_state(project_store, project.id)
        if attached_state is None:
            raise RuntimeError(f"project disappeared before attach: {project.id}")

    if project is None:  # pragma: no cover - every successful attach assigns it
        raise RuntimeError("project disappeared before attach")
    if not run:
        return _finish(
            result={"final_status": project.status},
            state=attached_state,
            event_kind="project.attached_from_group",
        )

    # Crossing this line starts execution.  Any later failure intentionally
    # leaves the bound project in place so its persisted state can be inspected
    # and recovered rather than being mistaken for an attach-only shell.
    result = engine.run(project.id, max_ticks=normalize_run_ticks(max_ticks))
    state = full_project_state(project_store, project.id)
    if state is None:
        raise RuntimeError(f"project disappeared after planning: {project.id}")
    return _finish(
        result=result,
        state=state,
        event_kind="project.run_from_group",
    )
