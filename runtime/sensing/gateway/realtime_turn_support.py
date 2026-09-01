"""Observable-output, cowork-context, and resume-intent helpers."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from runtime.protocol import ItemType, Turn
from runtime.sensing.gateway.realtime_turn_input import (
    _execution_resume_intent,
    _parse_resume_confirmation,
    _safe_int,
)

_logger = logging.getLogger(__name__)


def turn_has_observable_output(turn: Turn) -> bool:
    """Return true once the runtime produced visible output beyond input."""

    for item in turn.items:
        item_type = getattr(item, "type", None)
        if item_type in {ItemType.USER_MESSAGE, ItemType.STEERING_USER_MESSAGE}:
            continue
        if item_type == ItemType.AGENT_MESSAGE:
            if str(getattr(item, "text", "") or "").strip():
                return True
            continue
        if item_type == ItemType.REASONING:
            if str(getattr(item, "content", "") or "").strip() or bool(
                getattr(item, "summary", None)
            ):
                return True
            continue
        if item_type == ItemType.PLAN:
            if str(getattr(item, "text", "") or "").strip():
                return True
            continue
        if item_type == ItemType.TODO_LIST:
            if bool(getattr(item, "plan", None)):
                return True
            continue
        return True
    return False


def inject_cowork_turn_plan(
    runtime: Any,
    *,
    thread_id: str,
    text: str,
    intent: Any,
) -> None:
    """Attach cowork planning and context-grant diagnostics to an intent."""

    store = getattr(runtime, "_cowork_group_store", None)
    if store is None:
        store = getattr(getattr(runtime, "_app_state", None), "cowork_group_store", None)
    if store is None:
        return
    try:
        from runtime.memory.cowork.turn_plan import plan_turn_for_thread

        plan = plan_turn_for_thread(store, thread_id, text).to_dict()
    except Exception as exc:  # noqa: BLE001
        _logger.debug("cowork turn plan skipped: %s", exc, exc_info=True)
        return

    context = getattr(intent, "user_context", None)
    if not isinstance(context, dict):
        return
    context.setdefault("cowork_plan", plan)
    context.setdefault("cowork_mode", plan.get("mode"))
    context.setdefault("cowork_responders", plan.get("responders") or [])
    context.setdefault("cowork_is_multi", bool(plan.get("is_multi")))
    responders = [
        str(agent_id) for agent_id in (plan.get("responders") or []) if str(agent_id or "").strip()
    ]
    if plan.get("is_multi") and len(responders) > 1:
        context.setdefault(
            "agent_roster",
            [{"agent_id": agent_id, "display_name": agent_id} for agent_id in responders],
        )

    if not plan.get("is_multi") and len(responders) == 1:
        messages = context.get("conversation_messages")
        if isinstance(messages, list) and messages:
            try:
                from runtime.memory.cowork.context_view import resolve_view, slice_messages

                view = resolve_view(store.state(thread_id), responders[0], len(messages))
                if view is not None and view.scope != "all":
                    context["conversation_messages"] = slice_messages(view, messages)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("cowork grant slice skipped: %s", exc, exc_info=True)


async def record_pending_resume_intent(
    runtime: Any,
    thread_id: str,
    resume_intent: dict[str, Any],
) -> None:
    async with runtime._resume_intents_lock:
        runtime._pending_resume_intents[thread_id] = dict(resume_intent)
    if runtime._trace_store is None:
        return
    with contextlib.suppress(Exception):
        runtime._trace_store.record_resume_request(
            thread_id=thread_id,
            checkpoint_id=int(resume_intent.get("checkpoint_id") or 0),
            task_id=resume_intent.get("task_id"),
            status="pending",
            intent=resume_intent,
        )


async def consume_confirmed_resume_intent(
    runtime: Any,
    thread_id: str,
    text: str,
) -> dict[str, Any] | None:
    checkpoint_id = _parse_resume_confirmation(text)
    if checkpoint_id is None:
        return None
    async with runtime._resume_intents_lock:
        pending = runtime._pending_resume_intents.get(thread_id)
        pending_request_id: int | None = None
        if not isinstance(pending, dict) and runtime._trace_store is not None:
            with contextlib.suppress(Exception):
                request = runtime._trace_store.latest_pending_resume_request(thread_id=thread_id)
                if isinstance(request, dict):
                    pending = request.get("intent")
                    pending_request_id = _safe_int(request.get("id"))
        if not isinstance(pending, dict):
            return None
        if _safe_int(pending.get("checkpoint_id")) != checkpoint_id:
            return None
        runtime._pending_resume_intents.pop(thread_id, None)
    if runtime._trace_store is not None:
        with contextlib.suppress(Exception):
            confirmed = runtime._trace_store.confirm_resume_request(
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                confirmation_text=f"确认恢复 checkpoint #{checkpoint_id}",
            )
            if isinstance(confirmed, dict):
                confirmed_intent = confirmed.get("intent")
                pending = confirmed_intent if isinstance(confirmed_intent, dict) else pending
                pending_request_id = _safe_int(confirmed.get("id")) or pending_request_id
            if pending_request_id is not None:
                runtime._trace_store.consume_resume_request(pending_request_id)
    return _execution_resume_intent(pending, checkpoint_id)


__all__ = [
    "consume_confirmed_resume_intent",
    "inject_cowork_turn_plan",
    "record_pending_resume_intent",
    "turn_has_observable_output",
]
