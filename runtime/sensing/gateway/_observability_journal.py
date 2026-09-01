"""Journal, reflect, evolution and tool-effect endpoints for the observability router.

Pure structural extraction from ``_observability_router_factory.py`` (no logic
changes). Builder that registers the ``/api/journal``, ``/api/reflect``,
``/api/evolution/*`` and ``/api/tool-effects/*`` handlers onto the router.
"""

from __future__ import annotations

from typing import Any

from runtime.sensing._fastapi_guard import require_fastapi

from ._observability_auth import (
    _can_authorize_retry,
    _journal_scope_context,
    _observability_scope,
    _operator_actor,
    _require_global_control,
    _scoped_observability_journal,
)
from ._observability_helpers import HTTPException, Query, Request, _safe_call, _skill_forge_stub
from ._observability_state import ObservabilityContext


def register_journal_endpoints(router: Any, ctx: ObservabilityContext) -> None:
    """Register journal / reflect / evolution / tool-effect endpoints."""
    require_fastapi(__name__)

    journal = ctx.journal
    registry = ctx.registry
    planner = ctx.planner
    effect_store = ctx.effect_store

    @router.get("/api/tool-effects")
    def api_tool_effects(
        request: Request,
        state: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """List fenced external-effect receipts without exposing arguments/results."""

        _require_global_control(request, ctx, cross_tenant=cross_tenant)

        allowed_states = {
            "claimed",
            "started",
            "committed",
            "indeterminate",
            "retry_authorized",
        }
        if state is not None and state not in allowed_states:
            raise HTTPException(400, "invalid tool-effect state")
        if effect_store is None:
            return {
                "backend": "disabled",
                "global_control_plane": True,
                "shared_across_hosts": False,
                "can_authorize_retry": _can_authorize_retry(request, ctx),
                "count": 0,
                "state_counts": {},
                "receipts": [],
            }
        sampled = effect_store.list_receipts(limit=500)
        receipts = (
            sampled[:limit]
            if state is None
            else effect_store.list_receipts(state=state, limit=limit)
        )
        counts: dict[str, int] = {}
        for receipt in sampled:
            counts[receipt.state] = counts.get(receipt.state, 0) + 1
        return {
            "backend": str(getattr(effect_store, "backend_name", "unknown")),
            "global_control_plane": True,
            "shared_across_hosts": bool(getattr(effect_store, "shared_across_hosts", False)),
            "can_authorize_retry": _can_authorize_retry(request, ctx),
            "count": len(receipts),
            "state_counts": counts,
            "receipts": [receipt.to_dict() for receipt in receipts],
        }

    @router.post("/api/tool-effects/{effect_key:path}/authorize-retry")
    def api_tool_effect_authorize_retry(
        effect_key: str,
        body: dict[str, Any],
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Authorize one retry after an operator verifies no effect occurred."""

        scope = _require_global_control(request, ctx, cross_tenant=cross_tenant)
        actor = _operator_actor(request, ctx)
        if effect_store is None:
            raise HTTPException(503, "tool-effect receipt backend unavailable")
        if not effect_key or len(effect_key) > 512:
            raise HTTPException(400, "invalid effect_key")
        if body.get("confirm") != "AUTHORIZE RETRY":
            raise HTTPException(400, 'confirm must equal "AUTHORIZE RETRY"')
        reason = str(body.get("reason") or "").strip()
        if len(reason) < 8 or len(reason) > 500:
            raise HTTPException(400, "reason must be between 8 and 500 characters")
        token_value = body.get("fencing_token")
        if not isinstance(token_value, (str, int)) or isinstance(token_value, bool):
            raise HTTPException(400, "fencing_token must be an integer")
        try:
            expected_token = int(token_value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "fencing_token must be an integer") from exc
        if expected_token < 0:
            raise HTTPException(400, "fencing_token must be non-negative")
        changed = effect_store.authorize_retry(
            effect_key=effect_key,
            expected_fencing_token=expected_token,
            actor=actor,
            reason=reason,
        )
        if not changed:
            raise HTTPException(
                409,
                "receipt changed, is not indeterminate, or has a live owner",
            )
        audit_warning = ""
        try:
            with _journal_scope_context(scope):
                journal.write_tool_effect_reconciliation(
                    effect_key=effect_key,
                    fencing_token=expected_token,
                    action="authorize_retry",
                    reason=reason,
                    actor=actor,
                )
        except Exception as exc:
            audit_warning = f"journal audit append failed: {type(exc).__name__}"
        return {
            "ok": True,
            "global_control_plane": True,
            "effect_key": effect_key,
            "state": "retry_authorized",
            "fencing_token": expected_token,
            "actor": actor,
            "audit_warning": audit_warning,
        }

    # ─── /api/journal ───────────────────────────────────────
    @router.get("/api/journal")
    def api_journal(
        request: Request,
        limit: int = Query(default=20, ge=1, le=500),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        events = _scoped_observability_journal(journal, scope).read_all()
        counts: dict[str, int] = {}
        for e in events:
            counts[e.event_type] = counts.get(e.event_type, 0) + 1
        tail = [
            {
                "event_type": e.event_type,
                "ts": e.ts.isoformat(),
                "task_id": str(e.task_id) if e.task_id else None,
                "arm_id": e.arm_id,
            }
            for e in events[-limit:]
        ]
        return {"total": len(events), "counts": counts, "recent": tail}

    # ─── /api/journal/timeline ─────────────────────────────
    @router.get("/api/journal/timeline")
    def api_journal_timeline(
        request: Request,
        task_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        events = _scoped_observability_journal(journal, scope).read_all()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            tid = str(e.task_id) if e.task_id else "_no_task"
            if task_id and tid != task_id:
                continue
            entry = {
                "event_type": e.event_type,
                "ts": e.ts.isoformat(),
                "task_id": tid,
                "arm_id": e.arm_id,
            }
            payload = getattr(e, "payload", None)
            if isinstance(payload, dict):
                for k in (
                    "skill_name",
                    "strategy",
                    "strategy_id",
                    "thought",
                    "action",
                    "observation",
                    "iteration",
                    "final_answer",
                    "error",
                    "tokens_in",
                    "tokens_out",
                    "usd",
                    "latency_ms",
                    "model",
                    "provider",
                ):
                    if k in payload:
                        entry[k] = payload[k]
            # Direct field passthrough for structured events — the
            # settlement bridge (workflow/start·progress·end, job/change)
            # and other typed events carry their payload as model fields,
            # not a nested ``payload`` dict. Whitelisted only: the journal
            # envelope (event_id / actor / tenant / agent / conversation…)
            # is never leaked into the timeline.
            for k in (
                "skill_name",
                "strategy",
                "strategy_id",
                "thought",
                "action",
                "observation",
                "iteration",
                "final_answer",
                "error",
                "tokens_in",
                "tokens_out",
                "usd",
                "latency_ms",
                "model",
                "provider",
                "job_id",
                "kind",
                "label",
                "status",
                "detail",
                "run_id",
                "name",
                "description",
                "text",
                "agent_seq",
                "agent_label",
                "stop_reason",
                "agents_started",
            ):
                if k in entry:
                    continue
                value = getattr(e, k, None)
                if value is not None and not isinstance(value, (dict, list)):
                    entry[k] = value
            grouped.setdefault(tid, []).append(entry)
        task_ids = sorted(
            grouped.keys(), key=lambda k: grouped[k][0]["ts"] if grouped[k] else "", reverse=True
        )[:limit]
        return {
            "task_ids": task_ids,
            "timelines": {tid: grouped[tid] for tid in task_ids},
        }

    # ─── /api/reflect ───────────────────────────────────────
    @router.get("/api/reflect")
    def api_reflect(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        scoped_journal = _scoped_observability_journal(journal, scope)
        if len(scoped_journal.read_all()) == 0:
            return {"error": "journal empty · run something first"}

        # Lazy imports · these pull in heavier analysis code that
        # doesn't need to be at module-load time.
        from runtime.memory.knowledge_graph import KnowledgeGraph
        from runtime.safety.recovery import (
            KGUpdater,
            MemoryConsolidator,
            RecipeEvaluator,
            RuleExtractor,
            WorkflowRewriter,
        )

        sf_result = _safe_call(
            lambda: _skill_forge_stub(journal, registry, scope=scope),
        )
        re_report = RuleExtractor(journal, scope=scope).extract()
        kg = KnowledgeGraph()
        kg_report = KGUpdater(journal, kg, scope=scope).update()
        mc = MemoryConsolidator(journal, scope=scope).consolidate()
        wr = WorkflowRewriter(journal, scope=scope).analyze()
        rc = RecipeEvaluator(journal, scope=scope).evaluate()

        return {
            "skill_forge": sf_result,
            "rule_extractor": {"rules": len(re_report.rules_produced)},
            "kg": {
                "accepted": kg_report.triples_accepted,
                "total": kg.count(),
            },
            "memory": {"memories": len(mc.memories_produced)},
            "workflow": {
                "proposals": len(wr.proposals),
                "by_kind": wr.proposals_by_kind,
            },
            "recipe": {
                "recipes": rc.recipes_found,
                "best": rc.best.recipe_id if rc.best else None,
            },
        }

    def _parse_section_lines(section: str) -> list[str]:
        out: list[str] = []
        for ln in (section or "").splitlines():
            stripped = ln.lstrip()
            if stripped.startswith("- ["):
                out.append(stripped[2:].strip())  # Implementation note.
        return out

    def _rebuild_section(header: str, items: list[str]) -> str:
        if not items:
            return ""
        lines = [header]
        for item in items:
            lines.append(f"  - {item}")
        return "\n".join(lines)

    # ─── /api/evolution/status ──────────────────────────────
    # Cheap read-only view of what the planner has currently
    # internalized from past runs. Unlike /api/reflect (which
    # RUNS all 6 producers synchronously · expensive), this just
    # reads the pre-computed ``learned_*_section`` strings + counts
    # trajectories by strategy_id. Cost: O(1) + O(N_trajectories).
    @router.get("/api/evolution/status")
    def api_evolution_status(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        if planner is None:
            return {
                "global_control_plane": True,
                "enabled": False,
                "reason": "no planner wired to observability router",
            }
        rules_section = getattr(planner, "learned_rules_section", "") or ""
        memories_section = getattr(planner, "learned_memories_section", "") or ""
        # Count rule/memory lines: each bullet starts with "  - "
        # (see format_rules_for_prompt / format_memories_for_prompt).
        rules_count = sum(1 for ln in rules_section.splitlines() if ln.lstrip().startswith("- ["))
        memories_count = sum(
            1 for ln in memories_section.splitlines() if ln.lstrip().startswith("- [")
        )

        # Trajectory-level counts from journal · by strategy_id
        react_trajs = 0
        react_failures = 0
        total_trajs = 0
        try:
            from runtime.memory.journal import TrajectoryEvent

            for ev in journal.read_by_type("trajectory"):
                if not isinstance(ev, TrajectoryEvent):
                    continue
                total_trajs += 1
                if ev.trajectory.strategy_id == "react_loop":
                    react_trajs += 1
                    if not ev.trajectory.outcome.success:
                        react_failures += 1
        except (OSError, ImportError, AttributeError):  # noqa: BLE001 — observability metric source unavailable; skip
            pass

        rules_lines = _parse_section_lines(rules_section)
        memories_lines = _parse_section_lines(memories_section)

        react_variants: list[dict[str, Any]] = []
        try:
            from runtime.core.cerebrum.react_loop import (
                get_react_variant_stats,
            )

            react_variants = get_react_variant_stats()
        except (OSError, ImportError, AttributeError):
            react_variants = []

        return {
            "global_control_plane": True,
            "enabled": True,
            "rules_count": rules_count,
            "memories_count": memories_count,
            "rules_section": rules_section,
            "memories_section": memories_section,
            "rules_lines": rules_lines,
            "memories_lines": memories_lines,
            "trajectories": {
                "total": total_trajs,
                "react_loop": react_trajs,
                "react_loop_failures": react_failures,
            },
            "react_variants": react_variants,
        }

    # ─── DELETE /api/evolution/rules/{index} ────────────────
    @router.delete("/api/evolution/rules/{index}")
    def api_forget_rule(
        index: int,
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        if planner is None:
            raise HTTPException(status_code=503, detail="planner not wired")
        current = _parse_section_lines(
            getattr(planner, "learned_rules_section", "") or "",
        )
        if index < 0 or index >= len(current):
            raise HTTPException(
                status_code=404,
                detail=f"rule index {index} out of range (have {len(current)})",
            )
        dropped = current.pop(index)
        planner.learned_rules_section = _rebuild_section(
            "LEARNED MITIGATIONS (from past failures):",
            current,
        )
        return {"global_control_plane": True, "dropped": dropped, "remaining": len(current)}

    # ─── DELETE /api/evolution/memories/{index} ─────────────
    @router.delete("/api/evolution/memories/{index}")
    def api_forget_memory(
        index: int,
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        if planner is None:
            raise HTTPException(status_code=503, detail="planner not wired")
        current = _parse_section_lines(
            getattr(planner, "learned_memories_section", "") or "",
        )
        if index < 0 or index >= len(current):
            raise HTTPException(
                status_code=404,
                detail=f"memory index {index} out of range (have {len(current)})",
            )
        dropped = current.pop(index)
        planner.learned_memories_section = _rebuild_section(
            "CONSOLIDATED MEMORIES (past pattern stats):",
            current,
        )
        return {"global_control_plane": True, "dropped": dropped, "remaining": len(current)}


__all__ = ["register_journal_endpoints"]
