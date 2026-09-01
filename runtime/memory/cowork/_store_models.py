"""Internal data classes, phase constants, and helpers for ``store.py``.

This module is a private structural extraction — it owns the model
layer (``Task`` / ``Plan`` / ``Assignment``), the phase state-machine
constants, the task-id validation regex, the per-path lock registry,
and the small helpers (``_now_iso`` / ``_session_hash`` /
``_default_base_dir``) that ``CoworkStore`` and ``KanbanDispatcher``
build on. Everything is re-exported from ``store.py`` so callers
should keep importing from the public module.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Default lease duration for a claimed task. Workers must call
# ``update_assignment_status`` to keep the lease alive (or complete
# the task). Expired leases are reset by ``KanbanDispatcher``.
DEFAULT_LEASE_SECONDS = 600  # 10 minutes
DEFAULT_SYNTHESIS_TIMEOUT_SECONDS = 1800  # 30 minutes


# ─── Phase model ────────────────────────────────────────────

PHASE_PLAN = "plan"
PHASE_WORK = "work"
PHASE_SYNTHESIZE = "synthesize"
PHASE_COMPLETE = "complete"
PHASE_FAILED = "failed"

VALID_PHASES = frozenset({PHASE_PLAN, PHASE_WORK, PHASE_SYNTHESIZE, PHASE_COMPLETE, PHASE_FAILED})

# Allowed forward transitions. ``failed`` is reachable from any
# phase as a manual irreversible escape hatch and is added below.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    PHASE_PLAN: frozenset({PHASE_WORK, PHASE_FAILED}),
    PHASE_WORK: frozenset({PHASE_SYNTHESIZE, PHASE_FAILED}),
    PHASE_SYNTHESIZE: frozenset({PHASE_COMPLETE, PHASE_FAILED}),
    PHASE_COMPLETE: frozenset(),  # terminal
    PHASE_FAILED: frozenset(),  # terminal
}

# Assignment status values. We don't enforce a strict status machine
# here — agents may legitimately go claimed → in_progress → done, or
# straight to failed — but we DO validate the literal value to catch
# typos that would otherwise silently corrupt the on-disk JSON.
_VALID_ASSIGN_STATUS = frozenset({"claimed", "in_progress", "done", "failed"})

_FINAL_TASK_ID = "__final__"
_SAFE_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,239}$")


def _require_task_id(value: str, *, label: str = "task_id") -> str:
    task_id = str(value or "").strip()
    if not _SAFE_TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"invalid {label}: use letters, numbers, dot, underscore, or hyphen")
    return task_id


# ─── Data classes ───────────────────────────────────────────


@dataclass
class Task:
    """One unit of work in the plan."""

    id: str
    title: str
    description: str = ""
    required_capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "required_capabilities": list(self.required_capabilities),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        return cls(
            id=_require_task_id(str(raw["id"])),
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            required_capabilities=[str(c) for c in (raw.get("required_capabilities") or [])],
        )


@dataclass
class Plan:
    """Top-level cowork plan persisted as ``plan.json``."""

    session_id: str
    created_at: str
    created_by: str
    phase: str
    tasks: list[Task] = field(default_factory=list)
    phase_updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "phase": self.phase,
            "tasks": [t.to_dict() for t in self.tasks],
            "phase_updated_at": self.phase_updated_at or self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Plan:
        phase = str(raw.get("phase") or PHASE_PLAN)
        if phase not in VALID_PHASES:
            phase = PHASE_PLAN
        tasks_raw = raw.get("tasks") or []
        return cls(
            session_id=str(raw["session_id"]),
            created_at=str(raw.get("created_at") or ""),
            created_by=str(raw.get("created_by") or ""),
            phase=phase,
            tasks=[Task.from_dict(t) for t in tasks_raw if isinstance(t, dict) and t.get("id")],
            phase_updated_at=str(raw.get("phase_updated_at") or raw.get("created_at") or ""),
        )


@dataclass
class Assignment:
    """One row of ``assignments.json``."""

    agent_id: str
    claimed_at: str
    status: str = "claimed"
    artifact_ref: str | None = None
    completed_at: str | None = None
    # Kanban lease: ISO timestamp after which the claim expires and
    # another worker may re-claim the task. None = no expiry (legacy).
    lease_expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "claimed_at": self.claimed_at,
            "status": self.status,
            "artifact_ref": self.artifact_ref,
            "completed_at": self.completed_at,
            "lease_expires_at": self.lease_expires_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Assignment:
        status = str(raw.get("status") or "claimed")
        if status not in _VALID_ASSIGN_STATUS:
            status = "claimed"
        return cls(
            agent_id=str(raw.get("agent_id") or ""),
            claimed_at=str(raw.get("claimed_at") or ""),
            status=status,
            artifact_ref=(
                str(raw["artifact_ref"]) if raw.get("artifact_ref") is not None else None
            ),
            completed_at=(
                str(raw["completed_at"]) if raw.get("completed_at") is not None else None
            ),
            lease_expires_at=(
                str(raw["lease_expires_at"]) if raw.get("lease_expires_at") is not None else None
            ),
        )


# ─── Helpers ────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _session_hash(session_id: str) -> str:
    return hashlib.sha1(session_id.encode("utf-8"), usedforsecurity=False).hexdigest()


# ─── Per-path lock registry ─────────────────────────────────


class _PathLockRegistry:
    """One ``threading.Lock`` per absolute path, deduped on creation.

    Same trick ``ambient_suggestions._BucketLock`` uses; reproduced
    here to keep the cowork module self-contained.
    """

    _locks: dict[str, threading.Lock] = {}
    _guard = threading.Lock()

    @classmethod
    def for_path(cls, path: Path) -> threading.Lock:
        key = str(path.resolve() if path.exists() else path.absolute())
        with cls._guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                cls._locks[key] = lock
            return lock


# ─── Default base directory ─────────────────────────────────


def _default_base_dir() -> Path:
    """Resolve ``data/cowork`` the same way other memory modules do.

    ``ECHO_DATA_DIR`` wins when set (tests, alternative installs);
    falls back to ``<cwd>/data/cowork`` via ``app_paths``.
    """
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "cowork"
