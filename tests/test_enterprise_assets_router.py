"""数字分身归并 C · 消费侧:agent 列举企业版角色资产(只读、门控、优雅降级)。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.sensing.gateway.enterprise_assets_router import (
    create_enterprise_assets_router,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_enterprise_assets_router())
    return TestClient(app)


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


def test_unconfigured_available_false(monkeypatch):
    monkeypatch.delenv("ECHO_ENTERPRISE_URL", raising=False)
    body = _client().get("/api/agent-market/enterprise").json()
    assert body["available"] is False
    assert body["items"] == []


def test_list_unwraps_enterprise_envelope(monkeypatch):
    monkeypatch.setenv("ECHO_ENTERPRISE_URL", "http://ent:8000")
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _FakeResp({"success": True, "data": [{"id": "a", "name": "A"}], "total": 1})

    monkeypatch.setattr(httpx, "get", fake_get)
    body = _client().get("/api/agent-market/enterprise?category=coder&search=test").json()
    assert body["available"] is True
    assert body["items"] == [{"id": "a", "name": "A"}]
    assert captured["url"] == "http://ent:8000/api/v1/agent-assets"
    assert captured["params"] == {"category": "coder", "search": "test"}


def test_get_asset_unwraps_body(monkeypatch):
    monkeypatch.setenv("ECHO_ENTERPRISE_URL", "http://ent:8000")

    def fake_get(url, **kwargs):
        return _FakeResp({"success": True, "data": {"id": "a", "name": "A", "body": "soul"}})

    monkeypatch.setattr(httpx, "get", fake_get)
    body = _client().get("/api/agent-market/enterprise/a").json()
    assert body["available"] is True
    assert body["asset"]["body"] == "soul"


def test_get_asset_rejects_unsafe_asset_id_before_http(monkeypatch):
    monkeypatch.setenv("ECHO_ENTERPRISE_URL", "http://ent:8000")
    called = False

    def fake_get(url, **kwargs):
        nonlocal called
        called = True
        return _FakeResp({})

    monkeypatch.setattr(httpx, "get", fake_get)

    resp = _client().get("/api/agent-market/enterprise/bad:secret")

    assert resp.status_code == 400
    assert called is False


def test_get_asset_allows_safe_asset_id_variants(monkeypatch):
    monkeypatch.setenv("ECHO_ENTERPRISE_URL", "http://ent:8000")
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _FakeResp(
            {
                "success": True,
                "data": {"id": "coder.v2-prod_1", "name": "Coder"},
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    body = _client().get("/api/agent-market/enterprise/coder.v2-prod_1").json()

    assert body["asset"]["id"] == "coder.v2-prod_1"
    assert captured["url"] == "http://ent:8000/api/v1/agent-assets/coder.v2-prod_1"


def test_network_error_graceful(monkeypatch):
    monkeypatch.setenv("ECHO_ENTERPRISE_URL", "http://ent:8000")

    def fake_get(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(httpx, "get", fake_get)
    body = _client().get("/api/agent-market/enterprise").json()
    # 网络错:仍 available(已配),但带 error、items 空——前端不崩。
    assert body["available"] is True
    assert "network down" in (body["error"] or "")
    assert body["items"] == []


def test_scaffold_writes_minimal_agent(monkeypatch, tmp_path):
    import runtime.execution.agents.loader as loader

    monkeypatch.setattr(loader, "default_agents_root", lambda: tmp_path)
    from runtime.sensing.gateway.enterprise_assets_router import _scaffold_local_agent

    agent_id, agent_root = _scaffold_local_agent(
        {
            "id": "academic-anthropologist",  # 连字符 → 下划线
            "name": "Anthropologist",
            "description": "studies culture",
            "category": "researcher",
            "tags": ["culture", "research"],
            "icon": "🔬",
            "body": "You are an anthropologist. Observe cultures carefully.",
        }
    )
    assert agent_id == "academic_anthropologist"
    assert (agent_root / "profile.jsonc").is_file()
    soul = (agent_root / "agent-core" / "SOUL.md").read_text(encoding="utf-8")
    assert "Observe cultures" in soul  # persona body 进了 SOUL
    assert (agent_root / "agent-core" / "IDENTITY.md").is_file()
    assert (agent_root / "agent-core" / "tool-registry.jsonc").is_file()


def test_scaffold_rejects_symlink_agent_root(monkeypatch, tmp_path):
    import runtime.execution.agents.loader as loader

    monkeypatch.setattr(loader, "default_agents_root", lambda: tmp_path / "agents")
    from runtime.sensing.gateway.enterprise_assets_router import _scaffold_local_agent

    outside = tmp_path / "outside"
    outside.mkdir()
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "coder").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        _scaffold_local_agent({"id": "coder", "name": "Coder", "body": "You code."})

    assert not (outside / "profile.jsonc").exists()


def test_scaffold_rejects_symlink_agent_core(monkeypatch, tmp_path):
    import runtime.execution.agents.loader as loader

    monkeypatch.setattr(loader, "default_agents_root", lambda: tmp_path / "agents")
    from runtime.sensing.gateway.enterprise_assets_router import _scaffold_local_agent

    outside = tmp_path / "outside"
    outside.mkdir()
    agent_root = tmp_path / "agents" / "coder"
    agent_root.mkdir(parents=True)
    (agent_root / "agent-core").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        _scaffold_local_agent({"id": "coder", "name": "Coder", "body": "You code."})

    assert not (outside / "SOUL.md").exists()


def test_scaffold_cleans_temp_file_when_atomic_write_fails(monkeypatch, tmp_path):
    import runtime.execution.agents.loader as loader

    monkeypatch.setattr(loader, "default_agents_root", lambda: tmp_path / "agents")
    from runtime.sensing.gateway.enterprise_assets_router import _scaffold_local_agent

    original_replace = Path.replace

    def fail_profile_replace(self: Path, target: Path) -> Path:
        if self.name.startswith(".profile.jsonc.") and target.name == "profile.jsonc":
            raise OSError("replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_profile_replace)

    with pytest.raises(OSError, match="replace failed"):
        _scaffold_local_agent({"id": "coder", "name": "Coder", "body": "You code."})

    agent_root = tmp_path / "agents" / "coder"
    assert list(agent_root.glob(".profile.jsonc.*")) == []


def test_install_endpoint_scaffolds(monkeypatch, tmp_path):
    import runtime.execution.agents.loader as loader

    monkeypatch.setattr(loader, "default_agents_root", lambda: tmp_path)
    monkeypatch.setenv("ECHO_ENTERPRISE_URL", "http://ent:8000")

    def fake_get(url, **kwargs):
        return _FakeResp(
            {
                "success": True,
                "data": {
                    "id": "coder",
                    "name": "Coder",
                    "description": "codes",
                    "category": "coder",
                    "tags": ["code"],
                    "icon": "💻",
                    "body": "You code.",
                },
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    # registry/runtime=None(_client 不传)→ 只 scaffold,不 load,仍返回 installed。
    body = _client().post("/api/agent-market/enterprise/coder/install").json()
    assert body["installed"] is True
    assert body["agent_id"] == "coder"
    assert (tmp_path / "coder" / "agent-core" / "SOUL.md").read_text(
        encoding="utf-8"
    ) == "You code."


def test_install_rejects_unsafe_asset_id_before_http(monkeypatch, tmp_path):
    import runtime.execution.agents.loader as loader

    monkeypatch.setattr(loader, "default_agents_root", lambda: tmp_path)
    monkeypatch.setenv("ECHO_ENTERPRISE_URL", "http://ent:8000")
    called = False

    def fake_get(url, **kwargs):
        nonlocal called
        called = True
        return _FakeResp({})

    monkeypatch.setattr(httpx, "get", fake_get)

    resp = _client().post("/api/agent-market/enterprise/bad:secret/install")

    assert resp.status_code == 400
    assert called is False
    assert not any(tmp_path.iterdir())

