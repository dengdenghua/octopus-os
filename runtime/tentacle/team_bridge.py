"""Bridge the in-process tentacle coordinator to the team-task dispatcher.

The team room dispatches a task assigned to a phone (assignee ``mobile_<id>``)
by running it on the device through the coordinator — the same path as the
dashboard ``POST /api/tentacle/task``, but callable from the (sync) team-task
worker thread via ``asyncio.run_coroutine_threadsafe``.

A module-level accessor holds the coordinator the main app started, so the
team-task router can reach it without threading it through every factory.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

_ACTIVE_COORDINATOR: Any | None = None


def get_or_create_tentacle_token() -> str:
    """Stable shared secret a phone must present in ``device/hello`` to join over
    the LAN (loopback connects without one). Persisted under ``data/`` so the
    join 口令 / QR stays valid across restarts."""
    from runtime.platform.process.paths import app_paths

    path = app_paths().data_dir / "tentacle_token"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except (
        FileNotFoundError,
        OSError,
    ):  # best-effort · no token on disk yet, fall through to mint one
        pass
    token = secrets.token_urlsafe(18)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    except OSError:  # best-effort · non-persistent fallback, token still usable this run
        pass
    return token


def set_active_coordinator(coordinator: Any | None) -> None:
    """Record the coordinator the main app started (or ``None`` to clear)."""
    global _ACTIVE_COORDINATOR
    _ACTIVE_COORDINATOR = coordinator


def get_active_coordinator() -> Any | None:
    return _ACTIVE_COORDINATOR


def device_id_from_ref(ref: str) -> str:
    """``mobile_<tentacle_id>`` → ``<tentacle_id>``."""
    return ref[len("mobile_") :] if ref.startswith("mobile_") else ref


async def run_device_task(coordinator: Any, tentacle_id: str, task: str) -> dict[str, Any]:
    """Run a natural-language task on one device. Mirrors the dashboard
    ``/task`` flow: decision engine → ordered tool calls → execute on device.
    Returns ``{ok, output, error}`` — never raises (failures are captured)."""
    rec: dict[str, Any] = {"tentacle_id": tentacle_id, "ok": False, "output": "", "error": None}
    try:
        device = coordinator.pool.get(tentacle_id)
        if device is None:
            rec["error"] = f"device not connected: {tentacle_id}"
            return rec
        if not device.is_online:
            rec["error"] = f"device offline: {tentacle_id}"
            return rec
        if getattr(coordinator, "_decision_engine", None) is None:
            rec["error"] = "no decision engine configured on the coordinator"
            return rec

        start = time.time()
        tool_calls = await coordinator._decision_engine(task, device)
        if not tool_calls:
            rec["error"] = "planner produced no device actions for this goal"
            return rec

        lines: list[str] = []
        all_ok = True
        for call in tool_calls:
            result = await device.execute(call)
            ok = bool(getattr(result, "success", False))
            tool = getattr(call, "tool", "?")
            detail = getattr(result, "data", None) or getattr(result, "error_message", "")
            lines.append(f"{'✓' if ok else '✗'} {tool}: {detail}")
            if not ok:
                all_ok = False
                break

        rec["ok"] = all_ok
        rec["output"] = "\n".join(lines)
        rec["duration_ms"] = int((time.time() - start) * 1000)
        return rec
    except Exception as exc:  # noqa: BLE001 — one device's failure is isolated
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec
