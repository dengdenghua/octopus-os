"""Crash-safe tool effect receipts for durable agent turns.

The journal already records a completed :class:`StepEvent`, but a process can
die after a handler mutates external state and before that event is appended.
This module adds a write-ahead intent and a fenced receipt coordinator:

* committed calls are replayed without invoking the handler again;
* an unfinished side-effecting intent is reported as indeterminate instead of
  being retried blindly;
* concurrent deliveries in one or many processes wait for the owner and then
  reuse its receipt;
* live owners renew their lease, while an expired pre-handler claim may be
  safely taken over.

The identity is scoped to ``task_id + step_id + tool + canonical arguments``.
New user actions get a new task or step, so normal repeated tool use is not
mistaken for transport/recovery duplication.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from runtime.execution.tool_engine.effect_store import EffectStore, NativeEffectRecoveryRequired
from runtime.memory.journal import (
    Journal,
    JournalRecoveryReadError,
    StepEvent,
    ToolEffectIntentEvent,
)
from runtime.platform.models import CostEntry, ExecutionResult, Step, ToolCall, now_utc

_SIDE_EFFECT_AFFINITIES = frozenset({"write", "edit", "exec", "delete", "dangerous"})
_READ_ONLY_AFFINITIES = frozenset(
    {
        "read",
        "readonly",
        "observe",
        "search",
        "find",
        "list",
        "analysis",
        "math",
        "verify",
        "lint",
        "format",
    }
)
_VOLATILE_RUNTIME_ARG_KEYS = frozenset(
    {
        "session",
        "_session",
        "cancel_event",
        "cancellation_token",
    }
)

EFFECT_RECEIPT_SCHEMA = "echo.tool.effect_receipt.v1"
EffectClass = Literal[
    "none",
    "read_only",
    "workspace_write",
    "local_state",
    "external_or_unknown",
]
EffectState = Literal["not_executed", "committed", "failed", "indeterminate", "replayed"]


def args_fingerprint(args: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_effect_value(args),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_stable_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def effect_key(
    task_id: Any,
    step_id: int,
    sucker_id: Any,
    args: dict[str, Any],
) -> str:
    material = f"{task_id}\0{step_id}\0{sucker_id}\0{args_fingerprint(args)}"
    return "effect:v1:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def is_side_effecting(affinity: list[str] | None) -> bool:
    """Fail closed for unknown affinity; known read-only tags may retry."""

    if not affinity:
        return True
    tags = {str(tag).strip().lower() for tag in affinity}
    if tags & _SIDE_EFFECT_AFFINITIES:
        return True
    return not bool(tags & _READ_ONLY_AFFINITIES)


def should_refresh_read_result(skill: Any) -> bool:
    """Honor a server-registered live-read policy without weakening write receipts."""

    return getattr(skill, "replay_policy", "durable") == "refresh_read" and not is_side_effecting(
        getattr(skill, "affinity", None)
    )


def _canonical_effect_class(skill: Any, *, handler_executed: bool) -> EffectClass:
    """Classify only exact in-tree handlers; every replaceable handler fails closed.

    Skill names, affinity tags and ``trusted_source`` are registry metadata and
    can be supplied by plugins.  Object identity of the handler captured by
    ToolExecutor is the only signal used to grant the narrow ``read_only`` or
    ``workspace_write``/``local_state`` classes needed by automatic Loop repair.
    """

    if not handler_executed:
        return "none"
    name = str(getattr(skill, "name", "") or getattr(skill, "skill_id", ""))
    handler = getattr(skill, "handler", None)

    from runtime.execution.suckers.agent_meta_skills import _todo_read, _todo_write
    from runtime.execution.suckers.builtins import (
        _count_words,
        _file_stats,
        _hash_text,
        _list_cwd,
        _read_file,
    )
    from runtime.execution.suckers.fs_search_skills import (
        _glob_files,
        _grep_text,
        _read_file_range,
        _tree,
    )
    from runtime.execution.suckers.write_skills import _edit_file

    read_only_handlers = {
        "list_cwd": _list_cwd,
        "read_file": _read_file,
        "read_file_range": _read_file_range,
        "file_stats": _file_stats,
        "count_words": _count_words,
        "hash_text": _hash_text,
        "glob_files": _glob_files,
        "grep_text": _grep_text,
        "tree": _tree,
        "todo_read": _todo_read,
    }
    workspace_write_handlers = {"edit_file": _edit_file}
    local_state_handlers = {"todo_write": _todo_write}
    if read_only_handlers.get(name) is handler:
        return "read_only"
    # Appliance photo tools are server-owned, authorization-checked live
    # reads. Their handlers are bound methods, so object identity alone cannot
    # be compared with a module-level function; keep the allowlist exact to
    # this in-tree service instead of trusting affinity or a plugin label.
    owner = getattr(handler, "__self__", None)
    owner_type = type(owner) if owner is not None else None
    if (
        owner_type is not None
        and owner_type.__module__ == "appliance.photo_tools"
        and owner_type.__name__ == "PhotoToolService"
        and name in {"photos_library", "photos_search", "photos_status", "photos_index_plan"}
    ):
        return "read_only"
    if workspace_write_handlers.get(name) is handler:
        return "workspace_write"
    if local_state_handlers.get(name) is handler:
        return "local_state"
    return "external_or_unknown"


def not_executed_effect_receipt(
    *,
    call_id: Any,
    tool_name: Any,
    reason: str = "",
) -> dict[str, object]:
    """Return the server-owned proof used by pre-dispatch rejection paths."""

    return {
        "schema": EFFECT_RECEIPT_SCHEMA,
        "sealed": True,
        "emitted_by": "tool_executor",
        "tool_name": str(tool_name),
        "call_id": str(call_id),
        "effect_class": "none",
        "state": "not_executed",
        "handler_entered": False,
        "retry_safe": False,
        **({"reason": str(reason)[:240]} if reason else {}),
    }


def build_server_effect_receipt(
    *,
    skill: Any,
    call_id: Any,
    handler_executed: bool,
    result_status: str,
    resolution: EffectResolution | None,
    receipt_rewrite_source: str | None = None,
) -> dict[str, object]:
    """Seal one conservative execution-effect classification.

    ``committed`` means either no side effect was possible or the ReAct
    effect coordinator owns a durable execution resolution.  A handler/post
    hook replacement, a failed local/external handler, or a side effect that
    bypassed the coordinator is always indeterminate.
    """

    tool_name = str(getattr(skill, "name", "") or getattr(skill, "skill_id", ""))
    if not handler_executed:
        return not_executed_effect_receipt(
            call_id=call_id,
            tool_name=tool_name,
            reason=receipt_rewrite_source or "handler_not_executed",
        )

    effect_class = _canonical_effect_class(skill, handler_executed=True)
    if receipt_rewrite_source is not None:
        effect_class = "external_or_unknown"
        state: EffectState = "indeterminate"
    elif result_status == "success":
        if effect_class == "read_only" or resolution is not None:
            state = "committed"
        else:
            state = "indeterminate"
    elif effect_class == "read_only":
        state = "failed"
    else:
        state = "indeterminate"

    receipt: dict[str, object] = {
        "schema": EFFECT_RECEIPT_SCHEMA,
        "sealed": True,
        "emitted_by": "tool_executor",
        "tool_name": tool_name,
        "call_id": str(call_id),
        "effect_class": effect_class,
        "state": state,
        "handler_entered": True,
        "retry_safe": effect_class == "read_only" and state == "failed",
    }
    if resolution is not None:
        receipt.update(
            {
                "effect_key": resolution.key,
                "fencing_token": max(0, int(resolution.fencing_token)),
            }
        )
    if receipt_rewrite_source:
        receipt["reason"] = receipt_rewrite_source
    return receipt


@dataclass(frozen=True)
class EffectResolution:
    kind: Literal["execute", "replay", "indeterminate"]
    key: str
    args_fingerprint: str
    step: Step | None = None
    reason: str = ""
    holder_id: str = ""
    fencing_token: int = 0
    side_effecting: bool = False


class EffectLeaseLost(RuntimeError):
    """The caller lost its fenced claim before entering the handler."""


class ToolEffectReceiptIndex:
    """Journal-backed receipts plus optional cross-process coordination."""

    _native_memory_lock = threading.RLock()
    _native_memory_sequences: dict[tuple[object, str], int] = {}

    def __init__(
        self,
        journal: Journal,
        *,
        wait_timeout_s: float = 120.0,
        store: EffectStore | None = None,
        lease_ttl_s: float = 30.0,
        poll_interval_s: float = 0.05,
        holder_id: str | None = None,
    ) -> None:
        self._journal = journal
        self._wait_timeout_s = max(0.0, float(wait_timeout_s))
        self._store = store
        self._lease_ttl_s = max(0.15, float(lease_ttl_s))
        self._poll_interval_s = max(0.01, float(poll_interval_s))
        self._holder_id = holder_id or (f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}")
        self._condition = threading.Condition(threading.RLock())
        self._intents: dict[str, ToolEffectIntentEvent] = {}
        self._committed: dict[str, Step] = {}
        self._live: set[str] = set()
        self._seen_event_ids: set[str] = set()
        self._steps_by_call_id: dict[str, Step] = {}
        self._effect_by_call_id: dict[str, str] = {}
        self._heartbeats: dict[str, threading.Event] = {}
        self._native_max_steps: dict[str, int] = {}
        self._uncertain: dict[str, tuple[str, Step]] = {}
        self._task_by_key: dict[str, str] = {}

    @property
    def store(self) -> EffectStore | None:
        return self._store

    def reserve_native_step_ids(self, task_id: Any, count: int) -> list[int]:
        """Reserve IDs once, before dispatch; unused reservations remain consumed."""

        task = str(task_id)
        if not task or type(count) is not int or count < 0:
            raise ValueError("task_id and a non-negative integer count are required")
        with self._condition:
            self._refresh_from_journal()
            minimum = self._native_max_steps.get(task, 0)
            if self._store is not None:
                reserve = getattr(self._store, "reserve_native_step_ids", None)
                if not callable(reserve):
                    raise NativeEffectRecoveryRequired(
                        task, state="unavailable", reason="native_effect_sequence_unavailable"
                    )
                return reserve(task_id=task, count=count, minimum_step_id=minimum)
            # No configured receipt plane: serialize independent indexes in this
            # process. A JSONL restart seeds its high-water mark from actual events;
            # cross-process reservation requires the SQLite receipt plane.
            path = getattr(self._journal, "path", None) or getattr(self._journal, "_path", None)
            journal_identity = str(Path(path).resolve()) if path is not None else self._journal
            identity = (journal_identity, task)
            with self._native_memory_lock:
                maximum = max(minimum, self._native_memory_sequences.get(identity, 0))
                self._native_memory_sequences[identity] = maximum + count
                return list(range(maximum + 1, maximum + count + 1))

    def assert_native_task_recoverable(self, task_id: Any) -> None:
        """Do not escape an unfinished side effect by allocating a fresh step ID."""

        task = str(task_id)
        if not task:
            raise ValueError("task_id is required")
        with self._condition:
            # A recovery admission decision must not treat a parseable prefix
            # as the complete journal. JSONL readers expose a strict typed
            # path that raises when any later row is unreadable; translate that
            # loss of evidence into the same fail-closed native recovery
            # result used for an unresolved effect.
            try:
                self._refresh_from_journal(strict=True)
            except JournalRecoveryReadError as exc:
                raise NativeEffectRecoveryRequired(
                    task,
                    state="unavailable",
                    reason="native_effect_recovery_required",
                ) from exc
            unresolved = {
                key
                for key, intent in self._intents.items()
                if str(intent.task_id) == task
                and intent.side_effecting
                and key not in self._committed
            }
            unresolved.update(key for key, (owner, _) in self._uncertain.items() if owner == task)
            if self._store is not None:
                check = getattr(self._store, "assert_native_task_recoverable", None)
                if not callable(check):
                    raise NativeEffectRecoveryRequired(
                        task, state="unavailable", reason="native_effect_sequence_unavailable"
                    )
                # A durable successful Step can repair a process loss between its
                # journal append and SQL commit, but never overrides a live claim.
                for key, intent in self._intents.items():
                    if str(intent.task_id) == task and key in self._committed:
                        self._store.record_committed(effect_key=key, step=self._committed[key])
                check(task_id=task, unresolved_effect_keys=tuple(sorted(unresolved)))
                return
            if unresolved:
                key = sorted(unresolved)[0]
                live = key in self._live
                raise NativeEffectRecoveryRequired(
                    task,
                    state="started" if live else "indeterminate",
                    reason="native_effect_inflight" if live else "native_effect_recovery_required",
                    effect_key=key,
                )

    def begin(
        self,
        *,
        task_id: Any,
        step_id: int,
        sucker_id: Any,
        args: dict[str, Any],
        side_effecting: bool,
    ) -> EffectResolution:
        fingerprint = args_fingerprint(args)
        key = effect_key(task_id, step_id, sucker_id, args)
        deadline = time.monotonic() + self._wait_timeout_s
        with self._condition:
            self._refresh_from_journal()
            self._task_by_key[key] = str(task_id)
            while key in self._live:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return EffectResolution(
                        "indeterminate",
                        key,
                        fingerprint,
                        reason="another delivery is still executing this tool effect",
                    )
                self._condition.wait(timeout=remaining)
                self._refresh_from_journal()

            committed = self._committed.get(key)
            if committed is not None and self._store is None:
                return EffectResolution(
                    "replay",
                    key,
                    fingerprint,
                    step=_replayed_step(committed, effect_key=key),
                )
            if committed is not None and self._store is not None:
                # A journal row alone cannot overrule a live fenced lease.
                # Seed the shared store when safe, then let claim() make the
                # authoritative replay/busy decision below.
                self._store.record_committed(effect_key=key, step=committed)

            intent = self._intents.get(key)
            uncertain = key in self._uncertain
            side_effecting = side_effecting or uncertain
            if self._store is not None:
                while True:
                    decision = self._store.claim(
                        effect_key=key,
                        task_id=str(task_id),
                        step_id=step_id,
                        sucker_id=str(sucker_id),
                        args_fingerprint=fingerprint,
                        side_effecting=side_effecting,
                        holder_id=self._holder_id,
                        lease_ttl_s=self._lease_ttl_s,
                        observed_durable_intent=intent is not None or uncertain,
                    )
                    if decision.kind == "execute":
                        resolution = EffectResolution(
                            "execute",
                            key,
                            fingerprint,
                            holder_id=self._holder_id,
                            fencing_token=decision.fencing_token,
                            side_effecting=side_effecting,
                        )
                        self._live.add(key)
                        return resolution
                    if decision.kind == "replay":
                        assert decision.step is not None
                        self._committed[key] = decision.step
                        return EffectResolution(
                            "replay",
                            key,
                            fingerprint,
                            step=_replayed_step(decision.step, effect_key=key),
                        )
                    if decision.kind == "indeterminate":
                        return EffectResolution(
                            "indeterminate",
                            key,
                            fingerprint,
                            reason=decision.reason,
                            fencing_token=decision.fencing_token,
                            side_effecting=side_effecting,
                        )

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return EffectResolution(
                            "indeterminate",
                            key,
                            fingerprint,
                            reason="another process is still executing this tool effect",
                            side_effecting=side_effecting,
                        )
                    self._condition.wait(
                        timeout=min(self._poll_interval_s, remaining),
                    )
                    self._refresh_from_journal()
                    committed = self._committed.get(key)
                    if committed is not None:
                        self._store.record_committed(effect_key=key, step=committed)
                        # Re-enter claim(): journal repair may have published
                        # a terminal receipt, or a newer owner may still hold
                        # the lease and must not be bypassed.
                        continue
                    intent = self._intents.get(key)

            if uncertain or (intent is not None and (side_effecting or intent.side_effecting)):
                return EffectResolution(
                    "indeterminate",
                    key,
                    fingerprint,
                    reason=(
                        "a previous process entered this side-effecting tool but did not "
                        "durably record its result"
                    ),
                )

            self._live.add(key)
            return EffectResolution(
                "execute",
                key,
                fingerprint,
                side_effecting=side_effecting,
            )

    def mark_intent(
        self,
        event: ToolEffectIntentEvent,
        resolution: EffectResolution,
    ) -> None:
        with self._condition:
            self._refresh_from_journal()
            self._intents[event.effect_key] = event
            task = str(event.task_id)
            self._native_max_steps[task] = max(self._native_max_steps.get(task, 0), event.step_id)
            self._effect_by_call_id[event.call_id] = event.effect_key
            if self._store is not None:
                started = self._store.mark_started(
                    effect_key=resolution.key,
                    holder_id=resolution.holder_id,
                    fencing_token=resolution.fencing_token,
                    call_id=event.call_id,
                    lease_ttl_s=self._lease_ttl_s,
                )
                if not started:
                    self._live.discard(resolution.key)
                    self._condition.notify_all()
                    raise EffectLeaseLost("tool-effect lease was lost before handler entry")
                self._start_heartbeat(resolution)

    def finish(self, resolution: EffectResolution, step: Step) -> None:
        with self._condition:
            self._stop_heartbeat(resolution.key)
            uncertain = self._step_is_indeterminate(step)
            if uncertain:
                intent = self._intents.get(resolution.key)
                task = (
                    str(intent.task_id)
                    if intent is not None
                    else self._task_by_key.get(resolution.key, "")
                )
                self._uncertain[resolution.key] = (task, step)
                self._committed.pop(resolution.key, None)
                if self._store is not None:
                    self._store.finish_failed(
                        effect_key=resolution.key,
                        holder_id=resolution.holder_id,
                        fencing_token=resolution.fencing_token,
                        side_effecting=True,
                        reason="the server-sealed effect receipt is indeterminate",
                    )
            elif step.success:
                if self._store is not None:
                    committed = self._store.commit(
                        effect_key=resolution.key,
                        holder_id=resolution.holder_id,
                        fencing_token=resolution.fencing_token,
                        step=step,
                    )
                    if not committed:
                        self._live.discard(resolution.key)
                        self._condition.notify_all()
                        raise EffectLeaseLost("tool-effect lease was lost before result commit")
                self._committed[resolution.key] = step
                self._uncertain.pop(resolution.key, None)
            elif not self._intent_is_side_effecting(resolution.key):
                self._intents.pop(resolution.key, None)
                if self._store is not None:
                    self._store.finish_failed(
                        effect_key=resolution.key,
                        holder_id=resolution.holder_id,
                        fencing_token=resolution.fencing_token,
                        side_effecting=False,
                        reason=str(step.result.error_type or "tool execution failed"),
                    )
            elif self._store is not None:
                self._store.finish_failed(
                    effect_key=resolution.key,
                    holder_id=resolution.holder_id,
                    fencing_token=resolution.fencing_token,
                    side_effecting=True,
                    reason=(
                        "the side-effecting handler returned without a successful durable result"
                    ),
                )
            self._live.discard(resolution.key)
            self._condition.notify_all()

    def abandon(self, resolution: EffectResolution) -> None:
        """Release an execution claim while retaining any durable intent."""

        with self._condition:
            self._stop_heartbeat(resolution.key)
            if self._store is not None and resolution.key not in self._intents:
                self._store.release_unstarted(
                    effect_key=resolution.key,
                    holder_id=resolution.holder_id,
                    fencing_token=resolution.fencing_token,
                )
            self._live.discard(resolution.key)
            self._condition.notify_all()

    def _intent_is_side_effecting(self, key: str) -> bool:
        intent = self._intents.get(key)
        return bool(intent and intent.side_effecting)

    @staticmethod
    def _step_is_indeterminate(step: Step) -> bool:
        receipt = step.result.effect_receipt
        return (
            isinstance(receipt, dict)
            and receipt.get("schema") == EFFECT_RECEIPT_SCHEMA
            and receipt.get("sealed") is True
            and receipt.get("emitted_by") == "tool_executor"
            and receipt.get("state") == "indeterminate"
        )

    def _observe_step(self, task: str, key: str, step: Step) -> None:
        if self._step_is_indeterminate(step):
            self._uncertain[key] = (task, step)
            self._committed.pop(key, None)
        elif step.success:
            self._committed[key] = step
            self._uncertain.pop(key, None)

    def _refresh_from_journal(self, *, strict: bool = False) -> None:
        if strict:
            reader = getattr(self._journal, "read_by_type_for_recovery", None)
            if callable(reader):
                # Effects are represented by exactly these two event types.
                # Reading both through the strict backend preserves the
                # completeness guarantee while keeping compatibility with
                # journals that only implement the original read_all API.
                events = [
                    event
                    for event_type in ("step", "tool_effect_intent")
                    for event in reader(event_type)
                ]
            else:
                events = self._journal.read_all()
        else:
            events = self._journal.read_all()
        for event in events:
            event_id = str(event.event_id)
            if event_id in self._seen_event_ids:
                continue
            self._seen_event_ids.add(event_id)
            if isinstance(event, StepEvent):
                task = str(event.task_id)
                self._native_max_steps[task] = max(
                    self._native_max_steps.get(task, 0), event.step.step_id
                )
                call_id = str(event.step.action.call_id)
                self._steps_by_call_id[call_id] = event.step
                key = self._effect_by_call_id.get(call_id)
                if key is None:
                    key = effect_key(
                        event.task_id,
                        event.step.step_id,
                        event.step.action.sucker_id,
                        event.step.action.args,
                    )
                self._observe_step(task, key, event.step)
            elif isinstance(event, ToolEffectIntentEvent):
                task = str(event.task_id)
                self._native_max_steps[task] = max(
                    self._native_max_steps.get(task, 0), event.step_id
                )
                self._intents[event.effect_key] = event
                self._effect_by_call_id[event.call_id] = event.effect_key
                step = self._steps_by_call_id.get(event.call_id)
                if step is not None:
                    self._observe_step(task, event.effect_key, step)

    def _start_heartbeat(self, resolution: EffectResolution) -> None:
        if self._store is None or resolution.fencing_token <= 0:
            return
        stop = threading.Event()
        self._heartbeats[resolution.key] = stop
        interval = max(0.05, self._lease_ttl_s / 3)

        def _renew() -> None:
            while not stop.wait(interval):
                assert self._store is not None
                if not self._store.renew(
                    effect_key=resolution.key,
                    holder_id=resolution.holder_id,
                    fencing_token=resolution.fencing_token,
                    lease_ttl_s=self._lease_ttl_s,
                ):
                    return

        threading.Thread(
            target=_renew,
            name=f"effect-lease-{resolution.key[-8:]}",
            daemon=True,
        ).start()

    def _stop_heartbeat(self, key: str) -> None:
        stop = self._heartbeats.pop(key, None)
        if stop is not None:
            stop.set()


def indeterminate_step(
    *,
    step_id: int,
    node_id: str,
    call: ToolCall,
    effect_key: str,
    fencing_token: int = 0,
    reason: str,
) -> Step:
    result = ExecutionResult(
        call_id=call.call_id,
        status="failed",
        output={
            "error": reason,
            "status": "indeterminate",
            "side_effect_may_have_happened": True,
            "retry_safe": False,
            # Operator-safe signal for the realtime item protocol. Keep
            # arguments and handler output out of this envelope: the main
            # timeline needs only identity + state to surface the incident
            # immediately, while the authenticated detail endpoint remains
            # authoritative for mutation controls.
            "effect_receipt": {
                "effect_key": effect_key,
                "call_id": str(call.call_id),
                "state": "indeterminate",
                "reason": reason,
                "fencing_token": max(0, int(fencing_token)),
            },
        },
        error_type="indeterminate_side_effect",
        stderr_tags=["durable_effect_indeterminate", "manual_reconciliation_required"],
        cost=CostEntry(),
        effect_receipt={
            "schema": EFFECT_RECEIPT_SCHEMA,
            "sealed": True,
            "emitted_by": "tool_executor",
            "tool_name": str(call.sucker_id),
            "call_id": str(call.call_id),
            "effect_key": effect_key,
            "fencing_token": max(0, int(fencing_token)),
            "effect_class": "external_or_unknown",
            "state": "indeterminate",
            "handler_entered": True,
            "retry_safe": False,
            "reason": reason[:240],
        },
    )
    return Step(
        step_id=step_id,
        node_id=node_id,
        action=call,
        result=result,
        immune_verdict="allow",
    )


def _replayed_step(step: Step, *, effect_key: str) -> Step:
    tags = list(step.result.stderr_tags)
    if "durable_effect_replay" not in tags:
        tags.append("durable_effect_replay")
    existing = step.result.effect_receipt
    if (
        isinstance(existing, dict)
        and existing.get("schema") == EFFECT_RECEIPT_SCHEMA
        and existing.get("sealed") is True
        and existing.get("emitted_by") == "tool_executor"
    ):
        effect_receipt = {
            **existing,
            "state": "replayed",
            "replayed_from_state": str(existing.get("state") or "committed"),
            "retry_safe": False,
        }
    else:
        # Legacy journal/store rows did not carry a server-owned effect
        # classification. Replaying them remains transport-safe, but they are
        # ineligible as evidence for autonomous repair learning.
        effect_receipt = {
            "schema": EFFECT_RECEIPT_SCHEMA,
            "sealed": False,
            "emitted_by": "legacy_effect_replay",
            "tool_name": str(step.action.sucker_id),
            "call_id": str(step.action.call_id),
            "effect_key": effect_key,
            "effect_class": "external_or_unknown",
            "state": "replayed",
            "handler_entered": True,
            "retry_safe": False,
        }
    result = step.result.model_copy(
        update={
            "stderr_tags": tags,
            "cost": CostEntry(),
            "effect_receipt": effect_receipt,
            "ts": now_utc(),
        }
    )
    return step.model_copy(update={"result": result, "ts": now_utc()})


def _stable_default(value: Any) -> str:
    if hasattr(value, "model_dump"):
        return json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return str(value)


def _canonical_effect_value(value: Any) -> Any:
    """Remove volatile runtime plumbing from a logical tool identity."""

    if isinstance(value, Mapping):
        return {
            str(key): _canonical_effect_value(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_RUNTIME_ARG_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_effect_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        canonical = [_canonical_effect_value(item) for item in value]
        return sorted(
            canonical,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                default=_stable_default,
            ),
        )
    if isinstance(value, Path):
        return str(value.expanduser().resolve(strict=False))
    if hasattr(value, "model_dump"):
        return _canonical_effect_value(value.model_dump(mode="json"))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {
        "__runtime_type__": f"{type(value).__module__}.{type(value).__qualname__}",
    }


__all__ = [
    "EFFECT_RECEIPT_SCHEMA",
    "EffectLeaseLost",
    "EffectResolution",
    "NativeEffectRecoveryRequired",
    "ToolEffectReceiptIndex",
    "args_fingerprint",
    "build_server_effect_receipt",
    "effect_key",
    "indeterminate_step",
    "is_side_effecting",
    "not_executed_effect_receipt",
    "should_refresh_read_result",
]
