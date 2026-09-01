"""Authenticated Echo API for OMV inventory and constrained storage controls."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from appliance.approval import HighRiskApprovalService
from appliance.audit import ApplianceAudit
from appliance.omv_account_routes import register_omv_account_routes
from appliance.omv_client import OmvClient
from appliance.omv_health import OmvHealthMonitor
from appliance.omv_models import (
    GroupApplyRequest,
    GroupDesiredState,
    NfsApplyRequest,
    NfsDesiredState,
    QuotaApplyRequest,
    QuotaDesiredState,
    SmbApplyRequest,
    SmbDesiredState,
    UserApplyRequest,
    UserDesiredState,
    UserPasswordApplyRequest,
    UserPasswordDesiredState,
)
from appliance.omv_quota_routes import register_omv_quota_routes
from appliance.omv_read_routes import register_omv_read_routes
from appliance.omv_route_context import OmvRouteContext
from appliance.omv_sharing_routes import register_omv_sharing_routes
from appliance.security import ApplianceAuthenticator, resolve_authenticator


def create_omv_router(
    client: OmvClient | None = None,
    *,
    monitor: OmvHealthMonitor | None = None,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService | None = None,
    audit: ApplianceAudit | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    omv = client or OmvClient()
    health_monitor = monitor or OmvHealthMonitor(omv, None)
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_operator = auth.operator_dependency()
    router = APIRouter(
        prefix="/api/appliance/omv",
        tags=["appliance", "omv"],
        dependencies=[Depends(require_operator)],
    )
    context = OmvRouteContext(
        omv=omv,
        auth=auth,
        require_auth=require_operator,
        approval=approval,
        audit=audit,
    )

    register_omv_read_routes(router, omv, health_monitor)

    register_omv_account_routes(router, context)

    register_omv_sharing_routes(router, context)

    register_omv_quota_routes(router, context)

    return router


__all__ = [
    "GroupApplyRequest",
    "GroupDesiredState",
    "NfsApplyRequest",
    "NfsDesiredState",
    "QuotaApplyRequest",
    "QuotaDesiredState",
    "SmbApplyRequest",
    "SmbDesiredState",
    "UserApplyRequest",
    "UserDesiredState",
    "UserPasswordApplyRequest",
    "UserPasswordDesiredState",
    "create_omv_router",
]
