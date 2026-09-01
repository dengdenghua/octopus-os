"""Safe Agent capability projection tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.agent_assets import AgentAssetCatalogService, create_agent_assets_router
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "echo-agent-assets-test-secret-that-is-long-enough"


class _Catalog:
    def __init__(self, kind: str, *, fail: bool = False) -> None:
        self.kind = kind
        self.fail = fail

    def list(self, **kwargs: object) -> dict:
        assert kwargs in ({"limit": 80}, {"kind": "workbench", "limit": 80})
        if self.fail:
            raise RuntimeError("catalog unavailable")
        if self.kind == "plugins":
            result = {
                "items": [
                    {
                        "id": "documents",
                        "plugin": "documents",
                        "kind": "workbench",
                        "category": "productivity",
                        "author": "Echo",
                        "release_summary": "1.1.0：新增受信版本说明。",
                        "host_api": ">=0.2,<0.3",
                        "permissions": ["content.read", "content.write"],
                        "auth_modes": ["oauth"],
                        "dependencies": ["base-tools"],
                        "runtime_dependencies": ["renderer.whl"],
                        "connectors": ["documents-app"],
                        "name_zh": "文档助手",
                        "privateDatabaseRow": {"token": "must-not-cross-boundary"},
                        "localPath": "/private/agent/plugins/documents",
                    }
                ]
            }
            if kwargs.get("kind") == "workbench":
                return result
            return result
        return {"items": [{"name": "photo-organizer", "description": "整理照片"}]}

    def installed_plugins(self) -> list[str]:
        return ["documents", "documents"]

    def installed_skills(self) -> list[str]:
        return []

    def plugin_statuses(self) -> dict[str, dict]:
        return {
            "documents": {
                "plugin_id": "documents",
                "catalog_id": "documents",
                "kind": "workbench",
                "source": "cloud",
                "installed": True,
                "enabled": False,
                "lifecycle_state": "update_available",
                "version": "1.0.0",
                "available_version": "1.1.0",
                "path": "/private/agent/plugins/documents",
                "data_path": "/private/agent/data/documents",
                "recoveries": [{"path": "/private/recovery"}],
                "error": "private runtime error",
                "rollback_available": True,
                "transaction_id": "private-transaction",
                "trust": {
                    "level": "publisher",
                    "integrity_verified": True,
                    "publisher_verified": True,
                    "publisher_id": "Echo Publisher",
                    "key_id": "private-signing-key",
                    "content_digest": "private-content-digest",
                },
                "compatibility": {
                    "status": "compatible",
                    "host_api": ">=0.2,<0.3",
                    "private_reason": "must not cross boundary",
                },
                "release_summary": "1.1.0：新增受信版本说明。",
                "permissions": ["content.read", "content.write"],
                "auth_modes": ["oauth"],
                "dependencies": ["base-tools"],
                "runtime_dependencies": ["renderer.whl"],
                "connectors": ["documents-app"],
                "permissions_granted": [],
                "permission_review_required": False,
                "permission_active": False,
            }
        }


def _token() -> str:
    return encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )


def test_agent_assets_reuses_bounded_catalog_and_installed_state() -> None:
    service = AgentAssetCatalogService(lambda kind: _Catalog(kind))

    result = service.catalog()

    assert result == {
        "schema": "echo.agent-assets.v6",
        "available": True,
        "plugins": [
            {
                "id": "documents",
                "plugin": "documents",
                "kind": "workbench",
                "category": "productivity",
                "author": "Echo",
                "release_summary": "1.1.0：新增受信版本说明。",
                "host_api": ">=0.2,<0.3",
                "permissions": ["content.read", "content.write"],
                "authModes": ["oauth"],
                "dependencies": ["base-tools"],
                "runtimeDependencies": ["renderer.whl"],
                "connectors": ["documents-app"],
                "name_zh": "文档助手",
            }
        ],
        "skills": [{"name": "photo-organizer", "description": "整理照片"}],
        "installed": {"plugins": ["documents"], "skills": []},
        "pluginStates": [
            {
                "id": "documents",
                "catalogId": "documents",
                "kind": "workbench",
                "source": "cloud",
                "state": "update_available",
                "installed": True,
                "enabled": False,
                "rollbackAvailable": True,
                "recoveryCount": 1,
                "permissionsGranted": [],
                "permissionReviewRequired": False,
                "permissionActive": False,
                "trustLevel": "publisher",
                "integrityVerified": True,
                "publisherVerified": True,
                "publisher": "Echo Publisher",
                "compatibility": "compatible",
                "hostApi": ">=0.2,<0.3",
                "releaseSummary": "1.1.0：新增受信版本说明。",
                "permissions": ["content.read", "content.write"],
                "authModes": ["oauth"],
                "dependencies": ["base-tools"],
                "runtimeDependencies": ["renderer.whl"],
                "connectors": ["documents-app"],
                "version": "1.0.0",
                "availableVersion": "1.1.0",
            }
        ],
        "unavailableSources": [],
    }
    serialized = str(result)
    assert "/private/agent" not in serialized
    assert "private runtime error" not in serialized
    assert "private-transaction" not in serialized
    assert "private-signing-key" not in serialized
    assert "private-content-digest" not in serialized
    assert "private_reason" not in serialized


def test_agent_assets_keeps_partial_catalog_when_one_agent_source_is_offline() -> None:
    service = AgentAssetCatalogService(lambda kind: _Catalog(kind, fail=kind == "plugins"))

    result = service.catalog()

    assert result["available"] is True
    assert result["plugins"] == []
    assert result["skills"] == [{"name": "photo-organizer", "description": "整理照片"}]
    assert result["pluginStates"] == []
    assert result["unavailableSources"] == ["plugins"]


def test_agent_assets_reports_optional_agent_catalog_import_failure() -> None:
    def unavailable(_kind: str) -> _Catalog:
        raise ImportError("private Agent module moved")

    result = AgentAssetCatalogService(unavailable).catalog()

    assert result["available"] is False
    assert result["plugins"] == []
    assert result["skills"] == []
    assert result["unavailableSources"] == ["plugins", "skills"]


def test_agent_assets_keeps_catalog_when_plugin_lifecycle_projection_is_offline() -> None:
    class StatusUnavailable(_Catalog):
        def plugin_statuses(self) -> dict[str, dict]:
            raise RuntimeError("private status store unavailable")

    result = AgentAssetCatalogService(lambda kind: StatusUnavailable(kind)).catalog()

    assert result["available"] is True
    assert result["plugins"][0]["plugin"] == "documents"
    assert result["installed"]["plugins"] == ["documents"]
    assert result["pluginStates"] == []
    assert result["unavailableSources"] == ["plugin-statuses"]


def test_agent_assets_projects_only_bounded_public_fields_and_deduplicates() -> None:
    class UnsafeCatalog(_Catalog):
        def list(self, **kwargs: object) -> dict:
            assert kwargs in ({"limit": 80}, {"kind": "workbench", "limit": 80})
            if self.kind == "plugins":
                if kwargs.get("kind") == "workbench":
                    return {"items": []}
                return {
                    "items": [
                        {
                            "id": "documents",
                            "plugin": "documents",
                            "name": "Documents",
                            "description": "Public description",
                            "secret": "agent-private-token",
                            "config": {"database": "/private/agent.sqlite"},
                        },
                        {"id": "duplicate", "plugin": "documents"},
                        {"id": "bad\ncontrol"},
                        {"description": "missing public identity"},
                    ]
                }
            return {
                "items": [
                    {
                        "name": "photo-organizer",
                        "author": "Echo",
                        "internalState": ["must", "not", "leak"],
                    },
                    {"name": "photo-organizer", "description": "duplicate"},
                    {"name": "x" * 257},
                ]
            }

        def installed_plugins(self) -> list[str]:
            return ["documents", "bad\ncontrol", "x" * 257]

    result = AgentAssetCatalogService(lambda kind: UnsafeCatalog(kind)).catalog()

    assert result["plugins"] == [
        {
            "id": "documents",
            "plugin": "documents",
            "name": "Documents",
            "description": "Public description",
            "permissions": [],
            "authModes": [],
            "dependencies": [],
            "runtimeDependencies": [],
            "connectors": [],
        }
    ]
    assert result["skills"] == [{"name": "photo-organizer", "author": "Echo"}]
    assert result["installed"]["plugins"] == ["documents"]
    assert result["pluginStates"][0]["id"] == "documents"
    serialized = str(result)
    assert "agent-private-token" not in serialized
    assert "agent.sqlite" not in serialized
    assert "internalState" not in serialized


def test_agent_assets_reserves_bounded_catalog_space_for_workbench_apps() -> None:
    class LateWorkbenchCatalog(_Catalog):
        def list(self, **kwargs: object) -> dict:
            if self.kind != "plugins":
                return {"items": []}
            if kwargs.get("kind") == "workbench":
                return {
                    "items": [
                        {
                            "id": "workbench_design",
                            "plugin": "design",
                            "kind": "workbench",
                            "name_zh": "设计画布",
                        }
                    ]
                }
            return {
                "items": [
                    {"id": f"plugin-{index}", "plugin": f"plugin-{index}"} for index in range(80)
                ]
            }

        def installed_plugins(self) -> list[str]:
            return []

        def plugin_statuses(self) -> dict[str, dict]:
            return {}

    result = AgentAssetCatalogService(lambda kind: LateWorkbenchCatalog(kind)).catalog()

    assert len(result["plugins"]) == 80
    assert result["plugins"][0]["plugin"] == "design"
    assert result["plugins"][0]["kind"] == "workbench"


def test_agent_assets_downgrades_malformed_trust_without_losing_lifecycle() -> None:
    class MalformedTrustCatalog(_Catalog):
        def plugin_statuses(self) -> dict[str, dict]:
            state = super().plugin_statuses()["documents"]
            state["trust"] = {
                "level": "publisher",
                "integrity_verified": False,
                "publisher_verified": True,
                "publisher_id": "Do not trust",
            }
            state["compatibility"] = {
                "status": "compatible",
                "host_api": "x" * 161,
            }
            return {"documents": state}

    result = AgentAssetCatalogService(lambda kind: MalformedTrustCatalog(kind)).catalog()

    state = result["pluginStates"][0]
    assert state["state"] == "update_available"
    assert state["trustLevel"] == "unverified"
    assert state["integrityVerified"] is False
    assert state["publisherVerified"] is False
    assert "publisher" not in state
    assert state["compatibility"] == "compatible"
    assert "hostApi" not in state


def test_agent_assets_projects_permission_review_without_private_grant_state() -> None:
    class PermissionReviewCatalog(_Catalog):
        def plugin_statuses(self) -> dict[str, dict]:
            state = super().plugin_statuses()["documents"]
            state.update(
                kind="connector",
                lifecycle_state="disabled",
                enabled=False,
                permissions_granted=[],
                permission_review_required=True,
                permission_active=False,
            )
            return {"documents": state}

    result = AgentAssetCatalogService(lambda kind: PermissionReviewCatalog(kind)).catalog()

    state = result["pluginStates"][0]
    assert state["permissions"] == ["content.read", "content.write"]
    assert state["permissionsGranted"] == []
    assert state["permissionReviewRequired"] is True
    assert state["permissionActive"] is False


def test_agent_assets_router_uses_echo_session_authentication() -> None:
    app = FastAPI()
    service = AgentAssetCatalogService(lambda kind: _Catalog(kind))
    app.include_router(create_agent_assets_router(service, jwt_secret=JWT_SECRET))
    client = TestClient(app)

    assert client.get("/api/appliance/agent-assets/catalog").status_code == 401

    client.cookies.set("echo_session", _token())
    response = client.get("/api/appliance/agent-assets/catalog")

    assert response.status_code == 200
    assert response.json()["installed"]["plugins"] == ["documents"]
