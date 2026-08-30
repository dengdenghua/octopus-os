"""
Gene-lock gate · MVP implementation of the 4 most critical locks.

Implementation rules
--------------------

* **LEVEL** · every mutation kind maps to a minimum required maturity
  level. Operations below that level are blocked UNLESS the request
  carries a valid human-approver signature (QUORUM-satisfied).
* **TEMPORAL** · per-recipe cooldown between consecutive mutations of
  the same kind · prevents "auto-tick promotes vA at 03:00 then
  immediately re-promotes at 03:05 because weights haven't
  propagated yet".
* **PANIC** · kill switch. When set, every autonomous mutation is
  blocked; human-signed mutations are also blocked except for
  ``panic_override_emergency_rollback``. Flipping off requires
  human approval.
* **QUORUM (soft)** · request headers can carry
  ``X-Human-Approver: <name>``. Approval is counted by the
  deduplicated set of approver names in recent `APPROVAL_WINDOW`
  seconds. Hard enforcement is off by default in dev mode so
  current testing isn't broken · production deploys flip
  ``GENE_LOCKS_MODE=production`` and the soft-reminder becomes
  hard-block.

Dev vs Prod mode
----------------

``ECHO_GENE_LOCKS_MODE=production`` flips the gate into
hard-enforcement. Unset / ``dev`` / ``soft`` → the gate advises
without blocking for LEVEL / QUORUM checks (TEMPORAL + PANIC are
always hard, even in dev, because they don't break normal
workflows · only "calm down, too soon" or "emergency stop").

Persistence · data/gene_locks.json
----------------------------------

::

    {
      "schema_version": 1,
      "maturity_level": 2,
      "panic": {"active": false, "since": null, "reason": ""},
      "mode": "dev",
      "last_mutation_at": {
        "apply_addendum:llm@bad42": 1776500000.0,
        ...
      }
    }

Written atomically (tmp + rename). Read every call · OS caches
the file so perf is fine for the call rates RecipeForge generates.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.gene_locks")


# ═══════════════════════════════════════════════════════════
# IMMUTABLE constants · changed ONLY by a code release + restart
# ═══════════════════════════════════════════════════════════
#
# Per docs/gene-locks.md Lock-1: these values are bootstrapped by
# the compiled code. Once a ``genome_id`` is minted at first boot,
# no API / config / autonomous path can change it · a genome-ID
# mismatch between the code's expectation and the on-disk state
# is treated as "someone swapped my brain", triggers panic, and
# refuses mutations until a human inspects.
#
# To legitimately change a value here:
#   1. Edit this file in code
#   2. Delete ``data/gene_locks.json`` on the host
#   3. Restart · the next boot mints a new genome with the new
#      LOCK_SYSTEM_VERSION · all prior history is audit-only

LOCK_SYSTEM_VERSION = "1.0.0"  # matches docs/gene-locks.md §11
SCHEMA_VERSION = 1  # bumps require migration code
INVARIANTS_VERSION_PIN = "v1"  # INVARIANTS.md pin

# Name set of fields that cannot be mutated through ``set_maturity``,
# ``set_mode``, or any other admin endpoint. Attempts return
# ``immutable_field_locked`` instead of silently succeeding.
IMMUTABLE_FIELD_PATHS: frozenset[str] = frozenset(
    {
        "genome_id",
        "origin_signature",
        "schema_version",
        "invariants_version_pin",
        "lock_system_version",
    }
)

# Default cooldowns per mutation kind (seconds).
# Match the spec's TEMPORAL table as best we can for RecipeForge.
# Human-signed mutations can set ``bypass_cooldown=True`` but the
# gate records a flag so audit can see who bypassed when.
COOLDOWNS_S: dict[str, int] = {
    "apply_addendum": 3600,  # 1h per recipe · manual iteration OK
    "set_variant_weights": 21600,  # 6h · weights affect live traffic
    "auto_promote": 86400,  # 24h · autonomous promote is heaviest
    "delete_addendum": 60,  # 1m · delete is cheap, near-zero cost
}

# Approval window for QUORUM counting · an approver header counts
# only if posted within the last N seconds.
APPROVAL_WINDOW_S = 600


class MaturityLevel(IntEnum):
    HATCHLING = 0  # Implementation note.
    JUVENILE = 1  # Implementation note.
    ADOLESCENT = 2  # Implementation note.
    ADULT = 3  # Implementation note.
    MATURE = 4  # Implementation note.


class MutationKind:
    """Enum-ish string tags · one per RecipeForge mutation op."""

    APPLY_ADDENDUM = "apply_addendum"
    SET_VARIANT_WEIGHTS = "set_variant_weights"
    AUTO_PROMOTE = "auto_promote"
    DELETE_ADDENDUM = "delete_addendum"
    # Workflow rewrites mutate the StaticPlanner rule set — they
    # change which skills run for every future intent, so they sit
    # at the same blast radius as AUTO_PROMOTE.
    APPLY_WORKFLOW_REWRITE = "apply_workflow_rewrite"
    # Variant retirement removes prompt variants from rotation. A
    # single retirement is low-impact (the surviving variants pick
    # up the traffic), but a burst could starve all variants. The
    # rate-limit lives downstream in AutoRetire itself; the gate
    # adds the PANIC/cooldown overlay.
    RETIRE_VARIANT = "retire_variant"
    # Organization-level mutations · the team-topology evolver
    # proposes alternative team compositions (which role runs which
    # agent, sequential vs evaluator_optimizer protocol, etc.) and
    # the forge promotes high-performing ones to the active registry.
    # Topology promotion is high-blast-radius because every future
    # task in that bucket will run through the new topology.
    EVOLVE_TOPOLOGY = "evolve_topology"
    PROMOTE_TOPOLOGY = "promote_topology"


# Minimum maturity level required for each mutation kind.
MIN_LEVEL: dict[tuple[str, bool], int] = {
    # Autonomous (no human signature in the request):
    (MutationKind.APPLY_ADDENDUM, True): MaturityLevel.JUVENILE,
    (MutationKind.SET_VARIANT_WEIGHTS, True): MaturityLevel.ADOLESCENT,
    (MutationKind.AUTO_PROMOTE, True): MaturityLevel.ADULT,
    (MutationKind.DELETE_ADDENDUM, True): MaturityLevel.JUVENILE,
    (MutationKind.APPLY_WORKFLOW_REWRITE, True): MaturityLevel.ADULT,
    (MutationKind.RETIRE_VARIANT, True): MaturityLevel.ADOLESCENT,
    (MutationKind.EVOLVE_TOPOLOGY, True): MaturityLevel.ADOLESCENT,
    (MutationKind.PROMOTE_TOPOLOGY, True): MaturityLevel.ADULT,
    # Human-signed (request carried X-Human-Approver):
    (MutationKind.APPLY_ADDENDUM, False): MaturityLevel.HATCHLING,
    (MutationKind.SET_VARIANT_WEIGHTS, False): MaturityLevel.HATCHLING,
    (MutationKind.AUTO_PROMOTE, False): MaturityLevel.HATCHLING,
    (MutationKind.DELETE_ADDENDUM, False): MaturityLevel.HATCHLING,
    (MutationKind.APPLY_WORKFLOW_REWRITE, False): MaturityLevel.HATCHLING,
    (MutationKind.RETIRE_VARIANT, False): MaturityLevel.HATCHLING,
    (MutationKind.EVOLVE_TOPOLOGY, False): MaturityLevel.HATCHLING,
    (MutationKind.PROMOTE_TOPOLOGY, False): MaturityLevel.HATCHLING,
}


class LockViolation(Exception):
    """Raised when a mutation fails the gate. HTTP layer should turn
    this into a 403/409 response with the violation detail intact."""

    def __init__(
        self, code: str, message: str, *, hint: str = "", locks_hit: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.locks_hit = list(locks_hit or [])

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "gene_lock_violation": True,
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "locks_hit": self.locks_hit,
        }


# ═══════════════════════════════════════════════════════════
# Persistence · data/gene_locks.json
# ═══════════════════════════════════════════════════════════


def _store_path() -> Path:
    # app_paths honours ECHO_DATA_DIR/ECHO_HOME and anchors on the
    # project root — a bare Path("data") silently re-minted state when the
    # process ran from any other cwd.
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "gene_locks.json"


@dataclass
class PanicState:
    active: bool = False
    since: float | None = None
    reason: str = ""


@dataclass
class LockState:
    # ── IMMUTABLE fields (Lock-1) ──
    # These are minted once and never change for the lifetime of a
    # genome. A mismatch against the compiled constants at load-time
    # triggers panic + refuses mutations.
    genome_id: str = ""  # UUID4, minted at first boot
    origin_signature: str = ""  # hostname+mint-ts hash, audit only
    schema_version: int = SCHEMA_VERSION
    invariants_version_pin: str = INVARIANTS_VERSION_PIN
    lock_system_version: str = LOCK_SYSTEM_VERSION

    # ── mutable runtime state ──
    maturity_level: int = MaturityLevel.ADOLESCENT  # dev-friendly default
    panic: PanicState = field(default_factory=PanicState)
    mode: str = "dev"  # "dev" | "production"
    # {f"{kind}:{recipe}": last_ts}
    last_mutation_at: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "genome_id": self.genome_id,
            "origin_signature": self.origin_signature,
            "schema_version": self.schema_version,
            "invariants_version_pin": self.invariants_version_pin,
            "lock_system_version": self.lock_system_version,
            "maturity_level": int(self.maturity_level),
            "panic": asdict(self.panic),
            "mode": self.mode,
            "last_mutation_at": dict(self.last_mutation_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LockState:
        pd = data.get("panic") or {}
        return cls(
            genome_id=str(data.get("genome_id") or ""),
            origin_signature=str(data.get("origin_signature") or ""),
            schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
            invariants_version_pin=str(
                data.get("invariants_version_pin") or INVARIANTS_VERSION_PIN,
            ),
            lock_system_version=str(
                data.get("lock_system_version") or LOCK_SYSTEM_VERSION,
            ),
            maturity_level=int(data.get("maturity_level") or MaturityLevel.ADOLESCENT),
            panic=PanicState(
                active=bool(pd.get("active")),
                since=pd.get("since"),
                reason=str(pd.get("reason") or ""),
            ),
            mode=str(data.get("mode") or "dev"),
            last_mutation_at={
                str(k): float(v)
                for k, v in (data.get("last_mutation_at") or {}).items()
                if isinstance(v, (int, float))
            },
        )


_STATE_LOCK = threading.RLock()
_CACHED: LockState | None = None
# Set when the IMMUTABLE startup check fails · every gate call
# after this raises until a human inspects. Separate from
# ``panic.active`` because this one survives ``clear_panic``.
_INTEGRITY_FAILED: dict[str, Any] | None = None


def _bootstrap(s: LockState) -> LockState:
    """First-boot IMMUTABLE minting · called once per new state
    file. Generates a genome_id, writes an origin_signature, and
    pins the compiled-in version constants so we can detect
    mismatches on future boots."""
    if not s.genome_id:
        s.genome_id = uuid.uuid4().hex
    if not s.origin_signature:
        import hashlib
        import socket

        host = socket.gethostname() or "unknown"
        s.origin_signature = hashlib.blake2b(
            f"{s.genome_id}|{host}|{time.time()}".encode(),
            digest_size=8,
        ).hexdigest()
    s.schema_version = SCHEMA_VERSION
    s.invariants_version_pin = INVARIANTS_VERSION_PIN
    s.lock_system_version = LOCK_SYSTEM_VERSION
    return s


def _check_integrity(s: LockState) -> dict[str, Any] | None:
    """Verify persisted IMMUTABLE fields match the compiled
    constants. Returns None when healthy, or a dict describing
    the mismatch when tampered. Mismatch causes every subsequent
    gate call to refuse · a human must inspect + decide whether
    to rotate the genome (delete state file, let it re-bootstrap).
    """
    problems: list[str] = []
    if s.lock_system_version != LOCK_SYSTEM_VERSION:
        problems.append(
            f"lock_system_version: persisted={s.lock_system_version!r}, "
            f"compiled={LOCK_SYSTEM_VERSION!r}"
        )
    if s.schema_version != SCHEMA_VERSION:
        problems.append(f"schema_version: persisted={s.schema_version}, compiled={SCHEMA_VERSION}")
    if s.invariants_version_pin != INVARIANTS_VERSION_PIN:
        problems.append(
            f"invariants_version_pin: persisted={s.invariants_version_pin!r}, "
            f"compiled={INVARIANTS_VERSION_PIN!r}"
        )
    if not s.genome_id:
        problems.append("genome_id: missing (corrupted state?)")
    if not problems:
        return None
    return {
        "integrity_ok": False,
        "problems": problems,
        "hint": (
            "IMMUTABLE mismatch detected · to recover: (1) inspect "
            "the persisted state file, (2) if the mismatch is "
            "expected (e.g. intentional version upgrade), delete "
            "data/gene_locks.json to mint a new genome on next "
            "boot. Until then, all mutations are refused."
        ),
    }


def _load() -> LockState:
    """Read state · creates defaults if file missing. Cached between
    writes (invalidated on every ``_save``).

    On first boot (no file), mints IMMUTABLE fields. On subsequent
    boots, verifies the IMMUTABLE fields match the compiled
    constants · mismatch flips ``_INTEGRITY_FAILED`` and every
    subsequent gate call refuses until cleared.
    """
    global _CACHED, _INTEGRITY_FAILED
    with _STATE_LOCK:
        if _CACHED is not None:
            return _CACHED
        path = _store_path()
        if not path.is_file():
            _CACHED = _bootstrap(LockState())
            # Environment-driven initial mode · prod deploys override
            # the default by setting ``ECHO_GENE_LOCKS_MODE``.
            env_mode = os.environ.get("ECHO_GENE_LOCKS_MODE", "").lower()
            if env_mode in ("prod", "production"):
                _CACHED.mode = "production"
                # In prod default, start conservative: Hatchling.
                _CACHED.maturity_level = MaturityLevel.HATCHLING
            # Persist the bootstrapped genome_id immediately · if
            # we crash before first mutation, next boot finds the
            # mint ready.
            with contextlib.suppress(Exception):
                _save(_CACHED, _skip_integrity=True)
            return _CACHED
        try:
            _CACHED = LockState.from_dict(
                json.loads(path.read_text(encoding="utf-8")),
            )
        except (OSError, json.JSONDecodeError) as exc:
            _LOG.warning("gene_locks: state load failed · %s · using defaults", exc)
            _CACHED = _bootstrap(LockState())
            return _CACHED
        # Bootstrap any missing IMMUTABLE fields · happens when
        # upgrading from a pre-IMMUTABLE-support deployment that
        # wrote state without genome_id.
        if not _CACHED.genome_id:
            _CACHED = _bootstrap(_CACHED)
            with contextlib.suppress(Exception):
                _save(_CACHED, _skip_integrity=True)
        # Integrity check.
        mismatch = _check_integrity(_CACHED)
        if mismatch:
            _INTEGRITY_FAILED = mismatch
            _LOG.error(
                "gene_locks: IMMUTABLE INTEGRITY FAILURE · %s",
                mismatch["problems"],
            )
        else:
            _INTEGRITY_FAILED = None
        return _CACHED


def _save(state: LockState, *, _skip_integrity: bool = False) -> None:
    """Atomic write · tmp + rename. Invalidates the cache.

    Before writing, verifies that no IMMUTABLE field on ``state``
    disagrees with either the compiled constants OR the previously
    persisted state. The ``_skip_integrity`` flag is reserved for
    bootstrap writes inside ``_load`` · never pass it from a
    regular admin path."""
    global _CACHED
    if not _skip_integrity:
        # Compare against the compiled constants (blocks tampering
        # via direct LockState mutation) AND against the previously
        # persisted genome_id (blocks "rotate identity silently").
        if state.lock_system_version != LOCK_SYSTEM_VERSION:
            raise LockViolation(
                "immutable_mismatch",
                f"lock_system_version mismatch on save · "
                f"state={state.lock_system_version!r} vs "
                f"compiled={LOCK_SYSTEM_VERSION!r}",
                hint="IMMUTABLE · compile-time change + restart required",
                locks_hit=["IMMUTABLE:lock_system_version"],
            )
        if state.schema_version != SCHEMA_VERSION:
            raise LockViolation(
                "immutable_mismatch",
                "schema_version mismatch on save",
                hint="IMMUTABLE · migration needed",
                locks_hit=["IMMUTABLE:schema_version"],
            )
        # genome_id monotonic-check: never allow a save that
        # changes an existing genome_id to a different non-empty
        # value. Setting from empty → UUID is bootstrap, allowed.
        if _CACHED is not None and _CACHED.genome_id:  # noqa: SIM102
            if state.genome_id != _CACHED.genome_id:
                raise LockViolation(
                    "immutable_mismatch",
                    "genome_id cannot change after bootstrap",
                    hint="IMMUTABLE · delete state file to mint a new genome",
                    locks_hit=["IMMUTABLE:genome_id"],
                )
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    with _STATE_LOCK:
        _CACHED = state


def is_dev_mode() -> bool:
    return _load().mode != "production"


def set_mode(mode: str, *, human_signed: bool = False) -> dict[str, Any]:
    """Flip between dev / production · production is one-way
    without human sign (can't silently downgrade protections).

    ``mode`` must be "dev" / "soft" / "production" / "prod".
    Dev-mode warns-but-doesn't-block; prod-mode hard-blocks.
    """
    m = (mode or "").strip().lower()
    if m in ("prod", "production"):
        target = "production"
    elif m in ("dev", "soft", "test"):
        target = "dev"
    else:
        raise ValueError(f"unknown mode {mode!r}; use 'dev' or 'production'")
    with _STATE_LOCK:
        s = _load()
        going_soft = s.mode == "production" and target == "dev"
        if going_soft and not human_signed:
            raise LockViolation(
                "mode_relax_needs_human",
                f"flipping mode {s.mode} → {target} requires a human-approver header",
                hint="POST with X-Human-Approver: <your-id>",
                locks_hit=["MODE:relax"],
            )
        old = s.mode
        s.mode = target
        _save(s)
        return {"ok": True, "old_mode": old, "new_mode": target}


def get_state() -> dict[str, Any]:
    """Expose full state for admin UI · read-only snapshot.

    Includes IMMUTABLE fields (genome_id etc.) AND the integrity
    check result · the panel can show a red banner when
    integrity has failed so the operator knows the system is
    refusing all mutations until they inspect.
    """
    with _STATE_LOCK:
        s = _load()
        base = s.to_dict()
        # Split IMMUTABLE vs mutable for panel clarity · makes
        # "these values never change" explicit in the UI.
        immutable_view = {
            k: base.pop(k)
            for k in [
                "genome_id",
                "origin_signature",
                "schema_version",
                "invariants_version_pin",
                "lock_system_version",
            ]
            if k in base
        }
        return {
            **base,
            "immutable": immutable_view,
            "integrity_ok": _INTEGRITY_FAILED is None,
            "integrity_problems": (
                _INTEGRITY_FAILED.get("problems", []) if _INTEGRITY_FAILED else []
            ),
            "required_levels": {
                kind: {
                    "autonomous": MIN_LEVEL[(kind, True)],
                    "human_signed": MIN_LEVEL[(kind, False)],
                }
                for kind in [
                    MutationKind.APPLY_ADDENDUM,
                    MutationKind.SET_VARIANT_WEIGHTS,
                    MutationKind.AUTO_PROMOTE,
                    MutationKind.DELETE_ADDENDUM,
                ]
            },
            "cooldowns_seconds": dict(COOLDOWNS_S),
            "maturity_level_name": MaturityLevel(s.maturity_level).name,
        }


def reset_integrity_alarm(*, human_signed: bool = False) -> dict[str, Any]:
    """Clear the integrity-failed latch AFTER a human has inspected.

    Production requires an approver header · this is the only way
    out of "all mutations refused" short of deleting the state
    file and minting a new genome. It does NOT patch the
    IMMUTABLE fields · if the compiled code's constants truly
    differ from the persisted state, the next ``_load`` will
    re-detect the mismatch and re-trigger the refusal.

    So the operator's real recovery path is:
      1. Look at the problems list
      2. Either accept the mismatch (delete state file) OR
         redeploy with the correct constants
      3. Call this endpoint to silence the alarm for the
         in-memory cache until next ``_load``
    """
    global _INTEGRITY_FAILED
    s = _load()
    if not human_signed and s.mode == "production":
        raise LockViolation(
            "reset_integrity_needs_human",
            "clearing an integrity alarm requires a human-approver header",
            hint="POST again with X-Human-Approver: <your-id>",
            locks_hit=["IMMUTABLE"],
        )
    was = _INTEGRITY_FAILED
    _INTEGRITY_FAILED = None
    return {"ok": True, "previous_problems": was.get("problems", []) if was else []}


# ═══════════════════════════════════════════════════════════
# Admin API · mutate state
# ═══════════════════════════════════════════════════════════


def set_maturity(level: int, *, human_signed: bool = False) -> dict[str, Any]:
    if level < 0 or level > 4:
        raise ValueError("level must be in [0, 4]")
    with _STATE_LOCK:
        s = _load()
        going_up = level > s.maturity_level
        if going_up and not human_signed and s.mode == "production":
            raise LockViolation(
                "maturity_up_needs_human",
                f"raising maturity from {s.maturity_level}→{level} needs a human-approver header",
                hint="POST again with X-Human-Approver: <your-id>",
                locks_hit=["MONOTONIC:maturity_level"],
            )
        old = s.maturity_level
        s.maturity_level = int(level)
        _save(s)
        return {
            "ok": True,
            "old_level": old,
            "new_level": level,
            "direction": "up" if going_up else ("down" if level < old else "same"),
        }


def trigger_panic(reason: str) -> dict[str, Any]:
    """Engage panic state · freezes every mutation. Auto-degrades
    maturity to Level 1 (Juvenile) · cannot be re-upgraded until
    panic is cleared AND 30-day cooldown (MVP: just flag)."""
    with _STATE_LOCK:
        s = _load()
        s.panic = PanicState(
            active=True,
            since=time.time(),
            reason=str(reason)[:500],
        )
        # Auto-degrade (CC-G5 invariant).
        s.maturity_level = min(s.maturity_level, int(MaturityLevel.JUVENILE))
        _save(s)
        return {"ok": True, "panic_active": True, "maturity_level": s.maturity_level}


def clear_panic(*, human_signed: bool = False) -> dict[str, Any]:
    """Clear panic state · production requires human signature.
    Does NOT auto-raise maturity · operator must explicitly
    raise it post-incident."""
    with _STATE_LOCK:
        s = _load()
        if not human_signed and s.mode == "production":
            raise LockViolation(
                "clear_panic_needs_human",
                "clearing panic requires a human-approver header",
                hint="POST again with X-Human-Approver: <your-id>",
                locks_hit=["PANIC"],
            )
        was_active = s.panic.active
        s.panic = PanicState(active=False, since=None, reason="")
        _save(s)
        return {"ok": True, "was_active": was_active}


def record_mutation(kind: str, target: str = "") -> None:
    """Stamp last_mutation_at[kind:target] so TEMPORAL cooldown can
    see when the previous mutation of this kind hit this target."""
    key = f"{kind}:{target}" if target else kind
    with _STATE_LOCK:
        s = _load()
        s.last_mutation_at[key] = time.time()
        _save(s)


# ═══════════════════════════════════════════════════════════
# Gate · the function every mutation must go through
# ═══════════════════════════════════════════════════════════


def gate_mutation(
    *,
    kind: str,
    target: str = "",
    autonomous: bool = True,
    approver: str | None = None,
    bypass_cooldown: bool = False,
) -> dict[str, Any]:
    """Run the gate · raises ``LockViolation`` on block, returns
    a dict with "ok: true" + any soft warnings when allowed.

    Enforcement order (spec-aligned): PANIC → LEVEL → TEMPORAL →
    QUORUM. Earlier gates short-circuit so the caller's error
    surface matches the spec exactly.

    Params:
      kind · MutationKind.* string
      target · the scope identifier (recipe_id etc.) · "" when
        the mutation is scope-free (e.g. global addendum)
      autonomous · True when no human signature is present OR
        the daemon is calling. Dictates required level.
      approver · optional human-approver name from request header.
        When set, the mutation is NOT autonomous even if the
        caller set autonomous=True (convenience · panel can
        always pass the approver header and the gate figures
        out the right path).
      bypass_cooldown · emergency knob for explicit rollback
        operations; caller (admin endpoint) passes True only
        when an operator confirms "yes really, again".
    """
    s = _load()
    warnings: list[str] = []
    human_signed = bool(approver and approver.strip())
    effective_autonomous = autonomous and not human_signed

    # 0. IMMUTABLE integrity — hardest block, short-circuits
    # everything else. A tampered state file is a security event
    # that takes precedence over panic · a compromised genome
    # might even BE what triggered panic falsely.
    if _INTEGRITY_FAILED is not None:
        raise LockViolation(
            "integrity_failed",
            f"IMMUTABLE integrity check failed · {_INTEGRITY_FAILED['problems']}",
            hint=_INTEGRITY_FAILED.get("hint", ""),
            locks_hit=["IMMUTABLE"],
        )

    # 1. PANIC — hard block always
    if s.panic.active:
        raise LockViolation(
            "panic_active",
            f"panic state active since {s.panic.since} · reason: {s.panic.reason}",
            hint="clear panic first (POST /api/gene-locks/panic/clear)",
            locks_hit=["PANIC"],
        )

    # 2. LEVEL
    required = MIN_LEVEL.get(
        (kind, effective_autonomous),
        MaturityLevel.ADULT,  # unknown kind = conservative default
    )
    if s.maturity_level < required:
        msg = (
            f"mutation kind={kind} autonomous={effective_autonomous} "
            f"requires level {required} · current level {s.maturity_level}"
        )
        hint = (
            "raise maturity via /api/gene-locks/maturity · OR include "
            "X-Human-Approver: <name> header to downgrade the requirement"
            if effective_autonomous
            else "this should not happen for human-signed mutations · file a bug"
        )
        if s.mode == "production":
            raise LockViolation(
                "level_too_low",
                msg,
                hint=hint,
                locks_hit=[f"LEVEL<{required}"],
            )
        warnings.append(f"dev-mode override: LEVEL {msg}")

    # 3. TEMPORAL cooldown (always hard, dev + prod)
    if not bypass_cooldown:
        key = f"{kind}:{target}" if target else kind
        last_ts = s.last_mutation_at.get(key)
        cooldown = COOLDOWNS_S.get(kind, 0)
        if last_ts and cooldown > 0:
            elapsed = time.time() - last_ts
            if elapsed < cooldown:
                remaining = int(cooldown - elapsed)
                raise LockViolation(
                    "cooldown_active",
                    f"{kind} on {target!r} cooling down · {remaining}s remaining",
                    hint=(
                        "wait it out, or pass bypass_cooldown=true with "
                        "human signature if truly urgent"
                    ),
                    locks_hit=[f"TEMPORAL:{cooldown}s"],
                )

    # 4. QUORUM · 2-of-N distinct approvers within a time window.
    # High-risk kinds (live-traffic weight changes, deletions)
    # require two DIFFERENT approvers to have signed within the
    # ledger's window. The same person calling twice counts once.
    #
    # Dev mode: soft-advisory · logged in `warnings`, doesn't block.
    # Prod mode: HARD block until quorum is satisfied.
    #
    # The ledger is updated AFTER this call when the approver
    # header was present · the first call "registers" Alice,
    # then Bob's call satisfies 2-of-N and proceeds.
    quorum_required_for = {
        MutationKind.SET_VARIANT_WEIGHTS: 2,
        MutationKind.DELETE_ADDENDUM: 2,
    }
    qr = quorum_required_for.get(kind, 0)
    if qr > 0 and human_signed:
        from runtime.safety.gene_locks.approver_ledger import get_ledger

        ledger = get_ledger()
        # Register THIS approver before counting · ensures the
        # current call counts toward the tally.
        ledger.record(approver or "", kind, target)
        distinct = ledger.count_distinct(kind, target)
        approvers_seen = ledger.distinct_approvers(kind, target)
        if distinct < qr:
            msg = (
                f"QUORUM needs {qr} distinct approvers for {kind} "
                f"on {target!r} within {ledger.window_s:.0f}s · "
                f"seen {distinct}: {approvers_seen}"
            )
            hint = (
                f"Ask another operator to POST this endpoint "
                f"with their own X-Human-Approver header within "
                f"the next {ledger.window_s:.0f}s"
            )
            if s.mode == "production":
                raise LockViolation(
                    "quorum_insufficient",
                    msg,
                    hint=hint,
                    locks_hit=[f"QUORUM:{qr}of*"],
                )
            warnings.append(f"dev-mode override: {msg}")
        else:
            # Satisfied · note who the pair/group was for audit.
            warnings.append(f"QUORUM satisfied · approvers: {approvers_seen}")

    # 5. MONOTONIC · safety/config fields can tighten autonomously
    # but loosening needs human sign. Enforced by caller-supplied
    # metadata on the specific config-change endpoints (see
    # ``check_monotonic`` helper below); not computed from the
    # mutation's kind+target alone.

    return {
        "ok": True,
        "level": s.maturity_level,
        "effective_autonomous": effective_autonomous,
        "warnings": warnings,
    }


# ═══════════════════════════════════════════════════════════
# MONOTONIC gate · for config fields (auto-tick min_lead etc.)
# ═══════════════════════════════════════════════════════════

# Direction tags · "stricter" = the safe/conservative direction.
# What counts as stricter depends on the field:
#   min_uses:     HIGHER  = stricter (need more evidence)
#   min_lead:     HIGHER  = stricter (need wider lead to promote)
#   interval_h:   HIGHER  = stricter (less frequent = less churn)
# Call site supplies the direction explicitly so the gate doesn't
# need to bake in domain knowledge.

MONOTONIC_STRICTER: dict[str, str] = {
    "auto_tick.min_uses": "higher",
    "auto_tick.min_lead": "higher",
    "auto_tick.interval_hours": "higher",
}


def check_monotonic(
    *,
    field_path: str,
    old_value: float | int,
    new_value: float | int,
    approver: str | None = None,
) -> dict[str, Any]:
    """Gate a single MONOTONIC field change.

    Raises ``LockViolation`` when the change is in the
    "looser" direction AND production mode AND no approver
    header was supplied. Dev mode downgrades to a warning.

    Returns a ``{ok, direction, warnings}`` dict identical
    in shape to ``gate_mutation`` for uniform handling.
    """
    s = _load()
    direction_of_stricter = MONOTONIC_STRICTER.get(field_path)
    warnings: list[str] = []
    if direction_of_stricter is None:
        # Field isn't MONOTONIC-tracked · pass through.
        return {"ok": True, "direction": "untracked", "warnings": []}
    if new_value == old_value:
        return {"ok": True, "direction": "same", "warnings": []}
    going_higher = new_value > old_value
    is_tightening = (direction_of_stricter == "higher" and going_higher) or (
        direction_of_stricter == "lower" and not going_higher
    )
    direction = "tighter" if is_tightening else "looser"
    if is_tightening:
        # Always allowed · safe direction.
        return {"ok": True, "direction": direction, "warnings": []}
    # Looser direction · gate check
    human_signed = bool(approver and approver.strip())
    if human_signed:
        return {
            "ok": True,
            "direction": direction,
            "warnings": [f"MONOTONIC looser-direction signed by {approver!r}"],
        }
    msg = (
        f"{field_path}: {old_value} → {new_value} is looser direction "
        f"(stricter is {direction_of_stricter}) · requires human approver"
    )
    if s.mode == "production":
        raise LockViolation(
            "monotonic_needs_human",
            msg,
            hint="include X-Human-Approver header to signal human intent",
            locks_hit=[f"MONOTONIC:{field_path}"],
        )
    warnings.append(f"dev-mode override: {msg}")
    return {"ok": True, "direction": direction, "warnings": warnings}


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
    "MONOTONIC_STRICTER",
    "IMMUTABLE_FIELD_PATHS",
    "LOCK_SYSTEM_VERSION",
    "SCHEMA_VERSION",
]
