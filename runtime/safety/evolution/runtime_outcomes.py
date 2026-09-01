"""Durably attribute real turn outcomes to governed canary candidates.

Candidate activation is part of the data plane: once a canary changes a turn,
the corresponding outcome must remain recoverable even if the worker crashes
before the gateway settles that turn. This module therefore keeps a small,
bounded inbox under the application data directory. Every mutation is a
locked read/validate/atomic-replace transaction with a file and directory
``fsync`` barrier.

The inbox deliberately stores only a SHA-256 turn key. Tenant, actor, thread,
and turn identifiers never appear in a filename or in the persisted payload.
The authoritative raw ``turn_id`` is supplied by the gateway at settlement
time and is passed unchanged to the canary receipt ledger as ``outcome_id``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runtime.platform.io import JsonMutation, TransactionalFileError, mutate_json_file
from runtime.safety.evolution.candidate_registry import CandidateStatus

_LOG = logging.getLogger("echo.evolution.runtime_outcomes")
_SCHEMA = "echo.evolution.runtime_outcomes.v1"
_MAX_TRACKED_TURNS = 10_000
_MAX_CANDIDATES_PER_TURN = 256
_MAX_CANDIDATE_ID_LENGTH = 512
_ACTIVATION_TTL_S = 24 * 60 * 60
_UNGRADABLE = "ungradable"

# A path hint is only a same-process compatibility aid for callers which use a
# test/private inbox. Keys are opaque digests, never raw turn identifiers, and
# the durable file remains authoritative.
_PATH_HINTS: dict[str, Path] = {}
_PATH_HINTS_LOCK = threading.RLock()


class RuntimeOutcomePersistenceError(RuntimeError):
    """The activation inbox could not be proven valid and durable."""


class RuntimeOutcomeConflictError(ValueError):
    """One turn was presented with two different terminal outcomes."""


def _current_turn_id() -> str:
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        value = getattr(session, "turn_id", None) if session is not None else None
        return str(value).strip() if value else ""
    except (ImportError, AttributeError, TypeError):
        return ""


def _default_inbox_path() -> Path:
    from runtime.platform.process.paths import app_paths

    return app_paths().candidate_runtime_outcomes_path


def _resolve_inbox_path(
    inbox_path: str | Path | None,
    *,
    turn_key: str | None = None,
) -> Path:
    if inbox_path is not None:
        return Path(inbox_path).expanduser().resolve(strict=False)
    if turn_key:
        with _PATH_HINTS_LOCK:
            hinted = _PATH_HINTS.get(turn_key)
        if hinted is not None:
            return hinted
    return _default_inbox_path().expanduser().resolve(strict=False)


def _remember_path(turn_key: str, path: Path) -> None:
    with _PATH_HINTS_LOCK:
        _PATH_HINTS[turn_key] = path
        # Hints are not durable state. Keep them bounded without ever putting a
        # raw identity in memory keys.
        overflow = len(_PATH_HINTS) - _MAX_TRACKED_TURNS
        if overflow > 0:
            for key in list(_PATH_HINTS)[:overflow]:
                _PATH_HINTS.pop(key, None)


def _turn_key(turn_id: str) -> str:
    resolved = str(turn_id or "").strip()
    if not resolved:
        return ""
    return hashlib.sha256(
        b"echo.evolution.runtime-outcome.turn.v1\0" + resolved.encode("utf-8")
    ).hexdigest()


def _empty_inbox() -> dict[str, Any]:
    return {"schema": _SCHEMA, "entries": {}}


def _validate_inbox(value: Any) -> None:
    if not isinstance(value, dict):
        raise RuntimeOutcomePersistenceError("candidate outcome inbox root is not an object")
    if value.get("schema") != _SCHEMA:
        raise RuntimeOutcomePersistenceError("candidate outcome inbox schema is invalid")
    entries = value.get("entries")
    if not isinstance(entries, dict):
        raise RuntimeOutcomePersistenceError("candidate outcome inbox entries are invalid")
    if len(entries) > _MAX_TRACKED_TURNS:
        raise RuntimeOutcomePersistenceError("candidate outcome inbox exceeds its hard bound")
    for key, raw in entries.items():
        if (
            not isinstance(key, str)
            or len(key) != 64
            or any(char not in "0123456789abcdef" for char in key)
        ):
            raise RuntimeOutcomePersistenceError("candidate outcome inbox has an invalid turn key")
        if not isinstance(raw, dict):
            raise RuntimeOutcomePersistenceError("candidate outcome inbox entry is not an object")
        if set(raw) != {"created_at", "updated_at", "candidate_ids", "outcome"}:
            raise RuntimeOutcomePersistenceError("candidate outcome inbox entry shape is invalid")
        created_at = raw.get("created_at")
        updated_at = raw.get("updated_at")
        if (
            not isinstance(created_at, (int, float))
            or isinstance(created_at, bool)
            or not math.isfinite(float(created_at))
            or float(created_at) < 0
            or not isinstance(updated_at, (int, float))
            or isinstance(updated_at, bool)
            or not math.isfinite(float(updated_at))
            or float(updated_at) < float(created_at)
        ):
            raise RuntimeOutcomePersistenceError("candidate outcome inbox timestamp is invalid")
        candidate_ids = raw.get("candidate_ids")
        if (
            not isinstance(candidate_ids, list)
            or len(candidate_ids) > _MAX_CANDIDATES_PER_TURN
            or candidate_ids != sorted(set(candidate_ids))
            or any(
                not isinstance(candidate_id, str)
                or not candidate_id.strip()
                or candidate_id != candidate_id.strip()
                or len(candidate_id) > _MAX_CANDIDATE_ID_LENGTH
                for candidate_id in candidate_ids
            )
        ):
            raise RuntimeOutcomePersistenceError("candidate outcome inbox candidate set is invalid")
        outcome = raw.get("outcome")
        if outcome is not None and not isinstance(outcome, bool) and outcome != _UNGRADABLE:
            raise RuntimeOutcomePersistenceError("candidate outcome inbox outcome is invalid")
        if outcome == _UNGRADABLE and candidate_ids:
            raise RuntimeOutcomePersistenceError(
                "ungradable candidate outcome inbox entry is not terminal"
            )


def _prune_entries(entries: dict[str, Any], now: float) -> bool:
    expired = [
        key
        for key, raw in entries.items()
        if raw["outcome"] is not None
        and not raw["candidate_ids"]
        and now - float(raw["updated_at"]) > _ACTIVATION_TTL_S
    ]
    for key in expired:
        entries.pop(key, None)
        with _PATH_HINTS_LOCK:
            _PATH_HINTS.pop(key, None)
    return bool(expired)


def _mutate_inbox(
    path: Path,
    mutate: Callable[[dict[str, Any], float], JsonMutation[Any]],
) -> Any:
    now = time.time()

    def _apply(value: dict[str, Any]) -> JsonMutation[Any]:
        pruned = _prune_entries(value["entries"], now)
        result = mutate(value, now)
        return JsonMutation(result.value, changed=result.changed or pruned)

    try:
        return mutate_json_file(
            path,
            default_factory=_empty_inbox,
            validate=_validate_inbox,
            mutate=_apply,
            mode=0o600,
            indent=1,
        )
    except RuntimeOutcomeConflictError:
        raise
    except RuntimeOutcomePersistenceError:
        raise
    except (OSError, TransactionalFileError, TypeError, ValueError) as exc:
        raise RuntimeOutcomePersistenceError(
            f"candidate outcome inbox is not durable: {path}"
        ) from exc


def record_runtime_candidate_activation(
    candidate_id: str,
    *,
    turn_id: str | None = None,
    inbox_path: str | Path | None = None,
) -> bool:
    """Persist that one canary candidate materially affected the turn.

    ``False`` is a fail-closed result: runtime wiring must not let a canary
    affect a real turn when its activation cannot first be proven durable.
    """

    resolved_candidate = str(candidate_id or "").strip()
    resolved_turn = str(turn_id or _current_turn_id()).strip()
    key = _turn_key(resolved_turn)
    if not resolved_candidate or len(resolved_candidate) > _MAX_CANDIDATE_ID_LENGTH or not key:
        return False
    path = _resolve_inbox_path(inbox_path, turn_key=key)

    def _record(value: dict[str, Any], now: float) -> JsonMutation[bool]:
        entries: dict[str, Any] = value["entries"]
        entry = entries.get(key)
        if entry is None:
            if len(entries) >= _MAX_TRACKED_TURNS:
                # Never evict a non-expired, unsettled activation to make room:
                # declining the new canary is the only fail-closed choice.
                return JsonMutation(False, changed=False)
            entries[key] = {
                "created_at": now,
                "updated_at": now,
                "candidate_ids": [resolved_candidate],
                "outcome": None,
            }
            return JsonMutation(True)
        if entry["outcome"] is not None:
            return JsonMutation(False, changed=False)
        candidates = list(entry["candidate_ids"])
        if resolved_candidate in candidates:
            return JsonMutation(True, changed=False)
        if len(candidates) >= _MAX_CANDIDATES_PER_TURN:
            return JsonMutation(False, changed=False)
        candidates.append(resolved_candidate)
        entry["candidate_ids"] = sorted(candidates)
        entry["updated_at"] = now
        return JsonMutation(True)

    try:
        recorded = bool(_mutate_inbox(path, _record))
    except RuntimeOutcomePersistenceError as exc:
        _LOG.error("candidate activation persistence failed closed: %s", type(exc).__name__)
        return False
    if recorded:
        _remember_path(key, path)
    return recorded


def active_runtime_candidates(
    turn_id: str,
    *,
    inbox_path: str | Path | None = None,
) -> tuple[str, ...]:
    """Return a stable diagnostic snapshot without consuming it."""

    key = _turn_key(turn_id)
    if not key:
        return ()
    path = _resolve_inbox_path(inbox_path, turn_key=key)

    def _read(value: dict[str, Any], _now: float) -> JsonMutation[tuple[str, ...]]:
        entry = value["entries"].get(key)
        if entry is None:
            return JsonMutation((), changed=False)
        return JsonMutation(tuple(entry["candidate_ids"]), changed=False)

    try:
        return tuple(_mutate_inbox(path, _read))
    except RuntimeOutcomePersistenceError as exc:
        _LOG.error("candidate activation read failed closed: %s", type(exc).__name__)
        return ()


def _prepare_settlement(
    path: Path,
    key: str,
    success: bool | None,
) -> tuple[str, ...]:
    requested: bool | str = _UNGRADABLE if success is None else bool(success)

    def _prepare(value: dict[str, Any], now: float) -> JsonMutation[tuple[str, ...]]:
        entry = value["entries"].get(key)
        if entry is None:
            return JsonMutation((), changed=False)
        current = entry["outcome"]
        if current is not None and current != requested:
            raise RuntimeOutcomeConflictError(
                "runtime candidate turn was already settled with a different outcome"
            )
        candidate_ids = tuple(entry["candidate_ids"])
        if current is None:
            entry["outcome"] = requested
            entry["updated_at"] = now
            if requested == _UNGRADABLE:
                entry["candidate_ids"] = []
            return JsonMutation(candidate_ids)
        return JsonMutation(candidate_ids, changed=False)

    return tuple(_mutate_inbox(path, _prepare))


def _acknowledge_candidates(
    path: Path,
    key: str,
    candidate_ids: set[str],
    *,
    success: bool,
) -> None:
    if not candidate_ids:
        return

    def _ack(value: dict[str, Any], now: float) -> JsonMutation[None]:
        entry = value["entries"].get(key)
        if entry is None:
            return JsonMutation(None, changed=False)
        if entry["outcome"] is not success:
            raise RuntimeOutcomeConflictError(
                "runtime candidate acknowledgement conflicts with settlement"
            )
        remaining = [
            candidate_id
            for candidate_id in entry["candidate_ids"]
            if candidate_id not in candidate_ids
        ]
        if remaining == entry["candidate_ids"]:
            return JsonMutation(None, changed=False)
        entry["candidate_ids"] = remaining
        entry["updated_at"] = now
        return JsonMutation(None)

    _mutate_inbox(path, _ack)


def settle_runtime_candidate_outcomes(
    turn_id: str,
    *,
    success: bool | None,
    registry: Any = None,
    state_dir: Any = None,
    inbox_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Settle persisted activations exactly once by the authoritative turn id.

    ``success=None`` is used for cancellation, pause, or interruption. Such a
    turn becomes a durable ungradable tombstone and never changes canary score.
    A crash after a canary receipt is committed but before inbox acknowledgement
    is harmless: retrying passes the same raw ``turn_id`` as ``outcome_id`` and
    the canary ledger returns the existing receipt without incrementing again.
    """

    resolved_turn = str(turn_id or "").strip()
    key = _turn_key(resolved_turn)
    if not key:
        return []
    path = _resolve_inbox_path(inbox_path, turn_key=key)
    candidate_ids = list(_prepare_settlement(path, key, success))
    if not candidate_ids:
        return []
    _remember_path(key, path)
    if success is None:
        return [
            {"candidate_id": candidate_id, "recorded": False, "reason": "ungradable_turn"}
            for candidate_id in candidate_ids
        ]

    try:
        if registry is None or state_dir is None:
            from runtime.platform.process.paths import app_paths
            from runtime.safety.evolution.candidate_registry import CandidateRegistry

            paths = app_paths()
            registry = registry or CandidateRegistry(paths.evolution_candidates_path)
            state_dir = state_dir or paths.candidate_canary_state_dir

        from runtime.safety.evolution.candidate_canary import CandidateCanaryManager

        manager = CandidateCanaryManager(registry, state_dir)
    except Exception as exc:  # noqa: BLE001 - persisted activations remain retryable
        return [
            {
                "candidate_id": candidate_id,
                "recorded": False,
                "reason": type(exc).__name__,
            }
            for candidate_id in candidate_ids
        ]

    results: list[dict[str, Any]] = []
    acknowledged: set[str] = set()
    for candidate_id in sorted(candidate_ids):
        try:
            candidate = registry.get(candidate_id)
        except Exception as exc:  # noqa: BLE001 - corrupt/unavailable registry is retryable
            results.append(
                {
                    "candidate_id": candidate_id,
                    "recorded": False,
                    "reason": type(exc).__name__,
                }
            )
            continue
        if candidate is None:
            results.append(
                {"candidate_id": candidate_id, "recorded": False, "reason": "missing_candidate"}
            )
            continue
        if candidate.status not in {
            CandidateStatus.CANARY,
            CandidateStatus.PROMOTED,
            CandidateStatus.ROLLED_BACK,
        }:
            # This activation cannot become scoreable through retry. Retain the
            # outcome tombstone, but remove the terminal candidate reference.
            acknowledged.add(candidate_id)
            results.append(
                {
                    "candidate_id": candidate_id,
                    "recorded": False,
                    "reason": f"status_{candidate.status.value}",
                }
            )
            continue
        try:
            wire = manager.record_outcome(
                candidate_id,
                bool(success),
                outcome_id=resolved_turn,
            )
        except Exception as exc:  # noqa: BLE001 - failed durability remains in inbox
            results.append(
                {
                    "candidate_id": candidate_id,
                    "recorded": False,
                    "reason": type(exc).__name__,
                }
            )
        else:
            acknowledged.add(candidate_id)
            if candidate.status == CandidateStatus.CANARY:
                results.append(
                    {
                        "candidate_id": candidate_id,
                        "recorded": True,
                        "status": wire["candidate"]["status"],
                        "phase": (wire.get("canary") or {}).get("phase"),
                    }
                )
            else:
                results.append(
                    {
                        "candidate_id": candidate_id,
                        "recorded": False,
                        "reason": f"status_{candidate.status.value}",
                    }
                )
    # Acknowledgement is a second durable commit by design. If it fails, the
    # receipt remains in the canary state and the inbox references remain for a
    # restart/retry, where outcome_id=turn_id makes the operation idempotent.
    _acknowledge_candidates(path, key, acknowledged, success=bool(success))
    return results


__all__ = [
    "RuntimeOutcomeConflictError",
    "RuntimeOutcomePersistenceError",
    "active_runtime_candidates",
    "record_runtime_candidate_activation",
    "settle_runtime_candidate_outcomes",
]
