"""Default-closed policy preflight for system capability requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from appliance.capabilities.model import ApprovalMode, CapabilityDefinition
from appliance.capabilities.registry import CapabilityRegistry

_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_TRASH_ENTRY_ID = re.compile(r"^[0-9a-f]{32}$")
_HUB_APP_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_PLAN_ID = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CapabilityDecision:
    decision: str
    reason_code: str
    capability: CapabilityDefinition | None
    target: str | None


def _valid_relative_path(target: str) -> bool:
    if len(target) > 1024 or "\x00" in target or "\\" in target:
        return False
    path = PurePosixPath(target)
    return not path.is_absolute() and ".." not in path.parts


def _resolve_target(capability: CapabilityDefinition, target: str | None) -> str | None:
    scope = capability.scope
    supplied = target.strip() if isinstance(target, str) else None
    if scope.fixed_target is not None:
        if supplied not in (None, "", scope.fixed_target):
            return None
        return scope.fixed_target
    if scope.target_required and not supplied:
        return None
    return supplied if supplied is not None else ""


def _valid_scope(capability: CapabilityDefinition, target: str | None) -> bool:
    validation = capability.scope.validation
    if target is None:
        return False
    if validation == "fixed":
        return target == capability.scope.fixed_target
    if validation == "container-id":
        return _CONTAINER_ID.fullmatch(target) is not None
    if validation == "trash-entry-id":
        return _TRASH_ENTRY_ID.fullmatch(target) is not None
    if validation == "hub-app-id":
        return _HUB_APP_ID.fullmatch(target) is not None
    if validation == "plan-id":
        return _PLAN_ID.fullmatch(target) is not None
    if validation == "relative-path":
        return _valid_relative_path(target)
    return False


class CapabilityPolicy:
    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def decide(self, *, capability_id: str, target: str | None) -> CapabilityDecision:
        capability = self._registry.get(capability_id)
        if capability is None:
            return CapabilityDecision("deny", "UNKNOWN_CAPABILITY", None, None)

        resolved_target = _resolve_target(capability, target)
        if not _valid_scope(capability, resolved_target):
            return CapabilityDecision("deny", "INVALID_SCOPE", capability, resolved_target)

        if capability.authorization.approval is ApprovalMode.PASSWORD_STEP_UP:
            return CapabilityDecision(
                "ask",
                "PASSWORD_STEP_UP_REQUIRED",
                capability,
                resolved_target,
            )
        return CapabilityDecision("allow", "POLICY_ALLOWED", capability, resolved_target)


__all__ = ["CapabilityDecision", "CapabilityPolicy"]
