"""Single-agent stream drivers for the realtime runtime.

Split out of ``realtime_cerebrum.py``: pump the ReAct loop (or the
protocol-native tool loop / direct-LLM reflection fast path) on a worker
thread, marshal every yielded event onto an asyncio queue, and translate
each event into ``item/*`` notifications via ``_apply_react_event``.

Every function takes the owning :class:`~runtime.sensing.gateway.
realtime_cerebrum.CerebrumRuntime` as its first argument; cross-method
calls go through the runtime so subclass overrides keep working.

Implementation lives in the ``_realtime_react_stream_*`` submodules; this
module re-exports the full public surface so existing imports keep working.
"""

from __future__ import annotations

from runtime.sensing.gateway._realtime_react_stream_apply import _apply_react_event
from runtime.sensing.gateway._realtime_react_stream_drive import _drive_react
from runtime.sensing.gateway._realtime_react_stream_helpers import (
    _SINGLE_AGENT_HEARTBEAT_INTERVAL_S,
    _agentic_stream_event_to_react_event,
    _apply_orchestration_grant,
    _emit_turn_heartbeat,
    _is_auth_context_error,
    _logger,
    _model_error_reply,
    _personalize_reflex_reply,
    _safe_stream_error_message,
    _should_use_native_tool_loop,
    _should_use_reflection_fast_path,
    _try_reflex_reply,
)
from runtime.sensing.gateway._realtime_react_stream_reflection import (
    _drive_reflection_fast_path,
)

__all__ = [
    "_SINGLE_AGENT_HEARTBEAT_INTERVAL_S",
    "_agentic_stream_event_to_react_event",
    "_apply_orchestration_grant",
    "_apply_react_event",
    "_drive_react",
    "_drive_reflection_fast_path",
    "_emit_turn_heartbeat",
    "_is_auth_context_error",
    "_logger",
    "_model_error_reply",
    "_personalize_reflex_reply",
    "_safe_stream_error_message",
    "_should_use_native_tool_loop",
    "_should_use_reflection_fast_path",
    "_try_reflex_reply",
]
