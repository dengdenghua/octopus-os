"""Shared policy context for mutating Echo OMV routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from appliance.approval import (
    HighRiskApprovalService,
    consume_request_approval,
    request_intent_id,
)
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.omv_client import OmvClient, OmvControlRejected
from appliance.security import ApplianceAuthenticator


@dataclass(frozen=True, slots=True)
class OmvRouteContext:
    """Dependencies and fail-closed policy shared by OMV mutation routes."""

    omv: OmvClient
    auth: ApplianceAuthenticator
    require_auth: Callable[..., str]
    approval: HighRiskApprovalService | None
    audit: ApplianceAudit | None

    @staticmethod
    def control_error(exc: OmvControlRejected) -> HTTPException:
        return HTTPException(status_code=exc.status_code, detail=exc.detail)

    def consume_approval(
        self,
        request: Request,
        *,
        actor: str,
        action: str,
        target: str,
    ) -> None:
        if self.approval is None:
            if self.auth.required:
                raise HTTPException(status_code=503, detail="high-risk approval unavailable")
            return
        consume_request_approval(
            request,
            self.approval,
            actor=actor,
            action=action,
            target=target,
        )

    def record(
        self,
        request: Request,
        *,
        action: str,
        actor: str,
        target: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> None:
        if self.audit is None:
            if self.auth.required:
                raise HTTPException(status_code=503, detail="appliance audit unavailable")
            return
        details = dict(metadata)
        intent_id = request_intent_id(request)
        if intent_id:
            details["intentId"] = intent_id
        try:
            self.audit.record(
                actor=actor,
                action=action,
                target=target,
                outcome=outcome,
                metadata=details,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc


__all__ = ["OmvRouteContext"]
