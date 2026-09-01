"""The echo-mix virtual model is advertised on /api/llm-models so the
in-app ModelPicker (which reads that endpoint, not /api/models) can offer it."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.sensing.gateway.config_router import create_config_router  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_config_router(custom_models_path=None).router)
    return TestClient(app)


def test_llm_models_advertises_echo_mix() -> None:
    r = _client().get("/api/llm-models")
    assert r.status_code == 200
    models = r.json()["models"]
    by_id = {m["id"]: m for m in models}
    assert "echo-mix" in by_id
    entry = by_id["echo-mix"]
    assert entry["provider"] == "echo"
    assert entry["supports_tool_use"] is True
    # listed first — the echo-native flagship leads the catalog
    assert models[0]["id"] == "echo-mix"

