"""Stable, framework-independent lifecycle service for marketplace capabilities.

The HTTP routers and Echo OS are consumers of this module.  They must not open
the registry's JSON/SQLite files or depend on its private layout.  The service
also establishes the appliance ownership split:

* package install, uninstall and rollback are device-operator operations;
* permission review, activation and token credentials are principal-scoped;
* process-global CLI/OAuth/model-provider authentication fails closed in a
  shared principal scope until the corresponding runtime is isolated.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from runtime.platform.capabilities.tenant_context import use_capability_scope
from runtime.safety.auth.scope import TenantScope

CAPABILITY_SERVICE_SCHEMA = "echo.capability-service.v1"
_CAPABILITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_PLAN_ID = re.compile(r"^[0-9a-f]{64}$")
_TRANSACTION_ID = re.compile(r"^[0-9a-f]{32}$")
_OPERATOR_ROLES = frozenset({"admin", "operator"})


class CapabilityServiceError(RuntimeError):
    """A bounded domain error that adapters may translate to their transport."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CapabilityPrincipal:
    tenant_id: str
    actor_id: str
    roles: frozenset[str] = frozenset()

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        actor_id: str,
        roles: tuple[str, ...] | list[str] | set[str] | frozenset[str] = (),
    ) -> CapabilityPrincipal:
        tenant = _bounded_identity(tenant_id, "tenant_id")
        actor = _bounded_identity(actor_id, "actor_id")
        normalized_roles = frozenset(_bounded_identity(role, "role", maximum=64) for role in roles)
        return cls(tenant_id=tenant, actor_id=actor, roles=normalized_roles)

    @property
    def is_operator(self) -> bool:
        return bool(self.roles.intersection(_OPERATOR_ROLES))

    @property
    def scope(self) -> TenantScope:
        return TenantScope(tenant_id=self.tenant_id, actor_id=self.actor_id)


def _bounded_identity(value: Any, name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise CapabilityServiceError("INVALID_PRINCIPAL", f"{name} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise CapabilityServiceError("INVALID_PRINCIPAL", f"{name} is invalid")
    return normalized


def _json_copy(value: Any) -> Any:
    """Reject non-wire values while returning a detached JSON-compatible copy."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise CapabilityServiceError("INVALID_RUNTIME_RESULT", "runtime result is invalid") from exc


def _plan_id(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CapabilityLifecycleService:
    """Versioned lifecycle boundary shared by Agent transports and Echo OS."""

    _CAPABILITY_FIELDS = (
        "id",
        "name",
        "name_zh",
        "description",
        "description_zh",
        "source",
        "type",
        "category",
        "author",
        "version",
        "available_version",
        "auth_mode",
        "installed",
        "enabled",
        "lifecycle_state",
        "permissions",
        "permissions_granted",
        "permission_review_required",
        "permission_active",
        "auth_modes",
        "dependencies",
        "runtime_dependencies",
        "rollback_available",
        "transaction_id",
        "host_api",
    )
    _STATUS_FIELDS = (
        "connected",
        "installed",
        "enabled",
        "auth_mode",
        "has_token",
        "stored_keys",
        "oauth_servers",
    )

    _GENERIC_CREDENTIAL_FIELDS = (
        {
            "key": "access_token",
            "label": "Access Token",
            "label_zh": "访问令牌",
            "secret": True,
            "required": False,
        },
        {
            "key": "api_key",
            "label": "API Key",
            "label_zh": "API 密钥",
            "secret": True,
            "required": False,
        },
    )

    def __init__(
        self,
        registry: Any | None = None,
        *,
        catalog_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if registry is None:
            from runtime.platform.capabilities.capability_registry import CapabilityRegistry

            registry = CapabilityRegistry()
        if catalog_factory is None:
            from runtime.platform.plugins.cloud_catalog import CloudCatalog

            catalog_factory = CloudCatalog
        self._registry = registry
        self._catalog_factory = catalog_factory

    @contextmanager
    def _call(self, principal: CapabilityPrincipal) -> Iterator[None]:
        if not isinstance(principal, CapabilityPrincipal):
            raise CapabilityServiceError("AUTHENTICATION_REQUIRED", "principal is required")
        with use_capability_scope(principal.scope):
            yield

    @staticmethod
    def _require_operator(principal: CapabilityPrincipal) -> None:
        if not principal.is_operator:
            raise CapabilityServiceError(
                "DEVICE_OPERATOR_REQUIRED",
                "device operator permission is required",
            )

    @staticmethod
    def _validate_capability_id(capability_id: str) -> str:
        if not isinstance(capability_id, str) or _CAPABILITY_ID.fullmatch(capability_id) is None:
            raise CapabilityServiceError("INVALID_CAPABILITY_ID", "capability id is invalid")
        return capability_id

    def _item(self, capability_id: str) -> dict[str, Any]:
        safe = self._validate_capability_id(capability_id)
        item = self._registry.get(safe)
        if not isinstance(item, dict):
            raise CapabilityServiceError("CAPABILITY_NOT_FOUND", "capability was not found")
        return item

    @classmethod
    def _public_capability(cls, item: Mapping[str, Any]) -> dict[str, Any]:
        public = {key: _json_copy(item[key]) for key in cls._CAPABILITY_FIELDS if key in item}
        public["id"] = str(public.get("id") or "")
        return public

    @classmethod
    def _public_status(cls, status: Any) -> dict[str, Any]:
        if not isinstance(status, Mapping):
            raise CapabilityServiceError("INVALID_RUNTIME_RESULT", "capability status is invalid")
        return {key: _json_copy(status[key]) for key in cls._STATUS_FIELDS if key in status}

    def inspect(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
    ) -> dict[str, Any]:
        with self._call(principal):
            return {
                "schema": CAPABILITY_SERVICE_SCHEMA,
                "capability": self._public_capability(self._item(capability_id)),
            }

    def install_plan(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
    ) -> dict[str, Any]:
        with self._call(principal):
            self._item(capability_id)
            try:
                raw = self._registry.install_plan(capability_id)
            except KeyError as exc:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_FOUND", "capability was not found"
                ) from exc
            if not isinstance(raw, Mapping):
                raise CapabilityServiceError("INVALID_RUNTIME_RESULT", "install plan is invalid")
            plan = _json_copy(raw)
            plan["service_schema"] = CAPABILITY_SERVICE_SCHEMA
            plan_id = str(plan.get("plan_id") or "")
            if _PLAN_ID.fullmatch(plan_id) is None:
                raise CapabilityServiceError("INVALID_RUNTIME_RESULT", "install plan id is invalid")
            return plan

    @staticmethod
    def _require_plan(expected_plan_id: str, current: Mapping[str, Any]) -> None:
        if not isinstance(expected_plan_id, str) or _PLAN_ID.fullmatch(expected_plan_id) is None:
            raise CapabilityServiceError("INVALID_PLAN_ID", "plan id is invalid")
        if expected_plan_id != current.get("plan_id"):
            raise CapabilityServiceError(
                "PLAN_STALE", "capability state changed; review a new plan"
            )

    def _operation_result(
        self,
        operation: str,
        capability_id: str,
        raw: Any,
    ) -> dict[str, Any]:
        item = self._item(capability_id)
        result: dict[str, Any] = {}
        if isinstance(raw, Mapping):
            for key in (
                "installed",
                "enabled",
                "connected",
                "operation",
                "transaction_id",
                "rollback_available",
                "permission_review_required",
                "permissions",
                "copied_skills",
                "removed_skills",
                "message",
            ):
                if key in raw:
                    result[key] = _json_copy(raw[key])
        return {
            "schema": CAPABILITY_SERVICE_SCHEMA,
            "operation": operation,
            "capability": self._public_capability(item),
            "result": result,
        }

    def install(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
        plan_id: str,
    ) -> dict[str, Any]:
        with self._call(principal):
            self._require_operator(principal)
            current = self.install_plan(capability_id, principal=principal)
            self._require_plan(plan_id, current)
            if current.get("can_install") is not True:
                raise CapabilityServiceError("INSTALL_BLOCKED", "capability install is blocked")
            try:
                raw = self._registry.install(capability_id)
            except KeyError as exc:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_FOUND", "capability was not found"
                ) from exc
            return self._operation_result("install", capability_id, raw)

    @staticmethod
    def _principal_auth_isolated(item: Mapping[str, Any]) -> bool:
        return not (
            item.get("has_cli_auth") is True
            or item.get("model_provider") is True
            or item.get("oauth_supported") is True
            or bool(item.get("oauth_provider"))
        )

    def authorize(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
        plan_id: str,
        permissions: Any,
        activate: bool = True,
    ) -> dict[str, Any]:
        with self._call(principal):
            item = self._item(capability_id)
            if item.get("installed") is not True:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_INSTALLED", "capability is not installed"
                )
            current = self.install_plan(capability_id, principal=principal)
            self._require_plan(plan_id, current)
            try:
                grant = self._registry.grant_permissions(capability_id, permissions)
                changed = self._registry.set_enabled(capability_id, True) if activate else True
            except KeyError as exc:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_INSTALLED", "capability is not installed"
                ) from exc
            except (PermissionError, ValueError) as exc:
                raise CapabilityServiceError("PERMISSION_REVIEW_REQUIRED", str(exc)) from exc
            if not changed:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_INSTALLED", "capability is not installed"
                )
            return self._operation_result(
                "authorize",
                capability_id,
                {
                    "enabled": bool(activate),
                    "permissions": list(grant.get("granted") or []),
                    "permission_review_required": False,
                },
            )

    def disable(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
    ) -> dict[str, Any]:
        with self._call(principal):
            self._item(capability_id)
            if not self._registry.set_enabled(capability_id, False):
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_INSTALLED", "capability is not installed"
                )
            return self._operation_result("disable", capability_id, {"enabled": False})

    def status(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
    ) -> dict[str, Any]:
        with self._call(principal):
            self._item(capability_id)
            try:
                status = self._registry.status(capability_id)
            except KeyError as exc:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_FOUND", "capability was not found"
                ) from exc
            return {
                "schema": CAPABILITY_SERVICE_SCHEMA,
                "capability_id": capability_id,
                "status": self._public_status(status),
            }

    def connection_profile(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
    ) -> dict[str, Any]:
        """Describe the bounded, principal-safe connection form for a consumer.

        This is deliberately a form contract, not a projection of connector
        manifests or credential storage.  Echo can therefore render the same
        account flow without learning Agent package paths or private state.
        """

        with self._call(principal):
            item = self._item(capability_id)
            auth_mode = str(item.get("auth_mode") or "none")
            installed = item.get("installed") is True
            enabled = item.get("enabled") is True
            blockers: list[str] = []
            if not installed:
                blockers.append("capability_not_installed")
            elif not enabled:
                blockers.append("capability_not_enabled")

            isolated = self._principal_auth_isolated(item)
            if not isolated:
                blockers.append("principal_isolation_unavailable")
                mode = "agent_managed"
                fields: list[dict[str, Any]] = []
                minimum_credentials = 0
            elif item.get("source") != "connector" or auth_mode == "none":
                mode = "no_credentials"
                fields = []
                minimum_credentials = 0
            elif auth_mode == "oneid-token":
                mode = "principal_credentials"
                fields = [
                    {
                        "key": "oneid_token",
                        "label": "OneID Token",
                        "label_zh": "OneID 令牌",
                        "secret": True,
                        "required": True,
                    }
                ]
                minimum_credentials = 1
            else:
                mode = "principal_credentials"
                fields = _json_copy(self._GENERIC_CREDENTIAL_FIELDS)
                minimum_credentials = 1

            connected = False
            if installed and isolated:
                try:
                    connected = bool(
                        self._public_status(self._registry.status(capability_id)).get("connected")
                    )
                except KeyError:
                    connected = False

            return {
                "schema": CAPABILITY_SERVICE_SCHEMA,
                "capability_id": capability_id,
                "auth_mode": auth_mode,
                "mode": mode,
                "can_connect": not blockers,
                "connected": connected,
                "minimum_credentials": minimum_credentials,
                "fields": fields,
                "blockers": blockers,
            }

    @staticmethod
    def _tokens(tokens: Any) -> dict[str, str] | None:
        if tokens is None:
            return None
        if not isinstance(tokens, Mapping) or not 1 <= len(tokens) <= 32:
            raise CapabilityServiceError("INVALID_CREDENTIALS", "credential fields are invalid")
        result: dict[str, str] = {}
        for key, value in tokens.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key.strip()
                or len(key) > 128
                or not value
                or len(value) > 16_384
                or any(ord(character) < 32 for character in key)
            ):
                raise CapabilityServiceError("INVALID_CREDENTIALS", "credential fields are invalid")
            result[key] = value
        return result

    def connect(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
        tokens: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        with self._call(principal):
            item = self._item(capability_id)
            if not self._principal_auth_isolated(item):
                raise CapabilityServiceError(
                    "PRINCIPAL_ISOLATION_UNAVAILABLE",
                    "this authentication mode is not isolated per user",
                )
            profile = self.connection_profile(capability_id, principal=principal)
            blockers = set(profile.get("blockers") or [])
            if "capability_not_installed" in blockers:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_INSTALLED", "capability is not installed"
                )
            if "capability_not_enabled" in blockers:
                raise CapabilityServiceError(
                    "PERMISSION_REVIEW_REQUIRED", "capability is not enabled for this user"
                )
            normalized_tokens = self._tokens(tokens)
            credential_fields = {
                str(field.get("key"))
                for field in profile.get("fields") or []
                if isinstance(field, Mapping)
            }
            if normalized_tokens and not set(normalized_tokens) <= credential_fields:
                raise CapabilityServiceError("INVALID_CREDENTIALS", "credential fields are invalid")
            if len(normalized_tokens or {}) < int(profile.get("minimum_credentials") or 0):
                raise CapabilityServiceError(
                    "INVALID_CREDENTIALS", "required credentials are missing"
                )
            try:
                raw = self._registry.connect(
                    capability_id,
                    tokens=normalized_tokens,
                    run_cli=False,
                )
            except KeyError as exc:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_FOUND", "capability was not found"
                ) from exc
            except PermissionError as exc:
                raise CapabilityServiceError("PERMISSION_REVIEW_REQUIRED", str(exc)) from exc
            except ValueError as exc:
                raise CapabilityServiceError("CONNECT_BLOCKED", str(exc)) from exc
            if not isinstance(raw, Mapping) or raw.get("connected") is not True:
                raise CapabilityServiceError("CONNECT_BLOCKED", "capability did not connect")
            return self._operation_result("connect", capability_id, raw)

    def disconnect(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
    ) -> dict[str, Any]:
        with self._call(principal):
            item = self._item(capability_id)
            if not self._principal_auth_isolated(item):
                raise CapabilityServiceError(
                    "PRINCIPAL_ISOLATION_UNAVAILABLE",
                    "this authentication mode is not isolated per user",
                )
            raw = self._registry.disconnect(capability_id)
            return self._operation_result("disconnect", capability_id, raw)

    def uninstall_plan(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
    ) -> dict[str, Any]:
        with self._call(principal):
            item = self._item(capability_id)
            payload = {
                "schema": "echo.capability_uninstall_plan.v1",
                "service_schema": CAPABILITY_SERVICE_SCHEMA,
                "capability_id": capability_id,
                "source": str(item.get("source") or ""),
                "version": str(item.get("version") or ""),
                "installed": item.get("installed") is True,
                "transaction_id": item.get("transaction_id"),
                "changes": [
                    "disable_current_generation",
                    "remove_projected_skills",
                    "revoke_principal_permissions",
                ],
                "can_uninstall": item.get("installed") is True,
            }
            payload["plan_id"] = _plan_id(payload)
            return payload

    def uninstall(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
        plan_id: str,
    ) -> dict[str, Any]:
        with self._call(principal):
            self._require_operator(principal)
            current = self.uninstall_plan(capability_id, principal=principal)
            self._require_plan(plan_id, current)
            if current.get("can_uninstall") is not True:
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_INSTALLED", "capability is not installed"
                )
            if not self._registry.uninstall(capability_id):
                raise CapabilityServiceError(
                    "CAPABILITY_NOT_INSTALLED", "capability is not installed"
                )
            return self._operation_result("uninstall", capability_id, {"installed": False})

    def rollback_plan(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
    ) -> dict[str, Any]:
        with self._call(principal):
            item = self._item(capability_id)
            transaction_id = str(item.get("transaction_id") or "")
            available = item.get("rollback_available") is True and bool(
                _TRANSACTION_ID.fullmatch(transaction_id)
            )
            package_kind = "connector" if item.get("source") == "connector" else "codex"
            payload = {
                "schema": "echo.capability_rollback_plan.v1",
                "service_schema": CAPABILITY_SERVICE_SCHEMA,
                "capability_id": capability_id,
                "kind": package_kind,
                "version": str(item.get("version") or ""),
                "transaction_id": transaction_id or None,
                "changes": [
                    "deactivate_current_generation",
                    "restore_previous_signed_generation",
                    "restore_permission_generation",
                ],
                "can_rollback": available,
            }
            payload["plan_id"] = _plan_id(payload)
            return payload

    def rollback(
        self,
        capability_id: str,
        *,
        principal: CapabilityPrincipal,
        plan_id: str,
    ) -> dict[str, Any]:
        with self._call(principal):
            self._require_operator(principal)
            current = self.rollback_plan(capability_id, principal=principal)
            self._require_plan(plan_id, current)
            if current.get("can_rollback") is not True:
                raise CapabilityServiceError("ROLLBACK_UNAVAILABLE", "rollback is unavailable")
            catalog = self._catalog_factory("plugins")
            try:
                raw = catalog.rollback_plugin(
                    capability_id,
                    plugin_kind=str(current["kind"]),
                    transaction_id=str(current["transaction_id"]),
                )
            except KeyError as exc:
                raise CapabilityServiceError(
                    "ROLLBACK_UNAVAILABLE", "rollback is unavailable"
                ) from exc
            except ValueError as exc:
                raise CapabilityServiceError("ROLLBACK_REJECTED", str(exc)) from exc
            return self._operation_result("rollback", capability_id, raw)


__all__ = [
    "CAPABILITY_SERVICE_SCHEMA",
    "CapabilityLifecycleService",
    "CapabilityPrincipal",
    "CapabilityServiceError",
]
