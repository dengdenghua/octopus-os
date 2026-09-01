"""
runtime.memory.cowork · file-system-backed cowork state machine.

Why this exists
---------------
``runtime.memory.runtime_state.blackboard`` is the in-memory, turn-scoped k/v store
parallel sub-agents share inside one user turn. It evaporates on
process restart and only spans threads of the same Python process.

That's not enough when multiple agents — possibly running in different
processes, possibly across crash/restart boundaries — need to
coordinate on a longer-running task. The design pattern: shared
JSON files on disk drive a small state machine. Agents write
their progress into per-session directories, a coordinator (or any
agent at all) reads them back. Durability falls out of using
``atomic_write_json`` everywhere.

Three phases
------------
  1. PLAN        — one agent decomposes the task into ``tasks[]``
                   and persists ``plan.json``.
  2. WORK        — each agent atomically claims a task by inserting
                   its agent_id into ``assignments.json``. The first
                   writer wins; everyone else sees the slot already
                   taken and moves on.
  3. SYNTHESIZE  — designated synthesizer reads every ``artifacts/<id>
                   .json`` and writes the final consolidated artifact
                   under task_id ``__final__``.

Storage layout
--------------

    data/cowork/<session_hash>/
        plan.json
        assignments.json
        artifacts/
            <task_id>.json
            __final__.json     (after synthesize)

``<session_hash>`` is SHA-1 of ``session_id`` so opaque session ids
hash safely into directory names (matches the per-project hashing
in ``ambient_suggestions``).

Atomic claim
------------
``claim_task`` is the one operation that needs honest concurrency
safety. Implementation: take the per-session assignments-file
mutex, read assignments.json, check if the task slot is empty, and
only on a clean miss do we write the new entry. ``atomic_write_json``
handles the disk-level atomicity (temp + rename); the in-process
``threading.Lock`` keyed on the file path serializes read-modify-write
within one Python process. Cross-process safety is good-enough on a
single host because both writers go through the same lock dictionary
in the rare case they share a process, and the file rename is
atomic at the OS level (last-writer-wins, but the
"check-before-write" still produces exactly one success in the
single-process threaded case the requirements call for).
"""

from __future__ import annotations

from runtime.memory.cowork.store import (
    VALID_PHASES,
    Assignment,
    CoworkStore,
    Plan,
    Task,
)

__all__ = [
    "Assignment",
    "CoworkStore",
    "Plan",
    "Task",
    "VALID_PHASES",
]
