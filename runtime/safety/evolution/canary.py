from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from runtime.platform.io import (
    JsonMutation,
    TransactionalFileError,
    mutate_json_file,
    path_transaction,
)

_LOG = logging.getLogger("echo.evolution.canary")
_SAFE_CANARY_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,239}$")
_OUTCOME_RECEIPT_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_OUTCOME_RECEIPTS_KEY = "outcome_receipts"


class CanaryPersistenceError(RuntimeError):
    """A canary mutation could not be proven durable."""


class CanaryOutcomeConflictError(ValueError):
    """One idempotency key was reused for a different outcome."""


class CanaryReceiptLimitError(CanaryPersistenceError):
    """The durable receipt ledger cannot accept another unique outcome."""


class CanaryPhase(StrEnum):
    SHADOW = "shadow"
    CANARY_5 = "canary_5"
    CANARY_25 = "canary_25"
    CANARY_50 = "canary_50"
    FULL = "full"
    ROLLED_BACK = "rolled_back"


@dataclass
class CanaryConfig:
    shadow_runs: int = 10
    shadow_pass_rate: float = 0.70
    promotion_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "canary_5": 0.80,
            "canary_25": 0.80,
            "canary_50": 0.85,
            "full": 0.90,
        }
    )
    sample_window: int = 20
    outcome_receipt_limit: int = 10_000
    rollback_threshold: float = 0.50
    state_dir: str = "data/canary_states"
    auto_rollback_reason: str = "canary threshold breached"
    rollback_handler: Callable[[str, CanaryState, str], Any] | None = None


@dataclass
class CanaryState:
    skill_name: str
    phase: CanaryPhase
    entered_ts: str
    sample_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    current_rate: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


_PHASE_ORDER = [
    CanaryPhase.SHADOW,
    CanaryPhase.CANARY_5,
    CanaryPhase.CANARY_25,
    CanaryPhase.CANARY_50,
    CanaryPhase.FULL,
]


def _safe_canary_name(value: str) -> str | None:
    name = str(value or "").strip()
    if not _SAFE_CANARY_NAME_RE.fullmatch(name):
        return None
    return name


def _require_canary_name(value: str) -> str:
    name = _safe_canary_name(value)
    if name is None:
        raise ValueError(
            "invalid canary skill name: use letters, numbers, dot, underscore, or hyphen"
        )
    return name


class CanaryManager:
    def __init__(self, config: CanaryConfig | None = None) -> None:
        self.config = config or CanaryConfig()
        receipt_limit = self.config.outcome_receipt_limit
        if (
            isinstance(receipt_limit, bool)
            or not isinstance(receipt_limit, int)
            or receipt_limit <= 0
        ):
            raise ValueError("outcome_receipt_limit must be a positive integer")
        self._outcome_receipt_limit = receipt_limit
        self._states: dict[str, CanaryState] = {}
        self._lock = threading.RLock()
        self._state_dir = Path(self.config.state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._load_states()

    def register(
        self,
        skill_name: str,
        *,
        metadata: dict[str, Any] | None = None,
        initial_phase: CanaryPhase = CanaryPhase.SHADOW,
    ) -> CanaryState:
        skill_name = _require_canary_name(skill_name)
        self._reject_reserved_receipt_metadata(metadata)
        path = self._state_path(skill_name)
        with self._lock, path_transaction(path):
            state = self._read_current_locked(skill_name, path)
            if state is not None:
                if metadata:
                    next_state = copy.deepcopy(state)
                    next_state.metadata.update(metadata)
                    self._persist_state(next_state)
                    self._replace_state_locked(state, next_state)
                return state
            next_state = CanaryState(
                skill_name=skill_name,
                phase=CanaryPhase(initial_phase),
                entered_ts=datetime.now().isoformat(timespec="seconds"),
                metadata=metadata or {},
            )
            self._persist_state(next_state)
            self._states[skill_name] = next_state
            return next_state

    def record_outcome(
        self,
        skill_name: str,
        success: bool,
        *,
        outcome_id: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> CanaryState | None:
        """Durably record an outcome, optionally exactly once by ``outcome_id``.

        The idempotency receipt is committed in the same JSON replacement as
        the sample.  Reusing an id for a different boolean outcome fails
        closed instead of silently corrupting the rollout rate.
        """

        skill_name = _safe_canary_name(skill_name) or ""
        if not skill_name:
            return None
        path = self._state_path(skill_name)
        with self._lock, path_transaction(path):
            state = self._read_current_locked(skill_name, path)
            if state is None:
                return None
            receipt_key = self._outcome_receipt_key(outcome_id)
            if receipt_key is not None:
                receipts = self._receipt_map(state)
                if self._receipt_exists(receipts, receipt_key, success=bool(success)):
                    return state
                if len(receipts) >= self._outcome_receipt_limit:
                    raise CanaryReceiptLimitError(
                        "canary outcome receipt hard limit "
                        f"({self._outcome_receipt_limit}) reached; new outcomes are frozen"
                    )
            if state.phase == CanaryPhase.ROLLED_BACK:
                return state

            self._reject_reserved_receipt_metadata(metadata_updates)

            next_state = copy.deepcopy(state)
            self._record_windowed_outcome(next_state, success)

            if (
                next_state.current_rate < self.config.rollback_threshold
                and next_state.sample_count >= 5
            ):
                next_state.phase = CanaryPhase.ROLLED_BACK
                next_state.entered_ts = datetime.now().isoformat(timespec="seconds")
                next_state.metadata["last_rollback_reason"] = self.config.auto_rollback_reason
                _LOG.warning(
                    "canary ROLLBACK for %s: rate=%.2f < threshold=%.2f",
                    skill_name,
                    next_state.current_rate,
                    self.config.rollback_threshold,
                )
            else:
                threshold = self._promotion_threshold(next_state.phase)
                if (
                    next_state.current_rate >= threshold
                    and next_state.sample_count >= self._min_samples(next_state.phase)
                ):
                    self._promote(next_state)

            if receipt_key is not None:
                receipts = self._receipt_map(next_state)
                receipts[receipt_key] = bool(success)
                next_state.metadata[_OUTCOME_RECEIPTS_KEY] = receipts
            if metadata_updates:
                next_state.metadata.update(copy.deepcopy(metadata_updates))
                pending = next_state.metadata.get("registry_sync_pending")
                if isinstance(pending, dict):
                    pending["phase"] = next_state.phase.value
            self._persist_state(next_state)
            self._replace_state_locked(state, next_state)

            if next_state.phase == CanaryPhase.ROLLED_BACK:
                handler = self.config.rollback_handler
                if handler is not None:
                    try:
                        handler(skill_name, state, self.config.auto_rollback_reason)
                    except Exception as exc:  # noqa: BLE001
                        _LOG.warning("canary rollback handler failed for %s: %s", skill_name, exc)
            return state

    def update_metadata(
        self,
        skill_name: str,
        *,
        updates: dict[str, Any] | None = None,
        remove: tuple[str, ...] = (),
    ) -> CanaryState | None:
        """Durably amend coordination metadata without changing samples."""

        skill_name = _safe_canary_name(skill_name) or ""
        if not skill_name:
            return None
        self._reject_reserved_receipt_metadata(updates, remove=remove)
        path = self._state_path(skill_name)
        with self._lock, path_transaction(path):
            state = self._read_current_locked(skill_name, path)
            if state is None:
                return None
            next_state = copy.deepcopy(state)
            if updates:
                next_state.metadata.update(copy.deepcopy(updates))
            for key in remove:
                next_state.metadata.pop(key, None)
            if next_state.metadata == state.metadata:
                return state
            self._persist_state(next_state)
            self._replace_state_locked(state, next_state)
            return state

    def get_state(self, skill_name: str) -> CanaryState | None:
        skill_name = _safe_canary_name(skill_name) or ""
        if not skill_name:
            return None
        with self._lock:
            return self._states.get(skill_name)

    def refresh(self, skill_name: str) -> CanaryState | None:
        """Reload one state written by another manager/process."""

        skill_name = _safe_canary_name(skill_name) or ""
        if not skill_name:
            return None
        path = self._state_path(skill_name)
        with self._lock, path_transaction(path):
            if not path.exists():
                self._states.pop(skill_name, None)
                return None
            state = self._read_state_path(path, strict=True)
            assert state is not None
            current = self._states.get(skill_name)
            if current is None:
                self._states[skill_name] = state
                return state
            self._replace_state_locked(current, state)
            return current

    def should_route_to_skill(self, skill_name: str) -> bool:
        skill_name = _safe_canary_name(skill_name) or ""
        if not skill_name:
            return False
        with self._lock:
            state = self._states.get(skill_name)
            if state is None:
                return True
            if state.phase == CanaryPhase.ROLLED_BACK:
                return False
            if state.phase == CanaryPhase.FULL:
                return True

            import random

            traffic_pct = self._traffic_percent(state.phase)
            return random.random() < traffic_pct

    def list_active(self) -> list[CanaryState]:
        with self._lock:
            return [
                s
                for s in self._states.values()
                if s.phase not in (CanaryPhase.FULL, CanaryPhase.ROLLED_BACK)
            ]

    def list_all(self) -> list[CanaryState]:
        with self._lock:
            return list(self._states.values())

    def force_rollback(
        self,
        skill_name: str,
        *,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CanaryState | None:
        skill_name = _safe_canary_name(skill_name) or ""
        if not skill_name:
            return None
        self._reject_reserved_receipt_metadata(metadata)
        path = self._state_path(skill_name)
        with self._lock, path_transaction(path):
            state = self._read_current_locked(skill_name, path)
            if state is None:
                return None
            next_state = copy.deepcopy(state)
            next_state.phase = CanaryPhase.ROLLED_BACK
            next_state.entered_ts = datetime.now().isoformat(timespec="seconds")
            next_state.metadata["last_rollback_reason"] = (
                reason or next_state.metadata.get("last_rollback_reason") or "operator rollback"
            )
            if metadata:
                next_state.metadata.update(copy.deepcopy(metadata))
            self._persist_state(next_state)
            self._replace_state_locked(state, next_state)
            return state

    def _promote(self, state: CanaryState) -> None:
        idx = _PHASE_ORDER.index(state.phase) if state.phase in _PHASE_ORDER else -1
        if idx < len(_PHASE_ORDER) - 1:
            old_phase = state.phase
            state.phase = _PHASE_ORDER[idx + 1]
            state.entered_ts = datetime.now().isoformat(timespec="seconds")
            state.sample_count = 0
            state.success_count = 0
            state.failure_count = 0
            state.current_rate = 0.0
            state.metadata["outcome_window"] = []
            _LOG.info(
                "canary PROMOTE %s: %s -> %s",
                state.skill_name,
                old_phase.value,
                state.phase.value,
            )

    @staticmethod
    def _traffic_percent(phase: CanaryPhase) -> float:
        traffic = {
            CanaryPhase.SHADOW: 0.0,
            CanaryPhase.CANARY_5: 0.05,
            CanaryPhase.CANARY_25: 0.25,
            CanaryPhase.CANARY_50: 0.50,
            CanaryPhase.FULL: 1.0,
            CanaryPhase.ROLLED_BACK: 0.0,
        }
        return traffic.get(phase, 0.0)

    @staticmethod
    def _min_samples(phase: CanaryPhase) -> int:
        minimums = {
            CanaryPhase.SHADOW: 10,
            CanaryPhase.CANARY_5: 20,
            CanaryPhase.CANARY_25: 40,
            CanaryPhase.CANARY_50: 60,
        }
        return minimums.get(phase, 10)

    def _promotion_threshold(self, phase: CanaryPhase) -> float:
        if phase == CanaryPhase.SHADOW:
            return self.config.shadow_pass_rate
        return self.config.promotion_thresholds.get(phase.value, 0.80)

    def _record_windowed_outcome(self, state: CanaryState, success: bool) -> None:
        window = self._outcome_window(state)
        window.append(bool(success))
        sample_window = max(1, int(self.config.sample_window or 1))
        if len(window) > sample_window:
            window = window[-sample_window:]
        state.metadata["outcome_window"] = window
        self._sync_counts_from_window(state)

    @staticmethod
    def _outcome_window(state: CanaryState) -> list[bool]:
        raw = state.metadata.get("outcome_window") if isinstance(state.metadata, dict) else None
        if isinstance(raw, list):
            return [bool(item) for item in raw]
        if state.sample_count <= 0:
            return []
        successes = max(0, min(state.success_count, state.sample_count))
        failures = max(0, min(state.failure_count, state.sample_count - successes))
        return [True] * successes + [False] * failures

    @staticmethod
    def _sync_counts_from_window(state: CanaryState) -> None:
        window = CanaryManager._outcome_window(state)
        state.sample_count = len(window)
        state.success_count = sum(1 for item in window if item)
        state.failure_count = state.sample_count - state.success_count
        state.current_rate = state.success_count / max(1, state.sample_count)

    def _persist_state(self, state: CanaryState) -> None:
        if _safe_canary_name(state.skill_name) is None:
            raise CanaryPersistenceError(
                f"refusing to persist unsafe canary state name: {state.skill_name!r}"
            )
        path = self._state_path(state.skill_name)
        self._receipt_map(state)

        def _validate(value: Any) -> None:
            if not isinstance(value, dict):
                raise TransactionalFileError("canary state root is not an object")

        def _replace(value: dict[str, Any]) -> JsonMutation[None]:
            value.clear()
            value.update(asdict(state))
            return JsonMutation(None)

        try:
            mutate_json_file(
                path,
                default_factory=dict,
                validate=_validate,
                mutate=_replace,
                indent=2,
            )
        except (OSError, TransactionalFileError) as exc:
            raise CanaryPersistenceError(f"canary state is not durable: {path}") from exc

    def _load_states(self) -> None:
        if not self._state_dir.exists():
            return
        for path in self._state_dir.glob("*.json"):
            try:
                state = self._read_state_path(path, strict=True)
            except CanaryPersistenceError:
                # Preserve the historical quarantine behavior for a payload
                # that advertises an unsafe path-like skill name. All other
                # schema/receipt corruption is a hard load failure: silently
                # treating a broken active state as absent can route traffic.
                if self._has_unsafe_persisted_name(path):
                    _LOG.warning("skipping unsafe persisted canary state: %s", path.name)
                    continue
                raise
            assert state is not None
            self._states[state.skill_name] = state

    def _read_state_path(self, path: Path, *, strict: bool) -> CanaryState | None:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            name = _require_canary_name(d.get("skill_name", path.stem))
            if name != path.stem:
                raise ValueError("canary state name does not match its file")
            metadata = d.get("metadata", {})
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, dict):
                raise ValueError("canary metadata is not an object")
            state = CanaryState(
                skill_name=name,
                phase=CanaryPhase(d.get("phase", "shadow")),
                entered_ts=d.get("entered_ts", ""),
                sample_count=int(d.get("sample_count", 0) or 0),
                success_count=int(d.get("success_count", 0) or 0),
                failure_count=int(d.get("failure_count", 0) or 0),
                current_rate=float(d.get("current_rate", 0.0) or 0.0),
                metadata=metadata,
            )
            self._receipt_map(state)
            CanaryManager._sync_counts_from_window(state)
            return state
        except Exception as exc:  # noqa: BLE001 — normalized below
            if strict:
                raise CanaryPersistenceError(f"canary state is unreadable: {path}") from exc
            _LOG.debug("canary result parse failed: %s", exc)
            return None

    @staticmethod
    def _has_unsafe_persisted_name(path: Path) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return False
            return _safe_canary_name(payload.get("skill_name", path.stem)) is None
        except (OSError, TypeError, ValueError):
            return False

    def _state_path(self, skill_name: str) -> Path:
        return self._state_dir / f"{skill_name}.json"

    def _read_current_locked(self, skill_name: str, path: Path) -> CanaryState | None:
        if path.exists():
            state = self._read_state_path(path, strict=True)
            assert state is not None
            current = self._states.get(skill_name)
            if current is None:
                self._states[skill_name] = state
                return state
            self._replace_state_locked(current, state)
            return current
        return self._states.get(skill_name)

    @staticmethod
    def _replace_state_locked(current: CanaryState, next_state: CanaryState) -> None:
        # Preserve the identity historically returned by ``register`` while
        # ensuring observers see only a state whose atomic write succeeded.
        current.phase = next_state.phase
        current.entered_ts = next_state.entered_ts
        current.sample_count = next_state.sample_count
        current.success_count = next_state.success_count
        current.failure_count = next_state.failure_count
        current.current_rate = next_state.current_rate
        current.metadata = next_state.metadata

    @staticmethod
    def _outcome_receipt_key(outcome_id: str | None) -> str | None:
        resolved = str(outcome_id or "").strip()
        if not resolved:
            return None
        return hashlib.sha256(resolved.encode("utf-8")).hexdigest()

    def _receipt_map(self, state: CanaryState) -> dict[str, bool]:
        raw = state.metadata.get(_OUTCOME_RECEIPTS_KEY)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise CanaryPersistenceError("canary outcome receipt ledger is malformed")
        if len(raw) > self._outcome_receipt_limit:
            raise CanaryPersistenceError(
                "canary outcome receipt ledger exceeds configured hard limit"
            )
        receipts: dict[str, bool] = {}
        for key, value in raw.items():
            if (
                not isinstance(key, str)
                or not _OUTCOME_RECEIPT_KEY_RE.fullmatch(key)
                or not isinstance(value, bool)
            ):
                raise CanaryPersistenceError("canary outcome receipt ledger is malformed")
            receipts[key] = value
        return receipts

    @staticmethod
    def _receipt_exists(
        receipts: dict[str, bool],
        receipt_key: str,
        *,
        success: bool,
    ) -> bool:
        if receipt_key not in receipts:
            return False
        if receipts[receipt_key] is not success:
            raise CanaryOutcomeConflictError(
                "canary outcome id was already settled with a different result"
            )
        return True

    @staticmethod
    def _reject_reserved_receipt_metadata(
        updates: dict[str, Any] | None,
        *,
        remove: tuple[str, ...] = (),
    ) -> None:
        if (updates and _OUTCOME_RECEIPTS_KEY in updates) or _OUTCOME_RECEIPTS_KEY in remove:
            raise ValueError(
                "outcome_receipts is managed internally and cannot be overwritten or removed"
            )


__all__ = [
    "CanaryConfig",
    "CanaryManager",
    "CanaryOutcomeConflictError",
    "CanaryPhase",
    "CanaryPersistenceError",
    "CanaryReceiptLimitError",
    "CanaryState",
]
