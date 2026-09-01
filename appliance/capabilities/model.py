"""Wire model for the ECHO Capability Contract v0.1.

The contract describes an already-existing, narrowly scoped system operation.
It does not grant permission by itself: callers must ask the policy endpoint for
a decision and still satisfy the target operation's authentication/approval
gate when executing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

CAPABILITY_API_VERSION = "echo.ai/v1alpha1"
_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")


class CapabilityRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalMode(StrEnum):
    NONE = "none"
    PASSWORD_STEP_UP = "password-step-up"


@dataclass(frozen=True)
class CapabilityOperation:
    method: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {"method": self.method, "path": self.path}


@dataclass(frozen=True)
class CapabilityProvider:
    id: str
    transport: str
    operation: CapabilityOperation

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "transport": self.transport,
            "operation": self.operation.to_dict(),
        }


@dataclass(frozen=True)
class CapabilityEffect:
    type: str
    risk: CapabilityRisk
    reversible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "risk": self.risk.value,
            "reversible": self.reversible,
        }


@dataclass(frozen=True)
class CapabilityScope:
    resource_kind: str
    validation: str
    target_required: bool = True
    fixed_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "resourceKind": self.resource_kind,
            "validation": self.validation,
            "targetRequired": self.target_required,
        }
        if self.fixed_target is not None:
            result["fixedTarget"] = self.fixed_target
        return result


@dataclass(frozen=True)
class CapabilityAuthorization:
    approval: ApprovalMode = ApprovalMode.NONE
    approval_action: str | None = None
    ttl_seconds: int | None = None
    single_use: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"approval": self.approval.value}
        if self.approval_action is not None:
            result["approvalAction"] = self.approval_action
        if self.ttl_seconds is not None:
            result["ttlSeconds"] = self.ttl_seconds
        if self.single_use:
            result["singleUse"] = True
        return result


@dataclass(frozen=True)
class CapabilityAudit:
    action: str
    required: bool

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "required": self.required}


@dataclass(frozen=True)
class CapabilityDefinition:
    id: str
    version: int
    title: str
    description: str
    provider: CapabilityProvider
    request_schema: dict[str, Any]
    effect: CapabilityEffect
    scope: CapabilityScope
    authorization: CapabilityAuthorization
    audit: CapabilityAudit
    actor_kinds: tuple[str, ...] = field(default=("human", "agent"))

    def __post_init__(self) -> None:
        if _CAPABILITY_ID.fullmatch(self.id) is None:
            raise ValueError(f"invalid capability id: {self.id}")
        if self.version < 1:
            raise ValueError("capability version must be positive")
        if self.authorization.approval is ApprovalMode.PASSWORD_STEP_UP:
            if not self.authorization.approval_action:
                raise ValueError("step-up capability requires an approval action")
            if not self.authorization.single_use or not self.authorization.ttl_seconds:
                raise ValueError("step-up capability must be short-lived and single-use")

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": CAPABILITY_API_VERSION,
            "kind": "Capability",
            "metadata": {
                "id": self.id,
                "version": self.version,
                "title": self.title,
                "description": self.description,
            },
            "provider": self.provider.to_dict(),
            "requestSchema": self.request_schema,
            "effect": self.effect.to_dict(),
            "scope": self.scope.to_dict(),
            "authorization": {
                **self.authorization.to_dict(),
                "actorKinds": list(self.actor_kinds),
            },
            "audit": self.audit.to_dict(),
        }


__all__ = [
    "CAPABILITY_API_VERSION",
    "ApprovalMode",
    "CapabilityAudit",
    "CapabilityAuthorization",
    "CapabilityDefinition",
    "CapabilityEffect",
    "CapabilityOperation",
    "CapabilityProvider",
    "CapabilityRisk",
    "CapabilityScope",
]
