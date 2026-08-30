"""GEPA "runs" listing + CSV export endpoints.

Read-only views over the GEPA run store and the active addendum
map: the JSON listing (``/runs``, ``/addendums``) and their
CSV download variants (``/runs.csv``, ``/addendums.csv``) that
operators paste into Sheets / Pandas.
"""

from __future__ import annotations

import time
from datetime import UTC
from typing import Any

from fastapi.responses import PlainTextResponse

from runtime.platform.ui._reflex_admin_gepa_aliases import register_aliases


def register_gepa_runs(_reflex_admin: Any, *, stack: Any) -> None:
    """Register the GEPA runs / addendums listing + CSV export
    endpoints + aliases."""

    @_reflex_admin.get("/api/evolution/gepa/runs.csv")
    def _gepa_runs_csv() -> Any:
        """Export GEPA run history as CSV · operators paste into
        Sheets / load into Pandas for cross-run analysis. The
        React panel exposes this through a download button so
        "share this with the team" is one click instead of
        "curl, jq, manually shape".

        Header row matches the GepaRunRecord shape · history
        details are skipped here (they're too nested for CSV;
        the JSON endpoint stays the source of truth for that).
        """
        import csv
        import io

        from runtime.safety.recovery.gepa_runs import get_default_store

        store = get_default_store()
        runs = store.list_recent(limit=200)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "ts",
                "iso_ts",
                "trigger",
                "recipe_id",
                "iterations_run",
                "elapsed_s",
                "front_size",
                "best_candidate_id",
                "best_avg_score",
                "applied",
                "applied_at",
                "winner_lifecycle_state",
                "winner_proposal_id",
                "winner_canary_phase",
                "winner_rollback_reason",
                "best_rationale",
            ]
        )
        from datetime import datetime

        from runtime.safety.recovery.gepa_runs import enrich_run_records

        for r in enrich_run_records(runs):
            w.writerow(
                [
                    f"{r['ts']:.3f}",
                    datetime.fromtimestamp(r["ts"], tz=UTC).isoformat(),
                    r["trigger"],
                    r["recipe_id"] or "",
                    r["iterations_run"],
                    f"{r['elapsed_s']:.3f}",
                    r["front_size"],
                    r["best_candidate_id"] or "",
                    f"{r['best_avg_score']:.4f}" if r["best_avg_score"] is not None else "",
                    "1" if r["applied"] else "0",
                    f"{r['applied_at']:.3f}" if r["applied_at"] else "",
                    r["winner_lifecycle_state"] or "",
                    r["winner_proposal_id"] or "",
                    r["winner_canary_phase"] or "",
                    r["winner_rollback_reason"] or "",
                    # Quote-safe via csv writer · rationale can have
                    # commas/quotes/newlines · the writer escapes them.
                    r["best_rationale"] or "",
                ]
            )
        return PlainTextResponse(
            buf.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=gepa_runs_{int(time.time())}.csv",
            },
        )

    @_reflex_admin.get("/api/evolution/gepa/addendums.csv")
    def _gepa_addendums_csv() -> Any:
        """Export the active addendum map as CSV · one row per
        scope. Lets ops snapshot the production state for
        change-management or off-system inventory."""
        import csv
        import io
        from datetime import datetime

        from runtime.safety.recovery.gepa_addendum_store import list_all

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "scope",
                "recipe_id",
                "path",
                "size_bytes",
                "mtime",
                "iso_mtime",
                "preview",
            ]
        )
        for a in list_all():
            w.writerow(
                [
                    a["scope"],
                    a["recipe_id"] or "",
                    a["path"],
                    a["size"],
                    f"{a['mtime']:.3f}",
                    datetime.fromtimestamp(
                        a["mtime"],
                        tz=UTC,
                    ).isoformat(),
                    a["preview"],
                ]
            )
        return PlainTextResponse(
            buf.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=gepa_addendums_{int(time.time())}.csv",
            },
        )

    @_reflex_admin.get("/api/evolution/gepa/addendums")
    def _gepa_addendums() -> dict:
        """List every active GEPA addendum · global + per-recipe.

        Lets the operator see at a glance: which recipes have a
        custom prompt addendum, when each was applied, what its
        content preview is. Backs the panel's "Addendums by
        recipe" sub-card.
        """
        from runtime.safety.recovery.gepa_addendum_store import list_all

        return {"addendums": list_all(), "source": "gepa"}

    @_reflex_admin.get("/api/evolution/gepa/runs")
    def _gepa_runs(limit: int = 20) -> dict:
        """List recent GEPA runs (manual + auto), newest first.
        Each entry includes the trigger, the best candidate's
        id+score+rationale, and whether it's been applied."""
        from runtime.safety.recovery.gepa_runs import (
            enrich_run_records,
            get_default_store,
        )

        store = get_default_store()
        runs = store.list_recent(limit=limit)
        return {
            "runs": enrich_run_records(runs),
            "source": "gepa",
        }

    register_aliases(
        _reflex_admin,
        [
            ("GET", "/api/evolution/gepa/runs", "/api/evolution/forge/runs", _gepa_runs),
            (
                "GET",
                "/api/evolution/gepa/runs.csv",
                "/api/evolution/forge/runs.csv",
                _gepa_runs_csv,
            ),
            (
                "GET",
                "/api/evolution/gepa/addendums",
                "/api/evolution/forge/addendums",
                _gepa_addendums,
            ),
            (
                "GET",
                "/api/evolution/gepa/addendums.csv",
                "/api/evolution/forge/addendums.csv",
                _gepa_addendums_csv,
            ),
        ],
    )
