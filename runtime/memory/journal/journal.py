from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel

from runtime.platform.models import (
    AntigenSignature,
    ArmId,
    CostEntry,
    ImmuneVerdict,
    Source,
    Step,
    TaskId,
    Trajectory,
    new_id,
    now_utc,
)
from runtime.safety.auth.scope import TenantScope
from runtime.safety.invariants import AppendOnlyList
from runtime.safety.invariants.enforce import enforces

from ._chunk_rows import (
    MIN_RUN,
    chunk_packing_enabled,
    classify_chunk,
    continues_chunk_run,
    expand_chunk_row,
    is_chunk_row,
    pack_chunk_row,
)
from ._journal_base import Journal
from ._journal_models import (
    CURRENT_SCHEMA_VERSION,
    AssistantChunkEvent,
    BrowserArtifactEvent,
    BudgetBreakerResetEvent,
    BudgetEvent,
    CurriculumGoalDecisionEvent,
    FileOpEvent,
    FileRollbackEvent,
    HookInvokedEvent,
    HookResultEvent,
    ImmuneEvent,
    JournalEvent,
    JournalEventType,
    McpProposalDecisionEvent,
    NodeStartedEvent,
    PreviewRefreshEvent,
    ProtocolDriftDecisionEvent,
    ReactCheckpointEvent,
    ReflexHitEvent,
    SkillProposalDecisionEvent,
    StepEvent,
    SubSessionSummaryEvent,
    SubTextDeltaEvent,
    SubToolEndEvent,
    SubToolStartEvent,
    TaskCheckpointEvent,
    TaskPausedEvent,
    TaskResumedEvent,
    TaskStartedEvent,
    TokenUsageEvent,
    ToolEffectIntentEvent,
    ToolEffectReconciliationEvent,
    TrajectoryEvent,
)
from ._journal_parse import _parse_event, _parse_event_data


def _lock_windows_fd(fd: int, mode_name: str) -> None:
    """Call the Windows-only ``msvcrt.locking`` API without making POSIX
    type-checking depend on attributes absent from its platform stubs."""

    import msvcrt

    namespace = vars(msvcrt)
    namespace["locking"](fd, namespace[mode_name], 1)


def _fsync_parent_directory(
    path: Path,
    *,
    require_durability: bool,
    transaction_label: str,
) -> None:
    """Persist a file creation or rename in its parent directory on POSIX.

    ``fsync(file)`` makes file contents durable but does not commit a newly
    created directory entry or a later ``replace``. Atomic trajectory writes
    therefore use this helper before advancing their durable dedupe ledger.

    Python does not expose a portable Windows directory-handle fsync. Windows
    retains the existing file-handle flush/atomic-replace guarantee here; a
    stronger power-loss guarantee would require a native ``CreateFileW`` /
    ``FlushFileBuffers`` directory-handle implementation. Ordinary telemetry
    remains best-effort on every platform, while POSIX atomic transactions
    fail closed if the directory barrier cannot be established.
    """

    import os as _os

    if _os.name != "posix":
        return
    flags = _os.O_RDONLY | getattr(_os, "O_DIRECTORY", 0)
    try:
        directory_fd = _os.open(path.parent, flags)
    except OSError as exc:
        if require_durability:
            raise JournalTransactionError(
                f"cannot open durable {transaction_label} directory"
            ) from exc
        return
    try:
        _os.fsync(directory_fd)
    except OSError as exc:
        if require_durability:
            raise JournalTransactionError(
                f"cannot fsync durable {transaction_label} directory"
            ) from exc
    finally:
        with contextlib.suppress(OSError):
            _os.close(directory_fd)


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "AntigenSignature",
    "ArmId",
    "CostEntry",
    "ImmuneVerdict",
    "Source",
    "Step",
    "TaskId",
    "Trajectory",
    "new_id",
    "now_utc",
    "Journal",
    "JournalTransactionError",
    "TrajectoryConflictError",
    "JournalEvent",
    "JournalEventType",
    "AssistantChunkEvent",
    "StepEvent",
    "TrajectoryEvent",
    "ImmuneEvent",
    "BudgetEvent",
    "BudgetBreakerResetEvent",
    "TaskStartedEvent",
    "NodeStartedEvent",
    "TaskCheckpointEvent",
    "ReactCheckpointEvent",
    "ToolEffectIntentEvent",
    "ToolEffectReconciliationEvent",
    "TaskPausedEvent",
    "TaskResumedEvent",
    "TokenUsageEvent",
    "FileOpEvent",
    "FileRollbackEvent",
    "HookInvokedEvent",
    "HookResultEvent",
    "PreviewRefreshEvent",
    "ReflexHitEvent",
    "SkillProposalDecisionEvent",
    "CurriculumGoalDecisionEvent",
    "McpProposalDecisionEvent",
    "ProtocolDriftDecisionEvent",
    "SubSessionSummaryEvent",
    "SubTextDeltaEvent",
    "SubToolStartEvent",
    "SubToolEndEvent",
    "BrowserArtifactEvent",
    "InMemoryJournal",
    "JSONLJournal",
]


# ═══════════════════════════════════════════════════════════
# journal.py · concrete journal implementations.
#
#   §1  InMemoryJournal                                  ~L85
#   §2  JSONLJournal (file-backed, the production impl)  ~L108
#
# Shared building blocks live in sibling submodules:
#   - _journal_models.py — JournalEventType, CURRENT_SCHEMA_VERSION,
#     and every per-event-type Pydantic model (StepEvent, ...).
#   - _journal_base.py   — the abstract ``Journal`` base + all
#     ``write_*`` convenience methods.
#   - _journal_parse.py  — ``_EVENT_CLASSES``, ``_migrate_event``,
#     ``_parse_event`` (schema migration + JSONL parsing).
#
# All public names are re-exported here so ``from
# runtime.memory.journal.journal import ...`` keeps working.
# ═══════════════════════════════════════════════════════════


def _refresh_session_index(
    index: dict[str, list[JournalEvent]],
    events: list[JournalEvent],
    upto: int,
) -> None:
    """Extend a per-session index with events past ``upto`` (audit P-04)."""
    for event in events[upto:]:
        sid = str(getattr(event, "session_id", "") or "")
        if sid:
            index.setdefault(sid, []).append(event)


class JournalTransactionError(RuntimeError):
    """A durable journal transaction could not be established or verified."""


class TrajectoryConflictError(JournalTransactionError):
    """One idempotency key was reused for a different terminal payload."""


@dataclass(frozen=True)
class _TrajectoryDedupeState:
    state: str
    payload_digest: str | None


def _trajectory_dedupe_key(event: TrajectoryEvent) -> tuple[str, str, str, str]:
    """Return the exact ownership-scoped identity of one trajectory sample."""

    envelope_task_id = event.task_id
    trajectory_task_id = event.trajectory.task_id
    if envelope_task_id is not None and str(envelope_task_id) != str(trajectory_task_id):
        raise ValueError("trajectory event task_id does not match trajectory.task_id")
    task_id = str(envelope_task_id or trajectory_task_id)
    strategy_id = str(event.trajectory.strategy_id or "default")
    tenant_id = str(event.tenant_id or "")
    owner_actor_id = str(event.owner_actor_id or "")
    return task_id, strategy_id, tenant_id, owner_actor_id


def _trajectory_dedupe_digest(key: tuple[str, str, str, str]) -> str:
    """Hash an ownership key so the durable ledger does not expose identities."""

    canonical = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _trajectory_payload_digest(event: TrajectoryEvent) -> str:
    """Hash the canonical, durable terminal semantics for conflict checks.

    Envelope/trajectory UUIDs and wall-clock timestamps are deliberately
    excluded: independent workers finalizing the same task manufacture those
    locally. Terminal outcome, ordered steps, cost (except nondeterministic
    latency), ownership scope, agent and conversation remain bound to the
    reservation, so a success/failure or payload mismatch fails closed.
    """

    payload = event.model_dump(mode="json")
    payload.pop("event_id", None)
    payload.pop("ts", None)
    trajectory = payload.get("trajectory")
    if isinstance(trajectory, dict):
        trajectory.pop("trajectory_id", None)
        trajectory.pop("started_at", None)
        trajectory.pop("completed_at", None)
        steps = trajectory.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                result = step.get("result")
                if not isinstance(result, dict) or result.get("execution_source") != (
                    "native_bridge_preflight"
                ):
                    continue
                # Bridge/preflight attempts never cross ToolExecutor, so their
                # receipt models manufacture local UUIDs and timestamps while
                # assembling the terminal aggregate.  Those values differ when
                # another worker reconstructs the same reserved attempt after a
                # crash; the provider ordinal/node, action args and typed result
                # below remain the durable semantic identity.
                step.pop("ts", None)
                action = step.get("action")
                if isinstance(action, dict):
                    action.pop("call_id", None)
                    action.pop("ts", None)
                result.pop("call_id", None)
                result.pop("ts", None)
        outcome = trajectory.get("outcome")
        if isinstance(outcome, dict):
            cost = outcome.get("cost")
            if isinstance(cost, dict):
                cost.pop("latency_ms", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InMemoryJournal(Journal):
    def __init__(self, max_events: int = 0) -> None:
        """In-memory journal.

        ``max_events`` (audit R-04): ring-buffer cap. ``0`` keeps the old
        unbounded behaviour; a positive value drops the OLDEST events once
        the cap is reached so a long-running process's journal cannot grow
        without limit. ``CC-5`` continues to guard each append as a pure
        append (no in-place mutation); capacity eviction is an explicit
        ring policy applied outside the guarded call.
        """
        self._events = AppendOnlyList[JournalEvent](rule_id="CC-5")
        self._max_events = max(0, int(max_events))
        self._lock = Lock()
        # Audit P-04: incremental per-session index so projections consume
        # only a session's rows instead of re-scanning the whole log.
        self._session_index: dict[str, list[JournalEvent]] = {}
        self._session_index_upto = 0

    @enforces("CC-5")
    def _append(self, event: JournalEvent) -> None:
        self._events.append(event)

    def write(self, event: JournalEvent) -> None:
        event = self._apply_context(event)
        with self._lock:
            self._append(event)
            if self._max_events > 0:
                overflow = len(self._events) - self._max_events
                if overflow > 0:
                    self._events.drop_oldest(overflow)

    @enforces("CC-5")
    def write_trajectory_once(self, event: TrajectoryEvent) -> bool:
        inserted, _canonical = self.write_trajectory_once_canonical(event)
        return inserted

    def write_trajectory_once_canonical(
        self,
        event: TrajectoryEvent,
    ) -> tuple[bool, TrajectoryEvent]:
        canonical = self.canonicalize_trajectory_event(event)
        key = _trajectory_dedupe_key(canonical)
        payload_digest = _trajectory_payload_digest(canonical)
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._events.snapshot()
                    if isinstance(item, TrajectoryEvent) and _trajectory_dedupe_key(item) == key
                ),
                None,
            )
            if existing is not None:
                if _trajectory_payload_digest(existing) != payload_digest:
                    raise TrajectoryConflictError(
                        "trajectory key is already committed with a conflicting payload"
                    )
                return False, canonical
            self._append(canonical)
            if self._max_events > 0:
                overflow = len(self._events) - self._max_events
                if overflow > 0:
                    self._events.drop_oldest(overflow)
            return True, canonical

    def read_all(self, *, scope: TenantScope | None = None) -> list[JournalEvent]:
        with self._lock:
            events = self._events.snapshot()
        return [event for event in events if self._visible(event, scope)]

    def read_by_session(self, session_id: str) -> list[JournalEvent]:
        with self._lock:
            events = self._events.snapshot()
            _refresh_session_index(self._session_index, events, self._session_index_upto)
            self._session_index_upto = len(events)
            return list(self._session_index.get(session_id, ()))

    def __len__(self) -> int:
        return len(self._events)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class JSONLJournal(Journal):
    def __init__(
        self,
        path: Path | str,
        *,
        max_size_bytes: int | None = None,
        keep_ratio: float = 0.5,
        audit_chain: Any = None,
        redactor: Any = None,
        trace_store: Any = None,
    ) -> None:
        """
        Parameters
        ----------
        path :
            On-disk location for the JSONL file.
        max_size_bytes :
            Optional cap. When the file grows past this, the oldest
            lines are dropped so roughly ``keep_ratio`` of the cap
            worth of most-recent events survive. ``None`` (default)
            disables rotation — the journal grows forever, matching
            older behavior. Recommended ~10-50 MB for a demo setup.
        keep_ratio :
            Fraction of ``max_size_bytes`` retained after a rotation.
            Default 0.5 keeps the last half. Higher = less frequent
            rotation but less headroom before the next one.
        audit_chain :
            Optional ``runtime.safety.audit.audit_chain.AuditChain`` instance.
            When provided, every ``write(event)`` also appends a
            signed record to the chain so tampering with the JSONL
            file (or dropping/reordering lines) is detectable via
            ``audit_chain.verify()``. ``None`` disables audit signing —
            matches the prior default behaviour.
        redactor :
            Optional ``runtime.platform.observability.redactor.Redactor`` instance.
            When provided, the JSON payload is run through
            ``redactor.redact()`` before persistence so accidental
            secrets in tool outputs / args don't land on disk.
            ``None`` (default) disables redaction.
        trace_store :
            Optional ``runtime.memory.diagnostics.trace_store.AgentTraceStore`` sidecar.
            When provided, selected journal events are mirrored into
            SQLite tables for fast audit and recovery queries.
        """
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._cache: list[JournalEvent] = []
        self._cache_byte_pos: int = 0
        self._skipped_total: int = 0
        self._max_size_bytes = max_size_bytes
        self._keep_ratio = max(0.1, min(0.9, keep_ratio))
        self._audit_chain = audit_chain
        self._redactor = redactor
        self._trace_store = trace_store
        # Buffered chunk run awaiting packing (list of (entry, event) pairs).
        self._pending_chunk_run: list[tuple[dict, JournalEvent]] | None = None
        # Audit P-04: incremental per-session index (built from the parsed
        # cache; reset automatically when the file rotates/truncates).
        self._session_index: dict[str, list[JournalEvent]] = {}
        self._session_index_upto = 0

    @staticmethod
    def _scope_digest(value: str, *, field: str) -> str:
        """Return a deterministic, redactor-safe storage identifier.

        Ownership fields are authorization data, so replacing an embedded
        email/phone/token with a generic ``[REDACTED:*]`` marker is not safe:
        unrelated principals would collapse onto the same durable scope.  A
        field-separated digest preserves equality without persisting the PII.

        The hexadecimal digest is translated to letters only because the
        built-in phone detector intentionally accepts long digit runs.  Keeping
        the canonical identifier outside every built-in PII shape makes the
        operation idempotent when an event crosses more than one journal
        wrapper.
        """

        digest = hashlib.sha256(f"echo-journal-scope-v1\0{field}\0{value}".encode()).hexdigest()
        letters_only = digest.translate(str.maketrans("0123456789", "ghijklmnop"))
        return f"echo-scope-{field}-vone-{letters_only}"

    def _storage_scope_value(self, value: str | None, *, field: str) -> str | None:
        """Pseudonymize a scope value exactly when redaction would mutate it."""

        if value is None or self._redactor is None:
            return value
        text = str(value)
        if text.startswith(f"echo-scope-{field}-vone-"):
            return text
        try:
            redacted = self._redactor.redact(text)
        except Exception:  # noqa: BLE001 - broken redactors remain best-effort
            return text
        if redacted == text:
            return text
        return self._scope_digest(text, field=field)

    def _storage_scoped_event(self, event: JournalEvent) -> JournalEvent:
        """Map authoritative ownership fields before whole-payload redaction."""

        tenant_id = self._storage_scope_value(event.tenant_id, field="tenant")
        owner_actor_id = self._storage_scope_value(event.owner_actor_id, field="owner")
        if tenant_id == event.tenant_id and owner_actor_id == event.owner_actor_id:
            return event
        return event.model_copy(
            update={
                "tenant_id": tenant_id,
                "owner_actor_id": owner_actor_id,
            }
        )

    @staticmethod
    def _is_redaction_protected_model_field(field_name: str, field_value: Any) -> bool:
        """Return whether one typed model field is structural journal data.

        Loose PII patterns must not rewrite typed identifiers, timestamps, or
        the hashes that bind tool-effect receipts: a phone-shaped digit run
        inside one of those values is still structural data, not user content.
        Ownership fields are deliberately not restored here; they are
        pseudonymized by ``_storage_scoped_event`` and remain covered by the
        fail-closed ownership check below.
        """

        if field_name in {"tenant_id", "owner_actor_id"}:
            return False
        return bool(
            field_name
            in {
                "schema_version",
                "event_type",
                "effect_key",
                "args_fingerprint",
            }
            or field_name.endswith(("_fingerprint", "_hash"))
            or isinstance(field_value, (UUID, date, datetime, Enum))
        )

    @classmethod
    def _restore_redaction_protected_model_fields(
        cls,
        model: Any,
        original: Any,
        redacted: Any,
    ) -> Any:
        """Restore typed structural fields while retaining payload redaction.

        Only Pydantic model fields receive structural protection.  Arbitrary
        dictionaries such as tool ``args`` and ``output`` are still scanned in
        full, even if a user-controlled key happens to end in ``_id``.
        """

        if isinstance(model, BaseModel):
            if not isinstance(original, dict) or not isinstance(redacted, dict):
                return redacted
            restored = dict(redacted)
            for field_name in type(model).model_fields:
                if field_name not in original:
                    continue
                field_value = getattr(model, field_name, None)
                if cls._is_redaction_protected_model_field(field_name, field_value):
                    restored[field_name] = original[field_name]
                    continue
                if field_name in redacted:
                    restored[field_name] = cls._restore_redaction_protected_model_fields(
                        field_value,
                        original[field_name],
                        redacted[field_name],
                    )
            return restored
        if isinstance(model, (list, tuple)):
            if not isinstance(original, list) or not isinstance(redacted, list):
                return redacted
            return [
                cls._restore_redaction_protected_model_fields(item, original_item, redacted_item)
                for item, original_item, redacted_item in zip(
                    model,
                    original,
                    redacted,
                    strict=False,
                )
            ]
        if isinstance(model, dict):
            if not isinstance(original, dict) or not isinstance(redacted, dict):
                return redacted
            restored = dict(redacted)
            for key, item in model.items():
                if key not in original or key not in redacted:
                    continue
                restored[key] = cls._restore_redaction_protected_model_fields(
                    item,
                    original[key],
                    redacted[key],
                )
            return restored
        return redacted

    def _redact_json_string_values(self, value: Any) -> Any:
        """Redact string leaves without exposing JSON syntax to regexes.

        Redacting a serialized JSON document lets a loose pattern consume
        structural data: numeric literals, quoted field names, separators, or
        fragments of typed identifiers.  Walking decoded data keeps the
        redactor's input boundary aligned with one actual application string.
        Dictionary keys are schema, not payload, and are deliberately left
        untouched.
        """

        if self._redactor is None:
            return value
        if isinstance(value, str):
            try:
                redacted = self._redactor.redact(value)
            except Exception:  # noqa: BLE001 - redaction remains best-effort
                return value
            return redacted if isinstance(redacted, str) else value
        if isinstance(value, list):
            return [self._redact_json_string_values(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact_json_string_values(item) for key, item in value.items()}
        return value

    @classmethod
    def _string_redaction_changes(
        cls,
        original: Any,
        redacted: Any,
        path: tuple[str | int, ...] = (),
    ) -> list[tuple[tuple[str | int, ...], str]]:
        """Return changed string leaves in deterministic document order."""

        if isinstance(original, str) and isinstance(redacted, str):
            return [] if original == redacted else [(path, redacted)]
        if isinstance(original, list) and isinstance(redacted, list):
            changes: list[tuple[tuple[str | int, ...], str]] = []
            for index, (original_item, redacted_item) in enumerate(
                zip(original, redacted, strict=False)
            ):
                changes.extend(
                    cls._string_redaction_changes(
                        original_item,
                        redacted_item,
                        (*path, index),
                    )
                )
            return changes
        if isinstance(original, dict) and isinstance(redacted, dict):
            changes = []
            for key, original_item in original.items():
                if key not in redacted:
                    continue
                changes.extend(
                    cls._string_redaction_changes(
                        original_item,
                        redacted[key],
                        (*path, key),
                    )
                )
            return changes
        return []

    @classmethod
    def _replace_json_path(
        cls,
        value: Any,
        path: tuple[str | int, ...],
        replacement: str,
    ) -> Any:
        """Copy only the containers on ``path`` and replace one leaf."""

        if not path:
            return replacement
        head, *tail = path
        if isinstance(head, int) and isinstance(value, list):
            updated = list(value)
            updated[head] = cls._replace_json_path(
                updated[head],
                tuple(tail),
                replacement,
            )
            return updated
        if isinstance(head, str) and isinstance(value, dict):
            updated = dict(value)
            updated[head] = cls._replace_json_path(
                updated[head],
                tuple(tail),
                replacement,
            )
            return updated
        return value

    @staticmethod
    def _dump_json_payload(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _serialized_event_keeps_structure(event: JournalEvent, line: str) -> bool:
        """Validate both the event schema and its durable identity envelope."""

        try:
            durable_event = _parse_event(line)
        except (TypeError, ValueError):
            return False
        return bool(
            durable_event.event_id == event.event_id
            and durable_event.event_type == event.event_type
            and durable_event.task_id == event.task_id
            and durable_event.arm_id == event.arm_id
            and durable_event.tenant_id == event.tenant_id
            and durable_event.owner_actor_id == event.owner_actor_id
            and durable_event.ts == event.ts
            and durable_event.source == event.source
        )

    def _visible(self, event: JournalEvent, scope: TenantScope | None) -> bool:
        """Compare a request scope with the exact durable scope representation."""

        if scope is None or scope.allow_cross_tenant:
            return True
        tenant_ids = {
            scope.tenant_id,
            self._storage_scope_value(scope.tenant_id, field="tenant"),
        }
        owner_actor_ids = {
            scope.actor_id,
            self._storage_scope_value(scope.actor_id, field="owner"),
        }
        return bool(
            event.tenant_id
            and event.owner_actor_id
            and event.tenant_id in tenant_ids
            and event.owner_actor_id in owner_actor_ids
        )

    def attach_trace_store(self, trace_store: Any) -> None:
        """Attach or replace the optional SQLite trace sidecar."""
        self._trace_store = trace_store

    @contextlib.contextmanager
    def _interprocess_lock(self, *, required: bool = False) -> Any:
        """Exclusive cross-process lock on a STABLE sidecar (``<path>.lock``),
        held across BOTH the append and the rotation below.

        Rotation does a ``tmp.replace`` rename, which swaps the journal inode —
        so a per-fd ``flock`` on the journal file itself cannot serialise a
        concurrent worker's append against a rename (the appender ends up
        writing the orphaned inode and its events are lost under
        ``uvicorn --workers N``). Locking a sidecar that is never renamed gives
        every writer a single, stable mutex. Ordinary journal writes retain
        their historic best-effort fallback when the platform has no file
        locking. Atomic check-and-append callers pass ``required=True`` and
        fail closed instead: silently proceeding unlocked would permit two
        workers to persist the same trajectory."""
        import os as _os

        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        try:
            lock_file = lock_path.open("a")
        except OSError as exc:
            if required:
                raise JournalTransactionError(
                    f"cannot open journal transaction lock: {lock_path}"
                ) from exc
            yield False
            return
        fd = lock_file.fileno()
        locked = False
        try:
            try:
                if _os.name == "nt":
                    # ``msvcrt.locking`` locks one existing byte from the
                    # current cursor. Seed the stable sidecar once and always
                    # lock byte zero so independent Windows workers contend
                    # on the same region.
                    lock_file.seek(0, 2)
                    if lock_file.tell() == 0:
                        lock_file.write("\0")
                        lock_file.flush()
                    lock_file.seek(0)
                    _lock_windows_fd(fd, "LK_LOCK")
                    locked = True
                else:
                    import fcntl as _fcntl

                    _fcntl.flock(fd, _fcntl.LOCK_EX)
                    locked = True
            except (OSError, ImportError) as exc:
                if required:
                    raise JournalTransactionError(
                        f"cannot acquire journal transaction lock: {lock_path}"
                    ) from exc
                locked = False
            yield locked
        finally:
            if locked:
                try:
                    if _os.name == "nt":
                        with contextlib.suppress(OSError):
                            lock_file.seek(0)
                        _lock_windows_fd(fd, "LK_UNLCK")
                    else:
                        import fcntl as _fcntl

                        _fcntl.flock(fd, _fcntl.LOCK_UN)
                except OSError:  # best-effort · lock_file.close() below releases it anyway
                    pass
            with contextlib.suppress(OSError):
                lock_file.close()

    @enforces("CC-5")
    def write(self, event: JournalEvent) -> None:
        # Canonicalize ownership before chunk classification as packed chunk
        # rows bypass ``_serialize_event_locked``.  This also keeps mirrors and
        # ordinary direct writers aligned with the durable representation.
        event = self._storage_scoped_event(self._apply_context(event))
        with self._lock:
            entry = classify_chunk(event)
            if entry is not None and chunk_packing_enabled():
                # Defer the file write: hold the chunk run in memory so a
                # run of token-sized deltas lands as ONE packed storage row
                # (dsh ``chunk-rows``). Any non-chunk event, an explicit
                # read, or a run break flushes it first — so at most the
                # trailing chunk run is buffered at any moment.
                run = self._pending_chunk_run
                if run is not None and continues_chunk_run(run[-1][0], entry):
                    run.append((entry, event))
                else:
                    self._flush_pending_chunks_locked()
                    self._pending_chunk_run = [(entry, event)]
                return
            self._flush_pending_chunks_locked()
            self._append_event_locked(event)

    def _append_event_locked(self, event: JournalEvent) -> None:
        """Serialize + redact + append + mirror one event.

        Caller must hold ``self._lock`` (the interprocess lock is taken
        inside ``_append_raw_locked``).
        """
        line = self._serialize_event_locked(event)
        self._append_raw_locked(line + "\n")
        self._mirror_event_effects(event)

    def _serialize_event_locked(self, event: JournalEvent) -> str:
        """Serialize and redact an event without touching journal storage."""

        event = self._storage_scoped_event(event)
        original_payload = event.model_dump(mode="json")
        original_line = self._dump_json_payload(original_payload)
        if self._redactor is None:
            return original_line

        redacted_payload = self._redact_json_string_values(original_payload)
        redacted_payload = self._restore_redaction_protected_model_fields(
            event,
            original_payload,
            redacted_payload,
        )
        if redacted_payload == original_payload:
            return original_line

        candidate_line = self._dump_json_payload(redacted_payload)
        if self._serialized_event_keeps_structure(event, candidate_line):
            return candidate_line

        # A custom or overly broad rule may still rewrite a schema-constrained
        # string (for example, a Literal nested in tool metadata).  Retain all
        # independently valid payload redactions and reject only the leaf that
        # would invalidate or re-identify the journal event.  This preserves
        # secret scrubbing without letting observability take down execution.
        accepted_payload = original_payload
        for path, replacement in self._string_redaction_changes(
            original_payload,
            redacted_payload,
        ):
            trial_payload = self._replace_json_path(
                accepted_payload,
                path,
                replacement,
            )
            trial_line = self._dump_json_payload(trial_payload)
            if self._serialized_event_keeps_structure(event, trial_line):
                accepted_payload = trial_payload
        return self._dump_json_payload(accepted_payload)

    def _append_raw_locked(
        self,
        line: str,
        *,
        require_durability: bool = False,
    ) -> None:
        """Append one storage line under the interprocess lock, then rotate.

        Caller must hold ``self._lock``. ``self._interprocess_lock()``
        serialises writers across processes — with ``uvicorn --workers N``
        two processes can interleave ``write`` + ``flush`` cycles.
        POSIX ``O_APPEND`` is atomic only for writes ≤ PIPE_BUF (~4 KB);
        trajectory dumps routinely blow past that. On Windows ``"a"``
        mode is never atomic across processes. Wrap the actual
        ``write/flush`` in an OS-level file lock keyed on the journal
        path so one writer at a time touches the JSONL. Falls back
        silently when ``fcntl``/``msvcrt`` aren't importable (e.g. WASM).
        """
        with self._interprocess_lock(required=require_durability):
            self._append_raw_with_interprocess_lock_locked(
                line,
                require_durability=require_durability,
            )

    def _append_raw_with_interprocess_lock_locked(
        self,
        line: str,
        *,
        require_durability: bool = False,
    ) -> None:
        """Append one line while the stable sidecar lock is already held.

        Splitting this from :meth:`_append_raw_locked` is important for
        atomic read-check-write transactions: reacquiring ``flock`` through a
        second file descriptor in the same process can self-deadlock on some
        platforms.  Callers must hold ``self._lock`` and the stable sidecar
        lock before entering this helper.
        """

        import os as _os

        journal_was_missing = not self._path.exists()
        with self._path.open("a", encoding="utf-8") as f:
            fd = f.fileno()
            _locked = False
            try:
                if _os.name == "nt":
                    try:
                        # LK_LOCK: block up to ~10s per attempt, retry
                        # a few times. LK_LOCK retries internally, so
                        # a single call is enough in practice.
                        _lock_windows_fd(fd, "LK_LOCK")
                        _locked = True
                    except OSError:
                        _locked = False
                else:
                    try:
                        import fcntl as _fcntl

                        _fcntl.flock(fd, _fcntl.LOCK_EX)
                        _locked = True
                    except (OSError, ImportError):
                        _locked = False
                # Seek to end: another process may have extended the
                # file since our ``open("a")`` computed the cursor.
                try:  # noqa: SIM105
                    f.seek(0, 2)
                except OSError:  # noqa: BLE001 — seek-to-end best-effort; writes still append
                    pass
                f.write(line)
                f.flush()
                try:
                    _os.fsync(fd)
                except OSError as exc:
                    if require_durability:
                        raise JournalTransactionError("cannot fsync durable journal data") from exc
                    # Ordinary high-volume telemetry retains its historical
                    # best-effort fsync behavior. Atomic terminal samples do
                    # not: their ledger must never advance after this branch.
            finally:
                if _locked:
                    try:
                        if _os.name == "nt":
                            # Seek back to the lock byte before unlocking.
                            try:  # noqa: SIM105
                                f.seek(0, 0)
                            except OSError:  # noqa: BLE001 — file lock/seek/fsync best-effort
                                pass
                            _lock_windows_fd(fd, "LK_UNLCK")
                        else:
                            import fcntl as _fcntl

                            _fcntl.flock(fd, _fcntl.LOCK_UN)
                    except OSError:  # noqa: BLE001 — file lock/seek/fsync best-effort
                        pass
        # A successful file fsync alone cannot make a newly-created path
        # survive power loss. Atomic writes always establish the directory
        # barrier; ordinary telemetry pays that cost only for first creation.
        if require_durability or journal_was_missing:
            _fsync_parent_directory(
                self._path,
                require_durability=require_durability,
                transaction_label="journal",
            )
        # Rotate if we've blown past the cap — still under the
        # interprocess lock so a rename can't race another process's
        # append to the old inode (data-loss window on rotation).
        if self._max_size_bytes is not None:
            try:
                size = self._path.stat().st_size
            except OSError:
                return
            if size > self._max_size_bytes:
                self._rotate_locked(require_durability=require_durability)

    def _trajectory_payload_on_disk_locked(
        self,
        key: tuple[str, str, str, str],
    ) -> str | None:
        """Strictly inspect durable rows while the transaction lock is held.

        A truncated or malformed row means absence cannot be proven.  Atomic
        trajectory persistence therefore raises instead of skipping the bad
        row like the best-effort projection reader does; this is the crucial
        crash-safe, fail-closed boundary preventing a quiet duplicate append.
        """

        if not self._path.exists():
            return None
        try:
            payload = self._path.read_bytes()
        except OSError as exc:
            raise JournalTransactionError("cannot inspect journal transaction state") from exc
        if not payload:
            return None
        if not payload.endswith(b"\n"):
            raise JournalTransactionError("journal has an incomplete trailing row")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JournalTransactionError("journal contains invalid UTF-8") from exc
        matching_payload: str | None = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                event_payloads = expand_chunk_row(data) if is_chunk_row(data) else (data,)
                for event_payload in event_payloads:
                    candidate = _parse_event_data(event_payload)
                    if (
                        isinstance(candidate, TrajectoryEvent)
                        and _trajectory_dedupe_key(candidate) == key
                    ):
                        candidate_payload = _trajectory_payload_digest(candidate)
                        if matching_payload is not None and matching_payload != candidate_payload:
                            raise TrajectoryConflictError(
                                "journal contains conflicting trajectories for one key"
                            )
                        matching_payload = candidate_payload
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise JournalTransactionError(
                    "journal contains an invalid transaction row"
                ) from exc
        return matching_payload

    def _fsync_journal_data_locked(self) -> None:
        """Re-establish durability before completing a recovered reservation."""

        if not self._path.exists():
            raise JournalTransactionError("trajectory journal data is absent")
        try:
            import os as _os

            with self._path.open("rb") as journal_file:
                _os.fsync(journal_file.fileno())
            _fsync_parent_directory(
                self._path,
                require_durability=True,
                transaction_label="recovered journal",
            )
        except OSError as exc:
            raise JournalTransactionError(
                "cannot fsync recovered atomic trajectory journal data"
            ) from exc

    def _trajectory_dedupe_state_locked(
        self,
        digest: str,
    ) -> _TrajectoryDedupeState | None:
        """Read one strict reservation state from the non-rotating ledger."""

        ledger_path = self._path.with_suffix(self._path.suffix + ".trajectory-dedupe.jsonl")
        if not ledger_path.exists():
            return None
        try:
            payload = ledger_path.read_bytes()
        except OSError as exc:
            raise JournalTransactionError("cannot inspect trajectory dedupe ledger") from exc
        if not payload:
            return None
        if not payload.endswith(b"\n"):
            raise JournalTransactionError("trajectory dedupe ledger has a truncated row")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JournalTransactionError("trajectory dedupe ledger is not UTF-8") from exc

        state: str | None = None
        payload_digest: str | None = None
        for raw in text.splitlines():
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as exc:
                raise JournalTransactionError("trajectory dedupe ledger is invalid") from exc
            if not isinstance(record, dict):
                raise JournalTransactionError("trajectory dedupe ledger row is not an object")
            record_digest = record.get("key")
            record_state = record.get("state")
            record_payload = record.get("payload")
            if not isinstance(record_digest, str) or record_state not in {
                "reserved",
                "committed",
            }:
                raise JournalTransactionError("trajectory dedupe ledger row is invalid")
            if record_payload is not None and not isinstance(record_payload, str):
                raise JournalTransactionError("trajectory dedupe payload digest is invalid")
            if record_digest != digest:
                continue
            if (
                payload_digest is not None
                and record_payload is not None
                and payload_digest != record_payload
            ):
                raise TrajectoryConflictError(
                    "trajectory dedupe ledger contains conflicting payloads"
                )
            if state == "committed" and record_state == "reserved":
                raise JournalTransactionError("trajectory dedupe ledger regressed state")
            state = str(record_state)
            if record_payload is not None:
                payload_digest = record_payload
        if state is None:
            return None
        return _TrajectoryDedupeState(state=state, payload_digest=payload_digest)

    def _append_trajectory_dedupe_record_locked(
        self,
        digest: str,
        state: str,
        payload_digest: str | None = None,
    ) -> None:
        """Durably append a reservation state while the sidecar lock is held."""

        ledger_path = self._path.with_suffix(self._path.suffix + ".trajectory-dedupe.jsonl")
        row = json.dumps(
            {
                "version": 2,
                "key": digest,
                "payload": payload_digest,
                "state": state,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        try:
            import os as _os

            with ledger_path.open("a", encoding="utf-8") as ledger:
                ledger.seek(0, 2)
                ledger.write(row + "\n")
                ledger.flush()
                _os.fsync(ledger.fileno())
            _fsync_parent_directory(
                ledger_path,
                require_durability=True,
                transaction_label="trajectory dedupe ledger",
            )
        except OSError as exc:
            raise JournalTransactionError("cannot persist trajectory dedupe state") from exc

    def _canonicalize_trajectory_event_locked(
        self,
        event: TrajectoryEvent,
    ) -> tuple[TrajectoryEvent, str]:
        """Apply server context + redaction and parse the exact durable row."""

        scoped_event = cast(TrajectoryEvent, self._apply_context(event))
        storage_event = cast(TrajectoryEvent, self._storage_scoped_event(scoped_event))
        key = _trajectory_dedupe_key(storage_event)
        durable_event, line = self._canonicalize_event_locked(storage_event)
        if not isinstance(durable_event, TrajectoryEvent):
            raise JournalTransactionError("serialized trajectory changed event identity")
        if _trajectory_dedupe_key(durable_event) != key:
            raise JournalTransactionError("redaction changed trajectory transaction identity")
        return durable_event, line

    def _canonicalize_event_locked(
        self,
        event: JournalEvent,
    ) -> tuple[JournalEvent, str]:
        """Apply context + redaction and parse the exact storage event."""

        scoped_event = self._apply_context(event)
        storage_event = self._storage_scoped_event(scoped_event)
        line = self._serialize_event_locked(storage_event)
        try:
            durable_event = _parse_event(line)
        except (TypeError, ValueError) as exc:
            raise JournalTransactionError(
                "serialized payload is not a valid journal event"
            ) from exc
        if (
            durable_event.event_id != scoped_event.event_id
            or durable_event.event_type != scoped_event.event_type
        ):
            raise JournalTransactionError("serialized journal event changed identity")
        if (
            durable_event.tenant_id != storage_event.tenant_id
            or durable_event.owner_actor_id != storage_event.owner_actor_id
        ):
            raise JournalTransactionError("redaction changed journal ownership scope")
        return durable_event, line

    def canonicalize_event(self, event: JournalEvent) -> JournalEvent:
        with self._lock:
            durable_event, _line = self._canonicalize_event_locked(event)
            return durable_event

    def write_canonical(self, event: JournalEvent) -> JournalEvent:
        """Durably write and return the exact redacted/scoped event.

        Streaming callers need a single combined operation: canonicalizing
        before the append avoids a racy read-back, while returning only after
        strict fsync ensures no subscriber sees an event whose durable write
        failed. Canonical writes bypass deferred chunk packing because a
        buffered event is not yet durable.
        """

        with self._lock:
            self._flush_pending_chunks_locked()
            durable_event, line = self._canonicalize_event_locked(event)
            self._append_raw_locked(line + "\n", require_durability=True)
            self._mirror_event_effects(durable_event)
            return durable_event

    def canonicalize_trajectory_event(self, event: TrajectoryEvent) -> TrajectoryEvent:
        with self._lock:
            durable_event, _line = self._canonicalize_trajectory_event_locked(event)
            return durable_event

    @enforces("CC-5")
    def write_trajectory_once(self, event: TrajectoryEvent) -> bool:
        """Durably append one ownership-scoped trajectory at most once."""

        inserted, _durable_event = self.write_trajectory_once_canonical(event)
        return inserted

    def write_trajectory_once_canonical(
        self,
        event: TrajectoryEvent,
    ) -> tuple[bool, TrajectoryEvent]:
        with self._lock:
            # A buffered token run must become part of the durable prefix
            # before the strict transaction scan. This happens before taking
            # the required sidecar lock, avoiding recursive flock acquisition.
            self._flush_pending_chunks_locked()
            durable_event, line = self._canonicalize_trajectory_event_locked(event)
            key = _trajectory_dedupe_key(durable_event)
            key_digest = _trajectory_dedupe_digest(key)
            payload_digest = _trajectory_payload_digest(durable_event)
            with self._interprocess_lock(required=True):
                dedupe = self._trajectory_dedupe_state_locked(key_digest)
                on_disk_payload = self._trajectory_payload_on_disk_locked(key)

                if dedupe is not None and dedupe.payload_digest is not None:
                    if dedupe.payload_digest != payload_digest:
                        raise TrajectoryConflictError(
                            "trajectory key is reserved or committed with a conflicting payload"
                        )
                elif dedupe is not None:
                    # Version-1 ledger rows did not bind a payload. They are
                    # safe to upgrade only while the corresponding journal row
                    # is still available for an exact canonical comparison.
                    if on_disk_payload is None:
                        raise JournalTransactionError(
                            "legacy trajectory reservation payload cannot be verified"
                        )
                    if on_disk_payload != payload_digest:
                        raise TrajectoryConflictError(
                            "legacy trajectory key has a conflicting payload"
                        )
                    if dedupe.state == "committed":
                        # Bind the verified legacy commit to its canonical
                        # payload before returning. Otherwise a later rotation
                        # can remove the only evidence needed to interpret the
                        # version-1 row and permanently poison idempotent retry.
                        self._fsync_journal_data_locked()
                        self._append_trajectory_dedupe_record_locked(
                            key_digest,
                            "committed",
                            payload_digest,
                        )
                        dedupe = _TrajectoryDedupeState(
                            state="committed",
                            payload_digest=payload_digest,
                        )

                if on_disk_payload is not None and on_disk_payload != payload_digest:
                    raise TrajectoryConflictError(
                        "journal already contains a conflicting trajectory payload"
                    )

                if dedupe is not None and dedupe.state == "committed":
                    return False, durable_event

                if dedupe is not None and dedupe.state == "reserved":
                    if on_disk_payload is None:
                        # Reservation-before-append crash: the same canonical
                        # payload owns the reservation and may safely resume.
                        self._append_raw_with_interprocess_lock_locked(
                            line + "\n",
                            require_durability=True,
                        )
                        on_disk_payload = self._trajectory_payload_on_disk_locked(key)
                        if on_disk_payload != payload_digest:
                            raise JournalTransactionError(
                                "trajectory retry append could not be verified"
                            )
                        self._append_trajectory_dedupe_record_locked(
                            key_digest,
                            "committed",
                            payload_digest,
                        )
                        self._mirror_event_effects(durable_event)
                        return True, durable_event

                    # Crash after journal fsync but before ledger commit. A
                    # fresh fsync makes recovery safe even when the original
                    # worker observed an fsync error after its append.
                    self._fsync_journal_data_locked()
                    self._append_trajectory_dedupe_record_locked(
                        key_digest,
                        "committed",
                        payload_digest,
                    )
                    return False, durable_event

                if on_disk_payload is not None:
                    # Backfill trajectories written before the durable ledger
                    # was introduced so rotation cannot erase their dedupe
                    # evidence later.
                    self._fsync_journal_data_locked()
                    self._append_trajectory_dedupe_record_locked(
                        key_digest,
                        "committed",
                        payload_digest,
                    )
                    return False, durable_event

                # Reserve first. If the process dies during the following
                # journal append, a later worker resumes only this exact
                # canonical payload.
                self._append_trajectory_dedupe_record_locked(
                    key_digest,
                    "reserved",
                    payload_digest,
                )
                self._append_raw_with_interprocess_lock_locked(
                    line + "\n",
                    require_durability=True,
                )
                if self._trajectory_payload_on_disk_locked(key) != payload_digest:
                    raise JournalTransactionError("trajectory append could not be verified")
                self._append_trajectory_dedupe_record_locked(
                    key_digest,
                    "committed",
                    payload_digest,
                )
                self._mirror_event_effects(durable_event)
                return True, durable_event

    def _mirror_event_effects(self, event: JournalEvent) -> None:
        """Best-effort side mirrors (audit chain + trace store) for one event.

        Runs at flush time so the chain's order matches the file's line
        order. A mirror failure must NOT take down the journal write
        path (which is on every step).
        """
        if self._audit_chain is not None:
            try:
                self._audit_chain.append(
                    kind=type(event).__name__,
                    payload={
                        "event_type": getattr(event, "event_type", None),
                        "ts": (
                            event.ts.isoformat()
                            if hasattr(event, "ts") and event.ts is not None
                            else None
                        ),
                    },
                )
            except Exception:  # noqa: BLE001 — audit mirror is best-effort; never break the hot write path
                import logging

                logging.getLogger(__name__).warning(
                    "journal %s: audit chain append failed",
                    self._path,
                )
        self._mirror_trace_event(event)

    def _flush_pending_chunks_locked(self) -> None:
        """Flush the buffered chunk run: one packed row, or verbatim lines.

        Caller must hold ``self._lock``. A run of ``>= MIN_RUN``
        consecutive chunks writes a single packed row (lossless on
        read); shorter runs fall back to per-event lines. Every member
        still gets its audit/trace mirror at flush time.
        """
        run = self._pending_chunk_run
        if not run:
            return
        self._pending_chunk_run = None
        if len(run) >= MIN_RUN:
            # Packed rows are storage envelopes rather than JournalEvent
            # models. Canonicalize every member first, then repack the exact
            # validated events. This gives chunk deltas the same structured
            # string redaction as ordinary rows without ever running regexes
            # across the packed JSON schema or its numeric timestamp arrays.
            durable_events: list[JournalEvent] = []
            durable_entries: list[dict[str, Any]] = []
            for _entry, event in run:
                line = self._serialize_event_locked(event)
                try:
                    durable_event = _parse_event(line)
                except (TypeError, ValueError):
                    durable_event = event
                durable_entry = classify_chunk(durable_event)
                if durable_entry is None or (
                    durable_entries and not continues_chunk_run(durable_entries[-1], durable_entry)
                ):
                    # A redactor changed a packability discriminator. Persist
                    # the members independently rather than weakening the
                    # lossless codec's run invariant.
                    for _fallback_entry, fallback_event in run:
                        self._append_event_locked(fallback_event)
                    return
                durable_events.append(durable_event)
                durable_entries.append(durable_entry)

            row = pack_chunk_row(durable_entries)
            line = json.dumps(row, ensure_ascii=False)
            self._append_raw_locked(line + "\n")
            for event in durable_events:
                self._mirror_event_effects(event)
        else:
            for _entry, event in run:
                self._append_event_locked(event)

    def _mirror_trace_event(self, event: JournalEvent) -> None:
        if self._trace_store is None:
            return
        try:
            payload = event.model_dump(mode="json")
            task_id = str(event.task_id) if event.task_id is not None else None
            thread_id = str(event.conversation_id or "") or None
            agent_id = str(event.agent_id or "") or None
            ts = event.ts.isoformat() if event.ts is not None else None
            self._trace_store.record_event(
                event_type=str(event.event_type),
                payload=payload,
                thread_id=thread_id,
                task_id=task_id,
                agent_id=agent_id,
                tenant_id=str(event.tenant_id or "") or None,
                owner_actor_id=str(event.owner_actor_id or event.actor or "") or None,
                ts=ts,
            )
            if isinstance(event, TokenUsageEvent):
                self._trace_store.record_token_usage(
                    task_id=task_id,
                    thread_id=thread_id,
                    agent_id=agent_id,
                    iteration=event.iteration,
                    model=event.model,
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    cost_usd=event.cost_usd,
                    tenant_id=str(event.tenant_id or "") or None,
                    owner_actor_id=str(event.owner_actor_id or event.actor or "") or None,
                    ts=ts,
                )
            elif isinstance(event, ReactCheckpointEvent):
                self._trace_store.record_checkpoint(
                    task_id=str(event.task_id or ""),
                    thread_id=thread_id,
                    agent_id=agent_id,
                    checkpoint_type="react",
                    iteration=event.iteration_completed,
                    summary=event.progress_summary,
                    state={
                        "iteration_completed": event.iteration_completed,
                        "max_iterations": event.max_iterations,
                        "messages_snapshot": event.messages_snapshot,
                        "steps_snapshot": event.steps_snapshot,
                        "has_final_answer": event.has_final_answer,
                        "final_answer": event.final_answer,
                        "working_set_snapshot": event.working_set_snapshot,
                        "progress_summary": event.progress_summary,
                        "current_phase": event.current_phase,
                    },
                    tenant_id=str(event.tenant_id or "") or None,
                    owner_actor_id=str(event.owner_actor_id or event.actor or "") or None,
                    ts=ts,
                )
            elif isinstance(event, TaskCheckpointEvent):
                self._trace_store.record_checkpoint(
                    task_id=str(event.task_id or ""),
                    thread_id=thread_id,
                    agent_id=agent_id,
                    checkpoint_type="task",
                    iteration=event.nodes_completed,
                    summary=f"{event.nodes_completed}/{event.total_nodes} nodes",
                    state={
                        "nodes_completed": event.nodes_completed,
                        "total_nodes": event.total_nodes,
                        "tokens_spent": event.tokens_spent,
                        "usd_spent": event.usd_spent,
                    },
                    tenant_id=str(event.tenant_id or "") or None,
                    owner_actor_id=str(event.owner_actor_id or event.actor or "") or None,
                    ts=ts,
                )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).debug(
                "journal %s: trace mirror failed",
                self._path,
                exc_info=True,
            )

    def _rotate_locked(self, *, require_durability: bool = False) -> None:
        """Trim the file to the last ``keep_ratio * max_size_bytes``
        from the tail (so the most-recent events survive). Caller must
        hold ``self._lock``. Invalidates the incremental read cache
        because byte offsets shift. The newest complete row is always kept,
        even when that one trajectory is larger than the configured cap.
        """
        if self._max_size_bytes is None:
            return
        keep_bytes = int(self._max_size_bytes * self._keep_ratio)
        try:
            size = self._path.stat().st_size
        except OSError:
            return
        if size <= keep_bytes:
            return
        try:
            payload = self._path.read_bytes()
        except OSError:
            if require_durability:
                raise
            return
        if payload and not payload.endswith(b"\n"):
            import logging

            logging.getLogger(__name__).warning(
                "journal %s: rotate skipped · incomplete trailing row",
                self._path,
            )
            return
        lines = payload.splitlines(keepends=True)
        if not lines:
            return
        kept_reversed: list[bytes] = []
        kept_size = 0
        for row in reversed(lines):
            if kept_reversed and kept_size + len(row) > keep_bytes:
                break
            kept_reversed.append(row)
            kept_size += len(row)
        tail = b"".join(reversed(kept_reversed))
        # Atomic replace: write to .tmp then rename.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            import os as _os

            with tmp.open("wb") as f:
                f.write(tail)
                f.flush()
                try:
                    _os.fsync(f.fileno())
                except OSError as exc:
                    if require_durability:
                        raise JournalTransactionError(
                            "cannot fsync atomic trajectory rotation data"
                        ) from exc
            tmp.replace(self._path)
            _fsync_parent_directory(
                self._path,
                require_durability=require_durability,
                transaction_label="journal rotation",
            )
        except OSError:
            if require_durability:
                raise
            return
        # Invalidate cache · next read_all() reparses from scratch.
        self._cache = []
        self._cache_byte_pos = 0
        self._skipped_total = 0
        import logging

        logging.getLogger(__name__).info(
            "journal %s rotated · kept tail %d bytes (was %d)",
            self._path,
            len(tail),
            size,
        )

    def read_all(self, *, scope: TenantScope | None = None) -> list[JournalEvent]:
        with self._lock:
            # Make buffered chunk runs visible to readers: flush them to
            # disk first so the cache is always a complete prefix.
            self._flush_pending_chunks_locked()
            if not self._path.exists():
                # File gone entirely → reset cache (a fresh file may
                # appear later and we want to parse it from scratch).
                self._cache = []
                self._cache_byte_pos = 0
                return []

            file_size = self._path.stat().st_size
            if file_size == self._cache_byte_pos:
                # Nothing new — hand back cached list (shallow copy so
                # caller mutations don't poison the cache).
                events = list(self._cache)
                return [event for event in events if self._visible(event, scope)]
            if file_size < self._cache_byte_pos:
                # File shrank (manual truncate / rotation) → invalidate.
                self._cache = []
                self._cache_byte_pos = 0
                self._skipped_total = 0

            # Read only the tail that's new since last parse. Using
            # binary mode + seek avoids decoder issues when a multi-byte
            # char straddles a read boundary — we read to the current
            # end-of-file which always aligns to a line boundary in
            # append-only usage.
            with self._path.open("rb") as f:
                f.seek(self._cache_byte_pos)
                new_bytes = f.read()
                new_pos = self._cache_byte_pos + len(new_bytes)

            new_text = new_bytes.decode("utf-8", errors="replace")
            for _lineno_offset, raw in enumerate(new_text.splitlines(), 1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if is_chunk_row(data):
                        # Packed storage row (dsh chunk-rows): expand to the
                        # exact original events, same ids and timestamps.
                        for event_data in expand_chunk_row(data):
                            self._cache.append(_parse_event_data(event_data))
                    else:
                        self._cache.append(_parse_event(line))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    self._skipped_total += 1
                    if self._skipped_total == 1:
                        import logging

                        logging.getLogger(__name__).warning(
                            "journal %s: unparseable event %s at "
                            "byte ~%d · skipping (and any subsequent)",
                            self._path,
                            type(exc).__name__,
                            self._cache_byte_pos,
                        )
            self._cache_byte_pos = new_pos
            events = list(self._cache)
            return [event for event in events if self._visible(event, scope)]

    def read_by_session(self, session_id: str) -> list[JournalEvent]:
        # Ensure the parsed cache includes the latest file tail (read_all
        # flushes pending chunk runs and reads only the new delta).
        self.read_all()
        with self._lock:
            events = list(self._cache)
            if self._session_index_upto > len(events):
                # File rotated/truncated — the old offsets are stale; rebuild.
                self._session_index = {}
                self._session_index_upto = 0
            _refresh_session_index(self._session_index, events, self._session_index_upto)
            self._session_index_upto = len(events)
            return list(self._session_index.get(session_id, ()))

    def __len__(self) -> int:
        # Event count, not line count: a packed chunk row is one line but
        # N events. ``read_all`` flushes the pending run, so this is exact.
        return len(self.read_all())
