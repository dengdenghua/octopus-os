"""Tests for ``GET /api/smart-routing`` config endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.sensing.gateway.config_router import create_config_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_config_router(custom_models_path=None).router)
    return app


def test_smart_routing_endpoint_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_SMART_ROUTING", raising=False)
    monkeypatch.setenv("ECHO_MODEL_LOCAL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("ECHO_MODEL_VALUE", "glm-4-flash")
    monkeypatch.setenv("ECHO_MODEL_PERFORMANCE", "claude-sonnet-4")

    client = TestClient(_make_app())
    r = client.get("/api/smart-routing")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["tiers"] == {
        "local": "ollama/qwen2.5:7b",
        "value": "glm-4-flash",
        "performance": "claude-sonnet-4",
    }
    assert body["env_keys"]["local"] == "ECHO_MODEL_LOCAL"
    assert body["env_keys"]["value"] == "ECHO_MODEL_VALUE"
    assert body["env_keys"]["performance"] == "ECHO_MODEL_PERFORMANCE"
    assert body["kill_switch_env"] == "ECHO_SMART_ROUTING"


def test_smart_routing_endpoint_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_SMART_ROUTING", "off")
    client = TestClient(_make_app())
    r = client.get("/api/smart-routing")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_smart_routing_endpoint_partial_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local unconfigured → null; value has built-in default."""
    monkeypatch.delenv("ECHO_MODEL_LOCAL", raising=False)
    monkeypatch.delenv("ECHO_MODEL_PERFORMANCE", raising=False)
    monkeypatch.delenv("ECHO_SMART_ROUTING_CHEAP_MODEL", raising=False)
    monkeypatch.delenv("ECHO_SUBAGENT_CHEAP_MODEL", raising=False)
    monkeypatch.delenv("ECHO_MODEL_VALUE", raising=False)
    # Disable auto-derivation from custom_models.json so the host's
    # imported model entries (e.g. mimo) don't leak into this test.
    monkeypatch.setattr(
        "runtime.core.cerebrum.turn_complexity._auto_derive_tier_from_custom_models",
        lambda tier: None,
    )

    client = TestClient(_make_app())
    body = client.get("/api/smart-routing").json()
    assert body["tiers"]["local"] is None
    assert body["tiers"]["value"] == "glm-4-flash"  # built-in default
    assert body["tiers"]["performance"] is None
