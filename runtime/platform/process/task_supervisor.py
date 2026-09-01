from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.platform.process._task_supervisor_analysis import (
    build_task_recovery_queue,
    build_task_runs_overview,
    task_lease_health,
    task_recovery_advice,
)
from runtime.platform.process._task_supervisor_models import (
    ACTIVE_TASK_STATUSES,
    DEFAULT_CAPABILITY_GROUPS,
    TERMINAL_TASK_STATUSES,
    LostTaskLease,
    TaskCapabilityManifest,
    TaskLease,
    TaskLeaseConflict,
    TaskLeaseError,
    TaskRunRecord,
    TaskRunStatus,
    _now_iso,
)
from runtime.platform.process._task_supervisor_store import TaskSupervisorStore

_DEFAULT_HOLDER_ID = f"{socket.gethostname()}:{uuid4().hex[:12]}"


class TaskSupervisor:
    def __init__(
        self,
        store: TaskSupervisorStore,
        *,
        holder_id: str | None = None,
        lease_ttl_seconds: float = 300.0,
    ) -> None:
        self.store = store
        self.holder_id = str(holder_id or _DEFAULT_HOLDER_ID)
        self.lease_ttl_seconds = max(1.0, float(lease_ttl_seconds))
        self._lease_tokens: dict[str, int] = {}
        self._lease_lock = threading.RLock()

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        holder_id: str | None = None,
        lease_ttl_seconds: float = 300.0,
    ) -> TaskSupervisor:
        return cls(
            TaskSupervisorStore(path),
            holder_id=holder_id,
            lease_ttl_seconds=lease_ttl_seconds,
        )

    def start_task(
        self,
        *,
        task_id: str,
        kind: str = "task",
        owner_id: str | None = None,
        thread_id: str | None = None,
        title: str = "",
        goal: str = "",
        mode: str = "",
        workspace_path: str | None = None,
        capabilities: TaskCapabilityManifest | dict[str, Any] | None = None,
        parent_task_id: str | None = None,
        origin_task_id: str | None = None,
        resume_checkpoint_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: TaskRunStatus = TaskRunStatus.RUNNING,
    ) -> TaskRunRecord:
        def _mutate(
            existing: TaskRunRecord | None,
            next_lease_token: Callable[[], int],
        ) -> TaskRunRecord:
            lease = existing.lease if existing is not None else None
            if (
                existing is not None
                and existing.status not in TERMINAL_TASK_STATUSES
                and lease is not None
                and not lease.expired
                and lease.holder_id != self.holder_id
            ):
                raise TaskLeaseConflict(task_id, lease.holder_id)
            if lease is None or lease.expired or lease.holder_id == self.holder_id:
                lease = self._new_lease(next_lease_token())
            now = _now_iso()
            completed_at = (
                existing.completed_at
                if existing is not None and status in TERMINAL_TASK_STATUSES
                else None
            )
            if status in TERMINAL_TASK_STATUSES:
                completed_at = completed_at or now
                lease = None
            next_workspace_path = _prefer_text(
                workspace_path,
                existing.workspace_path if existing is not None else None,
            )
            manifest = _coerce_manifest(
                capabilities
                if capabilities is not None
                else existing.capabilities
                if existing is not None
                else None,
                workspace_path=next_workspace_path,
            )
            next_metadata = dict(existing.metadata) if existing is not None else {}
            if isinstance(metadata, dict):
                next_metadata.update(metadata)
            if existing is not None and existing.status in TERMINAL_TASK_STATUSES:
                events = list(next_metadata.get("restart_events") or [])
                restart_event = {
                    "previous_status": existing.status.value,
                    "previous_completed_at": existing.completed_at,
                    "previous_terminal_reason": existing.terminal_reason,
                    "previous_checkpoint_id": existing.latest_checkpoint_id,
                    "restarted_at": now,
                    "holder_id": self.holder_id,
                    "next_status": status.value,
                }
                events.append(restart_event)
                next_metadata.update(
                    {
                        "restart": True,
                        "restart_at": now,
                        "restart_holder_id": self.holder_id,
                        "restart_from_status": existing.status.value,
                        "restart_from_checkpoint_id": existing.latest_checkpoint_id,
                        "restart_events": events,
                    }
                )
            return TaskRunRecord(
                task_id=task_id,
                kind=_prefer_kind(kind, existing.kind if existing is not None else None),
                owner_id=_prefer_text(
                    owner_id,
                    existing.owner_id if existing is not None else None,
                ),
                thread_id=_prefer_text(
                    thread_id,
                    existing.thread_id if existing is not None else None,
                ),
                parent_task_id=_prefer_text(
                    parent_task_id,
                    existing.parent_task_id if existing is not None else None,
                ),
                origin_task_id=_prefer_text(
                    origin_task_id,
                    existing.origin_task_id if existing is not None else None,
                ),
                resume_checkpoint_id=_prefer_text(
                    resume_checkpoint_id,
                    existing.resume_checkpoint_id if existing is not None else None,
                ),
                status=status,
                title=_prefer_text(title, existing.title if existing is not None else None) or "",
                goal=_prefer_text(goal, existing.goal if existing is not None else None) or "",
                mode=_prefer_text(mode, existing.mode if existing is not None else None) or "",
                workspace_path=next_workspace_path,
                capabilities=manifest,
                lease=lease,
                terminal_reason=(
                    existing.terminal_reason
                    if existing is not None and status in TERMINAL_TASK_STATUSES
                    else ""
                ),
                latest_checkpoint_id=existing.latest_checkpoint_id
                if existing is not None
                else None,
                metadata=next_metadata,
                started_at=(existing.started_at if existing is not None else None) or now,
                completed_at=completed_at,
                heartbeat_at=now,
            )

        record = self.store.upsert_mutate(task_id, _mutate)
        self._remember_lease(record)
        return record

    def transition(
        self,
        task_id: str,
        status: TaskRunStatus | str,
        *,
        reason: str = "",
        checkpoint_id: str | int | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> TaskRunRecord:
        next_status = status if isinstance(status, TaskRunStatus) else TaskRunStatus(str(status))
        now = _now_iso()

        def _mutate(current: TaskRunRecord) -> TaskRunRecord:
            if current.status in TERMINAL_TASK_STATUSES:
                metadata = dict(current.metadata)
                if isinstance(metadata_patch, dict):
                    metadata.update(metadata_patch)
                terminal_events = list(metadata.get("terminal_transition_events") or [])
                checkpoint_recorded_to_latest = (
                    checkpoint_id is not None and current.latest_checkpoint_id is None
                )
                terminal_events.append(
                    {
                        "ignored_status": next_status.value,
                        "reason": str(reason or ""),
                        "checkpoint_id": checkpoint_id,
                        "checkpoint_recorded_to_latest": checkpoint_recorded_to_latest,
                        "previous_status": current.status.value,
                        "previous_terminal_reason": current.terminal_reason,
                        "previous_completed_at": current.completed_at,
                        "previous_checkpoint_id": current.latest_checkpoint_id,
                        "recorded_at": now,
                        "holder_id": self.holder_id,
                    }
                )
                metadata["terminal_transition_events"] = terminal_events
                return current.model_copy(
                    update={
                        "latest_checkpoint_id": checkpoint_id
                        if checkpoint_recorded_to_latest
                        else current.latest_checkpoint_id,
                        "metadata": metadata,
                        "heartbeat_at": current.heartbeat_at or now,
                        "lease": None,
                    },
                    deep=True,
                )
            if current.status not in TERMINAL_TASK_STATUSES:
                self._assert_current_holder(current)
            metadata = dict(current.metadata)
            if isinstance(metadata_patch, dict):
                metadata.update(metadata_patch)
            completed_at = current.completed_at
            lease = current.lease
            if current.status not in TERMINAL_TASK_STATUSES and lease is not None:
                lease = lease.model_copy(
                    update={
                        "heartbeat_at": now,
                        "expires_at": time.time() + self.lease_ttl_seconds,
                    }
                )
            if next_status in TERMINAL_TASK_STATUSES:
                completed_at = completed_at or now
                lease = None
            return current.model_copy(
                update={
                    "status": next_status,
                    "terminal_reason": reason or current.terminal_reason,
                    "latest_checkpoint_id": checkpoint_id
                    if checkpoint_id is not None
                    else current.latest_checkpoint_id,
                    "metadata": metadata,
                    "heartbeat_at": now,
                    "completed_at": completed_at,
                    "lease": lease,
                },
                deep=True,
            )

        record = self.store.mutate(task_id, _mutate)
        self._remember_lease(record)
        return record

    def recover_stale_turn(
        self,
        task_id: str,
        status: TaskRunStatus | str,
        *,
        expected_turn_id: str,
        reason: str,
        checkpoint_id: str | int | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> TaskRunRecord:
        """Close or pause a task whose owning realtime turn is known stale.

        A process restart leaves the previous worker's lease looking healthy
        until its TTL elapses.  Normal ``transition`` must reject that foreign
        lease, but startup replay has stronger evidence: the matching realtime
        turn has no live turn lease.  This narrowly-scoped recovery operation
        verifies the turn identity before releasing the orphaned worker lease.
        """
        next_status = status if isinstance(status, TaskRunStatus) else TaskRunStatus(str(status))
        if next_status not in {*TERMINAL_TASK_STATUSES, TaskRunStatus.PAUSED}:
            raise ValueError("stale turn recovery may only pause or finish a task")
        clean_turn_id = str(expected_turn_id or "").strip()
        if not clean_turn_id:
            raise ValueError("expected_turn_id is required")
        now = _now_iso()

        def _mutate(current: TaskRunRecord) -> TaskRunRecord:
            recorded_turn_id = str(
                current.origin_task_id or current.metadata.get("turn_id") or ""
            ).strip()
            if recorded_turn_id != clean_turn_id:
                raise ValueError(
                    f"task {task_id!r} belongs to turn {recorded_turn_id!r}, not {clean_turn_id!r}"
                )
            if current.status in TERMINAL_TASK_STATUSES:
                return current
            metadata = dict(current.metadata)
            if isinstance(metadata_patch, dict):
                metadata.update(metadata_patch)
            recovery_events = list(metadata.get("stale_turn_recovery_events") or [])
            recovery_events.append(
                {
                    "turn_id": clean_turn_id,
                    "previous_status": current.status.value,
                    "previous_holder_id": (
                        current.lease.holder_id if current.lease is not None else None
                    ),
                    "status": next_status.value,
                    "reason": str(reason or ""),
                    "recovered_at": now,
                    "holder_id": self.holder_id,
                }
            )
            metadata["stale_turn_recovery_events"] = recovery_events
            return current.model_copy(
                update={
                    "status": next_status,
                    "terminal_reason": str(reason or current.terminal_reason),
                    "latest_checkpoint_id": (
                        checkpoint_id if checkpoint_id is not None else current.latest_checkpoint_id
                    ),
                    "metadata": metadata,
                    "heartbeat_at": now,
                    "completed_at": (now if next_status in TERMINAL_TASK_STATUSES else None),
                    "lease": None,
                },
                deep=True,
            )

        record = self.store.mutate(task_id, _mutate)
        self._remember_lease(record)
        return record

    def record_approval_decision(
        self,
        task_id: str,
        *,
        approved: bool,
        decided_by: str | None = None,
        reason: str = "",
        resume_status: TaskRunStatus = TaskRunStatus.RUNNING,
    ) -> TaskRunRecord:
        now = _now_iso()
        clean_reason = str(reason or "").strip()
        clean_actor = str(decided_by or "").strip() or None
        next_status = resume_status if approved else TaskRunStatus.PAUSED
        if next_status in TERMINAL_TASK_STATUSES:
            raise ValueError("approval decision cannot transition directly to terminal")

        def _mutate(current: TaskRunRecord) -> TaskRunRecord:
            if current.status != TaskRunStatus.WAITING_APPROVAL:
                raise ValueError("task is not waiting for approval")
            self._assert_current_holder(current)
            metadata = dict(current.metadata)
            if bool(metadata.get("capability_denied")):
                raise ValueError("task is blocked by disabled capability")
            if bool(metadata.get("approval_denied")) and not bool(
                metadata.get("approval_required")
            ):
                raise ValueError("task is blocked by approval policy")
            decisions = list(metadata.get("approval_decisions") or [])
            decision = {
                "approved": bool(approved),
                "decided_by": clean_actor,
                "reason": clean_reason,
                "decided_at": now,
                "tool_name": metadata.get("approval_tool_name"),
                "approval_action": metadata.get("approval_action"),
            }
            decisions.append(decision)
            metadata.update(
                {
                    "approval_required": False,
                    "approval_denied": not approved,
                    "approval_decision": "approved" if approved else "rejected",
                    "approval_decided_by": clean_actor,
                    "approval_decided_at": now,
                    "approval_decision_reason": clean_reason,
                    "approval_decisions": decisions,
                }
            )
            if approved:
                metadata.pop("approval_reason", None)
            lease = current.lease
            if lease is not None:
                lease = lease.model_copy(
                    update={
                        "heartbeat_at": now,
                        "expires_at": time.time() + self.lease_ttl_seconds,
                    }
                )
            return current.model_copy(
                update={
                    "status": next_status,
                    "terminal_reason": "" if approved else clean_reason,
                    "metadata": metadata,
                    "heartbeat_at": now,
                    "completed_at": None,
                    "lease": lease,
                },
                deep=True,
            )

        record = self.store.mutate(task_id, _mutate)
        self._remember_lease(record)
        return record

    def takeover_task(
        self,
        task_id: str,
        *,
        by: str | None = None,
        reason: str = "",
        status: TaskRunStatus | str | None = None,
    ) -> TaskRunRecord:
        requested_status = (
            status
            if isinstance(status, TaskRunStatus)
            else TaskRunStatus(str(status))
            if status is not None
            else TaskRunStatus.RUNNING
        )
        if requested_status in TERMINAL_TASK_STATUSES:
            raise ValueError("takeover cannot transition directly to terminal")
        clean_actor = str(by or "").strip() or None
        clean_reason = str(reason or "").strip()

        def _mutate(
            existing: TaskRunRecord | None,
            next_lease_token: Callable[[], int],
        ) -> TaskRunRecord:
            if existing is None:
                raise KeyError(task_id)
            if existing.status in TERMINAL_TASK_STATUSES:
                raise ValueError("terminal task cannot be taken over")
            if existing.status == TaskRunStatus.WAITING_APPROVAL and (
                bool(existing.metadata.get("capability_denied"))
                or (
                    bool(existing.metadata.get("approval_denied"))
                    and not bool(existing.metadata.get("approval_required"))
                )
            ):
                raise ValueError("non-approvable task cannot be taken over")
            lease = existing.lease
            if lease is not None and not lease.expired and lease.holder_id != self.holder_id:
                raise TaskLeaseConflict(task_id, lease.holder_id)
            if lease is not None and not lease.expired and lease.holder_id == self.holder_id:
                raise ValueError("task is already held by this worker")
            now = _now_iso()
            metadata = dict(existing.metadata)
            events = list(metadata.get("takeover_events") or [])
            event = {
                "by": clean_actor,
                "reason": clean_reason,
                "previous_holder_id": lease.holder_id if lease is not None else None,
                "previous_lease_token": lease.token if lease is not None else None,
                "previous_status": existing.status.value,
                "taken_over_at": now,
            }
            events.append(event)
            metadata.update(
                {
                    "takeover": True,
                    "takeover_by": clean_actor,
                    "takeover_reason": clean_reason,
                    "takeover_at": now,
                    "takeover_events": events,
                }
            )
            next_status = (
                TaskRunStatus.WAITING_APPROVAL
                if existing.status == TaskRunStatus.WAITING_APPROVAL
                else requested_status
            )
            return existing.model_copy(
                update={
                    "status": next_status,
                    "metadata": metadata,
                    "heartbeat_at": now,
                    "lease": self._new_lease(next_lease_token()),
                    "completed_at": None,
                    "terminal_reason": "",
                },
                deep=True,
            )

        record = self.store.upsert_mutate(task_id, _mutate)
        self._remember_lease(record)
        return record

    def heartbeat(self, task_id: str) -> TaskRunRecord:
        now = _now_iso()

        def _mutate(current: TaskRunRecord) -> TaskRunRecord:
            if current.status in TERMINAL_TASK_STATUSES:
                return current
            self._assert_current_holder(current)
            lease = current.lease
            assert lease is not None
            lease = lease.model_copy(
                update={
                    "heartbeat_at": now,
                    "expires_at": time.time() + self.lease_ttl_seconds,
                }
            )
            return current.model_copy(
                update={
                    "heartbeat_at": now,
                    "lease": lease,
                },
                deep=True,
            )

        record = self.store.mutate(task_id, _mutate)
        self._remember_lease(record)
        return record

    def is_current_holder(self, task_id: str) -> bool:
        record = self.store.get(task_id)
        if record is None or record.status in TERMINAL_TASK_STATUSES:
            return False
        try:
            self._assert_current_holder(record)
        except LostTaskLease:
            return False
        return True

    def assert_current_holder(self, task_id: str) -> TaskRunRecord:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        if record.status in TERMINAL_TASK_STATUSES:
            raise LostTaskLease(task_id, "task is already terminal")
        self._assert_current_holder(record)
        return record

    def task_capabilities(self, task_id: str) -> TaskCapabilityManifest | None:
        record = self.store.get(task_id)
        return record.capabilities if record is not None else None

    def _new_lease(self, token: int) -> TaskLease:
        return TaskLease(
            holder_id=self.holder_id,
            token=token,
            expires_at=time.time() + self.lease_ttl_seconds,
        )

    def _assert_current_holder(self, record: TaskRunRecord) -> None:
        lease = record.lease
        if lease is None:
            raise LostTaskLease(record.task_id, "missing lease")
        if lease.expired:
            raise LostTaskLease(record.task_id, "lease expired")
        if lease.holder_id != self.holder_id:
            raise LostTaskLease(record.task_id, f"held by {lease.holder_id!r}")
        with self._lease_lock:
            expected_token = self._lease_tokens.get(record.task_id)
        if expected_token is not None and lease.token != expected_token:
            raise LostTaskLease(record.task_id, "lease token changed")

    def _remember_lease(self, record: TaskRunRecord) -> None:
        with self._lease_lock:
            if record.lease is not None and record.lease.holder_id == self.holder_id:
                self._lease_tokens[record.task_id] = record.lease.token
            else:
                self._lease_tokens.pop(record.task_id, None)


def _coerce_manifest(
    value: TaskCapabilityManifest | dict[str, Any] | None,
    *,
    workspace_path: str | None = None,
) -> TaskCapabilityManifest:
    if isinstance(value, TaskCapabilityManifest):
        manifest = value
    elif isinstance(value, dict):
        manifest = TaskCapabilityManifest.model_validate(value)
    else:
        manifest = TaskCapabilityManifest()
    workspace = str(workspace_path or "").strip()
    if workspace and workspace not in manifest.workspace_paths:
        manifest = manifest.model_copy(
            update={"workspace_paths": [*manifest.workspace_paths, workspace]},
            deep=True,
        )
    return manifest


def _prefer_text(value: Any, fallback: Any = None) -> str | None:
    text = str(value or "").strip()
    if text:
        return text
    fallback_text = str(fallback or "").strip()
    return fallback_text or None


def _prefer_kind(value: Any, fallback: Any = None) -> str:
    fallback_text = str(fallback or "").strip()
    text = str(value or "").strip()
    if fallback_text and text in {"", "task"}:
        return fallback_text
    return text or fallback_text or "task"


def manifest_from_session_metadata(
    metadata: dict[str, Any] | None,
) -> TaskCapabilityManifest | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("task_capability_manifest") or metadata.get("capability_manifest")
    if isinstance(raw, TaskCapabilityManifest):
        return raw
    if isinstance(raw, dict):
        try:
            return TaskCapabilityManifest.model_validate(raw)
        except Exception:
            return None
    return None


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "DEFAULT_CAPABILITY_GROUPS",
    "LostTaskLease",
    "TERMINAL_TASK_STATUSES",
    "TaskCapabilityManifest",
    "TaskLease",
    "TaskLeaseConflict",
    "TaskLeaseError",
    "TaskRunRecord",
    "TaskRunStatus",
    "TaskSupervisor",
    "TaskSupervisorStore",
    "build_task_recovery_queue",
    "build_task_runs_overview",
    "manifest_from_session_metadata",
    "task_lease_health",
    "task_recovery_advice",
]
