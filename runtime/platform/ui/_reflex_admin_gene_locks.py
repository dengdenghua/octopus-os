"""Gene-lock admin endpoints.

Extracted from ``_reflex_admin_endpoints.py`` so the router module
stays small. ``register_gene_lock_endpoints`` registers every
gene-lock endpoint on the given router.
"""

from __future__ import annotations

from typing import Any

from fastapi import Header as _Header


def register_gene_lock_endpoints(_reflex_admin: Any) -> None:
    """Register the gene-lock admin endpoints.

    Operator inspects current maturity level, triggers panic,
    clears panic. Levels are 0..4 · see docs/gene-locks.md.
    Panic engages immediately; maturity changes are MONOTONIC-
    aware (up requires signature in prod mode).

    ``_Header`` is imported at module scope because the endpoint
    functions below use it as a default parameter value · Python
    evaluates defaults at def-time, so the import must happen
    before any of these ``def`` statements.
    """

    @_reflex_admin.get("/api/gene-locks/status")
    def _gene_locks_status() -> dict:
        from runtime.safety.gene_locks import get_state

        return get_state()

    @_reflex_admin.post("/api/gene-locks/maturity")
    def _gene_locks_set_maturity(
        body: dict,
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Change maturity level · body: ``{"level": 0..4}``."""
        from runtime.safety.gene_locks import LockViolation, set_maturity

        try:
            lvl = int(body.get("level", 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "level must be 0..4"}
        try:
            return set_maturity(
                lvl,
                human_signed=bool(x_human_approver),
            )
        except LockViolation as lv:
            return lv.as_dict()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    @_reflex_admin.post("/api/gene-locks/panic")
    def _gene_locks_panic_trigger(body: dict) -> dict:
        """Engage panic state · freezes every mutation. Body:
        ``{"reason": "..."}``. Auto-degrades maturity to
        Level 1 per CC-G5 invariant."""
        from runtime.safety.gene_locks import trigger_panic

        reason = str(body.get("reason") or "operator-triggered")
        return trigger_panic(reason)

    @_reflex_admin.post("/api/gene-locks/mode")
    def _gene_locks_set_mode(
        body: dict,
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Flip dev↔production mode at runtime · relaxing
        (prod→dev) requires a human-approver header. Useful
        for smoke-testing the hard-block paths without a
        server restart."""
        from runtime.safety.gene_locks import LockViolation, set_mode

        try:
            return set_mode(
                str(body.get("mode", "")),
                human_signed=bool(x_human_approver),
            )
        except LockViolation as lv:
            return lv.as_dict()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    @_reflex_admin.post("/api/gene-locks/integrity/reset")
    def _gene_locks_integrity_reset(
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Clear a latched IMMUTABLE-integrity alarm · operator
        acknowledges they've inspected the mismatch. Prod mode
        requires the approver header. Doesn't patch IMMUTABLE
        values · a re-``_load`` will re-trigger the alarm if
        the persisted state still disagrees with compiled
        constants (so the real fix is a code release OR
        deleting the state file to re-bootstrap)."""
        from runtime.safety.gene_locks import (
            LockViolation,
            reset_integrity_alarm,
        )

        try:
            return reset_integrity_alarm(
                human_signed=bool(x_human_approver),
            )
        except LockViolation as lv:
            return lv.as_dict()

    @_reflex_admin.post("/api/gene-locks/debug/reload-cache")
    def _gene_locks_reload_cache() -> dict:
        """Force-invalidate the in-memory state cache · next
        ``_load()`` re-reads ``data/gene_locks.json`` from disk,
        re-runs the IMMUTABLE integrity check, and re-evaluates
        ``_INTEGRITY_FAILED``.

        Purpose: testing. Without this, a tamper test that
        modifies the state file can't see the check fire
        because the server's cached ``LockState`` survives
        the edit (held in RAM, not re-read). Exposing it as
        an admin endpoint lets integration tests simulate a
        restart without actually restarting the process.

        Side-effect only · returns the fresh status snapshot
        so the caller can assert on ``integrity_ok`` without
        a second round-trip."""
        from runtime.safety.gene_locks import simple_gate

        with simple_gate._STATE_LOCK:
            simple_gate._CACHED = None
            simple_gate._INTEGRITY_FAILED = None
        # Trigger a re-load so the response already reflects
        # the new on-disk state.
        from runtime.safety.gene_locks import get_state

        return {"ok": True, "reloaded": True, "state": get_state()}

    @_reflex_admin.get("/api/gene-locks/approvals")
    def _gene_locks_approvals(limit: int = 50) -> dict:
        """Recent approver signatures · feeds the audit view.
        Entries beyond the window are still returned so the
        operator can see stale signatures ('Alice signed 3
        days ago') · the gate itself only counts in-window."""
        from runtime.safety.gene_locks import get_ledger

        return {
            "window_s": get_ledger().window_s,
            "recent": get_ledger().recent(limit=limit),
        }

    @_reflex_admin.post("/api/gene-locks/panic/clear")
    def _gene_locks_panic_clear(
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Clear panic · production deploys require a human
        approver header. Maturity stays at whatever the panic
        degraded it to · operator must re-raise explicitly."""
        from runtime.safety.gene_locks import LockViolation, clear_panic

        try:
            return clear_panic(human_signed=bool(x_human_approver))
        except LockViolation as lv:
            return lv.as_dict()
