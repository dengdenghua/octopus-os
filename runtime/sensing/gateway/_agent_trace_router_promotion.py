"""Promotion and policy-review endpoint handlers for the agent trace router.

These endpoints plan and apply review-queue promotions (with replay-gate
enforcement), browse promotion audit records, and manage policy-review rule
drafts plus governance audit rotation.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, Request

from runtime.platform.process.paths import app_paths
from runtime.safety.evolution.governance_audit import append_governance_audit_event
from runtime.safety.evolution.policy_review_rules import (
    build_policy_review_rule_drafts,
    install_policy_review_rule_draft,
    verify_policy_review_rule_draft,
)

from ._agent_trace_router_stores import (
    RouterDeps,
    _default_promotion_audit_path,
    _get_promotion_applier,
    _get_store,
    _promotion_plan_has_target,
    _scope_for_request,
    _source_task_ids_from_promotion_plan,
)


def _promotion_applier(deps: RouterDeps, scope=None):
    return _get_promotion_applier(
        experience_ledger=deps.experience_ledger,
        experience_ledger_path=deps.experience_ledger_path,
        review_queue=deps.review_queue,
        review_queue_path=deps.review_queue_path,
        promotion_audit_path=deps.promotion_audit_path,
        proposal_ledger_path=deps.proposal_ledger_path,
        journal=deps.journal,
        registry=deps.registry,
        auto_persist_dir=deps.auto_persist_dir,
        scope=scope,
    )


def register_promotion_endpoints(router, deps: RouterDeps) -> None:
    @router.post("/api/agent-trace/review-queue/promotions/plan")
    def api_agent_trace_review_queue_promotion_plan(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload or {}
        scope = _scope_for_request(request)
        plan = _promotion_applier(deps, scope).plan(
            item_id=body.get("item_id"),
            target=body.get("target"),
            limit=int(body.get("limit") or 50),
        )
        source_task_ids = _source_task_ids_from_promotion_plan(plan)
        trace = _get_store(store=deps.store, db_path=deps.db_path)
        plan["replay_gate"] = trace.replay_gate_for_task_ids(
            source_task_ids,
            min_cases=int(body.get("min_replay_cases") or 1),
            min_score=float(body.get("min_replay_score") or 1.0),
            scope=scope,
        )
        return plan

    @router.post("/api/agent-trace/review-queue/promotions/apply")
    def api_agent_trace_review_queue_promotion_apply(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = deps.auth(request, force=True)
        body = payload or {}
        scope = _scope_for_request(request)
        applier = _promotion_applier(deps, scope)
        limit = int(body.get("limit") or 50)
        plan = applier.plan(
            item_id=body.get("item_id"),
            target=body.get("target"),
            limit=limit,
        )
        if _promotion_plan_has_target(plan, "forged_skill"):
            missing = [
                name
                for name, value in (
                    ("journal", deps.journal),
                    ("registry", deps.registry),
                    ("auto_persist_dir", deps.auto_persist_dir),
                )
                if value is None
            ]
            if missing:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "message": "forged_skill promotion dependencies unavailable",
                        "missing": missing,
                        "promotion_plan": plan,
                    },
                )
        source_task_ids = _source_task_ids_from_promotion_plan(plan)
        min_cases = int(body.get("min_replay_cases") or 1)
        min_score = float(body.get("min_replay_score") or 1.0)
        if int(plan.get("applicable") or 0) <= 0:
            min_cases = 0
        replay_gate = _get_store(store=deps.store, db_path=deps.db_path).replay_gate_for_task_ids(
            source_task_ids,
            min_cases=min_cases,
            min_score=min_score,
            scope=scope,
        )
        if (
            _promotion_plan_has_target(plan, "policy_review")
            and replay_gate.get("passed") is not True
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "policy_review promotion requires replay evidence",
                    "replay_gate": replay_gate,
                    "promotion_plan": plan,
                },
            )
        override = body.get("override_replay_gate") is True
        if replay_gate.get("passed") is not True and not override:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "replay gate did not pass",
                    "replay_gate": replay_gate,
                    "promotion_plan": plan,
                },
            )
        override_reason = str(body.get("override_reason") or "").strip()
        if replay_gate.get("passed") is not True and override and not override_reason:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "override_reason is required when replay gate is blocked",
                    "replay_gate": replay_gate,
                    "promotion_plan": plan,
                },
            )
        # In single-user local dev there may be no identity store; the boundary
        # is explicit here so audit actor is never accepted from request JSON.
        override_actor = actor or "local_operator"
        result = applier.apply(
            item_id=body.get("item_id"),
            target=body.get("target"),
            limit=limit,
            decision_context={
                "schema": "echo.promotion_decision_context.v1",
                "replay_gate": replay_gate,
                "override_replay_gate": override,
                "override_reason": override_reason if override else "",
                "override_actor": override_actor if override else "",
                "source": "agent_trace_router",
            },
        )
        result["replay_gate"] = replay_gate
        result["override_replay_gate"] = override
        return result

    @router.get("/api/agent-trace/review-queue/promotions/audit")
    def api_agent_trace_review_queue_promotion_audit(
        request: Request,
        item_id: str | None = Query(default=None),
        target: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return _promotion_applier(deps, _scope_for_request(request)).audit(
            item_id=item_id,
            target=target,
            limit=limit,
            offset=offset,
        )

    @router.get("/api/agent-trace/review-queue/promotions/audit/summary")
    def api_agent_trace_review_queue_promotion_audit_summary(request: Request) -> dict[str, Any]:
        return _promotion_applier(deps, _scope_for_request(request)).audit_summary()

    @router.get("/api/agent-trace/policy-review/rule-drafts")
    def api_agent_trace_policy_review_rule_drafts(
        request: Request,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        report = build_policy_review_rule_drafts(
            ledger_path=_promotion_applier(deps, _scope_for_request(request)).proposal_ledger._path,
            limit=limit,
        )
        report["verified"] = sum(
            1
            for draft in report.get("drafts") or []
            if verify_policy_review_rule_draft(draft).get("ok") is True
        )
        return report

    @router.post("/api/agent-trace/policy-review/rule-drafts/install")
    def api_agent_trace_policy_review_rule_draft_install(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope = _scope_for_request(request)
        actor = deps.auth(request, force=True) or "local_operator"
        body = payload or {}
        draft_id = str(body.get("draft_id") or "").strip()
        if not draft_id:
            raise HTTPException(400, "draft_id is required")
        report = build_policy_review_rule_drafts(
            ledger_path=_promotion_applier(deps, scope).proposal_ledger._path,
            limit=int(body.get("limit") or 100),
        )
        draft = next(
            (
                item
                for item in report.get("drafts") or []
                if isinstance(item, dict) and str(item.get("draft_id") or "") == draft_id
            ),
            None,
        )
        if draft is None:
            raise HTTPException(404, "policy review rule draft not found")
        try:
            result = install_policy_review_rule_draft(
                draft,
                policy_path=deps.approval_policy_path or app_paths().permissions_path,
                confirm_install=body.get("confirm_install") is True,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        append_governance_audit_event(
            event_type="policy_review_rule_install",
            target="approval_policy",
            status="installed",
            artifact=result,
            decision_context={
                "schema": "echo.policy_review_rule_install_context.v1",
                "actor": actor,
                "draft_id": draft_id,
                "source": "agent_trace_router",
            },
            audit_path=deps.promotion_audit_path or _default_promotion_audit_path(),
        )
        return result

    @router.get("/api/agent-trace/review-queue/promotions/audit/export")
    def api_agent_trace_review_queue_promotion_audit_export(
        request: Request,
    ) -> dict[str, Any]:
        deps.auth(request, force=True)
        from runtime.safety.evolution.governance_audit import (
            export_governance_audit_bundle,
        )

        return export_governance_audit_bundle(
            audit_path=deps.promotion_audit_path or _default_promotion_audit_path(),
        )

    @router.get("/api/agent-trace/review-queue/promotions/audit/rotation")
    def api_agent_trace_governance_audit_rotation_status(
        request: Request,
    ) -> dict[str, Any]:
        deps.auth(request, force=True)
        from runtime.safety.evolution.governance_audit_rotation import (
            governance_audit_rotation_status,
        )

        return governance_audit_rotation_status(
            audit_path=deps.promotion_audit_path or _default_promotion_audit_path(),
        )

    @router.post("/api/agent-trace/review-queue/promotions/audit/rotation")
    def api_agent_trace_governance_audit_rotation_configure(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = deps.auth(request, force=True) or "local_operator"
        body = payload or {}
        if body.get("confirm_rotation") is not True:
            raise HTTPException(400, "confirm_rotation=true is required")
        from runtime.safety.evolution.governance_audit_rotation import (
            configure_governance_audit_rotation,
            governance_audit_rotation_status,
        )

        try:
            config = configure_governance_audit_rotation(
                enabled=body.get("enabled") is True,
                cron_expression=str(body.get("cron_expression") or "0 2 * * *"),
                retention_count=int(body.get("retention_count") or 30),
                actor=actor,
                audit_path=deps.promotion_audit_path or _default_promotion_audit_path(),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        status = governance_audit_rotation_status(
            audit_path=deps.promotion_audit_path or _default_promotion_audit_path(),
        )
        return {"ok": True, "config": config, "status": status}

    @router.post("/api/agent-trace/review-queue/promotions/audit/rotation/run")
    def api_agent_trace_governance_audit_rotation_run(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = deps.auth(request, force=True) or "local_operator"
        body = payload or {}
        if body.get("confirm_export") is not True:
            raise HTTPException(400, "confirm_export=true is required")
        from runtime.safety.evolution.governance_audit_rotation import (
            run_due_governance_audit_rotation,
        )

        return run_due_governance_audit_rotation(
            force=body.get("force") is True,
            actor=actor,
            audit_path=deps.promotion_audit_path or _default_promotion_audit_path(),
        )
