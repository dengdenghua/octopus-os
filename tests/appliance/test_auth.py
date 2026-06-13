"""appliance 单用户认证:首启引导 + JWT 保护的启动器接口。"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.app_registry.router import create_appliance_router
from runtime.adapters.integrations.local_auth.config import verify_password
from runtime.safety.auth.identity import encode_jwt_hs256

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
    app.include_router(
        create_appliance_router(docker=_StubDocker(), jwt_secret=jwt_secret)
    )
    return TestClient(app)


def _bearer(secret: str, *, exp: int = 9_999_999_999) -> dict[str, str]:
    token = encode_jwt_hs256(
        {"sub": "local:admin", "exp": exp, "iat": 0}, secret=secret
    )
    return {"Authorization": f"Bearer {token}"}


class TestAuthGate:
    def test_status_public_and_reports_required(self):
        body = _client(JWT_SECRET).get("/api/appliance/auth/status").json()
        assert body == {"authRequired": True, "authenticated": False}

    def test_status_authenticated_with_valid_token(self):
        client = _client(JWT_SECRET)
        body = client.get(
            "/api/appliance/auth/status", headers=_bearer(JWT_SECRET)
        ).json()
        assert body == {"authRequired": True, "authenticated": True}

    def test_apps_401_without_token(self):
        assert _client(JWT_SECRET).get("/api/appliance/apps").status_code == 401

    def test_apps_200_with_valid_token(self):
        client = _client(JWT_SECRET)
        resp = client.get("/api/appliance/apps", headers=_bearer(JWT_SECRET))
        assert resp.status_code == 200
        assert resp.json()["apps"][0]["name"] == "jellyfin"

    def test_apps_401_with_wrong_secret(self):
        client = _client(JWT_SECRET)
        resp = client.get("/api/appliance/apps", headers=_bearer("y" * 48))
        assert resp.status_code == 401

    def test_apps_401_with_expired_token(self):
        client = _client(JWT_SECRET)
        resp = client.get(
            "/api/appliance/apps", headers=_bearer(JWT_SECRET, exp=1)
        )
        assert resp.status_code == 401

    def test_start_requires_auth(self):
        cid = "a1b2c3d4e5f6"
        client = _client(JWT_SECRET)
        assert client.post(f"/api/appliance/apps/{cid}/start").status_code == 401
        assert (
            client.post(
                f"/api/appliance/apps/{cid}/start", headers=_bearer(JWT_SECRET)
            ).status_code
            == 200
        )

    def test_no_secret_means_open(self):
        # jwt_secret=None → 无认证的本地开发,全部放行。
        client = _client(None)
        assert client.get("/api/appliance/apps").status_code == 200
        assert (
            client.get("/api/appliance/auth/status").json()["authRequired"] is False
        )


class TestBootstrap:
    def test_env_password_and_persistence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OCTOPUS_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("OCTOPUS_ADMIN_PASSWORD", "s3cret-pw")
        # 延迟导入以读取打了 patch 的环境变量。
        from appliance.auth import load_or_bootstrap_auth

        config, generated = load_or_bootstrap_auth()
        assert generated is None  # 用环境变量提供的密码,不生成
        assert config.enabled and config.password_required
        assert verify_password("s3cret-pw", config.users["admin"])
        assert config.jwt_expire_seconds >= 7 * 24 * 3600  # 长会话

        store = json.loads((tmp_path / "appliance-auth.json").read_text())
        assert store["username"] == "admin" and "password_hash" in store

        # 二次加载读取既有存储,密码哈希与 jwt_secret 不变。
        config2, generated2 = load_or_bootstrap_auth()
        assert generated2 is None
        assert config2.users["admin"] == config.users["admin"]
        assert config2.jwt_secret == config.jwt_secret

    def test_generated_password_when_no_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OCTOPUS_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("OCTOPUS_ADMIN_PASSWORD", raising=False)
        from appliance.auth import load_or_bootstrap_auth

        config, generated = load_or_bootstrap_auth()
        assert generated and len(generated) >= 12
        assert verify_password(generated, config.users["admin"])


@pytest.fixture(autouse=True)
def _isolate_auth_module(monkeypatch):
    # auth 模块无全局状态,但确保每个用例独立读取环境。
    yield
