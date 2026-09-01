"""File-rollback, rewind, blackboard, hemolymph, regeneration, budget and run endpoints.

Pure structural extraction from ``_observability_router_factory.py`` (no logic
changes). Builder that registers the observability-panel, rollback, rewind,
budget and probe-run handlers onto the router.
"""

from __future__ import annotations

import json
from typing import Any

from runtime.sensing._fastapi_guard import require_fastapi

from ._observability_auth import (
    _journal_scope_context,
    _observability_scope,
    _require_global_control,
    _scoped_observability_journal,
)
from ._observability_helpers import (
    HTTPException,
    Query,
    Request,
    _serialize_file_rollback_event,
    _serialize_rollback_result,
)
from ._observability_state import ObservabilityContext


def register_rollback_panels_endpoints(router: Any, ctx: ObservabilityContext) -> None:
    """Register the rollback / rewind / panel / budget / run endpoints."""
    require_fastapi(__name__)

    journal = ctx.journal
    registry = ctx.registry
    planner = ctx.planner

    def _rollback_file_events(
        source_journal: Any,
        *,
        event_id: str | None = None,
        task_id: str | None = None,
        path: str | None = None,
        limit: int = 500,
    ) -> list[Any]:
        events: list[Any] = []
        for event in source_journal.read_by_type("file_op"):
            if event_id and str(getattr(event, "event_id", "") or "") != event_id:
                continue
            if task_id and str(getattr(event, "task_id", "") or "") != task_id:
                continue
            if path and str(getattr(event, "path", "") or "") != path:
                continue
            events.append(event)
        return events[-limit:]

    def _rollback_result(
        source_journal: Any,
        scope: Any,
        *,
        event_id: str | None,
        task_id: str | None,
        path: str | None,
        project_root: str | None,
        limit: int,
        dry_run: bool,
    ) -> dict[str, Any]:
        from runtime.memory.runtime_state.file_transactions import (
            apply_file_rollback_ledger,
        )

        events = _rollback_file_events(
            source_journal,
            event_id=event_id,
            task_id=task_id,
            path=path,
            limit=limit,
        )
        result = apply_file_rollback_ledger(
            events,
            project_root=project_root,
            dry_run=dry_run,
        )
        if not dry_run:
            from runtime.memory.journal import FileRollbackEvent

            with _journal_scope_context(scope):
                journal.write(
                    FileRollbackEvent(
                        dry_run=False,
                        project_root=project_root or "",
                        event_id_filter=event_id,
                        task_id_filter=task_id,
                        path_filter=path,
                        applied=int(getattr(result, "applied", 0) or 0),
                        skipped=int(getattr(result, "skipped", 0) or 0),
                        failed=int(getattr(result, "failed", 0) or 0),
                        source_event_ids=[
                            str(getattr(entry, "source_event_id", "") or "")
                            for entry in getattr(result, "entries", ()) or ()
                            if getattr(entry, "source_event_id", "") or ""
                        ],
                        paths=[
                            str(getattr(entry, "path", "") or "")
                            for entry in getattr(result, "entries", ()) or ()
                            if getattr(entry, "path", "") or ""
                        ],
                        errors=list(getattr(result, "errors", ()) or ()),
                    )
                )
        return _serialize_rollback_result(
            result,
            dry_run=dry_run,
            matched_events=len(events),
            event_id=event_id,
            task_id=task_id,
            path=path,
            project_root=project_root,
        )

    # ═══════════════════════════════════════════════════════
    # Observability panels · feed the /workspace/observability UI.
    # Journal-backed views are request-scoped; process-global in-memory
    # panels require the explicit privileged cross-tenant control mode.
    # ═══════════════════════════════════════════════════════

    # ─── /api/blackboard · turn-scoped KV viewer ────────────
    # File rollback endpoints expose reversible file_op ledgers.
    @router.get("/api/files/rollback/preview")
    def api_files_rollback_preview(
        request: Request,
        event_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        path: str | None = Query(default=None),
        project_root: str | None = Query(default=None),
        limit: int = Query(default=500, ge=1, le=2000),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Dry-run reversible file operations from the journal."""
        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        return _rollback_result(
            _scoped_observability_journal(journal, scope),
            scope,
            event_id=event_id,
            task_id=task_id,
            path=path,
            project_root=project_root,
            limit=limit,
            dry_run=True,
        )

    @router.post("/api/files/rollback/apply")
    def api_files_rollback_apply(
        body: dict[str, Any],
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Apply a task/path-scoped rollback ledger under a project root."""
        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        project_root = body.get("project_root")
        if not isinstance(project_root, str) or not project_root.strip():
            raise HTTPException(400, "project_root required for rollback apply")
        event_id = body.get("event_id")
        task_id = body.get("task_id")
        path = body.get("path")
        event_filter = event_id.strip() if isinstance(event_id, str) else None
        task_filter = task_id.strip() if isinstance(task_id, str) else None
        path_filter = path.strip() if isinstance(path, str) else None
        if not event_filter and not task_filter and not path_filter:
            raise HTTPException(
                400,
                "event_id, task_id or path required for rollback apply",
            )
        limit_value = body.get("limit", 500)
        try:
            limit = int(limit_value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "limit must be an integer") from exc
        if limit < 1 or limit > 2000:
            raise HTTPException(400, "limit must be between 1 and 2000")

        return _rollback_result(
            _scoped_observability_journal(journal, scope),
            scope,
            event_id=event_filter,
            task_id=task_filter,
            path=path_filter,
            project_root=project_root.strip(),
            limit=limit,
            dry_run=False,
        )

    @router.get("/api/files/rollback/history")
    def api_files_rollback_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        events = _scoped_observability_journal(journal, scope).read_by_type("file_rollback")[
            -limit:
        ]
        serialized = [_serialize_file_rollback_event(event) for event in reversed(events)]
        return {
            "count": len(serialized),
            "events": serialized,
        }

    # ─── /api/tasks/{task_id}/rewind · turn-scoped rewind ────
    # Sibling to /api/files/rollback/* · rolls a task back to a
    # prior ``react_checkpoint`` anchor (Grok Build's /rewind
    # ergonomics). Reuses ``apply_file_rollback_ledger`` but slices
    # the file_op stream by checkpoint ``ts`` instead of by event_id.
    @router.get("/api/tasks/{task_id}/rewind/points")
    def api_task_rewind_points(
        task_id: str,
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """List every ``react_checkpoint`` anchor for ``task_id``.

        Each entry is a valid rewind target. The last one is the
        current state.
        """
        from runtime.core.cerebrum.rewind import list_rewind_points

        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        points = list_rewind_points(_scoped_observability_journal(journal, scope), task_id)
        return {
            "task_id": task_id,
            "count": len(points),
            "points": [p.to_dict() for p in points],
        }

    @router.post("/api/tasks/{task_id}/rewind/apply")
    def api_task_rewind_apply(
        task_id: str,
        body: dict[str, Any],
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Rewind ``task_id`` to the checkpoint at ``iteration``.

        Request body:
            {"iteration": 3, "project_root": "/abs/path", "dry_run": false}

        ``dry_run=true`` previews the rollback without touching disk.
        """
        from runtime.core.cerebrum.rewind import rewind_to_checkpoint

        iteration_raw = body.get("iteration")
        try:
            iteration = int(iteration_raw) if iteration_raw is not None else 0
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "iteration (int) required") from exc

        project_root = body.get("project_root")
        if isinstance(project_root, str):
            project_root = project_root.strip() or None

        dry_run = bool(body.get("dry_run", False))

        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        scoped_journal = _scoped_observability_journal(journal, scope)
        try:
            result = rewind_to_checkpoint(
                scoped_journal,
                task_id,
                iteration,
                project_root=project_root,
                dry_run=dry_run,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

        if not dry_run:
            # Mirror the existing rollback endpoint's audit trail so
            # /api/files/rollback/history still surfaces rewinds.
            from runtime.memory.journal import FileRollbackEvent

            with _journal_scope_context(scope):
                journal.write(
                    FileRollbackEvent(
                        dry_run=False,
                        project_root=project_root or "",
                        event_id_filter=None,
                        task_id_filter=task_id,
                        path_filter=None,
                        applied=int(getattr(result.file_rollback, "applied", 0) or 0),
                        skipped=int(getattr(result.file_rollback, "skipped", 0) or 0),
                        failed=int(getattr(result.file_rollback, "failed", 0) or 0),
                        source_event_ids=[
                            str(getattr(entry, "source_event_id", "") or "")
                            for entry in getattr(result.file_rollback, "entries", ()) or ()
                            if getattr(entry, "source_event_id", "") or ""
                        ],
                        paths=[
                            str(getattr(entry, "path", "") or "")
                            for entry in getattr(result.file_rollback, "entries", ()) or ()
                            if getattr(entry, "path", "") or ""
                        ],
                        errors=list(getattr(result.file_rollback, "errors", ()) or ()),
                    )
                )

        return result.to_dict()

    @router.get("/api/blackboard")
    def api_blackboard(
        request: Request,
        turn_id: str | None = None,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Return either the list of active turns (no ``turn_id``)
        or a single turn's full snapshot (with ``turn_id``).

        The blackboard is process-local and ephemeral — this endpoint
        only makes sense for live debugging during a run. For the UI:
        poll the list on a heartbeat, then drill into a specific
        ``turn_id`` on click.
        """
        from runtime.memory.runtime_state.blackboard import (
            get_blackboard,
            list_active_turns,
        )

        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        if not turn_id:
            return {"global_control_plane": True, "turns": list_active_turns()}
        bb = get_blackboard(turn_id)
        if bb is None:
            raise HTTPException(404, f"no blackboard for turn_id={turn_id!r}")
        # snapshot() is already shallow-copied inside the bb lock, so
        # serializing here is safe. Values may be arbitrary JSON —
        # stringify exotic types defensively.
        snap = bb.snapshot()
        safe: dict[str, Any] = {}
        for k, v in snap.items():
            try:
                json.dumps(v)
                safe[k] = v
            except (TypeError, ValueError):
                safe[k] = repr(v)[:500]
        return {
            "global_control_plane": True,
            "turn_id": turn_id,
            "key_count": len(safe),
            "entries": safe,
            "audit": bb.audit(),
        }

    # ─── /api/hemolymph/recent · compose ring-buffer ────────
    @router.get("/api/hemolymph/recent")
    def api_hemolymph_recent(
        request: Request,
        limit: int = Query(default=20, ge=1, le=50),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Recent ``ContextComposer.compose()`` snapshots.

        Each entry is a compact view of how the four quotas (system /
        suckers / memory / history) were filled on one compose call:
        ``{ts, budget_tokens, tokens_used, utilization,
        by_bucket: {bucket: {used, alloc}}, segment_count, recipe_id,
        task_type}``. Feeds the 4-bucket budget meter panel.
        """
        from runtime.memory.hemolymph.composer import (
            get_recent_compose_snapshots,
        )

        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        snaps = get_recent_compose_snapshots(limit=limit)
        return {
            "global_control_plane": True,
            "count": len(snaps),
            "max_tracked": 50,
            "snapshots": snaps,
        }

    # ─── /api/regeneration/summary · 6-producer aggregate ───
    @router.get("/api/regeneration/summary")
    def api_regeneration_summary(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Aggregate snapshot of the 6 reflection producers.

        Unlike ``/api/reflect`` (which RE-RUNS every producer on each
        call · expensive), this reads the pre-computed ``learned_*``
        sections off the planner + journal counters. Intended for
        the Observability panel's heartbeat polling: cheap, stateless,
        safe to call once per second.

        Shape: one key per producer; each carries a ``status`` and
        small count fields the UI renders as a tile.
        """
        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        from runtime.memory.journal import TrajectoryEvent

        # Trajectory counts · drive most producers' freshness signal
        traj_total = 0
        traj_failures = 0
        traj_by_strategy: dict[str, int] = {}
        try:
            for ev in journal.read_by_type("trajectory"):
                if not isinstance(ev, TrajectoryEvent):
                    continue
                traj_total += 1
                strat = ev.trajectory.strategy_id or "unknown"
                traj_by_strategy[strat] = traj_by_strategy.get(strat, 0) + 1
                if not ev.trajectory.outcome.success:
                    traj_failures += 1
        except (OSError, ImportError, AttributeError):  # noqa: BLE001 — observability metric source unavailable; skip
            pass

        # rule_extractor · sync with planner's learned rules
        rules_count = 0
        memories_count = 0
        if planner is not None:
            rules_count = sum(
                1
                for ln in (getattr(planner, "learned_rules_section", "") or "").splitlines()
                if ln.lstrip().startswith("- [")
            )
            memories_count = sum(
                1
                for ln in (getattr(planner, "learned_memories_section", "") or "").splitlines()
                if ln.lstrip().startswith("- [")
            )

        # skill_forge · count forged skills in registry
        forged_count = 0
        try:
            for name in registry.all_names():
                sk = registry.get(name)
                if "forged" in (getattr(sk, "affinity", []) or []):
                    forged_count += 1
        except (OSError, ImportError, AttributeError):  # noqa: BLE001 — observability metric source unavailable; skip
            pass

        # kg_updater · ambient triple count if KG is queryable
        kg_size = 0
        try:
            from runtime.memory.knowledge_graph import KnowledgeGraph
            from runtime.safety.recovery import KGUpdater

            kg = KnowledgeGraph()
            KGUpdater(journal, kg, scope=scope).update()
            kg_size = kg.count()
        except (OSError, ImportError, AttributeError):  # noqa: BLE001 — observability metric source unavailable; skip
            pass

        # recipe_evaluator · GEPA recipe count (best-effort)
        recipes_count = 0
        try:
            from runtime.core.cerebrum.react_loop import (
                get_react_variant_stats,
            )

            recipes_count = len(get_react_variant_stats())
        except (OSError, ImportError, AttributeError):  # noqa: BLE001 — observability metric source unavailable; skip
            pass

        return {
            "global_control_plane": True,
            "skill_forge": {
                "status": "ready" if forged_count else "idle",
                "forged_count": forged_count,
            },
            "rule_extractor": {
                "status": "ready" if rules_count else "idle",
                "rules_count": rules_count,
                "failure_trajectories": traj_failures,
            },
            "memory_consolidator": {
                "status": "ready" if memories_count else "idle",
                "memories_count": memories_count,
                "trajectories_scanned": traj_total,
            },
            "kg_updater": {
                "status": "ready" if kg_size else "idle",
                "triple_count": kg_size,
            },
            "workflow_rewriter": {
                "status": "ready" if traj_total >= 5 else "warming",
                "trajectories_scanned": traj_total,
            },
            "recipe_evaluator": {
                "status": "ready" if recipes_count else "idle",
                "recipes_tracked": recipes_count,
            },
            "trajectories": {
                "total": traj_total,
                "failures": traj_failures,
                "by_strategy": traj_by_strategy,
            },
        }

    # ─── /api/budget/summary · per-task cost roll-up ────────
    @router.get("/api/budget/summary")
    def api_budget_summary(
        request: Request,
        limit: int = Query(default=20, ge=1, le=200),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Aggregate cost across recent tasks.

        Reads ``budget_commit`` events from the journal, groups by
        ``task_id``, sums ``tokens_in + tokens_out`` and ``usd``.
        Returns the latest ``limit`` tasks (most recent first) plus
        a grand total. Cheap enough for a 2s heartbeat.
        """
        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        scoped_journal = _scoped_observability_journal(journal, scope)
        per_task: dict[str, dict[str, Any]] = {}
        total_tokens = 0
        total_usd = 0.0
        commit_count = 0
        try:
            for ev in scoped_journal.read_by_type("budget_commit"):
                commit_count += 1
                tid = str(ev.task_id) if ev.task_id else "(unbound)"
                cost = getattr(ev, "cost", None)
                tokens = 0
                usd = 0.0
                if cost is not None:
                    tokens = int(getattr(cost, "tokens_in", 0) + getattr(cost, "tokens_out", 0))
                    usd = float(getattr(cost, "usd", 0.0))
                entry = per_task.setdefault(
                    tid,
                    {
                        "task_id": tid,
                        "tokens": 0,
                        "usd": 0.0,
                        "commit_count": 0,
                        "last_ts": None,
                    },
                )
                entry["tokens"] += tokens
                entry["usd"] = round(entry["usd"] + usd, 6)
                entry["commit_count"] += 1
                entry["last_ts"] = ev.ts.isoformat() if ev.ts else None
                total_tokens += tokens
                total_usd += usd
        except (OSError, ImportError, AttributeError):  # noqa: BLE001 — observability metric source unavailable; skip
            pass

        # Sort by last_ts DESC · missing goes last
        tasks = sorted(
            per_task.values(),
            key=lambda x: x["last_ts"] or "",
            reverse=True,
        )[:limit]
        return {
            "total_tokens": total_tokens,
            "total_usd": round(total_usd, 6),
            "commit_count": commit_count,
            "task_count": len(per_task),
            "tasks": tasks,
        }

    # ─── /api/run (probe) ───────────────────────────────────
    @router.post("/api/run")
    def api_run(
        body: dict[str, Any],
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        from runtime.core.cerebrum import StaticPlanner
        from runtime.core.cerebrum.planner import Rule
        from runtime.core.graph_runtime import GraphRuntime
        from runtime.execution.tool_engine import ToolExecutor
        from runtime.platform.models import (
            ArmId,
            Budget,
            BudgetLimits,
            BudgetSpec,
            ParsedIntent,
            SkillId,
        )
        from runtime.safety.auth import TrustEngine

        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        goal = str(body.get("goal", "")).strip()
        if not goal:
            raise HTTPException(400, "goal required")

        immunity = TrustEngine(trusted_sources=["skill://public/*"])
        executor = ToolExecutor(
            registry=registry,
            immunity=immunity,
            journal=journal,
        )
        runtime = GraphRuntime(executor=executor, journal=journal)
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="ui_probe",
                    intent_types=["task"],
                    skill_sequence=[SkillId("list_cwd")],
                ),
            ],
            default_budget=BudgetSpec(tokens=10_000, usd=0.10),
            fallback_skill=SkillId("list_cwd"),
        )

        intent = ParsedIntent(
            raw=goal,
            intent_type="task",
            normalized_goal=goal,
        )
        graph = planner.plan(intent)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        with _journal_scope_context(scope):
            traj = runtime.run(
                graph,
                budget=budget,
                caller="arms/ui",
                arm_id=ArmId("ui_arm"),
            )
        return {
            "global_control_plane": True,
            "success": traj.outcome.success,
            "steps": traj.step_count,
            "tokens_spent": budget.tokens_spent,
            "usd_spent": round(budget.usd_spent, 6),
        }


__all__ = ["register_rollback_panels_endpoints"]
