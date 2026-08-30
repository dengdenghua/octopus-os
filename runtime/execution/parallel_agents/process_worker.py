from __future__ import annotations

import multiprocessing
import pickle
import queue
from collections.abc import Callable
from typing import Any


def process_runner_entry(
    runner: Callable[..., str],
    description: str,
    subagent_name: str,
    context: dict[str, Any],
    cancel_event: Any,
    messages: Any,
) -> None:
    """Execute one compatible subagent runner inside a killable process."""

    child_context = dict(context)

    def emit_tool_event(**payload: Any) -> None:
        messages.put(("tool_event", payload))

    child_context["emit_tool_event"] = emit_tool_event
    try:
        try:
            output = runner(
                description,
                subagent_name=subagent_name,
                context=child_context,
                cancel_event=cancel_event,
            )
        except TypeError:
            output = runner(
                description,
                subagent_name=subagent_name,
                context=child_context,
            )
        messages.put(("result", output))
    except BaseException as exc:  # noqa: BLE001 - child boundary must report all failures
        messages.put(("error", f"{type(exc).__name__}: {exc}"))


def spawn_process_runner(
    *,
    runner: Callable[..., str],
    description: str,
    subagent_name: str,
    context: dict[str, Any],
) -> tuple[Any, Any, Any]:
    """Spawn a fresh process and return ``(process, cancel_event, queue)``.

    ``spawn`` is deliberate: it does not inherit locks, thread state, open
    database handles, or ambient ContextVars from the parent runtime.
    Unpicklable custom runners fail closed instead of silently falling back to
    a thread and losing the hard-kill guarantee.
    """

    mp = multiprocessing.get_context("spawn")
    cancel_event = mp.Event()
    messages = mp.Queue()
    process = mp.Process(
        target=process_runner_entry,
        args=(
            runner,
            description,
            subagent_name,
            _picklable_context(context),
            cancel_event,
            messages,
        ),
        name=f"echo-subagent-{subagent_name}",
        daemon=True,
    )
    process.start()
    return process, cancel_event, messages


def process_runner_compatible(
    *,
    runner: Callable[..., str],
    context: dict[str, Any],
) -> bool:
    """Return whether the spawn boundary can carry this runner + context."""

    try:
        pickle.dumps(runner)
        pickle.dumps(_picklable_context(context))
    except Exception:  # noqa: BLE001 - compatibility probe must fail closed to thread mode
        return False
    return True


def poll_process_message(messages: Any, *, timeout: float = 0.02) -> tuple[str, Any] | None:
    try:
        value = messages.get(timeout=timeout)
    except queue.Empty:
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        return None
    return str(value[0]), value[1]


def close_process_messages(messages: Any) -> None:
    try:
        messages.close()
        messages.join_thread()
    except (AttributeError, OSError, ValueError):
        return


def terminate_process(process: Any, *, grace_s: float = 0.2) -> bool:
    """Terminate then hard-kill a live child; return whether it exited."""

    if process is None or not process.is_alive():
        return True
    process.terminate()
    process.join(timeout=max(0.01, grace_s))
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=max(0.01, grace_s))
    return not process.is_alive()


def _picklable_context(context: dict[str, Any]) -> dict[str, Any]:
    # Parent callbacks and threading primitives are reconstructed in the
    # child. The remaining dispatch context is made of JSON-like policy,
    # contract, workspace, and model metadata.
    clean = {
        key: value
        for key, value in context.items()
        if key not in {"emit_tool_event", "runtime_session_metadata"}
    }
    metadata = context.get("runtime_session_metadata")
    if isinstance(metadata, dict):
        clean["runtime_session_metadata"] = dict(metadata)
    return clean


__all__ = [
    "close_process_messages",
    "poll_process_message",
    "process_runner_compatible",
    "process_runner_entry",
    "spawn_process_runner",
    "terminate_process",
]
