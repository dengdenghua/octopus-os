"""Hook event dataclasses · one per lifecycle point."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HookEvent:
    """Base · carries session + a name for logging."""

    session: Any = None  # runtime.platform.process.session.Session (late-bound)
    event_name: str = ""


@dataclass
class PreToolUseEvent(HookEvent):
    """Fired just before a skill handler is called.

    Handler can cancel (return a failed step without running the
    handler) or modify args (handler is called with ``modified_args``
    instead of the original).
    """

    event_name: str = "PreToolUse"
    sucker_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    caller: str = ""


@dataclass
class PostToolUseEvent(HookEvent):
    """Fired just after a skill handler returns.

    Handler can modify the output (e.g. scrub sensitive fields
    before the output flows to the next step / assistant message).
    Cannot un-run the skill · any side effect already happened.
    """

    event_name: str = "PostToolUse"
    sucker_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    success: bool = True


@dataclass
class UserPromptSubmitEvent(HookEvent):
    """Fired before the planner sees a new user turn.

    Handler can cancel (reject prompt with a reason · user sees
    the reason) or modify the prompt text (e.g. inject context).
    """

    event_name: str = "UserPromptSubmit"
    prompt_text: str = ""
    thread_id: str = ""


@dataclass
class StopEvent(HookEvent):
    """Fired when a full agent turn completes · successfully or not.

    Handler can inspect the trajectory but not modify it · this is
    a notification-type hook (post-hoc audit / metrics).
    """

    event_name: str = "Stop"
    thread_id: str = ""
    success: bool = True
    step_count: int = 0


@dataclass
class SessionStartEvent(HookEvent):
    """Fired once at ``bind_thread_session`` time · useful for
    loading per-user context (preferences · feature flags)."""

    event_name: str = "SessionStart"
    thread_id: str = ""


@dataclass
class NotificationEvent(HookEvent):
    """Generic runtime notification · budget warnings · rate limits ·
    provider outages. Informational · decision is always pass_through."""

    event_name: str = "Notification"
    kind: str = ""  # "budget_warn" | "rate_limit" | "provider_down" | ...
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentStartEvent(HookEvent):
    """Fired when a sub-agent is spawned (before the runner executes).

    Notification-type: handlers observe the spawn (audit, budget, routing)
    but cannot cancel the call.
    """

    event_name: str = "SubagentStart"
    thread_id: str = ""
    agent_id: str = ""
    subagent_type: str = ""
    prompt_preview: str = ""
    session_id: str = ""


@dataclass
class SubagentStopEvent(HookEvent):
    """Fired when a sub-agent call finishes, successfully or not."""

    event_name: str = "SubagentStop"
    thread_id: str = ""
    agent_id: str = ""
    subagent_type: str = ""
    session_id: str = ""
    ok: bool = True
    duration_ms: int = 0
    output_preview: str = ""


@dataclass
class PostToolUseFailureEvent(HookEvent):
    """Fired when a tool execution raises / returns a failure.

    Notification-type: the failure has already happened; handlers get a
    chance to observe (metrics, alerts, incident hooks) before the error
    propagates.
    """

    event_name: str = "PostToolUseFailure"
    sucker_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class PermissionRequestEvent(HookEvent):
    """Fired when the approval gate is about to ask the human for a tool.

    Handler can grant (return ``allow``) or deny — the decision replaces
    the gate's ask, letting automation approve safe calls without a
    prompt.
    """

    event_name: str = "PermissionRequest"
    sucker_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    caller: str = ""


@dataclass
class PermissionDeniedEvent(HookEvent):
    """Fired when an approval is refused (by gate policy, hook, or human).

    Notification-type: audit / metrics.
    """

    event_name: str = "PermissionDenied"
    sucker_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
