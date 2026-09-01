from __future__ import annotations

import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clean_optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


class TaskRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"
    FAILED = "failed"
    COMPLETED = "completed"


class TaskLeaseError(RuntimeError):
    def __init__(self, task_id: str, message: str) -> None:
        super().__init__(message)
        self.task_id = task_id


class TaskLeaseConflict(TaskLeaseError):
    def __init__(self, task_id: str, holder_id: str) -> None:
        super().__init__(
            task_id,
            f"task {task_id!r} is already leased by {holder_id!r}",
        )
        self.holder_id = holder_id


class LostTaskLease(TaskLeaseError):
    def __init__(self, task_id: str, reason: str) -> None:
        super().__init__(task_id, f"task {task_id!r} lease is no longer current: {reason}")
        self.reason = reason


TERMINAL_TASK_STATUSES = {
    TaskRunStatus.CANCELLED,
    TaskRunStatus.DISCONNECTED,
    TaskRunStatus.FAILED,
    TaskRunStatus.COMPLETED,
}
ACTIVE_TASK_STATUSES = {
    TaskRunStatus.RUNNING,
    TaskRunStatus.WAITING_APPROVAL,
    TaskRunStatus.PAUSED,
    TaskRunStatus.VERIFYING,
    TaskRunStatus.REPAIRING,
}


DEFAULT_CAPABILITY_GROUPS: dict[str, bool] = {
    "builtin": True,
    "web": True,
    "browser": True,
    "computer": True,
    "fs_write": True,
    "git": True,
    "shell": True,
    "memory": True,
}


class TaskCapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    groups: dict[str, bool] = Field(default_factory=lambda: dict(DEFAULT_CAPABILITY_GROUPS))
    workspace_paths: list[str] = Field(default_factory=list)
    # ``None`` preserves the historical group-only policy.  Supplying a list
    # switches the manifest to a fail-closed, exact skill allowlist; an empty
    # list therefore intentionally grants no tools at all.
    allowed_skill_ids: list[str] | None = None
    source: str = "default"
    created_at: str = Field(default_factory=_now_iso)

    @field_validator("groups", mode="before")
    @classmethod
    def _normalize_groups(cls, value: Any) -> dict[str, bool]:
        groups = dict(DEFAULT_CAPABILITY_GROUPS)
        if isinstance(value, dict):
            for key, enabled in value.items():
                clean_key = str(key or "").strip()
                if clean_key:
                    groups[clean_key] = bool(enabled)
        return groups

    @field_validator("workspace_paths", mode="before")
    @classmethod
    def _normalize_paths(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    @field_validator("allowed_skill_ids", mode="before")
    @classmethod
    def _normalize_allowed_skill_ids(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple, set, frozenset)):
            # An explicitly malformed allowlist must fail closed rather than
            # silently restoring legacy group-only access.
            return []
        out: list[str] = []
        for item in value:
            skill_id = str(item or "").strip()
            if skill_id and skill_id not in out:
                out.append(skill_id)
        return out

    def allows_skill(self, skill_id: str) -> bool:
        if self.allowed_skill_ids is None:
            return True
        return str(skill_id or "").strip() in self.allowed_skill_ids

    def allows_group(self, group: str | None) -> bool:
        if not group:
            return True
        return bool(self.groups.get(str(group), False))


class TaskLease(BaseModel):
    model_config = ConfigDict(extra="ignore")

    holder_id: str
    token: int = Field(ge=1)
    acquired_at: str = Field(default_factory=_now_iso)
    heartbeat_at: str = Field(default_factory=_now_iso)
    expires_at: float = 0.0

    @property
    def expired(self) -> bool:
        return self.expires_at > 0 and time.time() >= self.expires_at


class TaskRunRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., min_length=1)
    kind: str = "task"
    owner_id: str | None = None
    thread_id: str | None = None
    parent_task_id: str | None = None
    origin_task_id: str | None = None
    resume_checkpoint_id: str | None = None
    status: TaskRunStatus = TaskRunStatus.PENDING
    title: str = ""
    goal: str = ""
    mode: str = ""
    workspace_path: str | None = None
    capabilities: TaskCapabilityManifest = Field(default_factory=TaskCapabilityManifest)
    lease: TaskLease | None = None
    terminal_reason: str = ""
    latest_checkpoint_id: str | int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    heartbeat_at: str | None = None

    @field_validator(
        "owner_id",
        "thread_id",
        "parent_task_id",
        "origin_task_id",
        "resume_checkpoint_id",
        "workspace_path",
        mode="before",
    )
    @classmethod
    def _normalize_optional_fields(cls, value: Any) -> str | None:
        return _clean_optional(value)
