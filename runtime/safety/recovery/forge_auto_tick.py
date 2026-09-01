"""
RecipeForge auto-promote scheduler · the last-mile autonomy knob.

Without this module, the operator has to click "Preview → Apply"
in the panel for every recipe every time the A/B data reaches
significance. That's 3-click-per-recipe manual labor · not great
when a household OS has 20+ recipes with variants running.

What this does
--------------

* Runs a background thread that ticks every N hours (default OFF
  · the operator opts in by setting ``ECHO_FORGE_AUTO_PROMOTE
  _INTERVAL_HOURS`` or calling the ``/auto-promote/enable``
  endpoint).
* Each tick: iterates over every recipe that has a manifest ·
  for each one, runs the same ``propose_weights`` the manual
  button runs, and auto-applies when a proposal comes back.
* Results land in the ``GepaRunStore`` tagged with ``trigger=
  auto_scheduler`` so the operator can audit what changed
  overnight from the panel's "Past runs" view.

Safety rules
------------

* **Default OFF**. Nothing runs until the operator enables it ·
  no surprise LLM-free but-still-real production changes.
* **Conservative thresholds**. Default ``min_uses=20`` (double
  the manual default) + ``min_lead=0.15`` (higher than manual).
  The auto-path is gated on stronger evidence than a human
  click, because the human is in the loop to second-guess; the
  scheduler isn't.
* **No LLM calls**. The scheduler only reshuffles EXISTING
  variant weights · never generates new GEPA candidates. Running
  GEPA unattended would burn budget overnight.
* **Audit trail**. Every apply writes to the run store, so
  "what did the robot change at 3am" is always queryable.
* **Clean shutdown**. A stop-event lets Ctrl-C / uvicorn reload
  exit cleanly without zombie threads.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from runtime.platform.process.service_provider import get_provider
from runtime.safety.auth.scope import TenantScope

_LOG = logging.getLogger("echo.forge.auto_tick")

# Default thresholds · deliberately stricter than the manual-path
# defaults so unattended promotions need better evidence.
AUTO_MIN_USES = 20
AUTO_MIN_LEAD = 0.15
AUTO_DEFAULT_INTERVAL_HOURS = 24.0


@dataclass
class TickResult:
    """One tick's outcome · per-recipe actions + timing."""

    ts: float
    elapsed_s: float
    recipes_scanned: int = 0
    recipes_promoted: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SchedulerState:
    """Singleton state for the scheduler · exposed to the admin
    endpoint so the panel can show when the next tick is."""

    enabled: bool = False
    interval_hours: float = AUTO_DEFAULT_INTERVAL_HOURS
    min_uses: int = AUTO_MIN_USES
    min_lead: float = AUTO_MIN_LEAD
    started_at: float | None = None
    last_tick: TickResult | None = None
    ticks_done: int = 0
    # Runtime-mutable thread handle · None when disabled.
    _thread: threading.Thread | None = None
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.RLock = field(default_factory=threading.RLock)


_STATE = SchedulerState()


def bind_stack(stack: Any) -> None:
    """Called once at backend startup · gives the scheduler access
    to the journal + planner router. Safe to call repeatedly ·
    last binding wins."""
    get_provider().register_instance("stack", stack)


# ═══════════════════════════════════════════════════════════
# Core · run a single tick
# ═══════════════════════════════════════════════════════════


def run_tick(
    *,
    min_uses: int | None = None,
    min_lead: float | None = None,
    apply: bool = True,
    journal: Any = None,
    scope: TenantScope | None = None,
) -> TickResult:
    from runtime.safety.recovery.gepa_runs import (
        GepaRunRecord,
        get_default_store,
    )
    from runtime.safety.recovery.gepa_variants import (
        list_all_manifests,
        set_weights,
    )
    from runtime.safety.recovery.variant_evaluator import (
        collect_variant_stats,
        propose_weights,
    )

    t0 = time.time()
    mu = min_uses if min_uses is not None else _STATE.min_uses
    ml = min_lead if min_lead is not None else _STATE.min_lead
    _stack = get_provider().get("stack")
    j = journal if journal is not None else (_stack.journal if _stack is not None else None)
    if j is None:
        return TickResult(
            ts=t0,
            elapsed_s=0.0,
            results=[{"ok": False, "error": "no journal bound"}],
        )

    manifests = list_all_manifests()
    results: list[dict[str, Any]] = []
    store = get_default_store()
    promoted = 0

    for m_sum in manifests:
        rid = m_sum.get("recipe_id")
        if not rid:
            continue
        try:
            comps = collect_variant_stats(j, base_recipe_id=rid, scope=scope)
            if not comps:
                results.append(
                    {
                        "recipe_id": rid,
                        "ok": False,
                        "skipped": True,
                        "reason": "no trajectories yet",
                    }
                )
                continue
            proposal = propose_weights(
                comps[0],
                min_uses=mu,
                min_lead=ml,
            )
            if proposal is None:
                results.append(
                    {
                        "recipe_id": rid,
                        "ok": False,
                        "skipped": True,
                        "reason": (f"no winner yet (min_uses={mu} min_lead={ml})"),
                    }
                )
                continue
            action: dict[str, Any] = {
                "recipe_id": rid,
                "ok": True,
                "winner": proposal.winner_variant_id,
                "weights": proposal.weights,
                "rationale": proposal.rationale,
                "applied": False,
            }
            if apply:
                # Gene-lock gate · the scheduler is the archetypal
                # "autonomous mutation" · no approver, needs LEVEL
                # ≥ 3 (Adult), respects TEMPORAL cooldown, respects
                # PANIC. A violation just skips this recipe for
                # this tick · next tick tries again.
                try:
                    from runtime.safety.gene_locks import (
                        LockViolation,
                        MutationKind,
                        gate_mutation,
                        record_mutation,
                    )

                    gate_mutation(
                        kind=MutationKind.AUTO_PROMOTE,
                        target=rid,
                        autonomous=True,
                    )
                except LockViolation as lv:
                    action["ok"] = False
                    action["applied"] = False
                    action["skipped"] = True
                    action["reason"] = f"gene-lock: {lv.code} · {lv.message}"
                    results.append(action)
                    continue
                m = set_weights(rid, weights=proposal.weights)
                if m is not None:
                    action["applied"] = True
                    promoted += 1
                    with contextlib.suppress(Exception):
                        record_mutation(MutationKind.AUTO_PROMOTE, rid)
                    # Audit trail · write a run record so the
                    # "Past runs" section in the panel surfaces
                    # what the scheduler did.
                    try:
                        rec = GepaRunRecord(
                            ts=time.time(),
                            trigger="auto_scheduler",
                            recipe_id=rid,
                            iterations_run=0,
                            elapsed_s=0.0,
                            front_size=len(proposal.weights),
                            best_candidate_id=proposal.winner_variant_id,
                            best_avg_score=proposal.winner_lower_bound,
                            best_rationale=proposal.rationale,
                            applied=True,
                            applied_at=time.time(),
                        )
                        store.add(rec)
                    except Exception as exc:  # noqa: BLE001
                        _LOG.warning(
                            "auto-tick: run record failed · %s",
                            exc,
                        )
            results.append(action)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "auto-tick: recipe %s failed · %s: %s",
                rid,
                type(exc).__name__,
                exc,
            )
            results.append(
                {
                    "recipe_id": rid,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    tr = TickResult(
        ts=t0,
        elapsed_s=time.time() - t0,
        recipes_scanned=len(manifests),
        recipes_promoted=promoted,
        results=results,
    )
    with _STATE._lock:  # noqa: SLF001
        _STATE.last_tick = tr
        _STATE.ticks_done += 1
    return tr


# ═══════════════════════════════════════════════════════════
# Scheduler thread · loops, sleeps, ticks
# ═══════════════════════════════════════════════════════════


def _scheduler_loop() -> None:
    """Runs in its own thread. Wakes every interval_hours, calls
    ``run_tick(apply=True)``, stores result. Exits when the stop
    event is set · allows clean shutdown on uvicorn reload."""
    _LOG.info(
        "forge auto-tick scheduler started · interval %.1f h",
        _STATE.interval_hours,
    )
    while not _STATE._stop_event.is_set():  # noqa: SLF001
        try:
            run_tick(apply=True)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("auto-tick scheduler: tick failed · %s", exc)
        # Sleep in short slices so stop_event is responsive
        # (we don't want a 24h sleep blocking shutdown for a day).
        wake_after = time.time() + _STATE.interval_hours * 3600
        while time.time() < wake_after:
            if _STATE._stop_event.wait(timeout=5.0):  # noqa: SLF001
                _LOG.info("forge auto-tick scheduler stopping")
                return


def enable(
    *,
    interval_hours: float | None = None,
    min_uses: int | None = None,
    min_lead: float | None = None,
) -> dict[str, Any]:
    """Start the scheduler thread · idempotent (repeat calls
    adjust config without spawning a new thread)."""
    with _STATE._lock:  # noqa: SLF001
        if interval_hours is not None:
            _STATE.interval_hours = max(0.1, float(interval_hours))
        if min_uses is not None:
            _STATE.min_uses = max(1, int(min_uses))
        if min_lead is not None:
            _STATE.min_lead = max(0.0, min(1.0, float(min_lead)))
        if _STATE.enabled and _STATE._thread is not None and _STATE._thread.is_alive():
            # Already running · config updates above already applied ·
            # return full state so the caller sees the new thresholds
            # (not just interval_hours).
            return {
                "ok": True,
                "already_running": True,
                "interval_hours": _STATE.interval_hours,
                "min_uses": _STATE.min_uses,
                "min_lead": _STATE.min_lead,
            }
        _STATE._stop_event.clear()  # noqa: SLF001
        _STATE._thread = threading.Thread(
            target=_scheduler_loop,
            name="forge-auto-tick",
            daemon=True,
        )
        _STATE._thread.start()
        _STATE.enabled = True
        _STATE.started_at = time.time()
        return {
            "ok": True,
            "enabled": True,
            "interval_hours": _STATE.interval_hours,
            "min_uses": _STATE.min_uses,
            "min_lead": _STATE.min_lead,
        }


def disable() -> dict[str, Any]:
    """Signal the scheduler to stop · non-blocking (returns
    immediately · thread exits on next stop-event check, at
    most 5 seconds later)."""
    with _STATE._lock:  # noqa: SLF001
        _STATE._stop_event.set()  # noqa: SLF001
        _STATE.enabled = False
        return {"ok": True, "enabled": False}


def get_status() -> dict[str, Any]:
    with _STATE._lock:  # noqa: SLF001
        next_tick_at: float | None = None
        if _STATE.enabled and _STATE.last_tick:
            next_tick_at = _STATE.last_tick.ts + _STATE.interval_hours * 3600
        elif _STATE.enabled and _STATE.started_at:
            next_tick_at = _STATE.started_at + _STATE.interval_hours * 3600
        return {
            "enabled": _STATE.enabled,
            "interval_hours": _STATE.interval_hours,
            "min_uses": _STATE.min_uses,
            "min_lead": _STATE.min_lead,
            "started_at": _STATE.started_at,
            "next_tick_at": next_tick_at,
            "ticks_done": _STATE.ticks_done,
            "last_tick": (
                {
                    "ts": _STATE.last_tick.ts,
                    "elapsed_s": _STATE.last_tick.elapsed_s,
                    "recipes_scanned": _STATE.last_tick.recipes_scanned,
                    "recipes_promoted": _STATE.last_tick.recipes_promoted,
                    "results": _STATE.last_tick.results,
                }
                if _STATE.last_tick
                else None
            ),
        }


__all__ = [
    "TickResult",
    "bind_stack",
    "run_tick",
    "enable",
    "disable",
    "get_status",
    "AUTO_MIN_USES",
    "AUTO_MIN_LEAD",
    "AUTO_DEFAULT_INTERVAL_HOURS",
]
