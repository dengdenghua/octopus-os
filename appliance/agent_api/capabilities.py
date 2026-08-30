"""Stable Echo OS bridge to Agent's public capability lifecycle service."""

from __future__ import annotations

import os
from typing import Any

_OPERATIONS = frozenset(
    {
        "inspect",
        "install_plan",
        "install",
        "authorize",
        "disable",
        "status",
        "connection_profile",
        "connect",
        "disconnect",
        "uninstall_plan",
        "uninstall",
        "rollback_plan",
        "rollback",
    }
)


class AgentCapabilityApiError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AgentCapabilityBridge:
    """Translate an Echo-authenticated actor into Agent's tenant authority."""

    def __init__(
        self,
        service: Any | None = None,
        *,
        tenant_id: str | None = None,
    ) -> None:
        self._principal_type: Any | None = None
        self._service_error_type: type[Exception] | tuple[type[Exception], ...] = ()
        if service is None:
            try:
                from runtime.platform.capabilities import (
                    CapabilityLifecycleService,
                    CapabilityPrincipal,
                    CapabilityServiceError,
                )
            except (ImportError, AttributeError):
                self._service = None
            else:
                self._service = CapabilityLifecycleService()
                self._principal_type = CapabilityPrincipal
                self._service_error_type = CapabilityServiceError
        else:
            self._service = service
            try:
                from runtime.platform.capabilities import (
                    CapabilityPrincipal,
                    CapabilityServiceError,
                )
            except (ImportError, AttributeError):
                self._principal_type = None
            else:
                self._principal_type = CapabilityPrincipal
                self._service_error_type = CapabilityServiceError
        self._tenant_id = tenant_id or os.environ.get("ECHO_APPLIANCE_TENANT_ID", "echo-appliance")

    def invoke(
        self,
        operation: str,
        capability_id: str,
        *,
        actor_id: str,
        roles: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> dict[str, Any]:
        if operation not in _OPERATIONS:
            raise AgentCapabilityApiError("UNSUPPORTED_OPERATION", "operation is unsupported")
        if self._service is None or self._principal_type is None:
            raise AgentCapabilityApiError(
                "AGENT_CAPABILITY_UNAVAILABLE",
                "installed Agent does not provide the capability lifecycle service",
            )
        try:
            principal = self._principal_type.create(
                tenant_id=self._tenant_id,
                actor_id=actor_id,
                roles=roles,
            )
            method = getattr(self._service, operation)
            result = method(capability_id, principal=principal, **kwargs)
        except Exception as exc:
            if self._service_error_type and isinstance(exc, self._service_error_type):
                raise AgentCapabilityApiError(exc.code, exc.message) from exc
            raise
        if not isinstance(result, dict):
            raise AgentCapabilityApiError(
                "INVALID_RUNTIME_RESULT", "Agent capability result is invalid"
            )
        return result


__all__ = ["AgentCapabilityApiError", "AgentCapabilityBridge"]
