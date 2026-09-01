"""Explicit Project OS command bridge for the realtime runtime.

Split out of ``realtime_cerebrum.py``: the Project OS control-command
parser, the milestone/todo mapping helpers and the ``_drive_project_os``
driver that runs Project OS directly from a cowork thread after an explicit
``/project`` command. Project binding is independent from the group's response
mode; ordinary chat messages never enter this driver.

Every function takes the owning ``CerebrumRuntime`` as its first
argument; cross-method calls go through the runtime so subclass
overrides keep working.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import TYPE_CHECKING, Any

from runtime.platform.models import ParsedIntent
from runtime.protocol import ReasoningItem, TodoEntry, TodoListItem

if TYPE_CHECKING:
    from runtime.memory.threads.event_log import EventLog
    from runtime.protocol import Turn
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import EventEmitter


_PROJECT_OS_HELP = """Project OS 控制命令（在工作群中显式调用）：

- /project run <目标>（或 /project start <目标>）—— 显式创建或继续推进项目
- /project report（或 /project pm）—— PM 驾驶舱：里程碑健康度 / 风险 / 下一步动作 / 指派
- /project retro —— 项目复盘：交付、失败、重试、耗时、建议
- /project recover [tasks=T1,T2] [run] —— 恢复被阻塞的项目（可指定重跑任务）
- /project task <task_id> <reassign|reset|complete|skip> [agent=agent-id] [reason=...] [run]
    - reassign agent=xxx —— 换人重派
    - reset —— 重置重跑
    - complete output="<结果>" —— 人工验收通过
    - skip reason="<原因>" —— 跳过该节点

只有显式输入 /project run <目标> 才会进入里程碑驱动的 Project OS 执行。"""


def _is_project_os_command(text: str) -> bool:
    """Return whether ``text`` explicitly addresses the Project OS command."""

    raw = str(text or "").strip()
    return raw == "/project" or (
        raw.startswith("/project") and raw[len("/project") : len("/project") + 1].isspace()
    )


def _format_project_os_result(state: dict[str, Any]) -> str:
    """Human-readable Project OS result for the realtime chat surface."""
    raw_project = state.get("project")
    project: dict[str, Any] = raw_project if isinstance(raw_project, dict) else {}
    raw_result = state.get("result")
    result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
    raw_milestones = state.get("milestones")
    milestones: list[Any] = raw_milestones if isinstance(raw_milestones, list) else []
    raw_tasks = state.get("tasks")
    tasks_by_ms: dict[str, Any] = raw_tasks if isinstance(raw_tasks, dict) else {}
    roster = [str(member) for member in (state.get("roster") or []) if str(member).strip()]

    project_name = str(project.get("name") or "当前项目")
    project_id = str(project.get("id") or "")
    status = str(result.get("final_status") or project.get("status") or "running")
    ticks = result.get("ticks")

    reused = bool(state.get("reused"))
    control = state.get("control") if isinstance(state.get("control"), dict) else None
    if control:
        headline = "Project OS 已执行控制命令。"
    else:
        headline = "Project OS 已继续推进项目。" if reused else "Project OS 已接管并运行项目。"
    lines = [
        headline,
        "",
    ]
    if project_id:
        lines.append(f"项目：{project_name}（{project_id}）")
    else:
        lines.append(f"项目：{project_name}")
    lines.append(f"状态：{status}" + (f" · ticks {ticks}" if ticks is not None else ""))
    if roster:
        lines.append(f"成员：{', '.join(roster)}")
    lines.append("")
    lines.append("里程碑进展：")

    for milestone in milestones[:6]:
        if not isinstance(milestone, dict):
            continue
        ms_id = str(milestone.get("id") or "")
        ms_name = str(milestone.get("name") or ms_id or "milestone")
        ms_status = str(milestone.get("status") or "pending")
        tasks = tasks_by_ms.get(ms_id) if isinstance(tasks_by_ms, dict) else []
        tasks = tasks if isinstance(tasks, list) else []
        done = sum(1 for task in tasks if isinstance(task, dict) and task.get("status") == "done")
        lines.append(f"- {ms_name}：{ms_status} · {done}/{len(tasks)} 任务完成")
        assignments: list[str] = []
        for task in tasks[:4]:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id") or "")
            assignee = str(task.get("assigned_agent") or task.get("assigned_role") or "")
            task_status = str(task.get("status") or "")
            if task_id and assignee:
                assignments.append(f"{task_id}->{assignee}({task_status})")
        if assignments:
            lines.append(f"  派发：{', '.join(assignments)}")
    if len(milestones) > 6:
        lines.append(f"- 其余 {len(milestones) - 6} 个里程碑已省略，可在 Project OS 视图继续查看。")
    # 现实 PM 摘要：健康度、风险、下一步、复盘。
    pm = state.get("pm") if isinstance(state.get("pm"), dict) else None
    if pm:
        lines.append("")
        lines.append("PM 驾驶舱：")
        health_label = {
            "on_track": "正常",
            "at_risk": "有风险",
            "overdue": "已逾期",
            "blocked": "阻塞",
            "completed": "完成",
        }
        for m in (pm.get("milestones") or [])[:8]:
            if not isinstance(m, dict):
                continue
            h = health_label.get(str(m.get("health")), str(m.get("health") or ""))
            tag = f" · {h}"
            if m.get("overdue_tasks"):
                tag += f" · 逾期{len(m['overdue_tasks'])}"
            lines.append(
                f"- {m.get('name')}：{m.get('done')}/{m.get('total')} · {int((m.get('progress') or 0) * 100)}%{tag}"
            )
        risks = pm.get("risks") or []
        if risks:
            lines.append(f"风险/阻塞（{len(risks)}）：")
            for r in risks[:5]:
                if isinstance(r, dict):
                    lines.append(
                        f"  - [{r.get('health')}] {r.get('milestone') or r.get('task')}：{r.get('detail')}"
                    )
        actions = pm.get("next_actions") or []
        if actions:
            lines.append("下一步：")
            for a in actions[:5]:
                if isinstance(a, dict):
                    lines.append(f"  - {a.get('priority')} {a.get('task')} · {a.get('milestone')}")
        retro = state.get("retro") if isinstance(state.get("retro"), dict) else None
        if retro:
            lines.append("复盘：")
            lines.append(
                f"  - {retro.get('done_tasks')}/{retro.get('task_count')} 任务完成"
                + (f" · 失败 {retro.get('failed_tasks')}" if retro.get("failed_tasks") else "")
                + (
                    f" · 重试 {retro.get('attempts_total')} 次"
                    if (retro.get("attempts_total") or 0) > (retro.get("task_count") or 0)
                    else ""
                )
                + (f" · 耗时 {retro.get('duration_days')} 天" if retro.get("duration_days") else "")
            )
            for rec in (retro.get("recommendations") or [])[:3]:
                lines.append(f"  - 💡 {rec}")
    if status == "blocked":
        lines.append("")
        lines.append("项目已阻塞；请处理失败任务、验收条件或依赖后再继续推进。")
    elif status not in {"done", "failed"}:
        lines.append("")
        lines.append("项目还未结束；后续回合会继续从当前 Project OS 状态推进。")
    # 对话框交互引导：告诉用户接下来可以用什么命令继续推进 / 查看管理视图。
    lines.append("")
    lines.append(
        "下一步可输入：/project report（PM 驾驶舱）· /project retro（复盘）"
        + (
            " · /project recover（恢复项目）"
            if status == "blocked"
            else " · /project help（全部命令）"
        )
    )
    return "\n".join(lines)


def _project_os_todo_item(state: dict[str, Any]) -> TodoListItem | None:
    """Map Project OS milestones to the existing realtime todo-list item."""
    raw_project = state.get("project")
    project: dict[str, Any] = raw_project if isinstance(raw_project, dict) else {}
    raw_milestones = state.get("milestones")
    milestones: list[Any] = raw_milestones if isinstance(raw_milestones, list) else []
    raw_tasks = state.get("tasks")
    tasks_by_ms: dict[str, Any] = raw_tasks if isinstance(raw_tasks, dict) else {}
    if not milestones:
        return None

    def _status(raw: Any) -> str:
        value = str(raw or "").strip()
        if value == "done":
            return "completed"
        if value in {"active", "in_progress", "running"}:
            return "in_progress"
        if value in {"blocked", "failed"}:
            return "blocked"
        return "pending"

    entries: list[TodoEntry] = []
    for milestone in milestones:
        if not isinstance(milestone, dict):
            continue
        ms_id = str(milestone.get("id") or "").strip()
        name = str(milestone.get("name") or ms_id or "milestone").strip()
        status = _status(milestone.get("status"))
        tasks = tasks_by_ms.get(ms_id) if isinstance(tasks_by_ms, dict) else []
        tasks = tasks if isinstance(tasks, list) else []
        done = sum(1 for task in tasks if isinstance(task, dict) and task.get("status") == "done")
        suffix = f" · {done}/{len(tasks)} tasks" if tasks else ""
        entries.append(TodoEntry(title=f"{name}{suffix}", status=status))
    if not entries:
        return None

    project_name = str(project.get("name") or "Project OS").strip()
    project_id = str(project.get("id") or "").strip()
    explanation = f"Project OS · {project_name}" + (f" ({project_id})" if project_id else "")
    return TodoListItem(explanation=explanation, plan=entries)


def _parse_project_os_control(text: str) -> dict[str, Any] | None:
    """Parse an explicit Project OS command independently of response mode."""
    raw = str(text or "").strip()
    if not _is_project_os_command(raw):
        return None
    try:
        parts = shlex.split(raw)
    except ValueError:
        return {"type": "help"}
    if len(parts) < 2:
        return {"type": "help"}
    command = parts[1].lower()
    rest = parts[2:]

    if command in {"run", "start"}:
        return {"type": "run", "goal": " ".join(rest).strip()}

    def _kv(tokens: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for token in tokens:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            key = key.strip().lower()
            if key:
                out[key] = value.strip()
        return out

    if command == "recover":
        opts = _kv(rest)
        task_ids = [
            item.strip()
            for item in opts.get("tasks", opts.get("task_ids", "")).split(",")
            if item.strip()
        ]
        return {
            "type": "recover",
            "task_ids": task_ids,
            "run": "run" in rest or opts.get("run", "").lower() in {"1", "true", "yes"},
        }
    if command == "task" and len(rest) >= 2:
        task_id = rest[0]
        action = rest[1].lower()
        tail = rest[2:]
        opts = _kv(tail)
        return {
            "type": "task",
            "task_id": task_id,
            "action": action,
            "assigned_agent": opts.get("agent") or opts.get("assigned_agent"),
            "assigned_role": opts.get("role") or opts.get("assigned_role"),
            "reason": opts.get("reason", ""),
            "output": opts.get("output"),
            "run": "run" in tail or opts.get("run", "").lower() in {"1", "true", "yes"},
            "cascade": opts.get("cascade", "true").lower() not in {"0", "false", "no"},
        }
    if command in {"report", "pm"}:
        return {"type": "report"}
    if command in {"retro", "retrospective"}:
        return {"type": "retro"}
    return {"type": "help"}


async def _drive_project_os(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    intent: ParsedIntent,
    *,
    thread_id: str,
    text: str,
) -> None:
    """Handle an explicit Project OS command from a cowork thread."""
    if runtime._cowork_group_store is None:
        await runtime._emit_agent_message(
            turn,
            log,
            emitter,
            "Project OS 需要先绑定协作组；当前线程还没有可用的 cowork group。",
        )
        return

    context = intent.user_context if isinstance(intent.user_context, dict) else {}
    emitter_actor = str(getattr(emitter, "actor_id", None) or "").strip()
    emitter_tenant = str(getattr(emitter, "tenant_id", None) or "").strip()
    context_actor = str(context.get("owner_actor_id") or "").strip()
    context_tenant = str(context.get("tenant_id") or "").strip()
    if emitter_actor and context_actor and emitter_actor != context_actor:
        raise PermissionError("realtime project principal does not match turn context")
    if emitter_tenant and context_tenant and emitter_tenant != context_tenant:
        raise PermissionError("realtime project tenant does not match turn context")
    owner_id = emitter_actor or context_actor
    tenant_id = emitter_tenant or context_tenant
    if owner_id and not tenant_id:
        tenant_id = f"legacy:{owner_id}"
    authenticated_project = bool(owner_id and tenant_id)

    if authenticated_project:
        from runtime.sensing.gateway.thread_access import ThreadAccessResolver

        if runtime._thread_store is None:
            raise PermissionError("authenticated project thread state is unavailable")
        access = ThreadAccessResolver(
            thread_store=runtime._thread_store,
            group_store=runtime._cowork_group_store,
            collaboration_store=runtime._collaboration_store,
        ).resolve(thread_id, owner_id, tenant_id)
        if access.thread is None or not access.can_manage:
            raise PermissionError("only the thread owner can control Project OS")
        owner_id = access.owner_actor_id or owner_id
        tenant_id = access.tenant_id or tenant_id

    if runtime._project_store is None:
        from runtime.projectos.store import ProjectStore

        runtime._project_store = ProjectStore()

    project_store = runtime._project_store
    project_hooks = dict(runtime._project_os_hooks)
    if authenticated_project:
        from runtime.safety.auth.scope import TenantScope
        from runtime.sensing.gateway.thread_workspace import verified_managed_workspace

        project_store = project_store.with_scope(
            TenantScope(tenant_id=tenant_id, actor_id=owner_id)
        )

        def _resolve_thread_context(resolved_thread_id: str) -> dict[str, Any]:
            if (
                resolved_thread_id != thread_id
                or runtime._thread_store is None
                or runtime._workspaces is None
                or not hasattr(runtime._thread_store, "get")
            ):
                raise RuntimeError("project must be bound to its authenticated thread")
            thread = runtime._thread_store.get(resolved_thread_id)
            raw_metadata = thread.get("metadata") if isinstance(thread, dict) else None
            thread_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            if thread_metadata.get("owner_actor_id") != owner_id:
                raise PermissionError("project thread belongs to another actor")
            if thread_metadata.get("tenant_id") != tenant_id:
                raise PermissionError("project thread belongs to another tenant")
            workspace = verified_managed_workspace(
                runtime._workspaces.root,
                thread_id=resolved_thread_id,
                metadata=thread_metadata,
            )
            if workspace is None:
                raise RuntimeError("project thread has no verified managed workspace")
            layout = runtime._workspaces.bind_managed(resolved_thread_id, workspace)
            return {
                "workspace_path": str(layout.root),
                "runtime_session_metadata": {
                    "thread_id": resolved_thread_id,
                    "workspace_path": str(layout.root),
                    "_artifact_output_root": str(layout.final),
                    "tenant_id": tenant_id,
                    "owner_actor_id": owner_id,
                },
            }

        # ProjectEngine resolves this immediately before every single, swarm
        # or cluster task. It therefore re-verifies ownership even when a
        # long-running project resumes in a later control-command turn.
        project_hooks["resolve_thread_context"] = _resolve_thread_context

    goal = str(getattr(intent, "normalized_goal", "") or text or "").strip() or "当前目标"
    raw_name = str(context.get("team_name") or context.get("project") or "").strip()
    name = raw_name[:80] if raw_name else "当前项目"
    try:
        max_ticks = int(context.get("project_os_max_ticks") or 50)
    except (TypeError, ValueError):
        max_ticks = 50
    max_ticks = max(1, min(max_ticks, 200))

    control = _parse_project_os_control(text)
    if control is not None and control.get("type") == "run":
        explicit_goal = str(control.get("goal") or "").strip()
        if explicit_goal:
            goal = explicit_goal
        control = None
    from runtime.projectos.cowork_bridge import full_project_state, run_project_from_group

    def _run() -> dict[str, Any]:
        if control is not None:
            project = project_store.project_for_thread(thread_id)
            if project is None:
                return {
                    "ok": False,
                    "error": "project_not_found",
                    "message": "Project OS 当前线程还没有可恢复或干预的项目。",
                }
            engine = None
            if control.get("type") in {"recover", "task"}:
                from runtime.projectos.cowork_bridge import engine_for_group

                engine = engine_for_group(
                    project_store,
                    runtime._cowork_group_store,
                    thread_id,
                    hooks=project_hooks,
                    owner_id=owner_id,
                    tenant_id=tenant_id,
                    subagent_runner=getattr(runtime, "_subagent_runner", None),
                    require_execution_binding=True,
                )
            if control.get("type") == "recover" and engine is not None:
                intervention = engine.recover(
                    project.id,
                    task_ids=control.get("task_ids") or [],
                )
                result = (
                    engine.run(project.id, max_ticks=max_ticks)
                    if control.get("run")
                    else {"final_status": intervention.get("project_status")}
                )
                state = full_project_state(project_store, project.id) or {}
                return {
                    "ok": True,
                    "roster": [],
                    "reused": True,
                    "control": control,
                    "intervention": intervention,
                    "result": result,
                    **state,
                }
            if control.get("type") in {"report", "retro"}:
                state = full_project_state(project_store, project.id) or {}
                return {
                    "ok": True,
                    "roster": [],
                    "reused": True,
                    "control": control,
                    "result": {"final_status": project.status},
                    **state,
                }
            if control.get("type") == "task" and engine is not None:
                intervention = engine.intervene_task(
                    project.id,
                    str(control.get("task_id") or ""),
                    action=str(control.get("action") or ""),
                    assigned_agent=control.get("assigned_agent"),
                    assigned_role=control.get("assigned_role"),
                    output=control.get("output"),
                    reason=str(control.get("reason") or ""),
                    cascade=bool(control.get("cascade", True)),
                )
                intervention_events = [str(event) for event in (intervention.get("events") or [])]
                if any(
                    event.startswith(
                        (
                            "task_not_found:",
                            "milestone_not_found:",
                            "unknown_task_action:",
                        )
                    )
                    for event in intervention_events
                ):
                    state = full_project_state(project_store, project.id) or {}
                    return {
                        "ok": False,
                        "error": "project_task_intervention_failed",
                        "message": "Project OS 任务控制命令未执行："
                        + ", ".join(intervention_events),
                        "control": control,
                        "intervention": intervention,
                        **state,
                    }
                result = (
                    engine.run(project.id, max_ticks=max_ticks)
                    if control.get("run")
                    else {"final_status": intervention.get("project_status")}
                )
                state = full_project_state(project_store, project.id) or {}
                return {
                    "ok": True,
                    "roster": [],
                    "reused": True,
                    "control": control,
                    "intervention": intervention,
                    "result": result,
                    **state,
                }
            return {
                "ok": False,
                "error": "unknown_project_command",
                "message": _PROJECT_OS_HELP,
            }
        return run_project_from_group(
            project_store,
            runtime._cowork_group_store,
            thread_id,
            name=name,
            goal=goal,
            hooks=project_hooks,
            run=True,
            max_ticks=max_ticks,
            reuse_active=True,
            actor=owner_id or "project-os",
            owner_id=owner_id,
            tenant_id=tenant_id,
            subagent_runner=getattr(runtime, "_subagent_runner", None),
        )

    loop = asyncio.get_running_loop()
    try:
        state = await loop.run_in_executor(None, _run)
    except ValueError:
        await runtime._emit_agent_message(
            turn,
            log,
            emitter,
            "Project OS 已收到显式运行请求，但当前协作组没有可执行的 agent 成员。"
            "请先添加至少一个参与者后再运行项目。",
        )
        return
    if not state.get("ok", True):
        await runtime._emit_agent_message(
            turn,
            log,
            emitter,
            str(state.get("message") or "Project OS 控制命令无法执行。"),
        )
        return
    raw_project = state.get("project")
    project: dict[str, Any] = raw_project if isinstance(raw_project, dict) else {}
    project_id = project.get("id")
    if project_id and isinstance(state.get("trace"), dict):
        state["trace"]["audit_events"] = project_store.events_for_project(
            str(project_id),
            limit=20,
        )
    if project_id and state.get("control"):
        state["trace"] = {
            "schema": "echo.projectos.control_trace.v1",
            "thread_id": thread_id,
            "project_id": project.get("id"),
            "project_name": project.get("name"),
            "project_status": (
                state.get("result", {}).get("final_status")
                if isinstance(state.get("result"), dict)
                else project.get("status")
            ),
            "available_actions": state.get("available_actions") or [],
            "action_specs": state.get("action_specs") or [],
            "control": state.get("control"),
            "intervention": state.get("intervention"),
            "audit_events": project_store.events_for_project(
                str(project_id),
                limit=20,
            ),
        }
    todo_item = _project_os_todo_item(state)
    if todo_item is not None:
        await runtime._emit_todo_list(turn, log, emitter, todo_item)
    trace = state.get("trace")
    if isinstance(trace, dict):
        await runtime._emit_reasoning(
            turn,
            log,
            emitter,
            ReasoningItem(
                summary=["Project OS run trace"],
                content=json.dumps(trace, ensure_ascii=False, sort_keys=True),
            ),
        )
    await runtime._emit_agent_message(
        turn,
        log,
        emitter,
        _format_project_os_result(state),
    )
