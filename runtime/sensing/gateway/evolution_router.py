from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from runtime.safety.evolution.agent_competitor_scorecard import (
    DEFAULT_TARGET_SCORE as DEFAULT_AGENT_SCORECARD_TARGET_SCORE,
)

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from runtime.safety.auth.principal import require_operator
from runtime.sensing.gateway._evolution_helpers import (
    _actor_from_request,
    _kimi_swarm_provider_caller,
    _kimi_swarm_provider_configured,
    _queue_agent_scorecard_gaps_impl,
    _resolve_api_base_url,
    _validate_kimi_swarm_quota_probe_request,
    _validate_kimi_swarm_real_provider_request,
)
from runtime.sensing.gateway._evolution_models import (
    AutomationPolicyRuleInstallBody,
    BrowserDesktopRepairRecipeEvidenceBody,
    BrowserDesktopRepairRecipeQueueBody,
    BrowserDesktopRepairRecipeRerunBatchBody,
    BrowserDesktopRepairRecipeRerunBody,
    BrowserDesktopStaleArtifactRejectionBody,
    CandidateCanaryOutcomeBody,
    CandidateRollbackBody,
    DualHelixShadowRunBody,
    DualHelixShadowSettingsBody,
    KimiSwarmLoadTestBody,
    KimiSwarmQuotaProbeBody,
    RepairRoutePromotionQueueBody,
    ScorecardGapQueueBody,
    SubagentPolicyDecisionBody,
    VerifierDriftQueueBody,
)

__all__ = [
    "AutomationPolicyRuleInstallBody",
    "BrowserDesktopRepairRecipeEvidenceBody",
    "BrowserDesktopRepairRecipeQueueBody",
    "BrowserDesktopRepairRecipeRerunBatchBody",
    "BrowserDesktopRepairRecipeRerunBody",
    "BrowserDesktopStaleArtifactRejectionBody",
    "CandidateCanaryOutcomeBody",
    "CandidateRollbackBody",
    "DualHelixShadowRunBody",
    "DualHelixShadowSettingsBody",
    "FASTAPI_AVAILABLE",
    "KimiSwarmLoadTestBody",
    "KimiSwarmQuotaProbeBody",
    "RepairRoutePromotionQueueBody",
    "ScorecardGapQueueBody",
    "SubagentPolicyDecisionBody",
    "VerifierDriftQueueBody",
    "create_evolution_router",
]


_LOG = logging.getLogger("echo.siphon.evolution_router")


def create_evolution_router(
    *,
    stack: Any = None,
    agent_registry: Any = None,
    project_root: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    if not FASTAPI_AVAILABLE:
        return APIRouter() if FASTAPI_AVAILABLE else None

    def _operator_dep(request: Request) -> None:
        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _candidate_scope(request: Request, *, cross_tenant: bool = False) -> Any:
        from runtime.safety.auth.scope import TenantScope, scope_from_principal

        principal = getattr(getattr(request, "state", None), "principal", None)
        if not cross_tenant:
            return scope_from_principal(principal)
        if principal is None:
            if require_auth:
                raise HTTPException(401, "auth required")
            return TenantScope(
                tenant_id="legacy:local-operator",
                actor_id="local-operator",
                allow_cross_tenant=True,
            )
        allowed_scopes = {
            "evolution:cross_tenant",
            "tenant:cross_tenant",
            "global:admin",
            "*",
        }
        if "admin" not in principal.roles or not principal.scopes.intersection(allowed_scopes):
            raise HTTPException(403, "explicit cross-tenant evolution admin permission required")
        return scope_from_principal(principal, allow_cross_tenant=True)

    # Evolution is a control plane: it can inspect and mutate proposals,
    # canaries, policy rules, forge state, and runtime behavior. In shared
    # deployments every endpoint therefore requires an authenticated operator
    # or admin. ``require_auth=False`` preserves the explicit local single-user
    # development mode used by the standalone unit-router tests.
    router = APIRouter(
        prefix="/api/evolution",
        tags=["evolution"],
        dependencies=[Depends(_operator_dep)],
    )
    from runtime.platform.process.paths import app_paths
    from runtime.safety.evolution.candidate_canary import CandidateCanaryManager
    from runtime.safety.evolution.candidate_registry import CandidateRegistry
    from runtime.safety.evolution.experiment_protocol import ExperimentStore
    from runtime.safety.evolution.runtime_deployment import CandidateRuntimeSelector

    paths = app_paths()
    experiment_store = ExperimentStore(paths.evolution_experiments_path)
    # Legacy/global objects remain available to the older shadow service.
    # Candidate HTTP operations below resolve their registry and canary state
    # per request instead of closing over this global store.
    candidate_registry = CandidateRegistry(paths.evolution_candidates_path)

    def _registry_paths(scope: Any) -> list[Path]:
        from runtime.safety.auth.scope import tenant_scoped_path

        base = paths.evolution_candidates_path
        if scope is None:
            return [base]
        if not scope.allow_cross_tenant:
            return [tenant_scoped_path(base, scope)]
        tenant_rows = sorted((base.parent / "tenants").glob(f"*/{base.name}"))
        return [base, *tenant_rows]

    def _services_for_scope(scope: Any) -> list[tuple[Any, Any]]:
        from runtime.safety.auth.scope import tenant_scoped_path

        services: list[tuple[Any, Any]] = []
        for registry_path in _registry_paths(scope):
            is_tenant_partition = registry_path != paths.evolution_candidates_path
            expected_scope = scope if scope is not None and not scope.allow_cross_tenant else None
            registry = CandidateRegistry(registry_path, tenant_scope=expected_scope)
            if is_tenant_partition:
                state_dir = registry_path.parent / paths.candidate_canary_state_dir.name
            elif scope is not None and not scope.allow_cross_tenant:
                state_dir = tenant_scoped_path(paths.candidate_canary_state_dir, scope)
            else:
                state_dir = paths.candidate_canary_state_dir
            manager = CandidateCanaryManager(
                registry,
                state_dir,
                runtime_registry=getattr(stack, "registry", None),
                # Tenant skill registries are not yet process-partitioned.
                # Keep their canary as an auditable control-plane rollout and
                # never inject tenant code into the process-global registry.
                materialize_runtime=not is_tenant_partition,
            )
            services.append((registry, manager))
        return services

    def _service_for_candidate(scope: Any, candidate_id: str) -> tuple[Any, Any]:
        matches: list[tuple[Any, Any]] = []
        for registry, manager in _services_for_scope(scope):
            if registry.get(candidate_id) is not None:
                matches.append((registry, manager))
        if not matches:
            raise KeyError(f"unknown evolution candidate: {candidate_id}")
        if len(matches) > 1:
            # Old records did not include ownership in their candidate ID.
            # Never guess which tenant owns a duplicated legacy identifier.
            raise ValueError(f"candidate id conflicts across tenant partitions: {candidate_id}")
        return matches[0]

    shadow_service = None
    if stack is not None and project_root is not None:
        try:
            from runtime.platform.process.paths import app_paths
            from runtime.safety.evolution.dual_helix_shadow import (
                DualHelixShadowService,
                build_codex_shadow_runner,
                build_native_shadow_runner,
            )

            shadow_service = DualHelixShadowService(
                app_paths().data_dir / "dual_helix_shadow.json",
                app_paths().data_dir / "dual_helix_shadows",
                allowed_workspace_root=project_root,
                codex_runner=build_codex_shadow_runner(stack, agent_registry),
                native_runner=build_native_shadow_runner(stack),
                candidate_registry=candidate_registry,
            )
        except Exception:  # noqa: BLE001 - status endpoint reports unavailable
            _LOG.exception("dual-helix shadow service failed to initialize")

    @router.get("/codex-gap")
    def get_codex_gap() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.codex_gap import compute_codex_gap_report

            return {"ok": True, **compute_codex_gap_report()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/dual-helix/evidence")
    def get_dual_helix_evidence(
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.dual_helix import build_dual_helix_evidence
            from runtime.safety.evolution.experiment_protocol import build_pair_evidence
            from runtime.safety.evolution.proposal_ledger import ProposalLedger

            controlled = build_pair_evidence(
                experiment_store.list_trials(limit=10_000),
                limit=limit,
            )
            records = ProposalLedger(paths.proposal_ledger_path).query(limit=10_000)
            observational = build_dual_helix_evidence(records, limit=limit)
            # Preserve the legacy projection consumed by the current UI while
            # making its evidence level explicit.  Controlled experiments are
            # exposed alongside it and become authoritative as soon as trials
            # exist; arbitrary completed turns are never relabelled as trials.
            return {
                **observational,
                "evidence_quality": (
                    "controlled_same_task" if controlled["trial_count"] else "observational"
                ),
                "controlled": controlled,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/experiments/evidence")
    def get_controlled_experiment_evidence(
        limit: int = Query(default=100, ge=1, le=1_000),
        primary_metric: str = Query(default="quality", min_length=1, max_length=80),
    ) -> dict[str, Any]:
        from runtime.safety.evolution.experiment_protocol import build_pair_evidence

        return build_pair_evidence(
            experiment_store.list_trials(limit=100_000),
            primary_metric=primary_metric,
            limit=limit,
        )

    @router.get("/candidates")
    def get_evolution_candidates(
        request: Request,
        limit: int = Query(default=100, ge=1, le=1_000),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _candidate_scope(request, cross_tenant=cross_tenant)
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for registry, manager in _services_for_scope(scope):
            for candidate in reversed(registry.list(limit=limit)):
                if candidate.candidate_id in seen_ids:
                    raise HTTPException(
                        409,
                        f"candidate id conflicts across tenant partitions: "
                        f"{candidate.candidate_id}",
                    )
                seen_ids.add(candidate.candidate_id)
                row = candidate.to_wire()
                try:
                    CandidateRuntimeSelector.validate_materializable(candidate)
                except ValueError:
                    row["runtime_consumer_ready"] = False
                else:
                    row["runtime_consumer_ready"] = True
                row["runtime_materialized"] = bool(manager.materialize_runtime)
                try:
                    row["canary"] = manager.status(candidate.candidate_id).get("canary")
                except (KeyError, OSError, TypeError, ValueError):
                    row["canary"] = None
                rows.append(row)
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        rows = rows[:limit]
        by_status: dict[str, int] = {}
        by_gene_type: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            gene_type = str(row.get("gene_type") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            by_gene_type[gene_type] = by_gene_type.get(gene_type, 0) + 1
        return {
            "ok": True,
            "schema": "echo.evolution.candidate_list.v1",
            "total": len(rows),
            "by_status": by_status,
            "by_gene_type": by_gene_type,
            "candidates": rows,
        }

    @router.get("/candidates/{candidate_id}/canary")
    def get_candidate_canary(
        request: Request,
        candidate_id: str,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            scope = _candidate_scope(request, cross_tenant=cross_tenant)
            _registry, manager = _service_for_candidate(scope, candidate_id)
            return manager.status(candidate_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/candidates/{candidate_id}/canary/register")
    def register_candidate_canary(
        request: Request,
        candidate_id: str,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            scope = _candidate_scope(request, cross_tenant=cross_tenant)
            _registry, manager = _service_for_candidate(scope, candidate_id)
            return manager.register(candidate_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/candidates/{candidate_id}/canary/outcome")
    def record_candidate_canary_outcome(
        request: Request,
        candidate_id: str,
        body: CandidateCanaryOutcomeBody,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            scope = _candidate_scope(request, cross_tenant=cross_tenant)
            _registry, manager = _service_for_candidate(scope, candidate_id)
            return manager.record_outcome(candidate_id, body.success)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/candidates/{candidate_id}/rollback")
    def rollback_candidate(
        request: Request,
        candidate_id: str,
        body: CandidateRollbackBody,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            scope = _candidate_scope(request, cross_tenant=cross_tenant)
            _registry, manager = _service_for_candidate(scope, candidate_id)
            return manager.force_rollback(candidate_id, reason=body.reason)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/dual-helix/shadow/status")
    def get_dual_helix_shadow_status() -> dict[str, Any]:
        if shadow_service is None:
            return {
                "ok": False,
                "enabled": False,
                "error": "dual-helix shadow service is unavailable",
                "runs": [],
            }
        return shadow_service.status()

    @router.post("/dual-helix/shadow/settings")
    def set_dual_helix_shadow_settings(
        body: DualHelixShadowSettingsBody,
    ) -> dict[str, Any]:
        if shadow_service is None:
            raise HTTPException(503, "dual-helix shadow service is unavailable")
        return shadow_service.set_enabled(body.enabled)

    @router.post("/dual-helix/shadow/run")
    async def run_dual_helix_shadow(
        body: DualHelixShadowRunBody,
    ) -> dict[str, Any]:
        if shadow_service is None:
            raise HTTPException(503, "dual-helix shadow service is unavailable")
        try:
            return {
                "ok": True,
                **shadow_service.queue(
                    goal=body.goal,
                    primary_engine=body.primary_engine,
                    primary_output=body.primary_output,
                    workspace_path=body.workspace_path or None,
                    source_thread_id=body.source_thread_id or None,
                    source_message_id=body.source_message_id or None,
                    candidate_id=body.candidate_id or None,
                    experiment_id=body.experiment_id or None,
                ),
            }
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from None
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/agent-scorecard")
    def get_agent_scorecard(
        target_score: int = Query(
            default=DEFAULT_AGENT_SCORECARD_TARGET_SCORE,
            ge=1,
            le=100,
        ),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.agent_competitor_scorecard import (
                compute_agent_competitor_scorecard,
            )

            return {
                "ok": True,
                **compute_agent_competitor_scorecard(target_score=target_score),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/agent-benchmark")
    def get_agent_benchmark() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.agent_benchmark import (
                compute_agent_benchmark,
            )

            return {"ok": True, **compute_agent_benchmark()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/kimi-swarm-certification")
    def get_kimi_swarm_certification() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.kimi_swarm_certification import (
                compute_kimi_swarm_certification,
            )

            return {
                "ok": True,
                **compute_kimi_swarm_certification(
                    provider_configured=_kimi_swarm_provider_configured("kimi-k3"),
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/kimi-swarm-certification/load-test")
    def run_kimi_swarm_certification_load_test(
        body: KimiSwarmLoadTestBody | None = None,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.kimi_swarm_load_test import (
                KimiSwarmLoadTestConfig,
                run_kimi_swarm_load_test,
            )

            body = body or KimiSwarmLoadTestBody()
            provider_caller = None
            if body.real_provider:
                _validate_kimi_swarm_real_provider_request(body)
                provider_caller = _kimi_swarm_provider_caller(body.model)
            result = run_kimi_swarm_load_test(
                config=KimiSwarmLoadTestConfig(
                    session_id=body.session_id,
                    provider_id=body.provider_id,
                    model=body.model,
                    agent_count=body.agent_count,
                    step_count=body.step_count,
                    max_concurrency=body.max_concurrency,
                    real_provider=body.real_provider,
                    confirm_real_provider=body.confirm_real_provider,
                    record_every_step=body.record_every_step,
                    max_provider_calls=body.max_provider_calls,
                    estimated_max_tokens=body.estimated_max_tokens,
                    stage_id=body.stage_id,
                    resume_from_session_id=body.resume_from_session_id,
                    resume_step_ranges=tuple(body.resume_step_ranges),
                ),
                provider_caller=provider_caller,
            )
            return {"ok": True, **result}
        except HTTPException:
            raise
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/kimi-swarm-certification/load-test/preflight")
    def preflight_kimi_swarm_certification_load_test(
        body: KimiSwarmLoadTestBody | None = None,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.kimi_swarm_load_test import (
                KimiSwarmLoadTestConfig,
                build_kimi_swarm_load_test_preflight,
            )

            body = body or KimiSwarmLoadTestBody()
            provider_configured = None
            if body.real_provider:
                provider_configured = _kimi_swarm_provider_configured(body.model)
            return {
                "ok": True,
                **build_kimi_swarm_load_test_preflight(
                    config=KimiSwarmLoadTestConfig(
                        session_id=body.session_id,
                        provider_id=body.provider_id,
                        model=body.model,
                        agent_count=body.agent_count,
                        step_count=body.step_count,
                        max_concurrency=body.max_concurrency,
                        real_provider=body.real_provider,
                        confirm_real_provider=body.confirm_real_provider,
                        record_every_step=body.record_every_step,
                        max_provider_calls=body.max_provider_calls,
                        estimated_max_tokens=body.estimated_max_tokens,
                        stage_id=body.stage_id,
                        resume_from_session_id=body.resume_from_session_id,
                        resume_step_ranges=tuple(body.resume_step_ranges),
                    ),
                    provider_configured=provider_configured,
                    data_dir=None,
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/kimi-swarm-certification/quota-probe")
    def run_kimi_swarm_certification_quota_probe(
        body: KimiSwarmQuotaProbeBody | None = None,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.kimi_swarm_load_test import (
                KimiSwarmQuotaProbeConfig,
                run_kimi_swarm_quota_probe,
            )

            body = body or KimiSwarmQuotaProbeBody()
            _validate_kimi_swarm_quota_probe_request(body)
            result = run_kimi_swarm_quota_probe(
                config=KimiSwarmQuotaProbeConfig(
                    session_id=body.session_id,
                    provider_id=body.provider_id,
                    model=body.model,
                    confirm_real_provider=body.confirm_real_provider,
                    max_tokens=body.max_tokens,
                ),
                provider_caller=_kimi_swarm_provider_caller(body.model),
            )
            return {"ok": True, **result}
        except HTTPException:
            raise
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/kimi-swarm-certification/proof-bundle")
    def get_kimi_swarm_certification_proof_bundle() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.kimi_swarm_load_test import (
                export_kimi_swarm_proof_bundle,
            )

            return {"ok": True, **export_kimi_swarm_proof_bundle()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/kimi-swarm-certification/next-stage")
    def get_kimi_swarm_certification_next_stage(
        provider_id: str = Query(default="volcengine_ark"),
        model: str = Query(default="kimi-k3"),
        agent_count: int = Query(default=300, ge=1, le=512),
        step_count: int = Query(default=4000, ge=1, le=20000),
        max_concurrency: int = Query(default=32, ge=1, le=256),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.kimi_swarm_load_test import (
                recommend_kimi_swarm_next_stage,
            )

            return {
                "ok": True,
                **recommend_kimi_swarm_next_stage(
                    provider_id=provider_id,
                    model=model,
                    agent_count=agent_count,
                    step_count=step_count,
                    max_concurrency=max_concurrency,
                    provider_configured=_kimi_swarm_provider_configured(model),
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/kimi-swarm-certification/resume-plan")
    def get_kimi_swarm_certification_resume_plan(
        provider_id: str = Query(default="volcengine_ark"),
        model: str = Query(default="kimi-k3"),
        agent_count: int = Query(default=300, ge=1, le=512),
        step_count: int = Query(default=4000, ge=1, le=20000),
        max_concurrency: int = Query(default=32, ge=1, le=256),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.kimi_swarm_load_test import (
                build_kimi_swarm_resume_plan,
            )

            return {
                "ok": True,
                **build_kimi_swarm_resume_plan(
                    provider_id=provider_id,
                    model=model,
                    agent_count=agent_count,
                    step_count=step_count,
                    max_concurrency=max_concurrency,
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/e2e-surpass-certification")
    def get_e2e_surpass_certification(
        target_score: int = Query(default=95, ge=1, le=100),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.e2e_surpass_certification import (
                compute_e2e_surpass_certification,
            )

            return {
                "ok": True,
                **compute_e2e_surpass_certification(target_score=target_score),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/agent-scorecard/gaps/queue")
    def queue_agent_scorecard_gaps(
        body: ScorecardGapQueueBody | None = None,
    ) -> dict[str, Any]:
        return _queue_agent_scorecard_gaps_impl(body)

    @router.get("/browser-desktop-quality")
    def get_browser_desktop_quality() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.browser_desktop_quality import (
                compute_browser_desktop_quality,
            )

            return {"ok": True, **compute_browser_desktop_quality()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/repo-context-quality")
    def get_repo_context_quality() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.repo_context_quality import (
                compute_repo_context_quality,
            )

            return {"ok": True, **compute_repo_context_quality()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/permission-sandbox-quality")
    def get_permission_sandbox_quality() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.permission_sandbox_quality import (
                compute_permission_sandbox_quality,
            )

            return {"ok": True, **compute_permission_sandbox_quality()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/product-experience-quality")
    def get_product_experience_quality() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.product_experience_quality import (
                compute_product_experience_quality,
            )

            return {"ok": True, **compute_product_experience_quality()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/agent-loop-quality")
    def get_agent_loop_quality() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.agent_loop_quality import (
                compute_agent_loop_quality,
            )

            return {"ok": True, **compute_agent_loop_quality()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/digital-employee-quality")
    def get_digital_employee_quality() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.digital_employee_quality import (
                compute_digital_employee_quality,
            )

            return {"ok": True, **compute_digital_employee_quality()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/automation-radar")
    def get_automation_radar(
        target_score: int = Query(default=95, ge=1, le=100),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.automation_radar import (
                compute_automation_radar,
            )

            return {
                "ok": True,
                **compute_automation_radar(target_score=target_score),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/automation-policy-rule-drafts")
    def get_automation_policy_rule_drafts(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.policy_review_rules import (
                build_automation_policy_rule_drafts,
                verify_policy_review_rule_draft,
            )

            report = build_automation_policy_rule_drafts(limit=limit)
            report["verified"] = sum(
                1
                for draft in report.get("drafts") or []
                if verify_policy_review_rule_draft(draft).get("ok") is True
            )
            return {"ok": True, **report}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/automation-policy-rule-drafts/install")
    def install_automation_policy_rule_draft(
        request: Request,
        body: AutomationPolicyRuleInstallBody,
    ) -> dict[str, Any]:
        try:
            from runtime.platform.process.paths import app_paths
            from runtime.safety.evolution.governance_audit import (
                append_governance_audit_event,
            )
            from runtime.safety.evolution.policy_review_rules import (
                build_automation_policy_rule_drafts,
                install_policy_review_rule_draft,
            )

            report = build_automation_policy_rule_drafts(limit=body.limit)
            draft = next(
                (
                    item
                    for item in report.get("drafts") or []
                    if isinstance(item, dict) and str(item.get("draft_id") or "") == body.draft_id
                ),
                None,
            )
            if draft is None:
                raise HTTPException(404, "automation policy rule draft not found")
            result = install_policy_review_rule_draft(
                draft,
                policy_path=app_paths().permissions_path,
                confirm_install=body.confirm_install,
            )
            append_governance_audit_event(
                event_type="automation_policy_rule_install",
                target="approval_policy",
                status="installed",
                artifact=result,
                decision_context={
                    "schema": "echo.automation_policy_rule_install_context.v1",
                    "actor": _actor_from_request(request),
                    "draft_id": body.draft_id,
                    "source": "evolution_router",
                },
                audit_path=app_paths().promotion_audit_path,
            )
            return {"ok": True, **result}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/browser-desktop-repair-recipes")
    def get_browser_desktop_repair_recipes(
        limit: int = Query(default=1000, ge=1, le=5000),
        min_occurrences: int = Query(default=1, ge=1, le=20),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.browser_desktop_repair_recipes import (
                compute_browser_desktop_repair_recipes,
            )

            return {
                "ok": True,
                **compute_browser_desktop_repair_recipes(
                    limit=limit,
                    min_occurrences=min_occurrences,
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/browser-desktop-repair-recipes/queue")
    def queue_browser_desktop_repair_recipes(
        body: BrowserDesktopRepairRecipeQueueBody | None = None,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.browser_desktop_repair_recipes import (
                queue_browser_desktop_repair_recipes,
            )

            body = body or BrowserDesktopRepairRecipeQueueBody()
            return {
                "ok": True,
                **queue_browser_desktop_repair_recipes(
                    limit=body.limit,
                    min_occurrences=body.min_occurrences,
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/browser-desktop-repair-recipes/stale-artifacts/reject")
    def reject_stale_browser_desktop_replay_artifacts(
        body: BrowserDesktopStaleArtifactRejectionBody | None = None,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.browser_desktop_repair_recipes import (
                reject_stale_browser_desktop_replay_artifacts,
            )

            body = body or BrowserDesktopStaleArtifactRejectionBody()
            return {
                "ok": True,
                **reject_stale_browser_desktop_replay_artifacts(limit=body.limit),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/browser-desktop-repair-recipes/verifications")
    def get_browser_desktop_repair_recipe_verifications(
        limit: int = Query(default=1000, ge=1, le=5000),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.browser_desktop_repair_recipes import (
                compute_browser_desktop_repair_recipe_verifications,
            )

            return {
                "ok": True,
                **compute_browser_desktop_repair_recipe_verifications(limit=limit),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/browser-desktop-repair-recipes/verifications/evidence")
    def attach_browser_desktop_repair_recipe_evidence(
        body: BrowserDesktopRepairRecipeEvidenceBody,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.browser_desktop_repair_recipes import (
                attach_browser_desktop_repair_recipe_evidence,
            )

            return {
                "ok": True,
                **attach_browser_desktop_repair_recipe_evidence(
                    item_id=body.item_id,
                    passed=body.passed,
                    provided=body.provided,
                    artifacts=body.artifacts,
                    notes=body.notes,
                    actor=body.actor,
                ),
            }
        except KeyError as exc:
            return {"ok": False, "error": f"recipe item not found: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/browser-desktop-repair-recipes/verifications/rerun")
    def rerun_browser_desktop_repair_recipe_evidence(
        body: BrowserDesktopRepairRecipeRerunBody,
        request: Request,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.browser_desktop_repair_recipes import (
                rerun_browser_desktop_repair_recipe_evidence,
            )

            api_base_url = _resolve_api_base_url(
                body.api_base_url,
                request=request,
            )
            return {
                "ok": True,
                **rerun_browser_desktop_repair_recipe_evidence(
                    item_id=body.item_id,
                    api_base_url=api_base_url,
                    promote_source_cases=body.promote_source_cases,
                    actor=body.actor,
                ),
            }
        except KeyError as exc:
            return {"ok": False, "error": f"recipe item not found: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/browser-desktop-repair-recipes/verifications/rerun-batch")
    def rerun_browser_desktop_repair_recipe_batch(
        request: Request,
        body: BrowserDesktopRepairRecipeRerunBatchBody | None = None,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.browser_desktop_repair_recipes import (
                rerun_browser_desktop_repair_recipe_batch,
            )

            body = body or BrowserDesktopRepairRecipeRerunBatchBody()
            api_base_url = _resolve_api_base_url(
                body.api_base_url,
                request=request,
            )
            return {
                "ok": True,
                **rerun_browser_desktop_repair_recipe_batch(
                    api_base_url=api_base_url,
                    promote_source_cases=body.promote_source_cases,
                    actor=body.actor,
                    limit=body.limit,
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/repair-route-quality")
    def get_repair_route_quality(
        limit: int = Query(default=1000, ge=1, le=5000),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.repair_route_quality import (
                compute_repair_route_quality,
            )

            return {"ok": True, **compute_repair_route_quality(limit=limit)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/repair-route-quality/promotions/queue")
    def queue_repair_route_promotions(
        body: RepairRoutePromotionQueueBody | None = None,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.repair_route_quality import (
                queue_repair_route_promotion_candidates,
            )

            body = body or RepairRoutePromotionQueueBody()
            return {
                "ok": True,
                **queue_repair_route_promotion_candidates(limit=body.limit),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/auto-verifier-metrics")
    def get_auto_verifier_metrics(
        limit: int = Query(default=1000, ge=1, le=5000),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.auto_verifier_metrics import (
                summarize_auto_verifier_metrics,
            )

            return {"ok": True, **summarize_auto_verifier_metrics(limit=limit)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/auto-verifier-metrics/drift/queue")
    def queue_auto_verifier_drift(
        body: VerifierDriftQueueBody | None = None,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.auto_verifier_metrics import (
                queue_verifier_drift_backlog,
            )

            body = body or VerifierDriftQueueBody()
            return {
                "ok": True,
                **queue_verifier_drift_backlog(limit=body.limit),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/subagent-fitness")
    def get_subagent_fitness(
        role: str | None = Query(default=None),
        limit: int = Query(default=2000, ge=1, le=5000),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.subagent_fitness import (
                compute_subagent_fitness,
            )

            return {"ok": True, **compute_subagent_fitness(role=role, limit=limit)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/subagent-policy")
    def get_subagent_policy() -> dict[str, Any]:
        try:
            from runtime.safety.evolution.subagent_policy import SubagentPolicyStore

            return {"ok": True, **SubagentPolicyStore().summary()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/subagent-policy/{role}/decision")
    def decide_subagent_policy(
        role: str,
        body: SubagentPolicyDecisionBody,
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.subagent_policy import SubagentPolicyStore

            result = SubagentPolicyStore().decide(
                role,
                action=body.action,
                reason=body.reason,
                evidence_item_ids=body.evidence_item_ids,
                actor=body.actor,
            )
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/fitness/{agent_id}")
    def get_fitness(
        request: Request,
        agent_id: str,
        window: int = Query(default=20, ge=5, le=100),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        # Authorization errors must escape as their real 401/403 response;
        # do not turn them into a successful ``{"ok": false}`` payload.
        scope = _candidate_scope(request, cross_tenant=cross_tenant)
        try:
            from runtime.safety.evolution.fitness import FitnessConfig, compute_fitness

            # A GET must remain observational: publishing this freshly
            # computed score would let a dashboard refresh trigger automatic
            # evolution through the process event bus.
            report = compute_fitness(
                agent_id,
                FitnessConfig(window=window),
                publish_event=False,
                scope=scope,
            )
            return {
                "ok": True,
                "agent_id": report.agent_id,
                "ts": report.ts,
                "l1": {
                    "score": report.l1.score,
                    "trend": report.l1.trend,
                    "success_rate": report.l1.success_rate,
                    "avg_rounds": report.l1.avg_rounds,
                },
                "l2": {
                    "score": report.l2.score,
                    "dominant_failure": report.l2.dominant_failure,
                    "action": report.l2.action,
                    "confidence": report.l2.confidence,
                }
                if report.l2
                else None,
                "governance": {
                    "score": report.governance.score,
                    "penalty": report.governance.penalty,
                    "audit_total": report.governance.audit_total,
                    "recent_total": report.governance.recent_total,
                    "override_count": report.governance.override_count,
                    "gate_failed_count": report.governance.gate_failed_count,
                    "gate_blocked_override_count": (report.governance.gate_blocked_override_count),
                    "failed_apply_count": report.governance.failed_apply_count,
                    "reasons": report.governance.reasons,
                }
                if report.governance
                else None,
                "combined": report.combined,
                "verdict": report.verdict,
                "scope_mode": report.scope_mode,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/drift/{agent_id}")
    def get_drift(
        request: Request,
        agent_id: str,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _candidate_scope(request, cross_tenant=cross_tenant)
        try:
            from runtime.safety.evolution.drift_monitor import DriftMonitor

            report = DriftMonitor(agent_id, scope=scope).check(publish_events=False)
            return {
                "ok": True,
                "agent_id": report.agent_id,
                "ts": report.ts,
                "has_drift": report.has_drift,
                "max_severity": report.max_severity,
                "scope_mode": report.scope_mode,
                "events": [
                    {"kind": e.kind, "severity": e.severity, "detail": e.detail}
                    for e in report.events
                ],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/ledger")
    def get_ledger(
        status: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus

            ledger = ProposalLedger()
            st = ProposalStatus(status) if status else None
            records = ledger.query(status=st, kind=kind, limit=limit)
            return {
                "ok": True,
                "total": len(records),
                "records": [
                    {
                        "id": r.proposal_id,
                        "kind": r.kind,
                        "description": r.description,
                        "status": r.status.value,
                        "proposer": r.proposer,
                        "ts": r.ts,
                        "fitness_before": r.fitness_before,
                        "fitness_after": r.fitness_after,
                        "model": r.model,
                        "engine": r.metadata.get("engine"),
                        "goal_fingerprint": r.metadata.get("goal_fingerprint"),
                    }
                    for r in records
                ],
                "stats": ledger.stats(),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/ledger/{proposal_id}")
    def get_ledger_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.canary import CanaryManager
            from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus

            ledger = ProposalLedger()
            record = next(
                (r for r in ledger.query(limit=10_000) if r.proposal_id == proposal_id),
                None,
            )
            if record is None:
                return {"ok": False, "error": f"proposal not found: {proposal_id}"}

            canaries = []
            for state in CanaryManager().list_all():
                metadata = state.metadata if isinstance(state.metadata, dict) else {}
                if metadata.get("proposal_id") == proposal_id:
                    canaries.append(
                        {
                            "skill_name": state.skill_name,
                            "phase": state.phase.value,
                            "sample_count": state.sample_count,
                            "success_count": state.success_count,
                            "failure_count": state.failure_count,
                            "current_rate": round(state.current_rate, 3),
                            "entered_ts": state.entered_ts,
                            "metadata": metadata,
                        }
                    )

            rollbacks = []
            for rb in ledger.query(
                status=ProposalStatus.ROLLED_BACK, kind="canary_rollback", limit=10_000
            ):
                metadata = rb.metadata if isinstance(rb.metadata, dict) else {}
                if metadata.get("source_proposal_id") == proposal_id:
                    rollbacks.append(
                        {
                            "id": rb.proposal_id,
                            "description": rb.description,
                            "ts": rb.ts,
                            "rolled_back_ts": rb.rolled_back_ts,
                            "metadata": metadata,
                        }
                    )

            return {
                "ok": True,
                "proposal": {
                    "id": record.proposal_id,
                    "kind": record.kind,
                    "description": record.description,
                    "status": record.status.value,
                    "proposer": record.proposer,
                    "ts": record.ts,
                    "fitness_before": record.fitness_before,
                    "fitness_after": record.fitness_after,
                    "model": record.model,
                    "cost_tokens": record.cost_tokens,
                    "cost_usd": record.cost_usd,
                    "metadata": record.metadata,
                    "applied_ts": record.applied_ts,
                    "rolled_back_ts": record.rolled_back_ts,
                    "rejection_reason": record.rejection_reason,
                },
                "canaries": canaries,
                "rollbacks": rollbacks,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/canary")
    def get_canary(
        include_all: bool = Query(default=True),
        phase: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.canary import CanaryManager, CanaryPhase

            cm = CanaryManager()
            canaries = cm.list_all() if include_all else cm.list_active()
            if phase:
                try:
                    phase_enum = CanaryPhase(phase)
                    canaries = [s for s in canaries if s.phase == phase_enum]
                except Exception:
                    return {"ok": False, "error": f"invalid phase: {phase}"}
            active_count = sum(
                1 for s in canaries if s.phase not in (CanaryPhase.FULL, CanaryPhase.ROLLED_BACK)
            )
            rolled_back_count = sum(1 for s in canaries if s.phase == CanaryPhase.ROLLED_BACK)
            full_count = sum(1 for s in canaries if s.phase == CanaryPhase.FULL)
            canaries = sorted(
                canaries,
                key=lambda s: s.entered_ts,
                reverse=True,
            )[:limit]
            return {
                "ok": True,
                "total": len(canaries),
                "active_count": active_count,
                "rolled_back_count": rolled_back_count,
                "full_count": full_count,
                "canaries": [
                    {
                        "skill_name": s.skill_name,
                        "phase": s.phase.value,
                        "sample_count": s.sample_count,
                        "success_count": s.success_count,
                        "failure_count": s.failure_count,
                        "current_rate": round(s.current_rate, 3),
                        "entered_ts": s.entered_ts,
                        "metadata": s.metadata,
                        "proposal_id": s.metadata.get("proposal_id"),
                        "proposal_kind": s.metadata.get("proposal_kind"),
                        "candidate_id": s.metadata.get("candidate_id"),
                        "recipe_id": s.metadata.get("recipe_id"),
                        "avg_score": s.metadata.get("avg_score"),
                        "last_rollback_reason": s.metadata.get("last_rollback_reason"),
                    }
                    for s in canaries
                ],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/canary/{skill_name}/rollback")
    def rollback_canary(skill_name: str) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.canary import CanaryManager

            cm = CanaryManager()
            state = cm.force_rollback(skill_name, reason="manual API rollback")
            if state is None:
                return {"ok": False, "error": "skill not in canary"}
            return {"ok": True, "skill_name": skill_name, "phase": state.phase.value}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    return router
