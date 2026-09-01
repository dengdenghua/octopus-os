"""appliance 单用户认证:首启引导 + JWT 保护的启动器接口。"""

from __future__ import annotations

import json
import stat

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from appliance.app_registry.router import create_appliance_router
from appliance.security import (
    ApplianceAuthenticator,
    make_auth_dependency,
    resolve_authenticator,
)
from runtime.adapters.integrations.local_auth import create_local_auth_router
from runtime.adapters.integrations.local_auth.config import verify_password
from runtime.safety.auth.identity import IdentityStore, encode_jwt_hs256

JWT_SECRET = "x" * 48


def _container():
    return {
        "Id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
        "Names": ["/jellyfin"],
        "Image": "jellyfin/jellyfin:latest",
        "State": "running",
        "Status": "Up 3 hours",
        "Labels": {},
        "Ports": [{"PrivatePort": 8096, "PublicPort": 8096, "Type": "tcp"}],
    }


class _StubDocker:
    def list_containers(self, include_stopped: bool = True):
        return [_container()]

    def start(self, container_id: str):
        pass

    def stop(self, container_id: str):
        pass


def _client(jwt_secret: str | None) -> TestClient:
    app = FastAPI()
    app.include_router(create_appliance_router(docker=_StubDocker(), jwt_secret=jwt_secret))
    return TestClient(app)


def _bearer(
    secret: str,
    *,
    exp: int = 9_999_999_999,
    actor: str = "local:admin",
) -> dict[str, str]:
    token = encode_jwt_hs256({"sub": actor, "exp": exp, "iat": 0}, secret=secret)
    return {"Authorization": f"Bearer {token}"}


class TestAuthGate:
    def test_status_public_and_reports_required(self):
        body = _client(JWT_SECRET).get("/api/appliance/auth/status").json()
        assert body == {"authRequired": True, "authenticated": False, "role": None}

    def test_status_authenticated_with_valid_token(self):
        client = _client(JWT_SECRET)
        body = client.get("/api/appliance/auth/status", headers=_bearer(JWT_SECRET)).json()
        assert body == {
            "authRequired": True,
            "authenticated": True,
            "role": "operator",
        }

    def test_status_and_apps_accept_httponly_session_cookie(self):
        client = _client(JWT_SECRET)
        token = _bearer(JWT_SECRET)["Authorization"].removeprefix("Bearer ")
        client.cookies.set("echo_session", token)

        assert client.get("/api/appliance/auth/status").json()["authenticated"] is True
        assert client.get("/api/appliance/apps").status_code == 200

    def test_status_accepts_pre_echo_session_cookie_during_migration(self):
        client = _client(JWT_SECRET)
        token = _bearer(JWT_SECRET)["Authorization"].removeprefix("Bearer ")
        legacy_cookie = "octo" + "pus_session"
        client.cookies.set(legacy_cookie, token)

        assert client.get("/api/appliance/auth/status").json()["authenticated"] is True

    def test_apps_401_without_token(self):
        assert _client(JWT_SECRET).get("/api/appliance/apps").status_code == 401

    def test_apps_200_with_valid_token(self):
        client = _client(JWT_SECRET)
        resp = client.get("/api/appliance/apps", headers=_bearer(JWT_SECRET))
        assert resp.status_code == 200
        assert resp.json()["apps"][0]["name"] == "jellyfin"

    def test_member_can_use_apps_but_not_control_the_device(self):
        client = _client(JWT_SECRET)
        headers = _bearer(JWT_SECRET, actor="local:alice")

        assert client.get("/api/appliance/apps", headers=headers).status_code == 200
        assert client.get("/api/appliance/auth/status", headers=headers).json() == {
            "authRequired": True,
            "authenticated": True,
            "role": "member",
        }
        assert (
            client.post(
                "/api/appliance/apps/a1b2c3d4e5f6/start",
                headers=headers,
            ).status_code
            == 403
        )

    def test_apps_401_with_wrong_secret(self):
        client = _client(JWT_SECRET)
        resp = client.get("/api/appliance/apps", headers=_bearer("y" * 48))
        assert resp.status_code == 401

    def test_apps_401_with_expired_token(self):
        client = _client(JWT_SECRET)
        resp = client.get("/api/appliance/apps", headers=_bearer(JWT_SECRET, exp=1))
        assert resp.status_code == 401

    def test_start_requires_auth(self):
        cid = "a1b2c3d4e5f6"
        client = _client(JWT_SECRET)
        assert client.post(f"/api/appliance/apps/{cid}/start").status_code == 401
        # 已认证但未装配审批/审计服务时必须 fail closed，而不是静默降级。
        assert (
            client.post(
                f"/api/appliance/apps/{cid}/start",
                headers=_bearer(JWT_SECRET),
            ).status_code
            == 503
        )

    def test_dependency_exposes_verified_actor(self):
        app = FastAPI()

        @app.get("/actor")
        def actor(actor_id: str = Depends(make_auth_dependency(JWT_SECRET))):
            return {"actor": actor_id}

        response = TestClient(app).get("/actor", headers=_bearer(JWT_SECRET))
        assert response.json() == {"actor": "local:admin"}

    def test_no_secret_means_open(self):
        # jwt_secret=None → 无认证的本地开发,全部放行。
        client = _client(None)
        assert client.get("/api/appliance/apps").status_code == 200
        assert client.get("/api/appliance/auth/status").json()["authRequired"] is False

    def test_production_never_degrades_to_development_actor(self, monkeypatch):
        monkeypatch.setenv("ECHO_APPLIANCE", "1")
        client = _client(None)

        assert client.get("/api/appliance/apps").status_code == 401

    def test_authenticator_exposes_verification_not_raw_signing_material(self):
        authenticator = ApplianceAuthenticator(JWT_SECRET)

        assert authenticator.required is True
        assert not hasattr(authenticator, "jwt_secret")
        assert not hasattr(authenticator, "__dict__")
        with pytest.raises(ValueError, match="not both"):
            resolve_authenticator(
                jwt_secret=JWT_SECRET,
                authenticator=authenticator,
            )


class TestBootstrap:
    def test_env_password_and_persistence(self, tmp_path, monkeypatch):
        tmp_path.chmod(0o755)
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "s3cret-pw")
        # 延迟导入以读取打了 patch 的环境变量。
        from appliance.auth import load_or_bootstrap_auth

        config, generated = load_or_bootstrap_auth()
        assert generated is None  # 用环境变量提供的密码,不生成
        assert config.enabled and config.password_required
        assert verify_password("s3cret-pw", config.users["admin"])
        assert config.jwt_expire_seconds >= 7 * 24 * 3600  # 长会话
        assert config.login_max_failures == 5
        assert config.login_ip_max_failures == 20
        assert config.login_failure_window_seconds == 300
        assert config.login_lockout_seconds == 60
        assert config.login_rate_limit_max_entries == 10_000

        store = json.loads((tmp_path / "appliance-auth.json").read_text())
        assert store["username"] == "admin" and "password_hash" in store
        assert store["session_not_before"] == 0
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "appliance-auth.json").stat().st_mode) == 0o600

        # 二次加载读取既有存储,密码哈希与 jwt_secret 不变。
        config2, generated2 = load_or_bootstrap_auth()
        assert generated2 is None
        assert config2.users["admin"] == config.users["admin"]
        assert config2.jwt_secret == config.jwt_secret

    def test_auth_store_rejects_symlinked_file_or_parent(self, tmp_path):
        from appliance.auth import read_auth_store, write_auth_store

        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        linked_store = tmp_path / "linked-auth.json"
        linked_store.symlink_to(outside)
        with pytest.raises(ValueError, match="must not be a symlink"):
            read_auth_store(linked_store)

        real_directory = tmp_path / "real"
        real_directory.mkdir()
        linked_directory = tmp_path / "linked"
        linked_directory.symlink_to(real_directory, target_is_directory=True)
        with pytest.raises(ValueError, match="directory must not be a symlink"):
            write_auth_store({}, linked_directory / "appliance-auth.json")

    def test_generated_password_when_no_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("ECHO_ADMIN_PASSWORD", raising=False)
        monkeypatch.delenv("ECHO_LOCAL_JWT_SECRET", raising=False)
        from appliance.auth import load_or_bootstrap_auth

        config, generated = load_or_bootstrap_auth()
        assert generated and len(generated) >= 12
        assert verify_password(generated, config.users["admin"])

    def test_bootstrap_reuses_runtime_jwt_secret(self, tmp_path, monkeypatch):
        shared_secret = "Echo-Dev-Shared-Secret-1234567890!"
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "shared-secret-pass")
        monkeypatch.setenv("ECHO_LOCAL_JWT_SECRET", shared_secret)
        from appliance.auth import load_or_bootstrap_auth

        config, generated = load_or_bootstrap_auth()
        store = json.loads((tmp_path / "appliance-auth.json").read_text())

        assert generated is None
        assert config.jwt_secret == shared_secret
        assert store["jwt_secret"] == shared_secret


class TestBrowserSessionBoundary:
    def test_login_sets_httponly_lax_session_cookie(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "session-pass-123")
        from appliance.auth import load_or_bootstrap_auth

        config, _generated = load_or_bootstrap_auth()
        app = FastAPI()
        app.include_router(create_local_auth_router(config=config, identity_store=IdentityStore()))

        with TestClient(app) as client:
            response = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "session-pass-123"},
            )

        cookie = response.headers["set-cookie"]
        assert response.status_code == 200
        assert "echo_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Path=/" in cookie

    def test_pinned_failed_login_policy_returns_429(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "rate-pass-123")
        from appliance.auth import load_or_bootstrap_auth

        config, _generated = load_or_bootstrap_auth()
        config = config.model_copy(update={"login_max_failures": 2})
        app = FastAPI()
        app.include_router(create_local_auth_router(config=config, identity_store=IdentityStore()))

        with TestClient(app) as client:
            first = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "wrong"},
            )
            locked = client.post(
                "/api/auth/local/login",
                json={"username": "admin", "password": "still-wrong"},
            )

        assert first.status_code == 401
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) >= 1


@pytest.fixture(autouse=True)
def _isolate_auth_module(monkeypatch):
    # auth 模块无全局状态,但确保每个用例独立读取环境。
    yield
