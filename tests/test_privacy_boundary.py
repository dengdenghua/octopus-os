"""Exercise the transport boundary without sending private data to a network."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.core.cerebrum import ai_mode, turn_complexity
from runtime.execution.codex_backend.backend import CodexExecutionSession
from runtime.execution.engines import EngineId, EngineSelectionError, select_execution_route
from runtime.memory.hemolymph import embedding_backend
from runtime.safety.privacy import PrivacyViolation, is_loopback_endpoint, tool_privacy_denial
from runtime.sensing.gateway.storage_proxy_router import create_storage_proxy_router
from runtime.sensing.model_router import Message, ModelRequest
from runtime.sensing.model_router.dispatch_router import ModelDispatchRouter
from runtime.sensing.model_router.ollama_router import OllamaModelRouter
from runtime.sensing.model_router.openai_router import OpenAIModelRouter, OpenAIRouterError


@pytest.fixture(autouse=True)
def private_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHO_AI_MODE", "privacy")
    monkeypatch.setenv("ECHO_AI_MODE_PATH", str(tmp_path / "ai_mode.json"))


def request():
    return ModelRequest(
        model="private-model", messages=[Message(role="user", content="PRIVATE-CANARY")]
    )


def client(handler):
    return httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://cloud.example/v1",
        "http://localhost.evil/v1",
        "http://192.168.1.8/v1",
        "http://127.0.0.1@cloud.example",
        "http://cloud.example@127.0.0.1",
        "file:///secret",
        "http://127.0.0.1:99999",
        "http://127.0.0.1/v1?proxy=https://cloud.example",
    ],
)
def test_locality_uses_address_not_label(endpoint):
    assert not is_loopback_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint", ["http://localhost:11434", "http://127.0.0.1:8080/v1", "http://[::1]:8080"]
)
def test_loopback_is_allowed(endpoint):
    assert is_loopback_endpoint(endpoint)


@pytest.mark.parametrize("stream", [False, True])
def test_cloud_request_is_blocked_before_transport(stream):
    sent = Mock(side_effect=AssertionError("private content reached transport"))
    router = OpenAIModelRouter(base_url="https://cloud.example/v1", client=client(sent))
    with pytest.raises(PrivacyViolation):
        list(router.call_stream(request())) if stream else router.call(request())
    sent.assert_not_called()


def test_loopback_inference_remains_usable():
    sent = []

    def respond(req):
        sent.append(req)
        return httpx.Response(
            200,
            json={
                "model": "private-model",
                "choices": [{"message": {"content": "local answer"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    router = OpenAIModelRouter(base_url="http://127.0.0.1/v1", client=client(respond))
    assert router.call(request()).text == "local answer"
    assert len(sent) == 1
    assert "PRIVATE-CANARY" in sent[0].content.decode()


def test_ollama_cloud_endpoint_is_not_assumed_local():
    sent = Mock(side_effect=AssertionError("sent"))
    router = OllamaModelRouter(
        base_url="https://cloud.example", auto_detect=False, client=client(sent)
    )
    with pytest.raises(PrivacyViolation):
        router.call(request())
    sent.assert_not_called()


def test_unverified_injected_client_is_rejected():
    sent = Mock()
    router = OpenAIModelRouter(base_url="http://127.0.0.1/v1", client=sent)
    with pytest.raises(PrivacyViolation):
        router.call(request())
    assert not sent.mock_calls


@pytest.mark.parametrize("stream", [False, True])
def test_local_failure_never_rescues_through_cloud(stream):
    local_calls, cloud_calls = [], []

    def offline(req):
        local_calls.append(req)
        raise httpx.ConnectError("offline", request=req)

    local = OpenAIModelRouter(base_url="http://127.0.0.1/v1", client=client(offline))
    cloud = OpenAIModelRouter(
        base_url="https://cloud.example/v1", client=client(lambda req: cloud_calls.append(req))
    )
    dispatcher = ModelDispatchRouter(
        fallback=cloud, routes={"private-model": local, "cloud": cloud}
    )
    with pytest.raises((OpenAIRouterError, httpx.ConnectError)):
        list(dispatcher.call_stream(request())) if stream else dispatcher.call(request())
    assert local_calls
    assert not cloud_calls


def test_local_redirect_does_not_forward_body():
    sent = []

    def redirect(req):
        sent.append(req)
        return httpx.Response(307, headers={"Location": "https://cloud.example/upload"})

    router = OpenAIModelRouter(base_url="http://127.0.0.1/v1", client=client(redirect))
    with pytest.raises(OpenAIRouterError):
        router.call(request())
    assert len(sent) == 1
    assert sent[0].url.host == "127.0.0.1"


def test_no_local_model_does_not_escalate_even_with_smart_routing_off(monkeypatch):
    from runtime.sensing.model_router import custom_model_flags

    monkeypatch.setenv("ECHO_SMART_ROUTING", "off")
    monkeypatch.setattr(turn_complexity, "_resolve_tier_model", lambda _: "cloud-model")
    monkeypatch.setattr(custom_model_flags, "read_custom_models", lambda: {})
    with pytest.raises(PrivacyViolation):
        turn_complexity.select_model_for_complexity("local", user_model="auto")


def test_explicit_cloud_model_cannot_override_privacy(monkeypatch):
    from runtime.sensing.model_router import custom_model_flags

    monkeypatch.setattr(custom_model_flags, "read_custom_models", lambda: {})
    with pytest.raises(PrivacyViolation):
        turn_complexity.select_model_for_complexity("local", user_model="chatgpt/cloud-model")


def test_auto_selects_exact_local_catalog_row(monkeypatch):
    from runtime.platform.models.custom_model_selection import custom_model_selection_id
    from runtime.sensing.model_router import custom_model_flags

    monkeypatch.setattr(turn_complexity, "_resolve_tier_model", lambda _: None)
    monkeypatch.setattr(
        custom_model_flags,
        "read_custom_models",
        lambda: {
            "cloud": {"base_url": "https://cloud.example/v1", "models": ["same-name"]},
            "local": {"base_url": "http://127.0.0.1/v1", "models": ["same-name"]},
        },
    )
    selected, reason = turn_complexity.select_model_for_complexity("performance", user_model="auto")
    assert selected == custom_model_selection_id("local", "same-name")
    assert reason == "privacy:local_only"


def test_codex_is_blocked_before_launch_or_prompt():
    # start() must reject before touching security state, process or credentials.
    session = object.__new__(CodexExecutionSession)
    with pytest.raises(PrivacyViolation):
        asyncio.run(session.start())
    with pytest.raises(EngineSelectionError, match="隐私"):
        select_execution_route(requested_engine=EngineId.CODEX)
    assert select_execution_route(codex_partner=True).engine is EngineId.NATIVE


def test_embedding_cannot_export_and_cloud_metadata_is_honest(monkeypatch):
    monkeypatch.setenv("ECHO_EMBED_URL", "https://cloud.example/v1")
    sent = Mock(side_effect=AssertionError("sent"))
    monkeypatch.setattr(embedding_backend.urllib.request, "urlopen", sent)
    assert embedding_backend.backend_info()["local_only"] is False
    with pytest.raises(PrivacyViolation):
        embedding_backend.embed_texts(["PRIVATE-CANARY"])
    sent.assert_not_called()


def test_auxiliary_vision_does_not_run_in_private_mode(monkeypatch):
    from runtime.sensing.model_router.vision_guard import _transcribe

    assert _transcribe("PRIVATE-CANARY") is None


def test_tool_policy_is_default_deny_and_retains_reviewed_local_reads():
    from runtime.execution.suckers.builtins import register_builtins
    from runtime.execution.suckers.registry import SkillRegistry

    registry = register_builtins(SkillRegistry())
    assert tool_privacy_denial(registry.get("read_file")) is None
    for name in ("exec_shell", "web_search", "send_email", "read_file"):
        assert tool_privacy_denial(
            SimpleNamespace(name=name, handler=lambda: None, privacy_local=True)
        )


def test_policy_write_failure_is_not_reported_as_success(monkeypatch):
    import runtime.platform.io

    monkeypatch.delenv("ECHO_AI_MODE")
    ai_mode.set_ai_mode("privacy")
    monkeypatch.setattr(
        runtime.platform.io, "atomic_write_json", Mock(side_effect=OSError("disk full"))
    )
    with pytest.raises(OSError):
        ai_mode.set_ai_mode("efficiency")
    assert ai_mode.current_ai_mode() == "privacy"


def test_device_probe_does_not_connect_to_cloud_in_privacy(monkeypatch):
    probe = Mock(side_effect=AssertionError("network probe"))
    monkeypatch.setattr(ai_mode, "_detect_cloud_reachable", probe)
    monkeypatch.setattr(ai_mode, "_detect_local_model", lambda: (False, ""))
    monkeypatch.setattr(ai_mode, "_detect_gpu", lambda: (False, ""))
    assert ai_mode.detect_device_summary().cloud_reachable is False
    probe.assert_not_called()


@pytest.mark.parametrize("accepted", [False, True])
def test_storage_verifies_policy_before_sending_query(monkeypatch, accepted):
    from runtime.execution.suckers import storage_skills
    from runtime.safety.storage_privacy import storage_policy

    sent = []
    monkeypatch.setattr(storage_skills, "_base_url", lambda: "http://127.0.0.1:8767")
    monkeypatch.setattr(storage_skills, "_storage_token", lambda: "test")

    def handler(req):
        sent.append(req)
        if req.url.path == "/v1/policy":
            return httpx.Response(
                200, json=storage_policy() if accepted else {"mode": "efficiency"}
            )
        if req.url.path == "/v1/models":
            return httpx.Response(
                200, json=[{"provider": "local", "endpoint": None, "status": "running"}]
            )

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"hits": []}'

        return httpx.Response(200, stream=Body(), headers={"Content-Type": "application/json"})

    app = FastAPI()
    app.include_router(
        create_storage_proxy_router(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        )
    )
    response = TestClient(app).post("/api/storage/v1/search", json={"query": "PRIVATE-CANARY"})
    assert response.status_code == (200 if accepted else 403)
    assert sum(b"PRIVATE-CANARY" in req.content for req in sent) == (1 if accepted else 0)


def test_storage_policy_is_system_owned(monkeypatch):
    app = FastAPI()
    app.include_router(create_storage_proxy_router())
    browser = TestClient(app)
    assert browser.get("/api/storage/v1/policy").json()["allow_cloud_answering"] is False
    assert browser.put("/api/storage/v1/policy", json={"mode": "efficiency"}).status_code == 409
    assert ai_mode.current_ai_mode() == "privacy"


def test_registered_local_model_row_reaches_its_local_transport(tmp_path):
    from runtime.platform.models.custom_model_selection import custom_model_selection_id
    from runtime.sensing.gateway.config_router import create_config_router
    from runtime.sensing.model_router.models import UnconfiguredModelRouter

    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps(
            {
                "local": {
                    "id": "local",
                    "provider": "openai",
                    "base_url": "http://127.0.0.1/v1",
                    "models": ["private-model"],
                }
            }
        ),
        encoding="utf-8",
    )
    dispatcher = ModelDispatchRouter(fallback=UnconfiguredModelRouter())
    create_config_router(
        stack=SimpleNamespace(planner=SimpleNamespace(router=dispatcher)),
        custom_models_path=catalog,
        codex_state_root=tmp_path / "codex",
        codex_preferences_path=tmp_path / "preferences.json",
    )
    selected = custom_model_selection_id("local", "private-model")
    wrapper = dispatcher._route_for(selected)
    assert wrapper is not None
    sent = []

    def respond(req):
        sent.append(req)
        assert json.loads(req.content)["model"] == "private-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "local"}}]})

    wrapper.privacy_upstream(selected)._client = client(respond)
    assert dispatcher.call(request().model_copy(update={"model": selected})).text == "local"
    assert len(sent) == 1


def test_enabling_privacy_closes_a_running_codex_sidecar():
    async def run():
        session = object.__new__(CodexExecutionSession)
        session._closed = False
        session._close_lock = asyncio.Lock()
        session._privacy_monitor = None
        session._client = SimpleNamespace(close=AsyncMock())
        session._context = None
        await session._watch_privacy_policy()
        assert session._closed
        session._client.close.assert_awaited_once()

    asyncio.run(run())


def test_native_bypass_approval_cannot_run_an_unverified_tool():
    from runtime.execution.suckers.registry import Skill, SkillRegistry
    from runtime.execution.tool_engine.native_tool_execution import execute_native_tool_call

    invoked = Mock()
    registry = SkillRegistry()
    registry.register(Skill(name="exec_shell", handler=invoked, trusted_source="builtin://test"))
    result = execute_native_tool_call(
        SimpleNamespace(executor=SimpleNamespace(registry=registry)),
        {"name": "exec_shell", "input": {"command": "send PRIVATE-CANARY"}},
    )
    assert result.execution_blocked == "privacy_egress_blocked"
    invoked.assert_not_called()


def test_storage_rejects_a_cloud_embedding_configuration():
    from runtime.safety.storage_privacy import storage_policy, verify_private_storage

    with pytest.raises(PrivacyViolation):
        verify_private_storage(
            storage_policy(),
            [
                {
                    "provider": "local",
                    "endpoint": "https://cloud.example/v1",
                    "status": "running",
                }
            ],
        )


def test_http_policy_persistence_failure_keeps_old_policy(monkeypatch):
    import runtime.platform.io
    from runtime.sensing.gateway.config_router import create_config_router

    monkeypatch.delenv("ECHO_AI_MODE")
    ai_mode.set_ai_mode("privacy")
    app = FastAPI()
    app.include_router(create_config_router(custom_models_path=None).router)
    monkeypatch.setattr(
        runtime.platform.io, "atomic_write_json", Mock(side_effect=OSError("disk full"))
    )
    response = TestClient(app).post("/api/ai-mode", json={"mode": "efficiency"})
    assert response.status_code == 503
    assert ai_mode.current_ai_mode() == "privacy"


def test_remote_ollama_configuration_does_not_probe_on_startup():
    sent = Mock(side_effect=AssertionError("remote probe"))
    router = OllamaModelRouter(base_url="https://remote.example", client=client(sent))
    assert router.is_available() is False
    assert router.list_models() == []
    sent.assert_not_called()


@pytest.mark.parametrize("has_models", [False, True])
def test_an_open_application_port_is_not_a_local_model(monkeypatch, has_models):
    from contextlib import contextmanager
    from io import BytesIO

    import runtime.safety.privacy as privacy
    import runtime.sensing.model_router.custom_model_flags as flags

    monkeypatch.setattr(flags, "read_custom_models", lambda: {})
    monkeypatch.setenv("ECHO_MODEL_LOCAL", "cloud-model-mislabeled-local")

    @contextmanager
    def respond(req, **_kwargs):
        assert is_loopback_endpoint(req.full_url)
        yield BytesIO(
            json.dumps(
                {"data": [{"id": "local"}]} if has_models else {"service": "echo-agent"}
            ).encode()
        )

    monkeypatch.setattr(privacy, "private_urlopen", respond)
    assert ai_mode._detect_local_model()[0] is has_models


@pytest.mark.parametrize("stream", [False, True])
def test_switching_to_privacy_stops_compatibility_retries(monkeypatch, stream):
    monkeypatch.setenv("ECHO_AI_MODE", "efficiency")
    sent = []

    def respond(req):
        sent.append(req)
        monkeypatch.setenv("ECHO_AI_MODE", "privacy")
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Unsupported parameter: 'temperature' is not supported with this model.",
                    "param": "temperature",
                    "code": "unsupported_parameter",
                }
            },
        )

    router = OpenAIModelRouter(base_url="https://cloud.example/v1", client=client(respond))
    req = request().model_copy(update={"temperature": 0.2})
    with pytest.raises(PrivacyViolation):
        list(router.call_stream(req)) if stream else router.call(req)
    assert len(sent) == 1


def test_storage_syncs_back_to_efficiency_before_new_computation(monkeypatch):
    from runtime.execution.suckers import storage_skills
    from runtime.safety.storage_privacy import storage_policy

    monkeypatch.setenv("ECHO_AI_MODE", "efficiency")
    sent = []

    def respond(method, path, payload=None):
        sent.append((method, path, payload))
        return storage_policy() if path == "/v1/policy" else {"hits": []}

    monkeypatch.setattr(storage_skills, "_request", respond)
    assert storage_skills._search_documents("QUERY")["ok"]
    assert [item[1] for item in sent] == ["/v1/policy", "/v1/search"]
    assert sent[0][2]["allow_cloud_answering"] is True


@pytest.mark.parametrize("unsafe_path", ["v1/search", "v1/answer", "v1/new-analysis"])
def test_unknown_read_routes_require_storage_privacy_admission(unsafe_path):
    from runtime.safety.storage_privacy import storage_compute_request

    assert storage_compute_request("GET", unsafe_path)
    assert not storage_compute_request("GET", "v1/files/document/content")


@pytest.mark.parametrize("trust_env,redirects", [(True, False), (False, True)])
def test_storage_does_not_use_an_unsafe_injected_transport(trust_env, redirects):
    sent = Mock(side_effect=AssertionError("unverified transport used"))
    app = FastAPI()
    app.include_router(
        create_storage_proxy_router(
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(sent),
                trust_env=trust_env,
                follow_redirects=redirects,
            )
        )
    )
    assert TestClient(app).get("/api/storage/v1/manifest").status_code == 403
    sent.assert_not_called()
