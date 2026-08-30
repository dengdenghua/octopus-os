"""P0 契约:appliance 经 agent 官方扩展点挂载(ECHO_APP_EXTENSIONS),不再 fork app.py。

守护「消费而非 fork」:appliance.extension:register_app 在 ECHO_APPLIANCE=1 时
把启动器/认证/文件路由挂到传入的 app 上;关掉则 no-op。见 appliance/extension.py。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from appliance.extension import register_app
from appliance.state_lock import StateDirectoryLock, StateLockError
from appliance.state_schema import CURRENT_SCHEMA_VERSION
from appliance.web_security import ApplianceWebSecurityMiddleware
from runtime.platform.extensions import AppExtensionContext


def test_extension_passes_one_authenticator_to_routes_instead_of_raw_jwt() -> None:
    path = Path("appliance/extension.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    router_calls = {
        "create_account_security_router",
        "create_agent_capabilities_router",
        "create_agent_assets_router",
        "create_appliance_router",
        "create_approval_router",
        "create_audit_router",
        "create_capabilities_router",
        "create_device_link_router",
        "create_device_sync_router",
        "create_files_router",
        "create_hub_router",
        "create_omv_router",
        "create_photos_router",
        "create_task_projection_router",
    }
    observed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in router_calls:
            continue
        observed.add(node.func.id)
        keywords = {keyword.arg for keyword in node.keywords}
        assert "authenticator" in keywords
        assert "jwt_secret" not in keywords
        if node.func.id in {"create_files_router", "create_photos_router"}:
            assert "data_access" in keywords

    assert observed == router_calls


def _routes(app: FastAPI) -> list[str]:
    paths: list[str] = []
    for route in app.router.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.append(path)
        original = getattr(route, "original_router", None)
        if original is not None:
            paths.extend(getattr(nested, "path", "") for nested in original.routes)
    return paths


def test_register_app_mounts_appliance_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "test-pass-123456")
    nas_root = tmp_path / "nas"
    nas_root.mkdir()  # 真实部署里是已挂载的存储卷
    monkeypatch.setenv("ECHO_NAS_ROOT", str(nas_root))

    app = FastAPI()
    register_app(app, AppExtensionContext(identity_store=None))

    paths = _routes(app)
    assert any(p.startswith("/api/appliance/apps") for p in paths), "启动器路由应挂载"
    assert any("/api/auth/local" in p for p in paths), "本地认证路由应挂载"
    assert any("/api/appliance/files" in p for p in paths), "文件管理器路由应挂载"
    assert any("/api/appliance/photos" in p for p in paths), "照片路由应挂载"
    assert any("/api/appliance/approvals" in p for p in paths), "高风险审批路由应挂载"
    assert any("/api/appliance/audit" in p for p in paths), "防篡改审计路由应挂载"
    assert any("/api/appliance/omv" in p for p in paths), "OMV 只读路由应挂载"
    assert any("/api/appliance/capabilities" in p for p in paths), "能力契约路由应挂载"
    assert any("/api/appliance/tasks" in p for p in paths), "任务投影路由应挂载"
    assert app.state.echo_appliance_audit is not None
    assert app.state.echo_appliance_approval is not None
    assert app.state.echo_appliance_omv_health is not None
    assert app.state.echo_remote_access is not None
    assert app.state.echo_remote_access.configured is False
    assert app.state.echo_remote_access.start in app.router.on_startup
    assert app.state.echo_remote_access.stop in app.router.on_shutdown
    assert app.state.echo_photo_service is not None
    assert app.state.echo_family_data_access._root == nas_root.resolve()
    assert len(app.state.echo_appliance_capabilities) == 26
    assert app.state.echo_appliance_omv_health.running is False
    assert app.state.echo_appliance_omv_health.start in app.router.on_startup
    assert app.state.echo_appliance_omv_health.stop in app.router.on_shutdown
    assert app.state.echo_appliance_state_lock is not None
    assert app.state.echo_appliance_state_schema["version"] == CURRENT_SCHEMA_VERSION
    assert (tmp_path / "echo-state-schema.json").is_file()
    with pytest.raises(StateLockError, match="already in use"):
        StateDirectoryLock.acquire(tmp_path, exclusive=True)
    assert (
        sum(middleware.cls is ApplianceWebSecurityMiddleware for middleware in app.user_middleware)
        == 1
    )


def test_register_app_is_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))

    app = FastAPI()
    before = len(_routes(app))
    register_app(app, AppExtensionContext(identity_store=None))
    # ECHO_APPLIANCE 未开 → 不挂任何 appliance 路由(语义同原 fork 块)。
    assert len(_routes(app)) == before
    assert all(
        middleware.cls is not ApplianceWebSecurityMiddleware for middleware in app.user_middleware
    )


def test_register_app_reuses_agent_local_auth_route(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "test-pass-123456")
    nas_root = tmp_path / "nas"
    nas_root.mkdir()
    monkeypatch.setenv("ECHO_NAS_ROOT", str(nas_root))
    app = FastAPI()
    agent_auth = APIRouter(prefix="/api/auth/local")

    @agent_auth.post("/login")
    def _agent_login():
        return {"source": "agent"}

    app.include_router(agent_auth)
    register_app(app, AppExtensionContext(identity_store=None))

    assert _routes(app).count("/api/auth/local/login") == 1


def test_local_dev_passwordless_login_accepts_only_provisioned_accounts(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_APPLIANCE_DEV_PASSWORDLESS", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "test-pass-123456")
    monkeypatch.setenv(
        "ECHO_LOCAL_JWT_SECRET",
        "Echo-Dev-Shared-Secret-1234567890!",
    )
    nas_root = tmp_path / "nas"
    nas_root.mkdir()
    monkeypatch.setenv("ECHO_NAS_ROOT", str(nas_root))
    app = FastAPI()
    register_app(app, AppExtensionContext(identity_store=None))

    with TestClient(app) as client:
        admin = client.post(
            "/api/auth/local/login",
            json={"username": "admin"},
            headers={"Origin": "http://testserver"},
        )
        unknown = client.post(
            "/api/auth/local/login",
            json={"username": "not-provisioned"},
            headers={"Origin": "http://testserver"},
        )

    assert admin.status_code == 200
    assert admin.json()["success"] is True
    assert unknown.status_code == 403


def test_registered_appliance_rejects_cross_origin_and_rebound_requests(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "browser-boundary-pass")
    nas_root = tmp_path / "nas"
    nas_root.mkdir()
    monkeypatch.setenv("ECHO_NAS_ROOT", str(nas_root))
    app = FastAPI()
    register_app(app, AppExtensionContext(identity_store=None))

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/local/login",
            json={"username": "admin", "password": "browser-boundary-pass"},
            headers={"Origin": "http://testserver"},
        )
        cross_origin = client.post(
            f"/api/appliance/apps/{'a' * 12}/start",
            headers={"Origin": "https://attacker.invalid"},
        )
        rebound = client.get(
            "/api/appliance/config",
            headers={"Host": "rebind.attacker.invalid"},
        )

    assert login.status_code == 200
    assert cross_origin.status_code == 403
    assert rebound.status_code == 400


def test_registered_appliance_serves_echo_desktop_at_root_inside_security_boundary(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "desktop-root-pass")
    nas_root = tmp_path / "nas"
    nas_root.mkdir()
    monkeypatch.setenv("ECHO_NAS_ROOT", str(nas_root))
    dist = tmp_path / "webui"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Echo OS</title>")
    monkeypatch.setenv("ECHO_WEBUI_DIST", str(dist))

    app = FastAPI()

    @app.get("/")
    def agent_dashboard():
        return {"surface": "agent-dashboard"}

    register_app(app, AppExtensionContext(identity_store=None))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.text == "<!doctype html><title>Echo OS</title>"
    assert response.headers["cache-control"] == "no-cache"
    assert "default-src 'self'" in response.headers["content-security-policy"]
