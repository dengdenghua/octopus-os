"""Agent runtime hooks · lifecycle events for the agent loop.

Hook events
-----------

``UserPromptSubmit``     — fires before the planner sees a new user turn
``PreToolUse``           — fires before each skill call · can modify args / cancel
``PostToolUse``          — fires after each skill call · can modify output
``PostToolUseFailure``   — fires when a tool call fails (notification)
``Stop``                 — fires when an agent turn ends
``SessionStart``         — fires when a new session binding is established
``Notification``         — fires on runtime notifications (budget warn etc.)
``SubagentStart``        — fires when a sub-agent spawns (notification)
``SubagentStop``         — fires when a sub-agent call finishes (notification)
``PermissionRequest``    — fires before the approval gate asks · can grant/deny
``PermissionDenied``     — fires when an approval is refused (notification)

Each event carries a structured payload (``event_type`` + context)
and the hook handler returns a ``HookDecision`` telling the runtime
what to do:

    * ``pass_through``   — default · runtime proceeds as usual
    * ``cancel(reason)`` — runtime aborts the action (skill blocked /
                           prompt rejected / turn halted)
    * ``modify(...)``    — runtime uses the modified args / output

Public API:

    @register_hook(PreToolUse)
    def block_rm_rf(event: PreToolUseEvent) -> HookDecision:
        if event.sucker_id == "exec_shell" and "rm -rf /" in event.args.get("cmd", ""):
            return HookDecision.cancel("refuse rm -rf /")
        return HookDecision.pass_through()

Usage from framework code:

    from runtime.safety.hooks import dispatch_pre_tool

    decision = dispatch_pre_tool(sucker_id, args, session)
    if decision.cancelled:
        return failed_step(reason=decision.reason)
    if decision.modified_args is not None:
        args = decision.modified_args

Community hooks live in ``~/.echo/hooks/*.py`` or project
``.echo/hooks/*.py`` · loaded at runtime startup.
"""

from __future__ import annotations

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
from .registry import (
    HookDecision,
    HookRegistry,
    get_global_registry,
    register_hook,
)
from .runner import (
    dispatch_notification,
    dispatch_permission_denied,
    dispatch_permission_request,
    dispatch_post_tool,
    dispatch_post_tool_failure,
    dispatch_pre_tool,
    dispatch_session_start,
    dispatch_stop,
    dispatch_subagent_start,
    dispatch_subagent_stop,
    dispatch_user_prompt,
)

__all__ = [
    # Events
    "HookEvent",
    "PreToolUseEvent",
    "PostToolUseEvent",
    "UserPromptSubmitEvent",
    "StopEvent",
    "SessionStartEvent",
    "NotificationEvent",
    "SubagentStartEvent",
    "SubagentStopEvent",
    "PostToolUseFailureEvent",
    "PermissionRequestEvent",
    "PermissionDeniedEvent",
    # Registry
    "HookDecision",
    "HookRegistry",
    "get_global_registry",
    "register_hook",
    # Dispatch · called by runtime internals
    "dispatch_pre_tool",
    "dispatch_post_tool",
    "dispatch_post_tool_failure",
    "dispatch_user_prompt",
    "dispatch_stop",
    "dispatch_session_start",
    "dispatch_notification",
    "dispatch_subagent_start",
    "dispatch_subagent_stop",
    "dispatch_permission_request",
    "dispatch_permission_denied",
]
