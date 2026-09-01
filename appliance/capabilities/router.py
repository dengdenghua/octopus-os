"""Authenticated discovery and policy-decision API for Echo capabilities."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from appliance.approval import APPROVAL_HEADER, INTENT_HEADER
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.capabilities.model import CAPABILITY_API_VERSION
from appliance.capabilities.policy import CapabilityDecision, CapabilityPolicy
from appliance.capabilities.registry import CapabilityRegistry
from appliance.security import ApplianceAuthenticator, resolve_authenticator

_INTENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CapabilityDecisionRequest(BaseModel):
    capability_id: str = Field(alias="capabilityId", min_length=3, max_length=128)
    intent_id: str = Field(alias="intentId", min_length=1, max_length=128)
    target: str | None = Field(default=None, max_length=1024)


def _audit_decision(
    audit: ApplianceAudit | None,
    *,
    auth_required: bool,
    actor: str,
    intent_id: str,
    capability_id: str,
    decision: CapabilityDecision,
) -> str | None:
    if audit is None:
        if auth_required:
            raise HTTPException(status_code=503, detail="capability audit unavailable")
        return None
    capability = decision.capability
    try:
        entry = audit.record(
            actor=actor,
            action="capability.decision",
            target=capability_id,
            outcome=decision.decision,
            metadata={
                "intentId": intent_id,
                "reasonCode": decision.reason_code,
                "requestedTarget": decision.target,
                "risk": capability.effect.risk.value if capability else "unknown",
            },
        )
    except (OSError, AuditIntegrityError) as exc:
        raise HTTPException(status_code=503, detail="capability audit unavailable") from exc
    return f"appliance-audit:{entry['seq']}"


def _decision_payload(
    decision: CapabilityDecision,
    *,
    actor: str,
    intent_id: str,
    capability_id: str,
    audit_event_id: str | None,
) -> dict[str, Any]:
    capability = decision.capability
    payload: dict[str, Any] = {
        "apiVersion": CAPABILITY_API_VERSION,
        "kind": "CapabilityDecision",
        "decision": decision.decision,
        "reasonCode": decision.reason_code,
        "actor": actor,
        "intentId": intent_id,
        "capabilityId": capability_id,
        "target": decision.target,
        "risk": capability.effect.risk.value if capability else "unknown",
        "auditEventId": audit_event_id,
    }
    if capability is None or decision.decision == "deny":
        return payload

    payload["execute"] = capability.provider.operation.to_dict()
    authorization = capability.authorization
    if decision.decision == "ask":
        payload["approval"] = {
            "mode": authorization.approval.value,
            "endpoint": "/api/appliance/approvals",
            "requestBody": {
                "action": authorization.approval_action,
                "target": decision.target,
                "intentId": intent_id,
            },
            "executionHeaders": {
                APPROVAL_HEADER: "<approvalToken>",
                INTENT_HEADER: intent_id,
            },
            "ttlSeconds": authorization.ttl_seconds,
            "singleUse": authorization.single_use,
        }
    return payload


def create_capabilities_router(
    registry: CapabilityRegistry,
    *,
    jwt_secret: str | None = None,
    audit: ApplianceAudit | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_auth = auth.dependency()
    policy = CapabilityPolicy(registry)
    router = APIRouter(prefix="/api/appliance/capabilities", tags=["appliance", "capabilities"])

    @router.get("")
    def list_capabilities(
        provider: str | None = Query(default=None, min_length=1, max_length=128),
        _actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        capabilities = registry.list(provider_id=provider)
        return {
            "apiVersion": CAPABILITY_API_VERSION,
            "kind": "CapabilityList",
            "count": len(capabilities),
            "capabilities": [item.to_dict() for item in capabilities],
        }

    @router.get("/{capability_id}")
    def get_capability(
        capability_id: str,
        _actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        capability = registry.get(capability_id)
        if capability is None:
            raise HTTPException(status_code=404, detail="capability not found")
        return capability.to_dict()

    @router.post("/decisions")
    def decide_capability(
        body: CapabilityDecisionRequest,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        if _INTENT_ID.fullmatch(body.intent_id) is None:
            raise HTTPException(status_code=422, detail="invalid intent id")
        decision = policy.decide(capability_id=body.capability_id, target=body.target)
        audit_event_id = _audit_decision(
            audit,
            auth_required=auth.required,
            actor=actor,
            intent_id=body.intent_id,
            capability_id=body.capability_id,
            decision=decision,
        )
        return _decision_payload(
            decision,
            actor=actor,
            intent_id=body.intent_id,
            capability_id=body.capability_id,
            audit_event_id=audit_event_id,
        )

    return router


__all__ = ["CapabilityDecisionRequest", "create_capabilities_router"]
