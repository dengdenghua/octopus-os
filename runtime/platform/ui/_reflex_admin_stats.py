"""Reflex stats / reload / tiers admin endpoints.

Extracted from ``_reflex_admin_endpoints.py`` so the router module
stays small. ``register_reflex_stats_endpoints`` registers every
stats / reload / tiers endpoint on the given router.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.ui._reflex_admin_helpers import snapshot_rules


def register_reflex_stats_endpoints(
    _reflex_admin: Any,
    *,
    _reflex_router: Any,
    stack: Any,
    last_reload_state: dict,
) -> None:
    """Register the reflex stats / reload / tiers admin endpoints."""
    # Module-scope mutable holding the most recent reload diff.
    # Survives across requests · cleared on process restart. The
    # admin panel reads this to render "last reload added X
    # removed Y modified Z" so operators can verify their yaml
    # edit landed correctly.
    _last_reload_state = last_reload_state

    @_reflex_admin.get("/api/reflex/stats")
    def _reflex_stats(stale_hours: float = 24.0) -> dict:
        return {
            "try_count": _reflex_router.try_count,
            "hit_count": _reflex_router.hit_count,
            "hit_rate": _reflex_router.hit_rate,
            "by_rule": _reflex_router.stats_by_rule(),
            # Coverage callout · which rules look unused so the
            # operator can prune them. Threshold is configurable
            "coverage": _reflex_router.coverage_summary(
                stale_hours=stale_hours,
            ),
        }

    @_reflex_admin.get("/api/reflex/rules")
    def _reflex_rules() -> dict:
        return {"rules": _reflex_router.list_rules()}

    @_reflex_admin.get("/api/reflex/timeseries")
    def _reflex_timeseries(
        window_minutes: int = 60,
        bucket_seconds: int = 60,
    ) -> dict:
        """Bucketed reflex_hit counts over the last ``window_minutes``.

        Reads ``stack.journal`` (in-process · works for both
        InMemoryJournal and JSONLJournal). Counts ALL reflex_hit
        events including the synthetic action-result ones · the
        UI can split them by ``rule_id`` (real rule) vs
        ``rule_id/kind`` shape (action result) when needed.

        Returns ``{buckets: [{ts, count, by_rule}], ...}`` ·
        empty buckets are included so the sparkline doesn't
        visually compress gaps.
        """
        from datetime import UTC, datetime, timedelta

        try:
            window = timedelta(minutes=max(1, int(window_minutes)))
            bucket = max(1, int(bucket_seconds))
            now = datetime.now(UTC)
            since = now - window
            events = stack.journal.read_by_type("reflex_hit")
            # Filter to window
            evs = [e for e in events if getattr(e, "ts", None) and e.ts >= since]
            # Bucket by floor(ts) → bucket-aligned epoch second
            num_buckets = max(1, int(window.total_seconds() / bucket))
            start_epoch = int(since.timestamp())
            buckets: list[dict] = []
            for i in range(num_buckets):
                buckets.append(
                    {
                        "ts": start_epoch + i * bucket,
                        "count": 0,
                        "by_rule": {},
                    }
                )
            for e in evs:
                epoch = int(e.ts.timestamp())
                idx = (epoch - start_epoch) // bucket
                if 0 <= idx < num_buckets:
                    b = buckets[idx]
                    b["count"] += 1
                    rid = getattr(e, "rule_id", "?")
                    b["by_rule"][rid] = b["by_rule"].get(rid, 0) + 1
            # Per-rule totals over the window · easy hit-leader chart
            totals: dict[str, int] = {}
            for b in buckets:
                for rid, n in b["by_rule"].items():
                    totals[rid] = totals.get(rid, 0) + n
            return {
                "window_minutes": int(window.total_seconds() / 60),
                "bucket_seconds": bucket,
                "buckets": buckets,
                "totals_by_rule": totals,
                "total_events": sum(b["count"] for b in buckets),
            }
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @_reflex_admin.get("/api/reflex/suggestions")
    def _reflex_suggestions(
        min_count: int = 3,
        limit: int = 20,
        cluster: bool = False,
        similarity: float = 0.6,
        draft_replies: bool = False,
        draft_model: str | None = None,
    ) -> dict:
        from runtime.core.nerves.reflex.suggestions import get_default_tracker

        t = get_default_tracker()
        sugs = t.suggestions(
            min_count=min_count,
            limit=limit,
            cluster=cluster,
            similarity=similarity,
        )
        drafts: dict[str, str] = {}
        if draft_replies and sugs:
            try:
                from runtime.core.nerves.reflex.reply_drafter import (
                    apply_drafts_to_yaml,
                )
                from runtime.core.nerves.reflex.reply_drafter import (
                    draft_replies as _draft,
                )

                router = getattr(stack.planner, "router", None)
                drafts = _draft(sugs, router=router, model=draft_model)
                # Stamp drafted replies into each suggestion's
                # ``suggested_yaml`` so the panel / curl user
                # sees the filled-in version directly.
                for s in sugs:
                    d = drafts.get(s.get("prompt", ""))
                    if d:
                        s["drafted_reply"] = d
                        s["suggested_yaml"] = apply_drafts_to_yaml(
                            s["suggested_yaml"],
                            d,
                        )
            except (OSError, ImportError, TypeError) as exc:
                drafts = {"_error": f"{type(exc).__name__}: {exc}"}
        return {
            "tracker": t.stats(),
            "min_count": min_count,
            "cluster": cluster,
            "similarity": similarity if cluster else None,
            "drafts_attempted": draft_replies,
            "drafts_count": len([k for k in drafts if not k.startswith("_")]),
            "suggestions": sugs,
        }

    @_reflex_admin.post("/api/reflex/suggestions/reset")
    def _reflex_suggestions_reset() -> dict:
        """Drop all tracked unmatched prompts · use after applying
        a batch of suggestions so the next round starts fresh."""
        from runtime.core.nerves.reflex.suggestions import get_default_tracker

        return {"dropped": get_default_tracker().reset()}

    @_reflex_admin.post("/api/reflex/auto-pr")
    def _reflex_auto_pr(
        min_count: int = 3,
        limit: int = 20,
        cluster: bool = True,
        similarity: float = 0.6,
        push: bool = False,
        open_pr: bool = False,
        base_branch: str = "main",
    ) -> dict:
        """Materialize current suggestions into a real git
        branch + commit (and optionally push + open a PR via
        ``gh``). The reply text is left as TODO · operator
        still owns picking the right answer.

        Defaults are conservative: ``push=false open_pr=false``
        means "stage everything locally so I can review on the
        box before pushing". Set both true to do the round trip
        in one call (CI / scripted use).
        """
        from runtime.core.nerves.reflex.auto_pr import generate_pr
        from runtime.core.nerves.reflex.rules_loader import find_default_rules_file
        from runtime.core.nerves.reflex.suggestions import get_default_tracker

        path = find_default_rules_file()
        if path is None:
            return {"ok": False, "error": "no rules file found"}
        sugs = get_default_tracker().suggestions(
            min_count=min_count,
            limit=limit,
            cluster=cluster,
            similarity=similarity,
        )
        if not sugs:
            return {
                "ok": False,
                "error": f"no suggestions with count >= {min_count}",
            }
        return generate_pr(
            file_path=path,
            suggestions=sugs,
            push=push,
            open_pr=open_pr,
            base_branch=base_branch,
        )

    @_reflex_admin.post("/api/reflex/reload")
    def _reflex_reload(
        reset_stats: bool = False,
        commit: bool = False,
    ) -> dict:
        """Re-read ``data/reflex_rules.yaml`` and swap matcher
        list in-place. Returns the new rule count + a diff vs
        the rules that were active before this call (added /
        removed / modified) so operators can confirm their edit
        had the intended effect.

        Query param ``reset_stats=true`` clears the hit-rate
        counters · default keeps them so before/after compares
        stay meaningful.
        """
        import time as _t

        from runtime.cli import _build_reflex_router

        try:
            # Snapshot the current rules BEFORE swapping.
            before = snapshot_rules(_reflex_router._reflexes)
            fresh = _build_reflex_router()
            count = _reflex_router.replace_reflexes(
                fresh._reflexes,
                reset_stats=reset_stats,
            )
            after = snapshot_rules(_reflex_router._reflexes)

            # Compute diff · added/removed by id, modified by
            # comparing the snapshot dicts.
            added = sorted(set(after) - set(before))
            removed = sorted(set(before) - set(after))
            modified = sorted(
                rid for rid in (set(after) & set(before)) if before[rid] != after[rid]
            )
            unchanged = len((set(after) & set(before)) - set(modified))
            _last_reload_state.update(
                {
                    "ts": _t.time(),
                    "added": added,
                    "removed": removed,
                    "modified": [
                        {
                            "rule_id": rid,
                            "before": before[rid],
                            "after": after[rid],
                        }
                        for rid in modified
                    ],
                    "unchanged_count": unchanged,
                    "rules_loaded": count,
                }
            )
            # Optional git auto-commit · only when the operator
            # the YAML file enables it via top-level
            # ``git_tracking: true`` · TODO future). The reload
            # itself succeeds regardless of git's outcome.
            git_result: dict = {}
            if commit:
                try:
                    from runtime.core.nerves.reflex.git_track import (
                        auto_commit,
                        format_diff_summary,
                    )
                    from runtime.core.nerves.reflex.rules_loader import (
                        find_default_rules_file,
                    )

                    path = find_default_rules_file()
                    if path is not None:
                        git_result = auto_commit(
                            path,
                            diff_summary=format_diff_summary(
                                {
                                    "added": added,
                                    "removed": removed,
                                    "modified": modified,
                                }
                            ),
                        )
                    else:
                        git_result = {"ok": False, "error": "no rules file"}
                except ImportError as ie:
                    git_result = {"ok": False, "error": str(ie)}
                except (OSError, ValueError) as exc:
                    git_result = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }

            return {
                "ok": True,
                "rules_loaded": count,
                "stats_reset": reset_stats,
                "diff": {
                    "added": added,
                    "removed": removed,
                    "modified": modified,
                    "unchanged_count": unchanged,
                },
                "git": git_result if commit else None,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @_reflex_admin.get("/api/reflex/test")
    def _reflex_test() -> dict:
        """Run every ``expects:`` test case from the YAML and
        return a CI-style summary. Lets the operator (or a
        pre-deploy hook) catch reflex regressions before they
        ship · "this rule used to match 'X' but doesn't anymore"
        is exactly the kind of bug a static test suite catches.

        Doesn't mutate state · safe to call repeatedly.
        """
        from runtime.core.nerves.reflex.test_runner import run_tests

        return run_tests(_reflex_router)

    @_reflex_admin.get("/api/reflex/git/history")
    def _reflex_git_history(limit: int = 20) -> dict:
        """Return the most recent git commits touching the rules
        file · empty list when git isn't initialized in the
        repo. Lets ops see "who changed what when" without
        shelling in."""
        from runtime.core.nerves.reflex.git_track import file_history
        from runtime.core.nerves.reflex.rules_loader import find_default_rules_file

        path = find_default_rules_file()
        if path is None:
            return {"history": [], "error": "no rules file"}
        return {"history": file_history(path, limit=limit)}

    @_reflex_admin.get("/api/reflex/last-reload")
    def _reflex_last_reload() -> dict:
        """Return the most recent reload's diff details · empty
        until /api/reflex/reload has been called at least once."""
        return dict(_last_reload_state)

    @_reflex_admin.get("/api/reflex/broadcast")
    def _reflex_broadcast_config() -> dict:
        """Show the active outbound broadcast config · sanitized
        (no credentials returned). Lets ops verify the yaml's
        ``broadcast.mqtt`` block was picked up correctly."""
        from runtime.core.nerves.reflex.broadcast import get_default_broadcaster

        return get_default_broadcaster().describe()

    @_reflex_admin.get("/api/reflex/tiers")
    def _reflex_tiers() -> dict:
        """Expose response-tier stats · how many requests each
        tier (fuzzy_cache / slm / ...) absorbed, what fraction
        never reached the planner. The reflex layer itself is
        tier 0 · its stats live in /api/reflex/stats already.
        """
        from runtime.core.nerves.reflex.tiers import (
            get_default_fuzzy_cache,
            get_default_slm,
        )

        return {
            "tiers": [
                get_default_fuzzy_cache().describe(),
                get_default_slm().describe(),
            ],
        }

    @_reflex_admin.post("/api/reflex/tiers/fuzzy-cache/clear")
    def _reflex_tiers_fuzzy_clear() -> dict:
        """Drop all entries from the fuzzy cache · use after
        changing rules to make sure the cache doesn't keep
        serving the now-superseded LLM reply."""
        from runtime.core.nerves.reflex.tiers import get_default_fuzzy_cache

        fc = get_default_fuzzy_cache()
        n = len(fc._store)  # noqa: SLF001 (deliberate access)
        fc._store.clear()  # noqa: SLF001
        fc.hits = 0
        fc.misses = 0
        return {"ok": True, "dropped": n}
