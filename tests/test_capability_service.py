from __future__ import annotations

from typing import Any

import pytest

from runtime.platform.capabilities.service import (
    CAPABILITY_SERVICE_SCHEMA,
    CapabilityLifecycleService,
    CapabilityPrincipal,
    CapabilityServiceError,
)
from runtime.platform.capabilities.tenant_context import current_capability_scope
from runtime.platform.connectors.credential_store import CredentialStore


class _Registry:
    def __init__(self) -> None:
        self.item: dict[str, Any] = {
            "id": "demo-token",
            "name": "Demo",
            "source": "connector",
            "version": "1.0.0",
            "installed": False,
            "enabled": False,
            "permissions": ["account.credentials"],
            "permissions_granted": [],
            "permission_review_required": True,
            "permission_active": False,
            "auth_mode": "token",
            "path": "/private/package/path",
        }
        self.seen_scopes: list[tuple[str, str]] = []

    def _scope(self) -> None:
        scope = current_capability_scope()
        assert scope is not None
        self.seen_scopes.append((scope.tenant_id, scope.actor_id))

    def get(self, capability_id: str) -> dict[str, Any] | None:
        self._scope()
        return self.item if capability_id == self.item["id"] else None

    def install_plan(self, capability_id: str) -> dict[str, Any]:
        self._scope()
        payload = {
            "schema": "echo.capability_install_plan.v1",
            "capability_id": capability_id,
            "kind": "connector",
            "version": self.item["version"],
            "permissions": list(self.item["permissions"]),
            "auth_modes": ["token"],
            "dependencies": [],
            "runtime_dependencies": [],
            "changes": ["verify_publisher_signature"],
            "permission_review_required": True,
            "can_install": True,
            "blockers": [],
        }
        encoded = __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload["plan_id"] = __import__("hashlib").sha256(encoded).hexdigest()
        return payload

    def install(self, capability_id: str) -> dict[str, Any]:
        self._scope()
        self.item.update(installed=True, enabled=False)
        return {
            "installed": True,
            "path": "/private/package/path",
            "transaction_id": "a" * 32,
            "rollback_available": True,
        }

    def grant_permissions(self, capability_id: str, permissions: Any) -> dict[str, Any]:
        self._scope()
        required = set(self.item["permissions"])
        granted = set(permissions)
        if required != granted:
            raise ValueError("all permissions must be confirmed")
        self.item.update(
            permissions_granted=sorted(granted),
            permission_review_required=False,
        )
        return {"granted": sorted(granted)}

    def set_enabled(self, capability_id: str, enabled: bool) -> bool:
        self._scope()
        if not self.item["installed"]:
            return False
        self.item.update(enabled=enabled, permission_active=enabled)
        return True

    def status(self, capability_id: str) -> dict[str, Any]:
        self._scope()
        return {
            "connected": False,
            "has_token": False,
            "stored_keys": [],
            "secret": "must-not-leak",
        }

    def connect(
        self,
        capability_id: str,
        *,
        tokens: dict[str, str] | None,
        run_cli: bool,
    ) -> dict[str, Any]:
        self._scope()
        assert run_cli is False
        assert tokens == {"access_token": "alice-token"}
        return {"connected": True, "token": "must-not-leak"}

    def disconnect(self, capability_id: str) -> dict[str, Any]:
        self._scope()
        return {"connected": False}

    def uninstall(self, capability_id: str) -> bool:
        self._scope()
        if not self.item["installed"]:
            return False
        self.item.update(installed=False, enabled=False)
        return True


class _Catalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def rollback_plugin(
        self,
        capability_id: str,
        *,
        plugin_kind: str,
        transaction_id: str,
    ) -> dict[str, Any]:
        self.calls.append((capability_id, plugin_kind, transaction_id))
        return {
            "operation": "restored_previous",
            "installed": True,
            "path": "/private/old-generation",
        }


class _ScopedCredentialRegistry(_Registry):
    def __init__(self, root: Any) -> None:
        super().__init__()
        self.item.update(installed=True, enabled=True, permissions=[])
        self.credentials = CredentialStore(root=root)

    def status(self, capability_id: str) -> dict[str, Any]:
        self._scope()
        keys = self.credentials.list_secrets(capability_id)
        return {
            "connected": bool(keys),
            "has_token": bool(keys),
            "stored_keys": keys,
        }

    def connect(
        self,
        capability_id: str,
        *,
        tokens: dict[str, str] | None,
        run_cli: bool,
    ) -> dict[str, Any]:
        self._scope()
        assert run_cli is False
        assert tokens
        for key, value in tokens.items():
            self.credentials.set_secret(capability_id, key, value)
        return {"connected": True}

    def disconnect(self, capability_id: str) -> dict[str, Any]:
        self._scope()
        self.credentials.clear_connector(capability_id)
        return {"connected": False}


@pytest.fixture
def principal() -> CapabilityPrincipal:
    return CapabilityPrincipal.create(
        tenant_id="family-a",
        actor_id="alice",
        roles={"member"},
    )


@pytest.fixture
def operator() -> CapabilityPrincipal:
    return CapabilityPrincipal.create(
        tenant_id="family-a",
        actor_id="admin",
        roles={"admin"},
    )


def test_install_requires_operator_and_exact_plan(
    principal: CapabilityPrincipal,
    operator: CapabilityPrincipal,
) -> None:
    registry = _Registry()
    service = CapabilityLifecycleService(registry)
    plan = service.install_plan("demo-token", principal=principal)

    with pytest.raises(CapabilityServiceError, match="device operator") as denied:
        service.install("demo-token", principal=principal, plan_id=plan["plan_id"])
    assert denied.value.code == "DEVICE_OPERATOR_REQUIRED"

    with pytest.raises(CapabilityServiceError) as stale:
        service.install("demo-token", principal=operator, plan_id="0" * 64)
    assert stale.value.code == "PLAN_STALE"

    result = service.install("demo-token", principal=operator, plan_id=plan["plan_id"])
    assert result["schema"] == CAPABILITY_SERVICE_SCHEMA
    assert result["result"]["installed"] is True
    assert "path" not in result["result"]
    assert "path" not in result["capability"]
    assert ("family-a", "admin") in registry.seen_scopes


def test_user_authorization_and_credentials_are_principal_scoped(
    principal: CapabilityPrincipal,
    operator: CapabilityPrincipal,
) -> None:
    registry = _Registry()
    service = CapabilityLifecycleService(registry)
    install_plan = service.install_plan("demo-token", principal=operator)
    service.install("demo-token", principal=operator, plan_id=install_plan["plan_id"])
    review_plan = service.install_plan("demo-token", principal=principal)

    authorized = service.authorize(
        "demo-token",
        principal=principal,
        plan_id=review_plan["plan_id"],
        permissions=["account.credentials"],
    )
    assert authorized["result"]["enabled"] is True

    connected = service.connect(
        "demo-token",
        principal=principal,
        tokens={"access_token": "alice-token"},
    )
    assert connected["result"] == {"connected": True}
    assert ("family-a", "alice") in registry.seen_scopes
    assert service.status("demo-token", principal=principal)["status"] == {
        "connected": False,
        "has_token": False,
        "stored_keys": [],
    }


def test_connection_profile_exposes_a_bounded_form_not_private_manifest_state(
    principal: CapabilityPrincipal,
) -> None:
    registry = _Registry()
    registry.item.update(installed=True, enabled=True)
    service = CapabilityLifecycleService(registry)

    profile = service.connection_profile("demo-token", principal=principal)

    assert profile == {
        "schema": CAPABILITY_SERVICE_SCHEMA,
        "capability_id": "demo-token",
        "auth_mode": "token",
        "mode": "principal_credentials",
        "can_connect": True,
        "connected": False,
        "minimum_credentials": 1,
        "fields": [
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
        ],
        "blockers": [],
    }
    assert "path" not in str(profile)


def test_connection_profile_routes_process_global_auth_back_to_agent(
    principal: CapabilityPrincipal,
) -> None:
    registry = _Registry()
    registry.item.update(installed=True, enabled=True, oauth_supported=True)
    service = CapabilityLifecycleService(registry)

    profile = service.connection_profile("demo-token", principal=principal)

    assert profile["mode"] == "agent_managed"
    assert profile["can_connect"] is False
    assert profile["connected"] is False
    assert profile["fields"] == []
    assert profile["blockers"] == ["principal_isolation_unavailable"]


def test_server_side_auth_requires_a_principal_credential_form(
    principal: CapabilityPrincipal,
) -> None:
    registry = _Registry()
    registry.item.update(installed=True, enabled=True, auth_mode="server-side")
    service = CapabilityLifecycleService(registry)

    profile = service.connection_profile("demo-token", principal=principal)

    assert profile["mode"] == "principal_credentials"
    assert profile["minimum_credentials"] == 1
    assert [field["key"] for field in profile["fields"]] == ["access_token", "api_key"]


def test_connect_rejects_missing_or_unadvertised_credentials(
    principal: CapabilityPrincipal,
) -> None:
    registry = _Registry()
    registry.item.update(installed=True, enabled=True)
    service = CapabilityLifecycleService(registry)

    with pytest.raises(CapabilityServiceError) as missing:
        service.connect("demo-token", principal=principal)
    assert missing.value.code == "INVALID_CREDENTIALS"

    with pytest.raises(CapabilityServiceError) as unexpected:
        service.connect(
            "demo-token",
            principal=principal,
            tokens={"PRIVATE_ENV_OVERRIDE": "secret"},
        )
    assert unexpected.value.code == "INVALID_CREDENTIALS"


def test_connect_never_turns_a_runtime_form_response_into_success(
    principal: CapabilityPrincipal,
) -> None:
    registry = _Registry()
    registry.item.update(installed=True, enabled=True)
    registry.connect = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "connected": False,
        "next_action": "form",
        "message": "private runtime detail",
    }
    service = CapabilityLifecycleService(registry)

    with pytest.raises(CapabilityServiceError) as blocked:
        service.connect(
            "demo-token",
            principal=principal,
            tokens={"access_token": "submitted"},
        )

    assert blocked.value.code == "CONNECT_BLOCKED"


def test_service_keeps_two_household_users_credentials_in_separate_partitions(
    tmp_path: Any,
) -> None:
    registry = _ScopedCredentialRegistry(tmp_path / "credentials")
    service = CapabilityLifecycleService(registry)
    alice = CapabilityPrincipal.create(tenant_id="family", actor_id="alice")
    bob = CapabilityPrincipal.create(tenant_id="family", actor_id="bob")

    service.connect(
        "demo-token",
        principal=alice,
        tokens={"access_token": "alice-secret"},
    )
    assert service.status("demo-token", principal=alice)["status"]["connected"] is True
    assert service.status("demo-token", principal=bob)["status"]["connected"] is False

    service.connect(
        "demo-token",
        principal=bob,
        tokens={"api_key": "bob-secret"},
    )
    assert service.status("demo-token", principal=alice)["status"]["stored_keys"] == [
        "access_token"
    ]
    assert service.status("demo-token", principal=bob)["status"]["stored_keys"] == [
        "api_key"
    ]

    service.disconnect("demo-token", principal=alice)
    assert service.status("demo-token", principal=alice)["status"]["connected"] is False
    assert service.status("demo-token", principal=bob)["status"]["connected"] is True


@pytest.mark.parametrize(
    "unsafe",
    [
        {"has_cli_auth": True},
        {"oauth_supported": True},
        {"oauth_provider": "github"},
        {"model_provider": True},
    ],
)
def test_shared_auth_modes_fail_closed(
    principal: CapabilityPrincipal,
    unsafe: dict[str, Any],
) -> None:
    registry = _Registry()
    registry.item.update(installed=True, enabled=True, **unsafe)
    service = CapabilityLifecycleService(registry)

    with pytest.raises(CapabilityServiceError) as caught:
        service.connect("demo-token", principal=principal, tokens={"API_TOKEN": "x"})
    assert caught.value.code == "PRINCIPAL_ISOLATION_UNAVAILABLE"


def test_uninstall_and_rollback_use_bound_operator_plans(
    operator: CapabilityPrincipal,
) -> None:
    registry = _Registry()
    registry.item.update(
        installed=True,
        rollback_available=True,
        transaction_id="b" * 32,
    )
    catalog = _Catalog()
    service = CapabilityLifecycleService(registry, catalog_factory=lambda _kind: catalog)

    rollback_plan = service.rollback_plan("demo-token", principal=operator)
    rolled_back = service.rollback(
        "demo-token",
        principal=operator,
        plan_id=rollback_plan["plan_id"],
    )
    assert rolled_back["result"]["operation"] == "restored_previous"
    assert catalog.calls == [("demo-token", "connector", "b" * 32)]
    assert "path" not in rolled_back["result"]

    uninstall_plan = service.uninstall_plan("demo-token", principal=operator)
    removed = service.uninstall(
        "demo-token",
        principal=operator,
        plan_id=uninstall_plan["plan_id"],
    )
    assert removed["result"]["installed"] is False


def test_principal_rejects_control_characters() -> None:
    with pytest.raises(CapabilityServiceError) as caught:
        CapabilityPrincipal.create(tenant_id="family", actor_id="alice\nadmin")
    assert caught.value.code == "INVALID_PRINCIPAL"


