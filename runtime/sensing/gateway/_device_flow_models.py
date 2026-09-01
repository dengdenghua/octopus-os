"""Typed wire models for connector device-flow generations."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DeviceFlowPayload(BaseModel):
    """One server-owned device authorization generation."""

    flow_id: str = Field(
        min_length=1,
        max_length=128,
        description="Opaque server-generated generation id; echo it when cancelling.",
    )
    connector_id: str
    verification_uri: str
    user_code: str
    expires_in: int
    code_embedded_in_uri: bool
    message: str | None = None


class DeviceFlowResponse(BaseModel):
    """Shared connect/status envelope while preserving connector-specific fields."""

    model_config = ConfigDict(extra="allow")

    connector_id: str | None = None
    capability_id: str | None = None
    connected: bool | None = None
    active: bool | None = None
    next_action: str | None = None
    auth_mode: str | None = None
    message: str | None = None
    command: str | None = None
    device_flow: DeviceFlowPayload | None = None


class DeviceFlowCancelResponse(BaseModel):
    """Idempotent generation-scoped cancellation result."""

    cancelled: bool
    connector_id: str
    reason: str | None = None


__all__ = [
    "DeviceFlowCancelResponse",
    "DeviceFlowPayload",
    "DeviceFlowResponse",
]
