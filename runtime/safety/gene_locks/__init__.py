"""
Gene Locks · governance layer for DNA mutations (see docs/gene-locks.md).

MVP scope
---------

The full spec defines 7 lock types (IMMUTABLE, MONOTONIC, QUORUM,
LEVEL, CONDITIONAL, TEMPORAL, CASCADE) + a 5-level maturity system.
This MVP implements the subset that closes the concrete risks opened
by the RecipeForge / auto-tick autonomous-mutation surface:

  * LEVEL · which maturity level can touch which mutation kind
  * TEMPORAL · per-recipe cooldown between mutations
  * PANIC · kill-switch that freezes every autonomous mutation
  * QUORUM (soft MVP) · suggests human-approver headers without
    blocking, so current dev workflow stays unbroken

CONDITIONAL / CASCADE / MONOTONIC / IMMUTABLE are left for a future
round · the doc stays the authoritative spec.
"""

from runtime.safety.gene_locks.approver_ledger import get_ledger
from runtime.safety.gene_locks.simple_gate import (
    IMMUTABLE_FIELD_PATHS,
    LOCK_SYSTEM_VERSION,
    SCHEMA_VERSION,
    LockViolation,
    MaturityLevel,
    MutationKind,
    check_monotonic,
    clear_panic,
    gate_mutation,
    get_state,
    is_dev_mode,
    record_mutation,
    reset_integrity_alarm,
    set_maturity,
    set_mode,
    trigger_panic,
)

__all__ = [
    "LockViolation",
    "MutationKind",
    "MaturityLevel",
    "gate_mutation",
    "check_monotonic",
    "get_state",
    "set_maturity",
    "set_mode",
    "trigger_panic",
    "clear_panic",
    "reset_integrity_alarm",
    "record_mutation",
    "is_dev_mode",
    "get_ledger",
    "IMMUTABLE_FIELD_PATHS",
    "LOCK_SYSTEM_VERSION",
    "SCHEMA_VERSION",
]
