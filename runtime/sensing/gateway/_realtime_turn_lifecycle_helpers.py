"""Shared helpers for the realtime turn lifecycle.

Unit: visible-output determination (``_turn_has_observable_output``) and
cowork context-authorization / turn-plan injection
(``_inject_cowork_turn_plan``).

Split out of ``realtime_turn_lifecycle.py`` so that orchestrator stays
under the god-file line budget.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from runtime.protocol import ItemType, Turn

_logger = logging.getLogger(__name__)

# Commands that plausibly run verification on the code the turn changed.
# Used by ``_background_task_is_verification`` so turn finalization only
# closes unverified code as completed-with-background when the model actually
# delegated verification to a background task — not when an unrelated
# watcher / dev-server / poller happens to still be running.
_VERIFICATION_COMMAND_HINTS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|\s)(pytest|tox|nox)\b"),
    re.compile(r"(^|\s)(ruff|mypy|pyright|flake8|pylint)\b"),
    re.compile(r"(^|\s)(tsc|eslint|vitest|jest|karma|ava)\b"),
    re.compile(
        r"(^|\s)(npm|pnpm|yarn|bun)\s+(run\s+)?(test|lint|check|typecheck|build|validate)\b"
    ),
    re.compile(r"(^|\s)(go\s+(test|vet)|cargo\s+(test|check|clippy)|golangci-lint)\b"),
    re.compile(r"(^|\s)(make|ninja)\s+(test|check|lint|validate)\b"),
    re.compile(r"(^|\s)cmake\s+--build\b"),
    re.compile(
        r"(^|\s)python(\d(\.\d+)*)?(\s+-[A-Za-z]+)*\s+-m\s+(pytest|unittest|tox|ruff|mypy|validate)"
    ),
)


def _background_task_is_verification(task_name: str) -> bool:
    """Whether a tagged background task plausibly runs code verification.

    The realtime bridge tags background watcher tasks with
    ``echo-background:<command>`` at launch. Turn finalization checks this
    before closing unverified code as completed-with-background, so an
    unrelated long-running task (file watcher, dev server, poller) no longer
    silently skips the verification gate.

    Untagged task names (created before tagging existed, or by code paths
    that never registered through the bridge) default to True so in-flight
    turns keep the pre-tagging behavior during a hot reload.
    """
    if not task_name:
        return False
    if ":" not in task_name:
        return True
    command = task_name.split(":", 1)[1]
    if not command.strip():
        return False
    return any(pattern.search(command) for pattern in _VERIFICATION_COMMAND_HINTS)


def _turn_has_observable_output(turn: Turn) -> bool:
    """Return true once the runtime produced anything visible beyond input.

    A turn that only contains the user's message but no agent text, no
    reasoning, no tool/file/artifact/error item is a silent failure. It
    should not be marked completed because the UI has nothing meaningful
    to render and the user sees a stuck/empty answer.
    """
    for item in turn.items:
        item_type = getattr(item, "type", None)
        if item_type in {
            ItemType.USER_MESSAGE,
            ItemType.STEERING_USER_MESSAGE,
        }:
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


def _project_is_bound_to_thread(runtime: Any, thread_id: str) -> bool:
    """Return whether ``thread_id`` is a Project OS home.

    Project homes deliberately keep their cowork mode as ``chat``; binding is
    therefore the durable signal that a one-agent roster is still a group room
    rather than a private 1:1 conversation.
    """

    project_store = getattr(runtime, "_project_store", None)
    project_for_thread = getattr(project_store, "project_for_thread", None)
    if not callable(project_for_thread):
        return False
    try:
        return project_for_thread(thread_id) is not None
    except Exception as exc:  # noqa: BLE001 — optional read-model signal
        _logger.debug("project-thread binding lookup skipped: %s", exc, exc_info=True)
        return False


def _collaboration_store(runtime: Any) -> Any:
    """Resolve the canonical collaboration store used by realtime turns."""

    store = getattr(runtime, "_collaboration_store", None)
    if store is not None:
        return store
    app_store = getattr(getattr(runtime, "_app_state", None), "collaboration_store", None)
    if app_store is not None:
        return app_store
    return None


def _inject_cowork_turn_plan(
    runtime: Any,
    *,
    thread_id: str,
    text: str,
    intent: Any,
) -> None:
    """Attach cowork turn-planning diagnostics to the realtime intent.

    Single-responder plans stay advisory; multi-responder plans are converted
    into the existing ``agent_roster`` shape so the stable group-fanout driver
    can run the selected members in parallel.
    """
    context = getattr(intent, "user_context", None)
    if not isinstance(context, dict):
        return
    store = getattr(runtime, "_cowork_group_store", None)
    if store is None:
        store = getattr(getattr(runtime, "_app_state", None), "cowork_group_store", None)
    if store is None:
        return
    try:
        from runtime.memory.cowork.turn_plan import plan_turn_for_thread

        state = store.state(thread_id)
        canonical_room: dict[str, Any] | None = None
        collaboration_store = _collaboration_store(runtime)
        room_for_session = getattr(collaboration_store, "room_for_session", None)
        if callable(room_for_session):
            candidate = room_for_session(thread_id)
            canonical_room = candidate if isinstance(candidate, dict) else None
        project_bound = _project_is_bound_to_thread(runtime, thread_id)
        room_id = str(
            state.room_id
            or (canonical_room or {}).get("id")
            or (canonical_room or {}).get("room_id")
            or ""
        ).strip()
        persistent_group = bool(room_id or project_bound)
        # ``GroupStore.state`` returns an empty default chat state for every
        # unknown thread.  Treating that as a group would suppress all normal
        # new/private chats, so only inject the contract for an actual group,
        # linked room, or Project OS binding.
        if not state.event_count and not persistent_group:
            return
        requested_override = context.get("response_mode_override")
        mode_override = (
            requested_override if requested_override in {"chat", "cluster", "swarm"} else None
        )
        plan = plan_turn_for_thread(
            store,
            thread_id,
            text,
            persistent_group=persistent_group,
            mode_override=mode_override,
        ).to_dict()
    except Exception as exc:  # noqa: BLE001
        _logger.debug("cowork turn plan skipped: %s", exc, exc_info=True)
        return
    # The server owns the final plan. Clients may request one of the three
    # conversational response strategies, but may not forge responders or a
    # roster outside the durable group membership.
    context["cowork_plan"] = plan
    context["cowork_mode"] = plan.get("mode")
    context["cowork_responders"] = plan.get("responders") or []
    context["cowork_is_multi"] = bool(plan.get("is_multi"))
    context["cowork_group"] = True
    context["cowork_persistent_group"] = persistent_group
    if room_id:
        context["cowork_room_id"] = room_id
    responders = [
        str(agent_id) for agent_id in (plan.get("responders") or []) if str(agent_id or "").strip()
    ]
    context["cowork_waiting_for_mention"] = bool(plan.get("mode") == "chat" and not responders)
    active_agents = [
        member.id
        for member in state.roster
        if member.kind == "agent" and member.role == "participant" and not member.muted
    ]
    context["agent_roster"] = [
        {"agent_id": agent_id, "display_name": agent_id} for agent_id in active_agents
    ]

    # Enforce the responder's context grant on the single-responder react path.
    # A member pulled in with from_join/range/summary must not see history beyond
    # their grant. The async runner already slices via context_view; this closes
    # the realtime path. (Multi-responder fanout passes only the current message,
    # not history, so there's nothing to leak there.)
    if not plan.get("is_multi") and len(responders) == 1:
        msgs = context.get("conversation_messages")
        if isinstance(msgs, list) and msgs:
            try:
                from runtime.memory.cowork.context_view import (
                    resolve_view,
                    slice_messages,
                )

                view = resolve_view(store.state(thread_id), responders[0], len(msgs))
                if view is not None and view.scope != "all":
                    context["conversation_messages"] = slice_messages(view, msgs)
            except Exception as exc:  # noqa: BLE001 — grant slice is best-effort
                _logger.debug("cowork grant slice skipped: %s", exc, exc_info=True)


def _persist_cowork_user_message(
    runtime: Any,
    *,
    thread_id: str,
    text: str,
    item_id: str,
    actor_id: str | None,
    intent: Any,
) -> dict[str, Any] | None:
    """Mirror one realtime human item into the canonical room exactly once.

    ``thread:<item-id>`` is also the frontend's Project-action anchor.  The
    collaboration store's unique source-id index makes a later UI retry return
    this same row instead of appending a duplicate.
    """

    context = getattr(intent, "user_context", None)
    if not isinstance(context, dict) or not context.get("cowork_persistent_group"):
        return None
    room_id = str(context.get("cowork_room_id") or "").strip()
    store = _collaboration_store(runtime)
    append_message = getattr(store, "append_message", None)
    message_for_session = getattr(store, "message_for_session", None)
    if not room_id or not callable(append_message):
        return None
    participant_id = str(actor_id or "anonymous").strip() or "anonymous"
    source_message_id = f"thread:{item_id}"
    try:
        seq = append_message(
            thread_id,
            room_id=room_id,
            text=text,
            participant_id=participant_id,
            display_name="我",
            metadata={
                "source_message_id": source_message_id,
                "message_type": "message",
            },
        )
        context.setdefault("cowork_room_message_seq", int(seq))
        context.setdefault("cowork_source_message_id", source_message_id)
        if callable(message_for_session):
            message = message_for_session(thread_id, int(seq))
            return message if isinstance(message, dict) else None
    except Exception as exc:  # noqa: BLE001 — thread durability remains authoritative
        _logger.warning("cowork room-message projection failed: %s", exc, exc_info=True)
    return None


def _resolve_cowork_responder_agent(
    runtime: Any,
    *,
    intent: Any,
    fallback: Any,
) -> Any:
    """Resolve an explicitly @addressed member from the server-owned roster.

    Existing-thread owner pinning protects ordinary chats from forged client
    metadata.  A cowork @mention is different: ``plan_turn`` already validated
    the id against the durable roster, so that responder may safely override
    the thread's default/leader agent for this turn only.
    """

    context = getattr(intent, "user_context", None)
    if not isinstance(context, dict) or not context.get("cowork_group"):
        return fallback
    plan = context.get("cowork_plan")
    addressed = plan.get("addressed") if isinstance(plan, dict) else None
    responders = context.get("cowork_responders")
    if not isinstance(addressed, list) or not isinstance(responders, list):
        return fallback
    responder_ids = [str(value).strip() for value in responders if str(value or "").strip()]
    addressed_ids = {str(value).strip() for value in addressed if str(value or "").strip()}
    if len(responder_ids) != 1 or responder_ids[0] not in addressed_ids:
        return fallback
    responder_id = responder_ids[0]
    registry = getattr(runtime, "_agent_registry", None)
    try:
        if registry is not None and registry.has(responder_id):
            context.setdefault("cowork_active_responder_id", responder_id)
            return registry.get(responder_id)
    except Exception as exc:  # noqa: BLE001 — report a clear routing failure below
        _logger.debug("cowork responder registry lookup failed: %s", exc, exc_info=True)
    fallback_id = str(getattr(fallback, "agent_id", "") or "").strip()
    if fallback is not None and fallback_id == responder_id:
        context.setdefault("cowork_active_responder_id", responder_id)
        return fallback
    raise RuntimeError(f"@addressed cowork agent is unavailable: {responder_id}")
