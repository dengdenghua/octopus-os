"""Synthetic local setup, transport boundaries and durable activation."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.sensing.gateway._config_endpoints_local_setup import register_verified_local_model
from runtime.sensing.model_router import hwfit
from runtime.sensing.model_router import local_model_setup as setup


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(setup, "_verified", {})
    monkeypatch.setattr(hwfit, "_pull_state", {})
    monkeypatch.setattr(
        hwfit, "detect_hardware", lambda: hwfit.Hardware("cpu", None, 8, 16, None, False)
    )
    state = {"digest": "abc", "text": "ECHO_READY", "tools": True, "vision": "red", "remote": False}
    calls = []

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        calls.append((path, body))
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [{"name": "fixture:1b", "digest": state["digest"], "size": 1024**3}]
                },
            )
        if path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "model_info": {"layers": 12},
                    "details": {"format": "gguf"},
                    "capabilities": ["vision", "tools"],
                    "remote_host": "https://cloud.invalid" if state["remote"] else "",
                },
            )
        if path == "/v1/chat/completions":
            if "tools" in body:
                message = {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "echo_check",
                                "arguments": '{"value":7}' if state["tools"] else "malformed",
                            }
                        }
                    ]
                }
            elif isinstance(body["messages"][0]["content"], list):
                message = {"content": state["vision"]}
            else:
                message = {"content": state["text"]}
            return httpx.Response(200, json={"choices": [{"message": message}]})
        return httpx.Response(404)

    def factory(**kwargs):
        return httpx.Client(
            base_url=kwargs.get("base") or setup.base_url(),
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

    monkeypatch.setattr(setup, "client", factory)
    return state, calls


def test_verifies_actual_text_tools_and_image_without_user_content(service):
    _, calls = service
    result = setup.verify_model("fixture:1b")
    assert result["supports_tool_use"] is True
    assert result["supports_vision"] is True
    assert setup.verified_model("fixture:1b")["digest"] == "abc"
    assert sum(path == "/v1/chat/completions" for path, _ in calls) == 3
    assert all(body.get("messages", [{}])[0].get("role", "user") == "user" for _, body in calls)


def test_optional_capabilities_are_not_assumed(service):
    state, _ = service
    state.update(tools=False, vision="blue")
    result = setup.verify_model("fixture:1b")
    assert result["supports_tool_use"] is False
    assert result["supports_vision"] is False


def test_failed_text_is_not_activatable(service):
    state, _ = service
    state["text"] = ""
    with pytest.raises(ValueError, match="文本"):
        setup.verify_model("fixture:1b")
    with pytest.raises(ValueError, match="验证"):
        setup.verified_model("fixture:1b")


def test_memory_rechecked_before_loading_downloaded_weights(service, monkeypatch):
    _, calls = service
    monkeypatch.setattr(
        hwfit, "detect_hardware", lambda: hwfit.Hardware("cpu", None, 0, 16, None, False)
    )
    with pytest.raises(ValueError, match="内存不足"):
        setup.verify_model("fixture:1b")
    assert not any(path == "/v1/chat/completions" for path, _ in calls)


def test_cloud_alias_is_rejected_before_inference(service):
    state, calls = service
    state["remote"] = True
    with pytest.raises(ValueError, match="远端"):
        setup.verify_model("fixture:1b")
    assert not any(path == "/v1/chat/completions" for path, _ in calls)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://cloud.invalid",
        "http://10.0.0.1:11434",
        "http://localhost:11434/v1",
        "http://user:secret@localhost:11434",
    ],
)
def test_remote_and_credential_endpoints_rejected(monkeypatch, endpoint):
    monkeypatch.setenv("OLLAMA_BASE_URL", endpoint)
    with pytest.raises(ValueError):
        setup.client()


def test_client_bypasses_proxy_and_redirects(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    factory = Mock()
    monkeypatch.setattr(setup.httpx, "Client", factory)
    setup.client()
    assert factory.call_args.kwargs["trust_env"] is False
    assert factory.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize(
    "status,payload",
    [(503, {"models": []}), (200, {}), (200, {"models": "oops"}), (200, {"models": [42]})],
)
def test_availability_requires_a_valid_catalog(monkeypatch, status, payload):
    monkeypatch.setattr(
        setup,
        "client",
        lambda: httpx.Client(
            base_url="http://127.0.0.1",
            transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
        ),
    )
    assert hwfit.ollama_available() is False
    assert hwfit.installed_models() == set()


def test_empty_but_valid_ollama_is_available(monkeypatch):
    monkeypatch.setattr(
        setup,
        "client",
        lambda: httpx.Client(
            base_url="http://127.0.0.1",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []})),
        ),
    )
    assert hwfit.ollama_available() is True


def test_changed_or_expired_verification_cannot_activate(service):
    state, _ = service
    setup.verify_model("fixture:1b")
    state["digest"] = "changed"
    with pytest.raises(ValueError, match="版本"):
        setup.verified_model("fixture:1b")
    setup.verify_model("fixture:1b")
    setup._verified["fixture:1b"]["verified_at"] = time.time() - 3600
    with pytest.raises(ValueError, match="过期"):
        setup.verified_model("fixture:1b")


def test_pull_pipeline_and_failure(service, monkeypatch):
    download = Mock(return_value={"status": "ok"})
    monkeypatch.setattr(hwfit, "pull_model", download)
    hwfit._pull_worker("fixture:1b", setup.base_url(), True)
    assert hwfit.pull_states()["fixture:1b"] == "ready"
    download.return_value = {"status": "error", "error": "download failed"}
    verify = Mock()
    monkeypatch.setattr(setup, "verify_model", verify)
    hwfit._pull_worker("fixture:1b", setup.base_url(), True)
    assert hwfit.pull_states()["fixture:1b"].startswith("error:")
    verify.assert_not_called()


def test_only_one_model_is_prepared_at_a_time(service):
    hwfit._pull_state["other:1b"] = "verifying"
    assert hwfit.start_verify("fixture:1b")["status"] == "error"


def _activation_client():
    ctx = SimpleNamespace(
        require_admin=lambda: None,
        serialize_custom_models=lambda fn: fn,
        custom_models={},
        register=Mock(return_value={"ok": True}),
        unregister_entry=Mock(),
        save=Mock(),
    )
    app = FastAPI()
    register_verified_local_model(app, ctx)
    return TestClient(app), ctx


def test_activation_persists_only_verified_capabilities(service):
    state, _ = service
    state["tools"] = False
    setup.verify_model("fixture:1b")
    client, ctx = _activation_client()
    response = client.post(
        "/api/config/local-models/activate", json={"tag": "fixture:1b", "supports_tool_use": True}
    )
    assert response.status_code == 200
    entry = ctx.custom_models[response.json()["model_id"]]
    assert entry["supports_tool_use"] is False
    assert response.json()["selection_id"].startswith("echo-custom-model:v1:")
    ctx.save.assert_called_once_with(entry["id"], strict=True)


@pytest.mark.parametrize("failure", ["save", "route"])
def test_failed_activation_rolls_back_config(service, failure):
    setup.verify_model("fixture:1b")
    client, ctx = _activation_client()
    ctx.custom_models["existing"] = {"models": ["old"]}
    if failure == "save":
        ctx.save.side_effect = OSError("disk full")
    else:
        ctx.register.return_value = {"ok": False}
    response = client.post("/api/config/local-models/activate", json={"tag": "fixture:1b"})
    assert response.status_code == 503
    assert ctx.custom_models == {"existing": {"models": ["old"]}}


def test_unverified_activation_never_mutates_routes(service):
    client, ctx = _activation_client()
    assert (
        client.post("/api/config/local-models/activate", json={"tag": "fixture:1b"}).status_code
        == 409
    )
    ctx.register.assert_not_called()
    ctx.save.assert_not_called()


@pytest.mark.parametrize("disk_failure", [False, True])
def test_real_registration_and_disk_write_do_not_steal_existing_alias(
    service, monkeypatch, tmp_path, disk_failure
):
    from runtime.sensing.gateway import config_router
    from runtime.sensing.model_router import ModelDispatchRouter, ModelRouter

    class Original(ModelRouter):
        def call(self, request):
            raise AssertionError("No live inference in registration test")

    monkeypatch.chdir(tmp_path)
    setup.verify_model("fixture:1b")
    original = Original()
    dispatcher = ModelDispatchRouter(fallback=original)
    dispatcher.register("fixture:1b", original)
    config_path = tmp_path / "custom-models.json"
    config = config_router.create_config_router(
        stack=SimpleNamespace(planner=SimpleNamespace(router=dispatcher)),
        custom_models_path=config_path,
    )
    app = FastAPI()
    app.include_router(config.router)
    if disk_failure:
        monkeypatch.setattr(config_router.os, "replace", Mock(side_effect=OSError("disk full")))
    with TestClient(app) as client:
        response = client.post("/api/config/local-models/activate", json={"tag": "fixture:1b"})
    assert dispatcher._routes["fixture:1b"] is original
    if disk_failure:
        assert response.status_code == 503
        assert config.custom_models == {}
        assert not config_path.exists()
    else:
        assert response.status_code == 200
        data = response.json()
        assert dispatcher.has(data["selection_id"])
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        assert persisted[data["model_id"]]["selection_only"] is True
