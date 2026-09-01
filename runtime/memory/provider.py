"""MemoryProvider — the pluggable memory block interface.

Design doc: ``docs/architecture/blocks.md`` §2 (``memory`` block).

Why this exists
---------------
The memory system is five layered subsystems (journal → hemolymph →
knowledge_graph → threads → learning) and the default implementation is
excellent — but consumers reach into ``runtime.memory.journal`` directly.
That couples every memory consumer to the journal's concrete API and makes
"swap the storage backend" (vector store, relational DB, remote memory
service) a kernel change instead of a block swap.

This module defines the narrow contract a memory block must satisfy, plus
the journal-backed default implementation. The composition layer
(``ServiceBus``) exposes it as the ``memory`` service; any provider
implementing this protocol can replace it without touching consumers.

Contract notes
--------------
* ``store`` is append-only — implementations may reject (return ``False``)
  instead of mutating history; the journal never deletes.
* ``forget`` is best-effort: append-only stores return ``0`` (nothing
  removed) rather than pretending deletion happened.
* ``reflect`` is the learning hook (review/promote). The journal adapter
  returns ``[]`` because the learning loop lives in
  ``runtime.memory.learning``; a provider backed by the ledger can honour it.
* ``scope`` is an opaque tenant/visibility token passed straight through to
  the implementation (the journal expects ``TenantScope``).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

_LOG = logging.getLogger(__name__)


@runtime_checkable
class MemoryProvider(Protocol):
    """Narrow, implementation-agnostic memory contract."""

    def store(self, record: Any, *, scope: Any = None) -> bool:
        """Persist one record (event/note/embedding…). Append-only preferred."""
        ...

    def recall(
        self,
        *,
        session_id: str | None = None,
        event_type: str | None = None,
        scope: Any = None,
        limit: int = 50,
    ) -> list[Any]:
        """Return up to ``limit`` records matching the optional filters."""
        ...

    def forget(self, record_ids: list[str], *, scope: Any = None) -> int:
        """Best-effort removal; returns how many records were removed."""
        ...

    def reflect(self, trigger: str, *, scope: Any = None) -> list[Any]:
        """Learning hook: returns actions/recommendations, never raises."""
        ...

    def health(self) -> dict[str, Any]:
        """Cheap liveness/identity report for observability."""
        ...


class JournalMemoryProvider:
    """Default ``MemoryProvider`` backed by the append-only journal.

    Wraps any object with the journal surface (``write`` / ``read_all`` /
    ``read_by_session``) — the real ``Journal`` or a test double.
    """

    name = "journal"

    def __init__(self, journal: Any) -> None:
        self._journal = journal

    def store(self, record: Any, *, scope: Any = None) -> bool:
        try:
            self._journal.write(record)
            return True
        except Exception as exc:  # noqa: BLE001 — provider never raises to callers
            _LOG.warning("memory.store failed (journal write): %s", exc)
            return False

    def recall(
        self,
        *,
        session_id: str | None = None,
        event_type: str | None = None,
        scope: Any = None,
        limit: int = 50,
    ) -> list[Any]:
        if session_id:
            events = list(self._journal.read_by_session(session_id))
        else:
            events = list(self._journal.read_all(scope=scope))
        if event_type:
            events = [event for event in events if getattr(event, "event_type", None) == event_type]
        bound = max(0, limit)
        return events[:bound]

    def forget(self, record_ids: list[str], *, scope: Any = None) -> int:
        # Append-only: history is never deleted. The interface contract is
        # honest about this instead of faking removal.
        if record_ids:
            _LOG.debug("memory.forget ignored %d ids (journal is append-only)", len(record_ids))
        return 0

    def reflect(self, trigger: str, *, scope: Any = None) -> list[Any]:
        # Learning lives in runtime.memory.learning (experience_ledger /
        # review_queue / promotion_applier). A provider that wraps the
        # ledger can override this; the journal itself has no loop.
        return []

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "ok": True, "append_only": True}
