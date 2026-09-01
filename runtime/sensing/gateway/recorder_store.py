"""Durable, privacy-aware event store for the optional Echo REC plugin.

The recorder is intentionally a side channel: capturing a demonstration must
never interfere with the task being demonstrated.  Sessions survive a web
refresh and a backend restart, while event payloads are bounded and scrubbed
before they touch disk.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from runtime.platform.process.paths import app_paths

MAX_RECORDING_MINUTES = 30
MAX_EVENT_BYTES = 32 * 1024
MAX_EVENTS_PER_BATCH = 100

_SENSITIVE_KEY_RE = re.compile(
    r"password|passcode|passwd|secret|token|api[_-]?key|otp|one[_-]?time|"
    r"credit|card|cvv|ssn|passport|authorization|cookie",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_ts(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _scrub(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Return a JSON-safe, bounded payload with sensitive values removed."""

    if _SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]"
    if depth >= 6:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, list):
        return [_scrub(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        sensitive_payload = bool(value.get("sensitive"))
        return {
            str(child_key)[:120]: (
                "[REDACTED]"
                if sensitive_payload and str(child_key).lower() in {"value", "content", "input"}
                else _scrub(
                    child_value,
                    key=str(child_key),
                    depth=depth + 1,
                )
            )
            for child_key, child_value in list(value.items())[:100]
        }
    return str(value)[:2000]


class RecorderStore:
    """File-backed recording sessions with at most one active session/thread."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else app_paths().data_dir / "recordings"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._active: dict[str, str] = {}
        self._restore_active_sessions()

    def _restore_active_sessions(self) -> None:
        for metadata_path in self.root.glob("*/session.json"):
            try:
                session = json.loads(metadata_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if session.get("status") != "recording":
                continue
            thread_id = str(session.get("thread_id") or "")
            session_id = str(session.get("session_id") or "")
            if thread_id and session_id:
                self._active[thread_id] = session_id

    def _session_dir(self, session_id: str) -> Path:
        if not re.fullmatch(r"rec_[a-f0-9]{24}", session_id):
            raise ValueError("invalid recording session id")
        return self.root / session_id

    def _metadata_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _events_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"

    def _read(self, session_id: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._metadata_path(session_id).read_text("utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write(self, session: dict[str, Any]) -> None:
        target = self._metadata_path(str(session["session_id"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(session, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)

    def _public(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            **session,
            "metadata_path": str(self._metadata_path(str(session["session_id"]))),
            "events_path": str(self._events_path(str(session["session_id"]))),
        }

    def _expire_if_needed(self, session: dict[str, Any]) -> dict[str, Any]:
        if session.get("status") != "recording":
            return session
        deadline = _parse_ts(str(session.get("started_at") or "")) + timedelta(
            minutes=MAX_RECORDING_MINUTES
        )
        if datetime.now(UTC) < deadline:
            return session
        session["status"] = "stopped"
        session["end_reason"] = "time_limit"
        session["ended_at"] = _now()
        self._active.pop(str(session.get("thread_id") or ""), None)
        self._write(session)
        return session

    def start(
        self,
        *,
        thread_id: str,
        name: str,
        description: str = "",
        provider: str = "hybrid",
    ) -> dict[str, Any]:
        with self._lock:
            active_id = self._active.get(thread_id)
            if active_id:
                active = self._read(active_id)
                if (
                    active is not None
                    and self._expire_if_needed(active).get("status") == "recording"
                ):
                    return self._public(active)

            session_id = f"rec_{uuid4().hex[:24]}"
            session = {
                "schema": "echo.recording.session.v1",
                "session_id": session_id,
                "thread_id": thread_id,
                "name": name,
                "description": description,
                "provider": provider,
                "status": "recording",
                "started_at": _now(),
                "ended_at": None,
                "end_reason": None,
                "event_count": 0,
                "step_count": 0,
                "max_duration_seconds": MAX_RECORDING_MINUTES * 60,
            }
            self._session_dir(session_id).mkdir(parents=True, exist_ok=True)
            self._events_path(session_id).touch(exist_ok=True)
            self._write(session)
            self._active[thread_id] = session_id
            return self._public(session)

    def status(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            session_id = self._active.get(thread_id)
            if not session_id:
                return None
            session = self._read(session_id)
            if session is None:
                self._active.pop(thread_id, None)
                return None
            session = self._expire_if_needed(session)
            return self._public(session)

    def append(self, thread_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            session = self.status(thread_id)
            if session is None or session.get("status") != "recording":
                raise LookupError("no active recording for this thread")
            accepted = 0
            event_path = Path(str(session["events_path"]))
            with event_path.open("a", encoding="utf-8") as handle:
                for raw in events[:MAX_EVENTS_PER_BATCH]:
                    if not isinstance(raw, dict):
                        continue
                    event = _scrub(raw)
                    assert isinstance(event, dict)
                    event.setdefault("schema", "echo.recording.event.v1")
                    event.setdefault("event_id", f"evt_{uuid4().hex[:20]}")
                    event.setdefault("ts", _now())
                    event.setdefault("source", "human")
                    encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
                        event["data"] = {"redacted": True, "reason": "event_too_large"}
                        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    handle.write(encoded + "\n")
                    accepted += 1

            stored = self._read(str(session["session_id"])) or session
            stored["event_count"] = int(stored.get("event_count") or 0) + accepted
            stored["step_count"] = int(stored.get("step_count") or 0) + accepted
            self._write(stored)
            return {**self._public(stored), "accepted": accepted}

    def stop(self, thread_id: str, *, reason: str = "user_stopped") -> dict[str, Any] | None:
        with self._lock:
            session_id = self._active.pop(thread_id, None)
            if not session_id:
                return None
            session = self._read(session_id)
            if session is None:
                return None
            session["status"] = "stopped"
            session["end_reason"] = reason
            session["ended_at"] = _now()
            self._write(session)
            return self._public(session)


__all__ = [
    "MAX_RECORDING_MINUTES",
    "RecorderStore",
]
