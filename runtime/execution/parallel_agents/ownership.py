"""Per-user ownership enforcement for ``ParallelAgentOrchestrator``.

Mixin pattern: methods defined here become methods of
``ParallelAgentOrchestrator`` via inheritance. Keeps ownership logic
visually grouped while preserving full access to instance state
(``self._lock``, ``self._batches``, ``self._task_index``,
``self._publish_task_update_locked``, ``self._maybe_close_batch_locked``).

Why a mixin instead of standalone functions:
  - Ownership methods need ``self._lock`` and the batch dict — passing
    them as arguments would be ugly (``ownership.cancel_all_for_owner(
    orch, owner_id)``) and break encapsulation.
  - The mixin file gets its own focused docstring explaining the
    visibility rules (None-owner = visible-to-all, etc.) without
    bloating the main orchestrator file.
  - ``mypy`` / IDEs see the methods on the class via standard MRO, no
    duck-typing tricks needed.

The mixin assumes the inheriting class has these attributes:
  - ``self._lock`` (threading.RLock)
  - ``self._batches`` (dict[str, _BatchEntry])
  - ``self._task_index`` (dict[str, str])  task_id → batch_id
  - ``self._publish_task_update_locked(batch, entry, *, phase, message)``
  - ``self._maybe_close_batch_locked(batch)``
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def _now() -> datetime:
    """Local helper to avoid importing from orchestrator (circular)."""
    return datetime.now(UTC)


class OwnershipMixin:
    """Per-user ownership enforcement for parallel batches.

    Mixed into ``ParallelAgentOrchestrator`` to provide:
      - ``get_batch_owner(batch_id)`` — owner_id or None
      - ``get_task_owner(task_id)`` — owner_id of containing batch
      - ``list_batch_ids_for_owner(owner_id)`` — visibility filter
      - ``cancel_all_for_owner(owner_id)`` — scoped cancel-all

    Visibility rule (router enforcement adds the authenticated boundary):
      - Batches with ``owner_id is None`` remain addressable in single-user
        dev mode, but authenticated routers expose them only to admins.
      - Batches with a specific owner_id are visible only to that owner.
      - Passing ``owner_id=None`` from the caller side returns ALL batches
        for the unauthenticated single-user control path.
    """

    # Type hints for the attributes the mixin reads — Python doesn't
    # enforce these but they document the contract for readers.
    _lock: object  # threading.RLock — the orchestrator's lock
    _batches: dict  # str -> _BatchEntry
    _task_index: dict  # task_id -> batch_id

    def get_batch_owner(self, batch_id: str) -> str | None:
        """Return ``owner_id`` for a batch, or None if unowned/missing.

        Used by parallel_agents_router to filter cross-user reads.
        Returning None for missing batches is intentional — the
        endpoint will then 404 rather than 403, matching how other
        routers handle missing resources.
        """
        with self._lock:  # type: ignore[attr-defined]
            batch = self._batches.get(batch_id)
            if batch is None:
                return None
            return batch.owner_id

    def get_task_owner(self, task_id: str) -> str | None:
        """Return ``owner_id`` for the batch containing ``task_id``."""
        with self._lock:  # type: ignore[attr-defined]
            bid = self._task_index.get(task_id)
            if bid is None:
                return None
            batch = self._batches.get(bid)
            return batch.owner_id if batch is not None else None

    def list_batch_ids_for_owner(
        self,
        owner_id: str | None,
    ) -> list[str]:
        """Return batch ids visible to ``owner_id`` (see visibility rule)."""
        with self._lock:  # type: ignore[attr-defined]
            if owner_id is None:
                return list(self._batches.keys())
            return [bid for bid, batch in self._batches.items() if batch.owner_id == owner_id]

    def cancel_all_for_owner(self, owner_id: str | None) -> bool:
        """Cancel only the batches the caller owns.

        ``None`` cancels everything (dev mode). Each task in eligible
        batches gets its cancel_event set; pending tasks are marked
        cancelled immediately and the batch is closed if all tasks
        are now terminal.
        """
        with self._lock:  # type: ignore[attr-defined]
            for batch in list(self._batches.values()):
                if owner_id is not None and batch.owner_id != owner_id:
                    continue
                for entry in batch.tasks.values():
                    if entry.status in ("completed", "failed", "cancelled", "timed_out"):
                        continue
                    entry.cancel_event.set()
                    entry.cancel_requested_at = entry.cancel_requested_at or _now()
                    if entry.status == "pending":
                        entry.status = "cancelled"
                        entry.completed_at = _now()
                        self._publish_task_update_locked(  # type: ignore[attr-defined]
                            batch,
                            entry,
                            phase="cancelled",
                            message=(f"{entry.subagent_name} cancelled before start"),
                        )
                self._maybe_close_batch_locked(batch)  # type: ignore[attr-defined]
        return True


# Re-export for backward compat with any callers that imported time
# from this module's neighbors.
__all__ = ["OwnershipMixin"]
_TIME_KEEP_REF = time  # silence unused-import linters
