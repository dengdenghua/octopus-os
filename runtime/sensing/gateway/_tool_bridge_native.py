"""Native model stream + timeout + tool-call fingerprint/dedup helpers.

Extracted from ``tool_bridge.py`` (the Claude-native agentic loop). This
satellite owns the hard-wall-clock deadline pump around a blocking model
stream, the per-round timeout policy, and the fingerprint/dedup/failure
heuristics used by the loop's retry control.

The parent ``tool_bridge`` module re-exports every name here so existing
importers and tests are unchanged.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from runtime.sensing.model_router.models import ModelRequest, ToolCall

_NATIVE_STREAM_DEADLINE = object()
_NATIVE_STREAM_REDIRECTED = object()


def _native_model_round_timeout_s() -> float:
    """Wall-clock ceiling for one native tool-loop model round."""
    raw = os.environ.get("ECHO_NATIVE_MODEL_ROUND_TIMEOUT_S", "120")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 120.0
    return max(10.0, min(value, 900.0))


def _native_model_recovery_timeout_s(base_timeout_s: float) -> float:
    """Use a shorter deadline once a native round is already recovering.

    A normal tool round may need the full reasoning allowance. A tools-disabled
    convergence retry should either synthesize the saved evidence promptly or
    end truthfully; granting it another full two-minute window makes the
    timeline look alive while the task is no longer making progress.
    """
    raw = os.environ.get("ECHO_NATIVE_MODEL_RECOVERY_TIMEOUT_S", "30")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 30.0
    recovery_ceiling = max(10.0, min(value, 120.0))
    return min(base_timeout_s, recovery_ceiling)


def _native_post_tool_timeout_s(base_timeout_s: float) -> float:
    """Bound silence after evidence exists without truncating visible output."""
    raw = os.environ.get("ECHO_NATIVE_POST_TOOL_TIMEOUT_S", "60")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 60.0
    post_tool_ceiling = max(10.0, min(value, 180.0))
    return min(base_timeout_s, post_tool_ceiling)


def _iter_native_model_stream_with_deadline(
    router: Any,
    request: ModelRequest,
    timeout_s: float,
    *,
    visible_started: Any = None,
    redirect_probe: Callable[[], bool] | None = None,
) -> Iterator[Any]:
    """Pump a blocking native model stream through a hard deadline.

    Provider read timeouts cannot help when an upstream keeps a stream open
    without producing a usable event.  The daemon pump lets the agent loop
    regain control, preserve completed tool results, and converge truthfully.
    Once a final answer is visibly streaming, the wall-clock ceiling becomes
    an inactivity deadline so long reports are not cut off mid-sentence.
    """
    event_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=64)
    stop_event = threading.Event()
    caller_context = contextvars.copy_context()
    _cancellation: Any = None
    try:
        from runtime.safety.approval import cancellation as _cancellation_module

        _cancellation = _cancellation_module
    except ImportError:  # pragma: no cover - optional subsystem
        _cancellation = None

    stream_cancel_source = None
    if _cancellation is not None:
        parent_token = _cancellation.current_cancellation_token()
        stream_cancel_source = (
            _cancellation.CancellationSource()
            if parent_token is _cancellation.CancellationToken.none()
            else parent_token.link()
        )

    def _put(kind: str, value: Any) -> None:
        while not stop_event.is_set():
            try:
                event_queue.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                continue

    def _consume() -> None:
        try:
            scope = (
                _cancellation.scoped_cancellation(stream_cancel_source.token)
                if _cancellation is not None and stream_cancel_source is not None
                else contextlib.nullcontext()
            )
            with scope:
                for event in router.call_stream(request):
                    if stop_event.is_set():
                        break
                    _put("event", event)
        except Exception as exc:  # pragma: no cover - reraised below
            _put("error", exc)
        finally:
            _put("done", None)

    worker = threading.Thread(
        target=lambda: caller_context.run(_consume),
        name="native-model-stream-pump",
        daemon=True,
    )
    worker.start()
    timeout_s = max(0.0, timeout_s)
    deadline = time.monotonic() + timeout_s
    visible_mode = False
    visible_activity: Any = None
    try:
        while True:
            token = _cancellation.current_cancellation_token() if _cancellation else None
            if token is not None and token.is_cancelled:
                return
            if redirect_probe is not None and redirect_probe():
                if stream_cancel_source is not None:
                    stream_cancel_source.cancel(reason="user redirected model stream")
                yield _NATIVE_STREAM_REDIRECTED
                return
            if callable(visible_started):
                current_visible_activity = visible_started()
                if current_visible_activity and (
                    not visible_mode or current_visible_activity != visible_activity
                ):
                    visible_mode = True
                    visible_activity = current_visible_activity
                    deadline = time.monotonic() + timeout_s
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield _NATIVE_STREAM_DEADLINE
                return
            try:
                kind, value = event_queue.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if kind == "event":
                yield value
            elif kind == "error":
                raise value
            else:
                return
    finally:
        stop_event.set()
        if stream_cancel_source is not None:
            stream_cancel_source.cancel(reason="model stream pump closed")


def _native_tool_call_fingerprint(call: ToolCall) -> str:
    """Return a stable native tool+arguments key for retry control."""
    try:
        payload = json.dumps(
            call.input or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        payload = repr(call.input)
    return f"{call.name}:{payload}"


def _deduplicate_native_tool_calls(calls: list[ToolCall]) -> tuple[list[ToolCall], int]:
    """Collapse identical native calls before they enter the public timeline."""
    unique: list[ToolCall] = []
    seen: set[str] = set()
    duplicates = 0
    for call in calls:
        fingerprint = _native_tool_call_fingerprint(call)
        if fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        unique.append(call)
    return unique, duplicates


def _native_tool_batch_fingerprint(calls: list[ToolCall]) -> str:
    fingerprints = [_native_tool_call_fingerprint(call) for call in calls]
    if len(fingerprints) == 1:
        return fingerprints[0]
    return "batch:" + json.dumps(fingerprints, ensure_ascii=False, separators=(",", ":"))


def _native_definitive_failure_target(call: ToolCall) -> str:
    """Identify a missing read target independent of pagination arguments."""
    aliases = {
        "read_file": "read",
        "read_text_file": "read",
        "list_cwd": "list",
        "glob_files": "glob",
    }
    family = aliases.get(call.name)
    if family is None:
        return ""
    arguments = call.input or {}
    raw_path = next(
        (
            arguments.get(key)
            for key in ("path", "file_path", "directory", "root")
            if arguments.get(key) is not None
        ),
        "",
    )
    path = str(raw_path).strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return f"{family}:{path.rstrip('/')}" if path else ""


def _native_call_failure_is_definitive(
    call: ToolCall,
    result_block: dict[str, Any],
) -> bool:
    if not _native_definitive_failure_target(call) or not result_block.get("is_error"):
        return False
    markers = (
        "not found",
        "file not found",
        "does not exist",
        "no such file",
        "enoent",
        "不存在",
        "未找到",
    )
    content = str(result_block.get("content") or "").casefold()
    return any(marker in content for marker in markers)


def _native_failure_is_definitive(
    calls: list[ToolCall],
    result_blocks: list[dict[str, Any]],
) -> bool:
    """Whether an identical read-only retry cannot produce a different result."""
    if not calls or len(calls) != len(result_blocks):
        return False
    return all(
        _native_call_failure_is_definitive(call, block)
        for call, block in zip(calls, result_blocks, strict=True)
    )
