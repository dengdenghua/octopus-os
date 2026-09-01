"""PHASE 1-2 turn bootstrap: entry guards + router / native-gate resolution,
plus PHASE 4/4.5 start events + agent auto-delegation short-circuit.

Leaf of the prompt-assembly split. Re-exported by ``react_prompt_assembly.py``
so ``react_loop.py``'s ``from ...react_prompt_assembly import _resolve_turn_bootstrap``
/ ``_emit_turn_start_events`` keep working. Never imports ``react_loop``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

from runtime.core.cerebrum.react_browser_iteration import (
    _browser_operation_requested,
    _ensure_browser_operation_skills,
)
from runtime.core.cerebrum.react_execution import _skill_available_in_executor
from runtime.core.cerebrum.react_explicit_reads import (
    _explicit_no_tool_goal,
    _explicit_observed_read_sequence,
    _explicit_read_only_goal,
)
from runtime.core.cerebrum.react_guards import _explicit_source_paths
from runtime.platform.models.llm import Message

_logger = logging.getLogger(__name__)


@dataclass
class _TurnBootstrap:
    """Products of the PHASE 1-2 turn bootstrap (entry guards / gating)."""

    router: Any
    reasoning_effort: Any
    no_tool_turn: bool
    executor: Any
    tools_active: bool
    effective_model: str
    native_mode: bool
    strict_explicit_reads: bool
    ordered_result_handoffs: bool
    native_public_update_tool_specs: list
    native_evidence_update_tool_specs: list
    react_task_id: Any
    camouflage_suffix: str


def _resolve_turn_bootstrap(
    stack: Any,
    intent: Any,
    agent: Any,
    *,
    model: str | None,
    enable_tools: bool,
    reasoning_effort: str | None,
    approval_provider: Any,
    resume_task_id: Any,
) -> _TurnBootstrap | None:
    """Entry guards + router/native-gate resolution (PHASE 1-2).

    Moved verbatim from ``react_loop.stream_react_loop``. Returns
    ``None`` when the stack exposes no router (the original early
    ``return None``); the caller aborts the turn in that case.
    """
    router = getattr(getattr(stack, "planner", None), "router", None)
    if router is None:
        _logger.warning("react_loop: stack.planner.router 不可用,无法进入 ReAct")
        return None

    from runtime.platform.models.llm import normalize_reasoning_effort

    _reasoning_effort: str | None = normalize_reasoning_effort(reasoning_effort)
    if _reasoning_effort is None and str(reasoning_effort or "").strip().lower() in (
        "off",
        "disabled",
    ):
        # DeepSeek native ``off`` is not an OpenAI-style effort tier; keep
        # it so the deepseek profile can emit ``thinking:{type:disabled}``.
        _reasoning_effort = "off"

    # Planning mode used to disable tool execution outright (the
    # model produced a plan, the user approved, then a follow-up turn
    # re-ran with ``planning_mode=false``). That hard-stop confused
    # users — the UI shows nothing happening and ``Action: web_search``
    # falls through to the "(未执行观察) 本次 ReAct 未启用工具执行"
    # placeholder. Updated semantics (2026-05-31): planning_mode keeps
    # tool execution ON; the system prompt simply nudges the model to
    # write/update plan.md first before substantial tool work. The
    # ``exit_plan_mode`` skill flow is still available for explicit
    # human-in-the-loop approval, but auto-detection no longer strands
    # the turn in plan-only territory.
    _no_tool_turn = _explicit_no_tool_goal(
        str(getattr(intent, "normalized_goal", "") or getattr(intent, "raw", "") or "")
    )
    executor = getattr(stack, "executor", None) if enable_tools and not _no_tool_turn else None
    tools_active = executor is not None
    # Explicit Browser turns must register their dependency-gated local tools
    # before native ToolSpecs are frozen below.  Registering later only changes
    # the text catalog; function-calling models would still be unable to call
    # the browser tools and tend to fall back to desktop automation.
    if tools_active and _browser_operation_requested(intent.user_context):
        _ensure_browser_operation_skills(executor)

    # Resolve the model up-front (was computed later) so the native
    # tool-use gate can be decided before the system prompt is built.
    # ``auto`` means "use the planner's configured model" — resolving it
    # here (instead of passing the literal string downstream) keeps
    # model_supports_thinking()/capability probes correct for auto turns.
    effective_model = (
        model
        if model and model not in ("echo-agent", "", "auto")
        else getattr(stack.planner, "planner_model", None) or "auto"
    )

    # ── Native tool-use gate (Phase 0) ─────────────────────────────────
    # For tool-use-capable models, drive the loop via native ``tool_calls``
    # instead of the text ``Action: name({...})`` protocol — eliminating the
    # single biggest brittleness source (regex-parsing the action out of free
    # text). Gated by ``ECHO_NATIVE_TOOLUSE`` (default off) AND the model's
    # advertised capability; otherwise the text protocol + its regex fallback
    # run byte-identically to before. Specs are built once per turn.
    from runtime.core.cerebrum.react_native import (
        build_loop_tool_specs,
        native_tool_use_active,
        require_public_update_on_tool_specs,
    )

    _native_mode = bool(tools_active) and native_tool_use_active(router, effective_model)
    _native_goal = getattr(intent, "normalized_goal", "") or getattr(intent, "raw", "") or ""
    _strict_explicit_reads = bool(
        _explicit_read_only_goal(_native_goal)
        and _explicit_source_paths(_native_goal)
        and not _browser_operation_requested(intent.user_context)
    )
    _ordered_result_handoffs = bool(
        len(_explicit_source_paths(_native_goal)) > 1
        and _explicit_observed_read_sequence(_native_goal)
    )
    _native_observed_read_sequence = bool(_strict_explicit_reads and _ordered_result_handoffs)
    _native_tool_specs = (
        build_loop_tool_specs(
            executor,
            agent=agent,
            goal=_native_goal,
            user_context=intent.user_context,
            strict_explicit_reads=_strict_explicit_reads,
        )
        if _native_mode
        else []
    )
    if _native_mode and not _native_tool_specs:
        # Spec build came back empty — nothing to call natively, so stay on
        # the proven text protocol rather than passing an empty tools list.
        _native_mode = False
    _native_public_update_tool_specs = (
        require_public_update_on_tool_specs(_native_tool_specs)
        if (
            _native_mode
            and bool(
                (intent.user_context or {}).get("realtime_public_orientation")
                or (intent.user_context or {}).get("realtime_public_narrative")
                or _native_observed_read_sequence
            )
        )
        else _native_tool_specs
    )
    _native_evidence_update_tool_specs = (
        require_public_update_on_tool_specs(
            _native_tool_specs,
            evidence_round=True,
        )
        if _native_public_update_tool_specs is not _native_tool_specs
        else _native_tool_specs
    )

    # Expose the live approval provider through the session so the
    # ``exit_plan_mode`` skill can issue an interactive approval
    # request without re-plumbing the param through every layer.
    try:
        from runtime.platform.process.session import current_session as _cs_for_provider

        _session_for_provider = _cs_for_provider()
        if (
            _session_for_provider is not None
            and _session_for_provider.metadata is not None
            and approval_provider is not None
        ):
            _session_for_provider.metadata["_approval_provider"] = approval_provider
    except (ImportError, AttributeError):  # noqa: BLE001 — session layer optional in tests
        pass

    # ── PHASE 2 · mode + budget detection ──────────────────────────────
    from runtime.platform.models import TaskId as _TaskId

    react_task_id: _TaskId = resume_task_id if resume_task_id is not None else _TaskId(uuid.uuid4())

    _camouflage_variant_name = "baseline"
    _camouflage_suffix = ""
    try:
        from runtime.safety.experiments.scheduler import (
            get_camouflage_scheduler,
        )

        _camouflage_variant_name, _camouflage_suffix = (
            get_camouflage_scheduler().assign_variant_suffix(str(react_task_id))
        )
    except ImportError:
        _logger.debug("camouflage scheduler not available", exc_info=True)
    return _TurnBootstrap(
        router=router,
        reasoning_effort=_reasoning_effort,
        no_tool_turn=_no_tool_turn,
        executor=executor,
        tools_active=tools_active,
        effective_model=effective_model,
        native_mode=_native_mode,
        strict_explicit_reads=_strict_explicit_reads,
        ordered_result_handoffs=_ordered_result_handoffs,
        native_public_update_tool_specs=_native_public_update_tool_specs,
        native_evidence_update_tool_specs=_native_evidence_update_tool_specs,
        react_task_id=react_task_id,
        camouflage_suffix=_camouflage_suffix,
    )


def _emit_turn_start_events(
    *,
    react_task_id: Any,
    thread_id: str,
    max_iterations: int,
    grounding_sources: Any,
    tools_active: bool,
    planning_mode: bool,
    intent: Any,
    executor: Any,
    stack: Any,
    messages: list,
    on_auto_parallel_batch: Callable[[str], None] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """react_started / grounding / auto-delegation events (PHASE 4/4.5).

    Moved verbatim from ``react_loop.stream_react_loop``. Mutates
    ``messages`` in place when a successful auto-delegation injects its
    synthetic observation.
    """
    yield {
        "type": "react_started",
        "task_id": str(react_task_id),
        "thread_id": thread_id or None,
        "max_iterations": max_iterations,
    }

    # Surface the codebase docs/chunks we actually grounded this turn on, so
    # the UI can show a plain-language "consulted N project docs" chip. Faithful
    # by construction: these are the exact sources folded into the prompt above.
    if grounding_sources:
        yield {
            "type": "codebase_grounding",
            "sources": grounding_sources,
        }

    # ── PHASE 4.5 · agent auto-delegation short-circuit ────────────────
    # When the user prompt has a single, unambiguous @agent: pin AND no
    # competing routing signals, we can save one full LLM round trip by
    # delegating directly. The plan only fires when ALL of these hold:
    #   - tools_active (delegation is a tool path)
    #   - not planning_mode (plan mode wants the model to think first)
    #   - the prompt passes plan_auto_delegation's heuristics
    #   - the executor's registry has the call_agent skill
    # On success, we inject the subagent's output as an Observation-style
    # user message so the next LLM turn synthesizes the final answer
    # against real evidence rather than re-planning the delegation.
    _auto_delegated = False
    if tools_active and not planning_mode:
        try:
            from runtime.core.cerebrum.agent_auto_delegate import (
                plan_auto_delegation,
            )

            _delegation_plan = plan_auto_delegation(
                intent.normalized_goal,
                registry=getattr(executor, "agent_registry", None)
                or getattr(stack, "agent_registry", None)
                or getattr(executor, "registry", None),
            )
        except (ImportError, AttributeError, TypeError):
            _delegation_plan = None
        if (
            _delegation_plan is not None
            and _delegation_plan.should_delegate
            and _skill_available_in_executor(executor, "call_agent")
        ):
            try:
                from runtime.execution.subagents.bridge import call_subagent

                _logger.info(
                    "react_loop auto-delegating to agent=%s reason=%s",
                    _delegation_plan.target_agent,
                    _delegation_plan.reason,
                )
                yield {
                    "type": "auto_delegation_started",
                    "target_agent": _delegation_plan.target_agent,
                    "reason": _delegation_plan.reason,
                }
                _delegate_result = call_subagent(
                    agent_id=_delegation_plan.target_agent or "",
                    prompt=_delegation_plan.cleaned_prompt,
                    context={
                        "thread_id": thread_id or "",
                        "source": "auto_delegation",
                        "parent_task_id": str(react_task_id),
                    },
                    timeout_s=120,
                )
                _delegate_output = str(
                    _delegate_result.get("output", "") or "",
                ).strip()
                _delegate_ok = bool(_delegate_result.get("success", False))
                if _delegate_ok and _delegate_output:
                    # Inject as a synthetic Observation so the model's
                    # next turn writes the Final Answer directly.
                    obs_block = (
                        "<auto-delegation-observation>\n"
                        f"Auto-delegated to @agent:{_delegation_plan.target_agent}.\n"
                        f"Reason: {_delegation_plan.reason}.\n"
                        f"Subagent output:\n\n{_delegate_output}\n"
                        "</auto-delegation-observation>\n\n"
                        "Use this as the primary evidence for your Final "
                        "Answer. Add your own synthesis or follow-up only "
                        "if the user's request demands more than the "
                        "subagent's output already covers."
                    )
                    messages.append(Message(role="user", content=obs_block))
                    _auto_delegated = True
                    yield {
                        "type": "auto_delegation_completed",
                        "target_agent": _delegation_plan.target_agent,
                        "output_length": len(_delegate_output),
                    }
                else:
                    err = str(_delegate_result.get("error", "") or "")
                    _logger.info(
                        "auto-delegation produced no usable output "
                        "(success=%s, error=%s) — falling back to model",
                        _delegate_ok,
                        err,
                    )
                    yield {
                        "type": "auto_delegation_skipped",
                        "target_agent": _delegation_plan.target_agent,
                        "reason": err or "no output",
                    }
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                _logger.debug(
                    "auto-delegation failed; falling back to model: %s",
                    exc,
                    exc_info=True,
                )
                yield {
                    "type": "auto_delegation_skipped",
                    "target_agent": getattr(
                        _delegation_plan,
                        "target_agent",
                        None,
                    ),
                    "reason": f"{type(exc).__name__}: {exc}",
                }

    # ── PHASE 4.6 · auto-decomposition + parallel short-circuit ────────
    # When a goal is complex enough to split into >=2 independent
    # sub-inquiries, pre-run them in parallel (orchestrator dispatch) and
    # inject the aggregated results as a synthetic Observation. The main
    # loop then synthesises the Final Answer against real per-subtask
    # evidence instead of re-reasoning about decomposition. Skipped when
    # a single-agent auto-delegation already short-circuited the turn.
    if tools_active and not planning_mode and not _auto_delegated:
        try:
            from runtime.core.cerebrum.agent_auto_parallel import (
                build_thread_memory_summary,
                plan_auto_parallel,
                run_auto_parallel,
            )

            # Cross-turn memory (conversation history stamped by the gateway)
            # feeds the decomposition so each parallel sub-agent executes
            # against prior context instead of a blank slate. Best-effort —
            # an empty summary is a plain no-op for both the plan and run.
            # Board evidence (the previous turn's blackboard key/value
            # findings, persisted by ``run_auto_parallel``) rides the same
            # lane: concrete parallel-exploration outputs beat re-research.
            _parallel_memory = build_thread_memory_summary(intent.user_context)
            try:
                from runtime.memory.threads.board_evidence import load_board_evidence

                _board_evidence = load_board_evidence(thread_id or "")
            except (ImportError, AttributeError, TypeError):
                _board_evidence = ""
            if _board_evidence:
                _parallel_memory = "\n\n".join(
                    part for part in (_parallel_memory, _board_evidence) if part
                )
            _auto_goal = str(intent.normalized_goal or "") or str(intent.raw or "")
            _parallel_plan = plan_auto_parallel(
                _auto_goal,
                context=_parallel_memory,
            )
        except (ImportError, AttributeError, TypeError):
            _parallel_plan = None
            _parallel_memory = ""
        if _parallel_plan is not None and _parallel_plan.should_parallelize():
            _subtask_descriptions = [t.description for t in _parallel_plan.subtasks]
            yield {
                "type": "auto_parallel_started",
                "subtasks": _subtask_descriptions,
                "reason": _parallel_plan.reason,
            }
            try:
                _parallel_result = run_auto_parallel(
                    _parallel_plan,
                    thread_id=thread_id or "",
                    context={
                        "thread_id": thread_id or "",
                        "source": "auto_parallel",
                        "parent_task_id": str(react_task_id),
                        "memory_summary": _parallel_memory,
                    },
                    on_batch_started=on_auto_parallel_batch,
                )
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                _parallel_result = None
                _logger.debug(
                    "auto-parallel failed; falling back to model: %s",
                    exc,
                    exc_info=True,
                )
            _parallel_ok = bool(_parallel_result is not None and _parallel_result.get("success"))
            _parallel_content = str((_parallel_result or {}).get("content", "") or "").strip()
            if _parallel_ok and _parallel_content:
                obs_block = (
                    "<auto-parallel-observation>\n"
                    f"Goal decomposed into {len(_subtask_descriptions)} "
                    "independent sub-inquiries and resolved in parallel:\n"
                    + "\n".join(f"- {d}" for d in _subtask_descriptions)
                    + "\n\nAggregated sub-agent outputs:\n\n"
                    f"{_parallel_content}\n"
                    "</auto-parallel-observation>\n\n"
                    "Use these as the primary evidence for your Final "
                    "Answer. Add your own synthesis, cross-referencing, or "
                    "follow-up only if the user's request demands more than "
                    "the aggregated outputs already cover."
                )
                messages.append(Message(role="user", content=obs_block))
                yield {
                    "type": "auto_parallel_completed",
                    "subtasks": len(_subtask_descriptions),
                    "output_length": len(_parallel_content),
                    "batch_id": (_parallel_result or {}).get("batch_id") or None,
                }
            else:
                err = str((_parallel_result or {}).get("error") or "") or ""
                _logger.info(
                    "auto-parallel produced no usable output (ok=%s, err=%s) — "
                    "falling back to model",
                    _parallel_ok,
                    err,
                )
                yield {
                    "type": "auto_parallel_skipped",
                    "subtasks": _subtask_descriptions,
                    "reason": err or "no usable output",
                }
