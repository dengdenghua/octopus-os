from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from ._channels_models import (
    _CONTROL_RE,
    _FALLBACK_ASSIGNMENTS,
    _is_oversized_file,
    _normalize_agent_id,
    _normalize_channel_id,
    _normalize_pairing_ref,
    _normalize_platform_id,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Pairing store + state persistence.
#
# Split out of channels_router.py (pure structural refactor —
# no logic changes). Imported back by channels_router.py.
# ═══════════════════════════════════════════════════════════

# Per-channel queue cap + entry TTL for ``pending``. Prevents an
# unresponsive channel_id from growing the dict without bound.
_PENDING_MAX_PER_CHANNEL = 200
_PENDING_TTL_SECONDS = 24 * 3600


class _PairingStore:
    def __init__(self) -> None:
        self.users: dict[str, set[str]] = {}
        self.groups: dict[str, set[str]] = {}
        self.pending: dict[str, list[dict[str, Any]]] = {}

    def record(
        self,
        channel_id: str,
        *,
        sender_id: str | None = None,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        safe_channel_id = _normalize_channel_id(channel_id)
        if safe_channel_id is None:
            return
        safe_thread_id = _normalize_pairing_ref(thread_id)
        safe_sender_id = _normalize_pairing_ref(sender_id)
        if is_group and safe_thread_id:
            bucket = self.groups.setdefault(safe_channel_id, set())
            if len(bucket) < _MAX_PAIRINGS_PER_CHANNEL:
                bucket.add(safe_thread_id)
        if safe_sender_id:
            bucket = self.users.setdefault(safe_channel_id, set())
            if len(bucket) < _MAX_PAIRINGS_PER_CHANNEL:
                bucket.add(safe_sender_id)

    def _gc_pending(self, channel_id: str) -> None:
        """Drop expired entries and cap the queue length."""
        import time as _t

        bucket = self.pending.get(channel_id)
        if not bucket:
            return
        cutoff = _t.time() - _PENDING_TTL_SECONDS
        bucket[:] = [e for e in bucket if float(e.get("ts", 0.0)) >= cutoff]
        if len(bucket) > _PENDING_MAX_PER_CHANNEL:
            del bucket[: len(bucket) - _PENDING_MAX_PER_CHANNEL]
        if not bucket:
            self.pending.pop(channel_id, None)

    def enqueue_pending(
        self,
        channel_id: str,
        msg: Any,
    ) -> None:
        import time as _t

        safe_channel_id = _normalize_channel_id(channel_id)
        if safe_channel_id is None:
            return
        sender_id = _normalize_pairing_ref(getattr(msg, "sender_id", "") or "")
        thread_id = _normalize_pairing_ref(getattr(msg, "thread_id", "") or "")
        entry = {
            "sender_id": sender_id or "",
            "thread_id": thread_id or "",
            "content": (getattr(msg, "content", "") or "")[:500],
            "ts": _t.time(),
        }
        self.pending.setdefault(safe_channel_id, []).append(entry)
        self._gc_pending(safe_channel_id)

    def drain_pending(self, channel_id: str) -> list[dict[str, Any]]:
        self._gc_pending(channel_id)
        out = self.pending.get(channel_id, [])
        self.pending[channel_id] = []
        return out

    def metrics(self, channel_id: str) -> dict[str, int]:
        self._gc_pending(channel_id)
        return {
            "pairings_count": len(self.users.get(channel_id, set())),
            "group_count": len(self.groups.get(channel_id, set())),
            "pending_count": len(self.pending.get(channel_id, [])),
        }

    def users_list(self, channel_id: str) -> list[str]:
        return sorted(self.users.get(channel_id, set()))

    def groups_list(self, channel_id: str) -> list[str]:
        return sorted(self.groups.get(channel_id, set()))


def _pairings(manager: Any) -> _PairingStore:
    store = getattr(manager, "_channel_pairings", None)
    if store is None or not isinstance(store, _PairingStore):
        store = _PairingStore()
        with contextlib.suppress(AttributeError):
            manager._channel_pairings = store
    return store


# ═══════════════════════════════════════════════════════════
# State persistence (assignments + pairings)
# ═══════════════════════════════════════════════════════════
#
#
#   {
#     "version": 1,
#     "assignments": {"slack": "coder", "wechat": "general"},
#     "users":  {"slack": ["U1", "U2"]},
#     "groups": {"slack": ["C_ABC"]}
#   }
#


_STATE_SCHEMA_VERSION = 1
_MAX_STATE_FILE_BYTES = 2 * 1024 * 1024
_MAX_CREDENTIALS_FILE_BYTES = 1024 * 1024
_MAX_ASSIGNMENTS = 512
_MAX_PAIRING_CHANNELS = 256
_MAX_PAIRINGS_PER_CHANNEL = 5_000
_MAX_CREDENTIAL_KEYS = 64
_MAX_CREDENTIAL_TOTAL_BYTES = 256 * 1024
_MAX_CREDENTIAL_VALUE_BYTES = 64 * 1024


def _clean_assignments(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for channel_id, agent_id in raw.items():
        if len(out) >= _MAX_ASSIGNMENTS:
            break
        safe_channel_id = _normalize_channel_id(channel_id)
        safe_agent_id = _normalize_agent_id(agent_id)
        if safe_channel_id and safe_agent_id:
            out[safe_channel_id] = safe_agent_id
    return out


def _clean_pairing_map(raw: Any) -> dict[str, set[str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, set[str]] = {}
    for channel_id, ids in raw.items():
        if len(out) >= _MAX_PAIRING_CHANNELS:
            break
        safe_channel_id = _normalize_channel_id(channel_id)
        if not safe_channel_id or not isinstance(ids, (list, set, tuple)):
            continue
        clean_ids: set[str] = set()
        for raw_id in ids:
            if len(clean_ids) >= _MAX_PAIRINGS_PER_CHANNEL:
                break
            safe_id = _normalize_pairing_ref(raw_id)
            if safe_id:
                clean_ids.add(safe_id)
        if clean_ids:
            out[safe_channel_id] = clean_ids
    return out


def _sanitize_credentials_body(body: dict[str, Any]) -> dict[str, Any]:
    if len(body) > _MAX_CREDENTIAL_KEYS:
        raise ValueError(f"too many credential fields (max {_MAX_CREDENTIAL_KEYS})")
    out: dict[str, Any] = {}
    total_bytes = 0
    for key, value in body.items():
        if not isinstance(key, str) or not key or len(key) > 96 or _CONTROL_RE.search(key):
            raise ValueError("invalid credential field name")
        if isinstance(value, str):
            value_bytes = len(value.encode("utf-8"))
            if value_bytes > _MAX_CREDENTIAL_VALUE_BYTES or "\x00" in value:
                raise ValueError(f"credential field {key!r} is too large or invalid")
            total_bytes += value_bytes
            if key == "channel_id":
                safe_channel_id = _normalize_channel_id(value)
                if safe_channel_id is None:
                    raise ValueError("invalid channel_id")
                out[key] = safe_channel_id
            else:
                out[key] = value
        elif value is None or isinstance(value, (bool, int, float)):
            total_bytes += len(str(value).encode("utf-8"))
            out[key] = value
        else:
            raise ValueError(f"credential field {key!r} must be scalar")
        total_bytes += len(key.encode("utf-8"))
        if total_bytes > _MAX_CREDENTIAL_TOTAL_BYTES:
            raise ValueError(
                f"credentials payload too large (max {_MAX_CREDENTIAL_TOTAL_BYTES} bytes)",
            )
    return out


def _clean_credentials_map(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for platform, body in raw.items():
        safe_platform = _normalize_platform_id(platform)
        if safe_platform is None or not isinstance(body, dict):
            continue
        try:
            out[safe_platform] = _sanitize_credentials_body(body)
        except ValueError:
            continue
    return out


def _load_state(manager: Any, state_file: Path | None) -> None:
    if state_file is None or not state_file.exists():
        return
    if _is_oversized_file(state_file, _MAX_STATE_FILE_BYTES):
        logger.warning(
            "channel state load skipped: file too large (> %s bytes)",
            _MAX_STATE_FILE_BYTES,
        )
        return
    try:
        raw = state_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "channel state load failed (%s): %s · starting empty",
            type(e).__name__,
            e,
        )
        return

    if not isinstance(data, dict):
        return
    if data.get("version") != _STATE_SCHEMA_VERSION:
        logger.info(
            "channel state schema v%s unknown · starting empty",
            data.get("version"),
        )
        return

    try:
        target = _assignments_on(manager)
        target.update(_clean_assignments(data.get("assignments") or {}))
    except (AttributeError, TypeError, ValueError):
        logger.exception("channel state: failed to restore assignments")

    try:
        store = _pairings(manager)
        store.users.update(_clean_pairing_map(data.get("users") or {}))
        store.groups.update(_clean_pairing_map(data.get("groups") or {}))
    except (AttributeError, TypeError, ValueError):
        logger.exception("channel state: failed to restore pairings")


def _save_state(manager: Any, state_file: Path | None) -> None:
    if state_file is None:
        return
    try:
        assigns = _assignments_on(manager)
        store = _pairings(manager)
        payload = {
            "version": _STATE_SCHEMA_VERSION,
            "assignments": _clean_assignments(assigns),
            "users": {k: sorted(v) for k, v in _clean_pairing_map(store.users).items()},
            "groups": {k: sorted(v) for k, v in _clean_pairing_map(store.groups).items()},
        }
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(state_file.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, state_file)
    except OSError as e:
        logger.warning("channel state save failed: %s", e)


def _assignments_on(manager: Any) -> dict[str, str]:
    a = getattr(manager, "_channel_assignments", None)
    if a is None:
        a = {}
        try:
            manager._channel_assignments = a
        except (AttributeError, TypeError):
            _FALLBACK_ASSIGNMENTS.clear()
            return _FALLBACK_ASSIGNMENTS
    return a
