"""RecipeForge auto-promote scheduler endpoints.

The daemon side of auto-promote · runs on an interval and
reshuffles variant weights based on accumulated trajectory data,
with no human in the loop. Off by default; opt in via the enable
endpoint or the panel toggle. See forge_auto_tick.py for safety
rules.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import Header as _Header

from runtime.platform.ui._reflex_admin_gepa_aliases import register_aliases


def register_gepa_autotick(_reflex_admin: Any, *, stack: Any) -> None:
    """Register the GEPA auto-tick scheduler endpoints + aliases,
    and bind the scheduler to the stack at boot time."""

    try:
        from runtime.safety.recovery import forge_auto_tick

        forge_auto_tick.bind_stack(stack)
        # Boot-time opt-in · the ECHO_FORGE_AUTO_PROMOTE_
        # INTERVAL_HOURS env var is the "I want this running
        # from every uvicorn restart" switch. Unset → scheduler
        # stays off, operator can still toggle at runtime.
        import os as _os

        _boot_iv = _os.environ.get("ECHO_FORGE_AUTO_PROMOTE_INTERVAL_HOURS")
        if _boot_iv:
            with contextlib.suppress(TypeError, ValueError):
                forge_auto_tick.enable(interval_hours=float(_boot_iv))
    except (ImportError, OSError, TypeError, AttributeError):  # noqa: BLE001
        pass

    @_reflex_admin.get("/api/evolution/gepa/auto-tick/status")
    def _gepa_auto_tick_status() -> dict:
        from runtime.safety.recovery import forge_auto_tick

        return {**forge_auto_tick.get_status(), "source": "gepa"}

    @_reflex_admin.post("/api/evolution/gepa/auto-tick/enable")
    def _gepa_auto_tick_enable(
        interval_hours: float = 24.0,
        min_uses: int = 20,
        min_lead: float = 0.15,
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Start the scheduler · idempotent (call again to tune
        the interval without a restart).

        MONOTONIC enforcement · safety thresholds can only be
        autonomously tightened (min_uses up, min_lead up,
        interval_hours up = less frequent = safer). Loosening
        any of them requires an ``X-Human-Approver`` header
        (prod mode) or emits a warning (dev mode).
        """
        from runtime.safety.gene_locks import LockViolation, check_monotonic
        from runtime.safety.recovery import forge_auto_tick

        # Read current thresholds to compute direction.
        status = forge_auto_tick.get_status()
        current_interval = status.get("interval_hours", 24.0)
        current_min_uses = status.get("min_uses", 20)
        current_min_lead = status.get("min_lead", 0.15)
        mono_warnings: list[str] = []
        try:
            for path, old, new in [
                ("auto_tick.interval_hours", current_interval, interval_hours),
                ("auto_tick.min_uses", current_min_uses, min_uses),
                ("auto_tick.min_lead", current_min_lead, min_lead),
            ]:
                r = check_monotonic(
                    field_path=path,
                    old_value=old,
                    new_value=new,
                    approver=x_human_approver,
                )
                mono_warnings.extend(r.get("warnings", []))
        except LockViolation as lv:
            return lv.as_dict()
        result = forge_auto_tick.enable(
            interval_hours=interval_hours,
            min_uses=min_uses,
            min_lead=min_lead,
        )
        if mono_warnings:
            result["gene_lock_warnings"] = mono_warnings
        result["source"] = "gepa"
        return result

    @_reflex_admin.post("/api/evolution/gepa/auto-tick/disable")
    def _gepa_auto_tick_disable() -> dict:
        """Signal the scheduler to stop. Returns immediately ·
        thread exits on its next stop-event check (≤ 5 s)."""
        from runtime.safety.recovery import forge_auto_tick

        return {**forge_auto_tick.disable(), "source": "gepa"}

    @_reflex_admin.post("/api/evolution/gepa/auto-tick/run-now")
    def _gepa_auto_tick_now(
        apply: bool = True,
        min_uses: int = 20,
        min_lead: float = 0.15,
    ) -> dict:
        """Force one tick right now · for testing / on-demand
        "apply every pending proposal" from the panel. Runs
        synchronously so the endpoint returns the result
        directly · careful, this could be slow on a deployment
        with many recipes."""
        from dataclasses import asdict

        from runtime.safety.recovery import forge_auto_tick

        tr = forge_auto_tick.run_tick(
            apply=apply,
            min_uses=min_uses,
            min_lead=min_lead,
        )
        return {**asdict(tr), "apply": apply, "source": "gepa"}

    register_aliases(
        _reflex_admin,
        [
            (
                "GET",
                "/api/evolution/gepa/auto-tick/status",
                "/api/evolution/forge/auto-tick/status",
                _gepa_auto_tick_status,
            ),
            (
                "POST",
                "/api/evolution/gepa/auto-tick/enable",
                "/api/evolution/forge/auto-tick/enable",
                _gepa_auto_tick_enable,
            ),
            (
                "POST",
                "/api/evolution/gepa/auto-tick/disable",
                "/api/evolution/forge/auto-tick/disable",
                _gepa_auto_tick_disable,
            ),
            (
                "POST",
                "/api/evolution/gepa/auto-tick/run-now",
                "/api/evolution/forge/auto-tick/run-now",
                _gepa_auto_tick_now,
            ),
        ],
    )
