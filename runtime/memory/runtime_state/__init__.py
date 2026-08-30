"""Runtime state · per-turn blackboard, hot cache, and file-transaction ledger.

Submodules:

* ``blackboard`` / ``blackboard_store`` — per-turn key/value blackboard
  (in-memory LRU+TTL by default, SQLite-backed when
  ``ECHO_BLACKBOARD_DB`` is set)
* ``hot_cache`` — per-agent session hot cache (24h TTL, atomic file write)
* ``hub`` — read-only facade aggregating user store / memory.md / planner
  sections (does not own writes yet)
* ``file_transactions`` — file-op ledger summarizer + optimistic rollback
* ``process_timeline`` — 5-lane task run timeline builder
* ``scope_paths`` — project root / scope path resolver from metadata

Existing imports go through the submodule path, e.g.::

    from runtime.memory.runtime_state.blackboard import get_blackboard
    from runtime.memory.runtime_state.hot_cache import SessionHotCache

This package intentionally does NOT re-export symbols at the top level
to avoid triggering heavy submodule imports (SQLite, pydantic) for
callers that only need a single module.
"""
