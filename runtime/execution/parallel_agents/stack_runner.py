from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any, cast

from runtime.core.cerebrum.planner import PlannerError
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    ParsedIntent,
    Trajectory,
)
from runtime.platform.process.session import Session, current_session, session_scope

from .orchestrator import TaskRunner

_log = logging.getLogger(__name__)


def _summarize_trajectory(traj: Trajectory, *, max_chars: int = 1200) -> str:
    if not traj.steps:
        status = "OK" if traj.outcome.success else "FAILED"
        return f"[{status} · 0 step(s) · nothing ran]"

    lines: list[str] = []
    for step in traj.steps:
        marker = "✓" if step.success else "✗"
        skill = step.action.sucker_id
        args = getattr(step.action, "args", None) or {}
        args_preview = ""
        if isinstance(args, dict) and args:
            parts = []
            for k, v in list(args.items())[:3]:
                s = str(v)
                if len(s) > 60:
                    s = s[:59] + "…"
                parts.append(f"{k}={s}")
            args_preview = ", ".join(parts)
        out = step.result.output
        out_preview = ""
        if isinstance(out, dict) and out:
            parts = []
            for k, v in list(out.items())[:2]:
                if k.startswith("_"):
                    continue
                s = str(v)
                if len(s) > 80:
                    s = s[:79] + "…"
                parts.append(f"{k}={s}")
            out_preview = ", ".join(parts)
        elif isinstance(out, str) and out:
            out_preview = out if len(out) <= 120 else out[:119] + "…"

        if args_preview and out_preview:
            lines.append(f"{marker} {skill}({args_preview}) → {out_preview}")
        elif args_preview:
            lines.append(f"{marker} {skill}({args_preview})")
        elif out_preview:
            lines.append(f"{marker} {skill} → {out_preview}")
        else:
            lines.append(f"{marker} {skill}()")

    status = "OK" if traj.outcome.success else "FAILED"
    summary = f"[{status} · {traj.step_count} step(s)]"
    full = "\n".join(lines + ["", summary])
    if len(full) > max_chars:
        full = full[: max_chars - 1] + "…"
    return full


def _preview_value(value: Any, *, max_chars: int = 240) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        parts: list[str] = []
        for key, item in list(value.items())[:6]:
            if str(key).startswith("_"):
                continue
            item_text = str(item)
            if len(item_text) > 80:
                item_text = item_text[:79] + "..."
            parts.append(f"{key}={item_text}")
        text = ", ".join(parts)
    else:
        text = str(value)
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _artifact_paths(value: Any) -> list[str]:
    import re

    if value is None:
        return []
    text = value if isinstance(value, str) else str(value)
    path_re = re.compile(r"(?:[A-Za-z]:)?(?:[/\\][\w .-]+)+\.\w+")
    return sorted(set(path_re.findall(text)))


def _emit_trajectory_tool_events(
    traj: Trajectory,
    *,
    emit_tool_event: Callable[..., None] | None,
) -> None:
    if emit_tool_event is None:
        return
    for step in traj.steps:
        action = step.action
        result = step.result
        output = getattr(result, "output", None)
        emit_tool_event(
            tool_name=str(action.sucker_id),
            status="completed" if step.success else "failed",
            input_preview=_preview_value(getattr(action, "args", None)),
            output_preview=_preview_value(output),
            artifact_paths=_artifact_paths(output),
            message=f"{action.sucker_id} {'completed' if step.success else 'failed'}",
            payload={
                "success": step.success,
                "tool_call_id": getattr(action, "call_id", None),
            },
        )


def make_stack_subagent_runner(
    stack: Any,
    *,
    agent_registry: Any = None,
    default_tokens: int = 50_000,
    default_usd: float = 0.50,
    default_latency_ms: int = 600_000,
    default_arm: str = "code_arm",
    summarizer: Callable[[Trajectory], str] | None = None,
) -> TaskRunner:
    if stack is None:
        raise ValueError("stack must not be None")
    planner = getattr(stack, "planner", None)
    runtime = getattr(stack, "runtime", None)
    if planner is None or runtime is None:
        raise ValueError("stack is missing planner or runtime")

    summ = summarizer or _summarize_trajectory

    try:
        _accepted: set[str] = set(inspect.signature(planner.plan).parameters.keys())
    except (TypeError, ValueError):  # pragma: no cover
        _accepted = {"allowed_skills", "soul", "model"}

    def runner(
        description: str,
        *,
        subagent_name: str,
        context: Any = None,
        cancel_event: Any = None,
    ) -> str:
        if cancel_event is not None and cancel_event.is_set():
            return ""

        agent = None
        if agent_registry is not None and subagent_name:
            if hasattr(agent_registry, "has") and agent_registry.has(subagent_name):
                agent = agent_registry.get(subagent_name)
            elif subagent_name not in ("general-purpose", "general", ""):
                _log.info(
                    "subagent_name %r not in registry · running in free mode",
                    subagent_name,
                )

        ctx: dict[str, Any] = context if isinstance(context, dict) else {}
        if agent is not None:
            from runtime.execution.codex_backend.role_runner import (
                agent_uses_codex_execution_backend,
                run_agent_role_sync,
            )

            if agent_uses_codex_execution_backend(agent):
                result = run_agent_role_sync(
                    stack,
                    agent,
                    description,
                    context=ctx,
                    is_interrupted=lambda: bool(cancel_event is not None and cancel_event.is_set()),
                )
                if not result.success:
                    detail = result.output or result.status
                    raise RuntimeError(f"Codex role execution failed: {detail}")
                return result.output or f"[{agent.display_name} completed via Codex]"

        plan_kwargs: dict[str, Any] = {}
        if agent is not None:
            plan_kwargs["allowed_skills"] = agent.allowed_skill_union() or None
            try:
                from runtime.execution.agents.loader import compose_runtime_soul

                runtime_soul = compose_runtime_soul(agent, metadata=context or {})
            except (ImportError, AttributeError):  # noqa: BLE001
                runtime_soul = agent.soul
            if runtime_soul:
                plan_kwargs["soul"] = runtime_soul
        model = ctx.get("model_name")
        if not model and agent is not None:
            model = agent.model
        if model and model not in ("echo-agent", ""):
            plan_kwargs["model"] = model

        actor = ctx.get("actor") or ctx.get("file_write_owner")
        runtime_metadata: dict[str, Any] = (
            cast(dict[str, Any], ctx.get("runtime_session_metadata"))
            if isinstance(ctx.get("runtime_session_metadata"), dict)
            else {}
        )
        emit_tool_event = (
            ctx.get("emit_tool_event") if callable(ctx.get("emit_tool_event")) else None
        )
        thread_id = ctx.get("thread_id")

        # 3. Intent
        user_context = {
            "subagent_name": subagent_name,
            "parallel": True,
            **({"thread_id": ctx["thread_id"]} if ctx.get("thread_id") else {}),
        }
        for key in (
            "lead_agent_name",
            "research_job_id",
            "research_ephemeral_workers",
            "research_sources",
            "research_materials",
            "research_roles",
            "research_prefetch_logs",
            # Security: carry the spawning parent's prompt-injection taint
            # into the sub-agent so a tainted parent can't launder a risky
            # action through delegation. Honored at the sub-agent's
            # react-loop start (stream_react_loop). MUST stay in this list.
            "_inherited_injection_taint",
        ):
            if key in ctx:
                user_context[key] = ctx[key]

        intent = ParsedIntent(
            raw=description,
            intent_type="task",
            normalized_goal=description,
            user_context=user_context,
        )

        safe_kwargs = {k: v for k, v in plan_kwargs.items() if k in _accepted}
        with session_scope(
            Session(
                actor=actor,
                thread_id=thread_id,
                metadata=runtime_metadata,
            )
        ):
            # A delegated task without an explicitly selected project is
            # confined to its thread artifact directory.  That directory is
            # normally created by the realtime workspace lifecycle, but
            # persistent/background delegation can arrive first.  Materialize
            # only this internal fallback root so an initial read/list probe
            # observes an empty workspace instead of failing the whole task.
            if not runtime_metadata.get("workspace_path"):
                try:
                    from runtime.platform.process.scope import resolve_execution_scope

                    fallback_root = resolve_execution_scope(current_session()).primary_read
                    if fallback_root is not None:
                        fallback_root.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    _log.warning("subagent fallback workspace unavailable: %s", exc)
            try:
                graph = planner.plan(intent, **safe_kwargs)
            except PlannerError as e:
                _log.info("planner error for subagent=%s · msg=%s", subagent_name, e)
                return f"[planner error] {e}"

            if cancel_event is not None and cancel_event.is_set():
                return ""

            budget = Budget(
                task_id=graph.task_id,
                limits=BudgetLimits(
                    tokens=default_tokens,
                    usd=default_usd,
                    latency_ms=default_latency_ms,
                ),
            )

            arm_for_log = agent.agent_id if agent is not None else default_arm
            traj: Trajectory = runtime.run(
                graph,
                budget=budget,
                caller=f"parallel/{arm_for_log}",
                arm_id=ArmId(arm_for_log),
                actor=actor,
            )
        _emit_trajectory_tool_events(
            traj,
            emit_tool_event=emit_tool_event,
        )

        return summ(traj)

    # Delegation visibility uses the same concrete AgentRegistry as dispatch.
    # Function attributes keep the bridge API backward compatible while
    # avoiding a second process-global registry.
    runner.agent_registry = agent_registry  # type: ignore[attr-defined]
    runner.execution_stack = stack  # type: ignore[attr-defined]
    return runner
