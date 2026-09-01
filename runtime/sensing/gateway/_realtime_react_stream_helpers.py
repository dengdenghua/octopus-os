"""Shared helpers & reactive predicates for the realtime stream drivers.

Extracted from ``realtime_react_stream.py``: self-contained error-message
helpers, keepalive emission, event translation, orchestration grants,
reflex routing and the native-tool-loop / reflection-fast-path predicates.
Nothing here imports the other ``_realtime_react_stream_*`` submodules, so
this module is the acyclic base of the split.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from runtime.execution.tool_engine import (
    normalize_tool_lifecycle_event,
    tool_lifecycle_event_to_react_event,
)
from runtime.platform.models import ParsedIntent
from runtime.protocol import ServerMethod, TurnParams
from runtime.sensing.gateway.realtime_gateway import EventEmitter
from runtime.sensing.gateway.realtime_turn_input import (
    _conversation_messages_from_params,
    _input_metadata,
    _reflex_response_to_text,
    _turn_mode,
)

if TYPE_CHECKING:
    from runtime.protocol import Turn
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

_logger = logging.getLogger(__name__)

# Cadence for the single-agent keepalive. Team turns get a heartbeat from
# the team runner; a solo ReAct turn has no such signal, so a slow model or
# a silently-running tool looks identical to a wedged connection from the
# frontend. We emit a heartbeat only when the event queue has been idle for
# this long (see the consumer loops), so a normally-streaming turn never
# pays for it. Kept well under the frontend's ~10s stall threshold so a
# live-but-quiet turn always gets a keepalive before it's flagged "slow".
_SINGLE_AGENT_HEARTBEAT_INTERVAL_S = 5.0
_CRITICAL_STRUCTURAL_EVENT_TYPES = frozenset(
    {
        "react_started",
        "tool_start",
        "tool_background",
        "tool_end",
        "react_completed",
        "react_error",
        "react_paused",
        "react_cancelled",
        "react_resumed",
    }
)
_TERMINAL_REACT_EVENT_TYPES = frozenset(
    {"react_completed", "react_error", "react_paused", "react_cancelled"}
)
_REACT_QUEUE_PUT_TIMEOUT_S = 10.0
_COALESCABLE_DELTA_TYPES = frozenset({"throughput", "visibility"})


@dataclass(slots=True)
class _QueuedReactEvent:
    """Internal event envelope with an optional reducer-apply receipt."""

    event: dict[str, Any]
    applied: Future[None] | None = None


class _ReactStructuralDeliveryError(RuntimeError):
    """A critical lifecycle event could not reach the durable reducer."""


class _ToolStartAuditError(_ReactStructuralDeliveryError):
    """The durable tool-start audit boundary could not be established."""


def _is_coalescable_delta(event: dict[str, Any] | None) -> bool:
    """True for decorative deltas that may be dropped under queue pressure."""
    return isinstance(event, dict) and event.get("type") in _COALESCABLE_DELTA_TYPES


def _lease_renewal_interval_s(lease_ttl_seconds: float) -> float:
    """Return the bounded supervisor renewal cadence."""
    return max(0.1, min(float(lease_ttl_seconds) / 3.0, 30.0))


def _safe_stream_error_message(exc: BaseException, *, limit: int = 1200) -> str:
    """Redact an exception before it becomes a user-visible error item."""

    message = str(exc).strip() or type(exc).__name__
    try:
        from runtime.platform.observability.redactor import redact_text

        message = redact_text(message)
    except Exception:  # pragma: no cover - error reporting must not recurse
        message = type(exc).__name__
    return message[:limit]


async def _emit_turn_heartbeat(emitter: EventEmitter, turn: Turn, started_at: float) -> None:
    """Best-effort ``turn/heartbeat`` for a solo turn's idle stretches.

    Mirrors the team runner's keepalive so the frontend's stream-vitals
    can tell "model still working" from "connection stuck". Never allowed
    to disturb the turn — a failed notify is swallowed.
    """
    with contextlib.suppress(Exception):
        await emitter.notify(
            ServerMethod.TURN_HEARTBEAT,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "elapsedS": round(time.monotonic() - started_at, 1),
            },
        )


def _agentic_stream_event_to_react_event(
    kind: str,
    delta: Any,
    final: Any,
) -> dict[str, Any] | None:
    """Translate native tool-loop tuple events into realtime bridge events."""

    if kind == "text":
        return {"type": "text_delta", "delta": str(delta or "")}
    if kind in {"commentary", "commentary_runtime"}:
        text = str(delta or "")
        return {
            "type": "commentary_delta",
            "delta": text,
            "progress_source": ("runtime" if kind == "commentary_runtime" else "model"),
        }
    if kind == "reasoning":
        return {"type": "thinking_delta", "delta": str(delta or "")}
    if kind == "tool-call-delta" and isinstance(delta, dict):
        # Live assembly preview (dsh ``tool-call-delta`` lane): the
        # bridge forwards raw fragments before the completed call's
        # tool_start. Never executed — preview only.
        return {
            "type": "tool_call_delta",
            "tool_call_id": str(delta.get("id") or ""),
            "tool_name": str(delta.get("name") or ""),
            "index": delta.get("index"),
            "argumentsDelta": str(delta.get("argumentsDelta") or ""),
        }
    if kind == "tool_start" and isinstance(delta, dict):
        return tool_lifecycle_event_to_react_event(
            normalize_tool_lifecycle_event(
                "tool_start",
                delta,
                origin="native",
            )
        )
    if kind == "tool_end" and isinstance(delta, dict):
        return tool_lifecycle_event_to_react_event(
            normalize_tool_lifecycle_event(
                "tool_end",
                delta,
                origin="native",
            )
        )
    if kind == "stats" and isinstance(delta, dict):
        return {"type": "throughput", "usage": delta}
    if kind == "done":
        if final and not isinstance(final, str):
            return {"type": "text_delta", "delta": str(final)}
        return {"type": "react_completed"}
    if kind == "error":
        payload = delta if isinstance(delta, dict) else {}
        return {
            "type": "react_error",
            "kind": str(payload.get("kind") or "agentic_error"),
            "message": str(payload.get("message") or delta or "agentic error"),
        }
    return None


def _apply_orchestration_grant(session_metadata: dict[str, Any]) -> None:
    """Scrub then (maybe) grant the per-turn orchestration token budget.

    ``run_orchestration`` treats ``session.metadata["orchestration_token_budget"]``
    as a TRUSTED spawn-ceiling source, but this metadata dict starts life as the
    client-supplied ``user_context`` — so any client-sent value is a spawn-budget
    escalation and is dropped unconditionally. When the turn carries the
    ``audit.deep`` workflow preset, the SERVER grants the budget
    (``ultracode_token_budget()``: preset env → operator env → default), which is
    what lets the preset actually widen the fan-out without the client ever
    choosing the number.
    """
    session_metadata.pop("orchestration_token_budget", None)
    preset = str(session_metadata.get("workflow_preset") or "").strip().lower()
    if preset not in ("audit.deep", "audit.ultracode", "ultracode"):
        return
    from runtime.execution.suckers.delegation_budget import ultracode_token_budget

    session_metadata["orchestration_token_budget"] = ultracode_token_budget()


def _apply_react_session_metadata(
    session_metadata: dict[str, Any], stack: Any, approval_provider: Any
) -> None:
    """Apply server-owned execution context to one ReAct turn session."""
    _apply_orchestration_grant(session_metadata)
    session_metadata["_execution_stack"] = stack
    session_metadata["_approval_provider"] = approval_provider


def _should_use_native_tool_loop(
    stack: Any,
    intent: ParsedIntent,
    *,
    planning_mode: bool,
    model: str | None = None,
) -> bool:
    """Whether this turn should use protocol-native tool calls first.

    ``planning_mode`` is a plan-first prompt nudge, not a plan-only execution
    tier.  Since 2026-05-31 it deliberately leaves tools enabled, so it must
    not downgrade capable models to the legacy text-parsed ReAct path.

    The native loop is only valid when the router actually sends a ``tools``
    block.  ``OpenAIModelRouter.capabilities`` is hard-coded to
    ``supports_tool_use=True``, but the payload builder honours the
    operator's ``supports_tool_use: false`` declaration in
    ``custom_models.json`` and omits the tool definitions entirely.  When a
    declared-incompatible model takes the native path anyway, the request
    carries no tools and the model cannot emit ``tool_calls`` — it degrades
    into blank/plain-text output and the loop spins.  So when the active
    model is explicitly declared tool-averse, fall through to the text
    ReAct loop (which parses ``Action:`` text and still gets work done).
    """
    flag = os.environ.get("ECHO_NATIVE_TOOL_LOOP", "1").strip().lower()
    if flag in {"0", "false", "off", "no"}:
        return False

    user_context = intent.user_context or {}
    explicit = user_context.get("native_tool_loop")
    if explicit is False:
        return False
    metadata = user_context.get("metadata")
    if isinstance(metadata, dict) and metadata.get("native_tool_loop") is False:
        return False

    from runtime.core.cerebrum.todo_protocol import context_mode

    if context_mode(user_context) == "chat":
        return False

    executor = getattr(stack, "executor", None)
    router = getattr(getattr(stack, "planner", None), "router", None)
    if executor is None or router is None or not hasattr(router, "call_stream"):
        return False

    # A custom model explicitly declared as tool-averse never takes the
    # native path — the request would carry no ``tools`` and the loop would
    # spin on blank output instead of doing work.
    if model:
        from runtime.sensing.model_router.custom_model_flags import (
            model_supports_tool_use,
        )

        if not model_supports_tool_use(model):
            return False

    caps = getattr(router, "capabilities", None)
    supports = getattr(caps, "supports_tool_use", None)
    if supports is True:
        return True
    if supports is False:
        return False

    primary = getattr(router, "primary", None)
    primary_caps = getattr(primary, "capabilities", None)
    return getattr(primary_caps, "supports_tool_use", None) is True


def _is_auth_context_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return (
        "current_actor" in text
        or "登录态" in text
        or "Unauthorized" in text
        or "Credentials" in text
        or "Auth" in text
    )


def _model_error_reply(exc: BaseException) -> str | None:
    text = f"{type(exc).__name__}: {exc}"
    lower = text.lower()
    if "http_402" in lower or "insufficient_balance" in lower or "模型账户余额不足" in text:
        return "当前模型账户余额不足，所以这次没有完成。请给当前模型供应商账户充值，或切换到其他可用模型后重试。"
    if "http_401" in lower or "http_403" in lower or "api key" in lower:
        return "当前模型 API Key 无效或没有权限，所以这次没有完成。请在模型设置里更新 Key，或切换到其他可用模型后重试。"
    return None


def _personalize_reflex_reply(reply: str, agent: Any) -> str:
    display_name = str(
        getattr(agent, "display_name", None)
        or getattr(agent, "name", None)
        or getattr(agent, "agent_id", None)
        or ""
    ).strip()
    if not display_name:
        return reply
    return reply.replace("我是 Echo", f"我是 {display_name}").replace(
        "I'm Echo", f"I'm {display_name}"
    )


def _should_use_reflection_fast_path(
    runtime: CerebrumRuntime,
    text: str,
    params: TurnParams,
    *,
    conversation_messages: list[dict[str, object]] | None = None,
    has_resumable_task: bool = False,
    thread_id: str | None = None,
) -> bool:
    """Route simple, non-tool turns through the reflective direct path."""
    router = getattr(getattr(runtime._stack, "planner", None), "router", None)
    if router is None:
        return False
    mode = _turn_mode(params)
    metadata = _input_metadata(params)
    context = metadata.get("context")
    context_payload = context if isinstance(context, dict) else {}
    capability_mode = str(
        context_payload.get("capability_mode") or metadata.get("capability_mode") or ""
    ).strip()
    # A short message in a thread with a durable paused task is contextual by
    # definition.  In particular, punctuation-only probes such as "?" must
    # reach the agentic path with checkpoint context instead of producing an
    # unrelated greeting from the direct-chat fast path.
    if thread_id and not has_resumable_task:
        with contextlib.suppress(Exception):
            from runtime.core.cerebrum.pause_control import get_pause_controller

            has_resumable_task = any(
                request.thread_id == thread_id for request in get_pause_controller().list_paused()
            )
    if has_resumable_task:
        return False
    # Capability-bearing turns must reach an agentic driver.  The direct
    # reflection path cannot inspect a workspace, invoke browser tools, edit
    # files, or produce verifiable side effects.  Previously ``mode=code``
    # fell through to the broad final return below and silently became a
    # text-only answer.
    if capability_mode or mode in {
        "browser",
        "chrome",
        "code",
        "deep",
        "research",
        "swarm",
    }:
        return False
    from runtime.sensing.gateway.realtime_turn_routing import (
        looks_like_contextual_tool_followup,
        looks_like_plain_chat,
    )

    history = conversation_messages or _conversation_messages_from_params(params)
    if mode == "chat":
        return not looks_like_contextual_tool_followup(text, history)
    if looks_like_contextual_tool_followup(text, history):
        return False
    if mode in {"", "react"}:
        return looks_like_plain_chat(text)
    return True


def _try_reflex_reply(runtime: CerebrumRuntime, intent: ParsedIntent) -> str | None:
    router = runtime._reflex_router
    if router is None:
        return None
    try:
        result = router.try_match(intent)
    except Exception:  # noqa: BLE001
        _logger.debug("realtime reflex match skipped", exc_info=True)
        return None
    if not hasattr(result, "response"):
        return None
    return _reflex_response_to_text(getattr(result, "response", None))
