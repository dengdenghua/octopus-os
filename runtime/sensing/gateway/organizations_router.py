"""REST endpoints for team-topology management.

Exposes the organization-level evolution artefacts to the UI / API
consumers:

  * ``GET  /api/organizations/topologies`` — list active topologies
  * ``GET  /api/organizations/topologies/{fingerprint}`` — one entry
  * ``GET  /api/organizations/topology-proposals`` — latest proposals
  * ``POST /api/organizations/topology-proposals/{idx}/promote`` —
    apply a proposal through the forge (gene_locks gated)
  * ``GET  /api/organizations/topology-performance`` — recent run log
  * ``POST /api/organizations/topologies/{fingerprint}/retire`` —
    remove a topology from the active registry
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = object  # type: ignore[assignment, misc]

from runtime.platform.process.paths import app_paths
from runtime.safety.evolution.subagent_policy import evaluate_agent_policy
from runtime.safety.evolution.subagent_team_promotion import (
    merged_topology_proposals,
)
from runtime.safety.organization import TeamTopology
from runtime.safety.organization.evolver import Proposal
from runtime.safety.organization.forge import (
    TopologyForge,
    load_registry,
    save_registry,
)
from runtime.safety.organization.performance_log import read_runs
from runtime.safety.organization.promotion_lift import (
    compute_topology_promotion_lift,
)

_logger = logging.getLogger("echo.sensing.organizations_router")


def _proposals_path() -> Path:
    try:
        return app_paths().data_dir / "topology_proposals.json"
    except (AttributeError, OSError, TypeError):
        return Path("data") / "topology_proposals.json"


def _load_proposals() -> list[dict[str, Any]]:
    p = _proposals_path()
    if not p.is_file():
        return []
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = body.get("proposals") if isinstance(body, dict) else None
    return raw if isinstance(raw, list) else []


def _topology_payload(topology: TeamTopology) -> dict[str, Any]:
    payload = topology.to_dict()
    payload["subagent_policy"] = evaluate_agent_policy(
        {str(role): spec.agent_id for role, spec in topology.agents.items()}
    )
    return payload


def create_organizations_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    agent_registry: Any = None,
) -> Any:
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("fastapi required for organizations router")

    router = APIRouter(tags=["organizations"])

    def _auth(request: Any, *, force: bool = False) -> str | None:
        """Resolve actor; mutation endpoints force-auth when identity is configured.

        ``force`` only enforces authentication once an identity store is wired
        (shared deployment). In local/single-user mode (no identity store,
        ``require_auth=False``) mutations degrade to an anonymous actor so the
        control plane stays usable; the caller treats ``None`` as autonomous.
        """
        effective_auth = require_auth or (force and identity_store is not None)
        try:
            from runtime.sensing.gateway.openai_gateway import _resolve_actor

            return _resolve_actor(
                request,
                identity_store,
                effective_auth,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            if effective_auth:
                raise HTTPException(401, "auth required") from exc
            return None

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    # ── GET /api/organizations/topologies ─────────────────

    @router.get("/api/organizations/topologies")
    def list_topologies(request: Request) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — topology registry is global
        registry = load_registry()
        return {
            "count": len(registry),
            "topologies": [_topology_payload(t) for t in registry.values()],
        }

    @router.get("/api/organizations/topologies/{fingerprint}")
    def get_topology(fingerprint: str, request: Request) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — topology registry is global
        registry = load_registry()
        t = registry.get(fingerprint)
        if t is None:
            raise HTTPException(404, "topology not found")
        return _topology_payload(t)

    # ── GET /api/organizations/topology-proposals ─────────

    @router.get("/api/organizations/topology-proposals")
    def list_proposals(request: Request) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — proposals are global
        proposals = _load_proposals()
        return merged_topology_proposals(
            proposals,
            registry=load_registry(),
            review_queue_path=app_paths().review_queue_path,
            subagent_policy_path=app_paths().subagent_policy_path,
        )

    # ── POST .../proposals/{idx}/promote ──────────────────

    @router.post(
        "/api/organizations/topology-proposals/{idx}/promote",
        dependencies=[Depends(_operator_dep)],
    )
    async def promote_proposal(
        idx: int,
        request: Request,
    ) -> dict[str, Any]:
        # Mutation: force-auth regardless of global require_auth.
        actor = _auth(request, force=True)

        def _do_promote_blocking() -> dict[str, Any]:
            # Sync file IO (proposals, registry, review/subagent policy
            # paths) plus the forge promotion run are offloaded to the
            # thread pool so the event loop stays responsive.
            proposals = merged_topology_proposals(
                _load_proposals(),
                registry=load_registry(),
                review_queue_path=app_paths().review_queue_path,
                subagent_policy_path=app_paths().subagent_policy_path,
            )["proposals"]
            if idx < 0 or idx >= len(proposals):
                raise HTTPException(404, f"proposal index out of range: {idx}")
            raw = proposals[idx]
            try:
                proposal = Proposal(
                    kind=str(raw["kind"]),
                    base_topology=str(raw["base_topology"]),
                    bucket=str(raw.get("bucket") or "default"),
                    detail=raw.get("detail") or {},
                    confidence=float(raw.get("confidence") or 0.0),
                    rationale=str(raw.get("rationale") or ""),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(400, f"malformed proposal: {exc}") from exc
            forge = TopologyForge(
                agent_registry=agent_registry,
                subagent_policy_path=app_paths().subagent_policy_path,
            )
            # The actor's identity becomes the approver — promoting via
            # an authenticated UI counts as human-signed (HATCHLING).
            result = forge.promote(proposal, approver=actor)
            return {
                "accepted": result.accepted,
                "reason": result.reason,
                "new_topology": (
                    _topology_payload(result.new_topology)
                    if result.new_topology is not None
                    else None
                ),
            }

        return await asyncio.to_thread(_do_promote_blocking)

    # ── GET /api/organizations/topology-performance ───────

    @router.get("/api/organizations/topology-performance")
    def list_performance(
        request: Request,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — performance stats are global
        rows = read_runs(limit=limit)
        return {"count": len(rows), "runs": rows}

    @router.get("/api/organizations/topology-promotion-lift")
    def list_promotion_lift(
        request: Request,
        limit: int = Query(default=2000, ge=1, le=10000),
    ) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — promotion lift is global
        return compute_topology_promotion_lift(limit=limit)

    # ── POST .../topologies/{fp}/retire ───────────────────

    @router.post(
        "/api/organizations/topologies/{fingerprint}/retire",
        dependencies=[Depends(_operator_dep)],
    )
    def retire_topology(fingerprint: str, request: Request) -> dict[str, Any]:
        # Mutation: force-auth.
        actor = _auth(request, force=True)
        registry = load_registry()
        target: TeamTopology | None = registry.get(fingerprint)
        if target is None:
            raise HTTPException(404, "topology not found")
        # Use the gene-lock gate so PANIC freezes retirement too.
        try:
            from runtime.safety.gene_locks import (
                LockViolation,
                MutationKind,
                gate_mutation,
            )

            gate_mutation(
                kind=MutationKind.PROMOTE_TOPOLOGY,
                target=f"retire:{fingerprint}",
                autonomous=actor is None,
                approver=actor,
            )
        except LockViolation as lv:
            raise HTTPException(409, f"gene_locks blocked: {lv}") from lv
        except (ImportError, AttributeError, OSError):  # noqa: BLE001 — gene_locks unavailable; proceed with unlocked unregister
            pass
        registry.pop(fingerprint, None)
        save_registry(registry)
        return {
            "retired": fingerprint,
            "remaining": len(registry),
            "topology": _topology_payload(target),
        }

    return router


__all__ = ["create_organizations_router"]
