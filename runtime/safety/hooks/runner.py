"""Dispatch helpers · the runtime calls these at lifecycle points.

One ``dispatch_<event>`` per event type. Each iterates handlers in
registration order · first ``cancelled=True`` decision wins and
returns early. Modifications (``modified_args``, ``modified_output``,
``modified_prompt``) accumulate · later handlers see earlier mods.

Handlers that raise are **caught** · their exception is logged and
the hook treated as pass_through. Rationale: a buggy 3rd-party hook
should not take down the runtime. Same posture as constitution gate.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from typing import Any

from .events import (
    HookEvent,
    NotificationEvent,
    PermissionDeniedEvent,
    PermissionRequestEvent,
    PostToolUseEvent,
    PostToolUseFailureEvent,
    PreToolUseEvent,
    SessionStartEvent,
    StopEvent,
    SubagentStartEvent,
    SubagentStopEvent,
    UserPromptSubmitEvent,
)
from .registry import HookDecision, get_global_registry

_logger = logging.getLogger("runtime.safety.hooks")


def _run_coroutine_sync(coro: Any) -> Any:
    """Run an awaitable from sync context · handle both cases:

    * **No loop running** (executor thread · test code · most of
      our runtime) · ``asyncio.run`` drives a fresh loop.
    * **Loop already running** (async web-framework caller that
      somehow reaches us · rare) · spin a throwaway thread with
      its own loop to avoid ``RuntimeError: asyncio.run() cannot
      be called from a running event loop``.

    The cost is real (new loop per call) · but hook dispatch is
    not a hot path · and async hooks are opt-in.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop here · simple case.
        return asyncio.run(coro)

    # Loop already running in this thread · offload to a worker
    # thread with its own loop. Blocks the caller · acceptable
    # for a lifecycle hook (the point of hooks is to gate on the
    # result before continuing).
    result: list[Any] = [None]
    error: list[BaseException | None] = [None]

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            result[0] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001
            error[0] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if error[0] is not None:
        raise error[0]
    return result[0]


def _run_chain(event: HookEvent, event_type: type) -> HookDecision:
    """Iterate registered handlers · accumulate modifications ·
    short-circuit on cancel. Async handlers (``async def``) are
    supported transparently · ``inspect.iscoroutinefunction`` /
    ``inspect.iscoroutine`` gate the ``await``-via-temp-loop
    path."""
    reg = get_global_registry()
    handlers = reg.handlers_for(event_type)
    current = HookDecision.pass_through()

    for handler in handlers:
        try:
            result = handler(event)
            # Async path · coroutine returned · drive it to completion.
            # Works for both ``async def`` handlers AND sync handlers
            # that happen to return a coroutine (rarer · same handling).
            if inspect.iscoroutine(result):
                result = _run_coroutine_sync(result)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "hook handler %s for %s raised · treating as pass_through: %s",
                getattr(handler, "__name__", repr(handler)),
                event_type.__name__,
                exc,
            )
            continue

        if result is None:
            continue

        if result.cancelled:
            return result

        # Accumulate modifications · later handler wins for the
        # field it modified · earlier mods survive for untouched
        # fields.
        merged_args = (
            result.modified_args if result.modified_args is not None else current.modified_args
        )
        merged_output = (
            result.modified_output
            if result.modified_output is not None
            else current.modified_output
        )
        merged_prompt = (
            result.modified_prompt
            if result.modified_prompt is not None
            else current.modified_prompt
        )
        current = HookDecision(
            cancelled=False,
            modified_args=merged_args,
            modified_output=merged_output,
            modified_prompt=merged_prompt,
        )

    return current


def dispatch_pre_tool(
    sucker_id: str,
    args: dict[str, Any],
    caller: str = "",
    session: Any = None,
) -> HookDecision:
    event = PreToolUseEvent(
        session=session,
        sucker_id=sucker_id,
        args=args,
        caller=caller,
    )
    return _run_chain(event, PreToolUseEvent)


def dispatch_post_tool(
    sucker_id: str,
    args: dict[str, Any],
    output: Any,
    success: bool = True,
    session: Any = None,
) -> HookDecision:
    event = PostToolUseEvent(
        session=session,
        sucker_id=sucker_id,
        args=args,
        output=output,
        success=success,
    )
    return _run_chain(event, PostToolUseEvent)


def dispatch_user_prompt(
    prompt_text: str,
    thread_id: str = "",
    session: Any = None,
) -> HookDecision:
    event = UserPromptSubmitEvent(
        session=session,
        prompt_text=prompt_text,
        thread_id=thread_id,
    )
    return _run_chain(event, UserPromptSubmitEvent)


def dispatch_stop(
    thread_id: str,
    success: bool,
    step_count: int = 0,
    session: Any = None,
) -> HookDecision:
    event = StopEvent(
        session=session,
        thread_id=thread_id,
        success=success,
        step_count=step_count,
    )
    return _run_chain(event, StopEvent)


def dispatch_session_start(
    thread_id: str,
    session: Any = None,
) -> HookDecision:
    event = SessionStartEvent(
        session=session,
        thread_id=thread_id,
    )
    return _run_chain(event, SessionStartEvent)


def dispatch_notification(
    kind: str,
    details: dict[str, Any] | None = None,
    session: Any = None,
) -> HookDecision:
    event = NotificationEvent(
        session=session,
        kind=kind,
        details=details or {},
    )
    return _run_chain(event, NotificationEvent)


def dispatch_subagent_start(
    thread_id: str,
    agent_id: str,
    subagent_type: str,
    prompt_preview: str = "",
    session_id: str = "",
    session: Any = None,
) -> HookDecision:
    event = SubagentStartEvent(
        session=session,
        thread_id=thread_id,
        agent_id=agent_id,
        subagent_type=subagent_type,
        prompt_preview=prompt_preview,
        session_id=session_id,
    )
    return _run_chain(event, SubagentStartEvent)


def dispatch_subagent_stop(
    thread_id: str,
    agent_id: str,
    subagent_type: str,
    session_id: str = "",
    ok: bool = True,
    duration_ms: int = 0,
    output_preview: str = "",
    session: Any = None,
) -> HookDecision:
    event = SubagentStopEvent(
        session=session,
        thread_id=thread_id,
        agent_id=agent_id,
        subagent_type=subagent_type,
        session_id=session_id,
        ok=ok,
        duration_ms=duration_ms,
        output_preview=output_preview,
    )
    return _run_chain(event, SubagentStopEvent)


def dispatch_post_tool_failure(
    sucker_id: str,
    args: dict[str, Any] | None = None,
    error: str = "",
    session: Any = None,
) -> HookDecision:
    event = PostToolUseFailureEvent(
        session=session,
        sucker_id=sucker_id,
        args=args or {},
        error=error,
    )
    return _run_chain(event, PostToolUseFailureEvent)


def dispatch_permission_request(
    sucker_id: str,
    args: dict[str, Any] | None = None,
    caller: str = "",
    session: Any = None,
) -> HookDecision:
    event = PermissionRequestEvent(
        session=session,
        sucker_id=sucker_id,
        args=args or {},
        caller=caller,
    )
    return _run_chain(event, PermissionRequestEvent)


def dispatch_permission_denied(
    sucker_id: str,
    args: dict[str, Any] | None = None,
    reason: str = "",
    session: Any = None,
) -> HookDecision:
    event = PermissionDeniedEvent(
        session=session,
        sucker_id=sucker_id,
        args=args or {},
        reason=reason,
    )
    return _run_chain(event, PermissionDeniedEvent)
