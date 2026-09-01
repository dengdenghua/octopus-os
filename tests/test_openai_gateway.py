"""Implementation note."""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config  # noqa: E402
from runtime.platform.models import ParsedIntent  # noqa: E402
from runtime.sensing.gateway import create_openai_router  # noqa: E402
from runtime.sensing.gateway.openai_gateway import _stream_direct_llm_fallback  # noqa: E402
from runtime.sensing.gateway.openai_gateway.request_parser import (  # noqa: E402
    _model_runtime_options,
)
from runtime.sensing.model_router.models import ModelResponse, ModelStreamEvent  # noqa: E402

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def stack():
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/gw",
            mock_response=json.dumps(
                {
                    "reasoning": "r",
                    "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                }
            ),
        ),
    )
    return build_from_config(cfg)


@pytest.fixture
def client(stack):
    app = FastAPI()
    app.include_router(create_openai_router(stack))
    return TestClient(app)


# ═══════════════════════════════════════════════════════════
# /v1/models
# ═══════════════════════════════════════════════════════════


class TestListModels:
    def test_models_endpoint_returns_openai_shape(self, client):
        r = client.get("/v1/models")
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 1

        # Implementation note.
        for m in data["data"]:
            assert "id" in m
            assert m["object"] == "model"
            assert "created" in m
            assert "owned_by" in m

        # Implementation note.
        ids = {m["id"] for m in data["data"]}
        assert "echo-agent" in ids

    def test_list_includes_registered_skills(self, client):
        data = client.get("/v1/models").json()
        ids = {m["id"] for m in data["data"]}
        # Implementation note.
        assert "echo-agent/list_cwd" in ids


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestChatCompletionsNonStream:
    def test_happy_path(self, client):
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "echo-agent",
                "messages": [{"role": "user", "content": "list cwd"}],
            },
        )
        assert r.status_code == 200
        data = r.json()

        # OpenAI shape
        assert data["object"] == "chat.completion"
        assert "id" in data and data["id"].startswith("chatcmpl-")
        assert "created" in data
        assert data["model"] == "echo-agent"
        assert isinstance(data["choices"], list) and len(data["choices"]) == 1

        choice = data["choices"][0]
        assert choice["index"] == 0
        assert choice["message"]["role"] == "assistant"
        assert isinstance(choice["message"]["content"], str)
        assert choice["finish_reason"] in ("stop", "failed")

        # Implementation note.
        assert "usage" in data
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            assert k in data["usage"]

        # Implementation note.
        assert "echo" in data
        assert "task_id" in data["echo"]
        assert "step_count" in data["echo"]

    def test_multimodal_content_text_extracted(self, client):
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "echo-agent",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "list the current directory"},
                            {"type": "image_url", "image_url": "data:..."},  # Implementation note.
                        ],
                    }
                ],
            },
        )
        assert r.status_code == 200
        # Implementation note.

    def test_last_user_message_wins(self, client):
        """Implementation note."""
        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "user", "content": "old question"},
                    {"role": "assistant", "content": "old answer"},
                    {"role": "user", "content": "the real goal"},
                ],
            },
        )
        assert r.status_code == 200

    def test_full_history_reaches_planner_context(self, client, stack):
        """The gateway should keep the last user message as the goal
        while still passing prior turns into the planner prompt."""
        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "Always prefer concise plans."},
                    {"role": "user", "content": "My project is echo-agent."},
                    {"role": "assistant", "content": "Noted."},
                    {"role": "user", "content": "Use that context now."},
                ],
            },
        )
        assert r.status_code == 200

        planner_request = stack.planner.router.call_log[0]
        planner_user_prompt = planner_request.messages[-1].content
        assert "CONVERSATION HISTORY" in planner_user_prompt
        assert "[system] Always prefer concise plans." in planner_user_prompt
        assert "[user] My project is echo-agent." in planner_user_prompt
        assert "[assistant] Noted." in planner_user_prompt
        assert "USER GOAL: Use that context now." in planner_user_prompt

    def test_explicit_profile_memory_reaches_planner_context(self, client, stack):
        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "remember that I prefer concise Chinese answers",
                    },
                    {"role": "user", "content": "Use my preference now."},
                ],
            },
        )
        assert r.status_code == 200

        planner_request = stack.planner.router.call_log[0]
        planner_user_prompt = planner_request.messages[-1].content
        assert "USER PROFILE MEMORY" in planner_user_prompt
        assert "I prefer concise Chinese answers" in planner_user_prompt
        assert "USER GOAL: Use my preference now." in planner_user_prompt


class TestChatCompletionsErrors:
    def test_empty_messages_400(self, client):
        r = client.post("/v1/chat/completions", json={"messages": []})
        assert r.status_code == 400
        assert "messages" in r.json()["detail"]

    def test_no_messages_400(self, client):
        r = client.post("/v1/chat/completions", json={})
        assert r.status_code == 400

    def test_only_system_no_user_400(self, client):
        r = client.post(
            "/v1/chat/completions", json={"messages": [{"role": "system", "content": "you are x"}]}
        )
        assert r.status_code == 400
        assert "no user message" in r.json()["detail"]

    def test_static_planner_fallback_does_not_500(self):
        """Implementation note."""
        cfg = AgentConfig(planner=PlannerConfig(type="static"))
        stack = build_from_config(cfg)
        app = FastAPI()
        app.include_router(create_openai_router(stack))
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "no rules"}],
            },
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["object"] == "chat.completion"
        assert payload["choices"][0]["message"]["role"] == "assistant"


def test_custom_model_ignores_configured_max_tokens(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "custom_models.json").write_text(
        json.dumps(
            {
                "mimo2.5": {
                    "model": "mimo-v2.5-pro",
                    "max_tokens": 8192000,
                    "supports_thinking": True,
                    # Non-openai-compat custom models clamp to the gateway
                    # default (131072). Openai-compat is exercised below.
                    "provider": "anthropic",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))

    supports_thinking, max_tokens = _model_runtime_options(
        "mimo2.5",
        "mimo-v2.5-pro",
    )

    assert supports_thinking is True
    assert max_tokens == 131072


def test_custom_model_without_max_tokens_uses_unbounded_default(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "custom_models.json").write_text(
        json.dumps(
            {
                "mimo2.5": {
                    "model": "mimo-v2.5-pro",
                    "supports_thinking": True,
                    "provider": "anthropic",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))

    supports_thinking, max_tokens = _model_runtime_options(
        "mimo2.5",
        "mimo-v2.5-pro",
    )

    assert supports_thinking is True
    assert max_tokens == 131072


def test_custom_openai_compat_model_returns_unbounded(tmp_path, monkeypatch):
    """Custom OpenAI-compatible models intentionally return ``None`` for
    max_tokens so the upstream provider's own limit applies. Pins the
    distinction added when the gateway started supporting non-openai
    custom adapters.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "custom_models.json").write_text(
        json.dumps(
            {
                "mimo2.5": {
                    "model": "mimo-v2.5-pro",
                    "supports_thinking": True,
                    "provider": "openai-compatible",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))

    supports_thinking, max_tokens = _model_runtime_options(
        "mimo2.5",
        "mimo-v2.5-pro",
    )

    assert supports_thinking is True
    assert max_tokens is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestChatCompletionsStream:
    def test_stream_returns_event_stream(self, client):
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "stream": True,
                "messages": [{"role": "user", "content": "list"}],
            },
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")

            body_parts: list[str] = []
            for chunk in r.iter_text():
                body_parts.append(chunk)
                if "[DONE]" in chunk:
                    break

            body = "".join(body_parts)
            # Implementation note.
            assert "data: " in body
            assert "[DONE]" in body
            # Implementation note.
            for line in body.splitlines():
                if line.startswith("data: ") and "[DONE]" not in line:
                    payload = line[len("data: ") :]
                    parsed = json.loads(payload)
                    assert parsed["object"] == "chat.completion.chunk"
                    assert "choices" in parsed
                    break
            else:
                pytest.fail("no valid chunk found in stream")

    def test_glm_stream_does_not_emit_fake_reasoning(self):
        class _Router:
            default_model = "glm-test"

            def call(self, _request):
                return ModelResponse(
                    text="final answer should stream without fake reasoning",
                    input_tokens=3,
                    output_tokens=12,
                )

            def call_stream(self, _request):
                yield ModelStreamEvent(
                    type="text_delta",
                    delta="final answer should stream without fake reasoning",
                )
                yield ModelStreamEvent(
                    type="done",
                    final=ModelResponse(
                        text="final answer should stream without fake reasoning",
                        input_tokens=3,
                        output_tokens=12,
                    ),
                )

        class _Planner:
            router = _Router()
            planner_model = "glm-test"

        class _Stack:
            planner = _Planner()
            journal = None

        intent = ParsedIntent(
            raw="test",
            intent_type="task",
            normalized_goal="test",
            user_context={
                "conversation_messages": [
                    {"role": "user", "content": "test"},
                ],
                "interaction_mode": "office",
            },
        )

        events = list(
            _stream_direct_llm_fallback(
                _Stack(),
                intent,
                agent=None,
                model="glm-test",
            )
        )

        assert events
        assert not any(kind == "reasoning" for kind, _delta, _final in events)
        assert [kind for kind, _delta, _final in events].count("done") == 1
        text_chunks = [delta for kind, delta, _final in events if kind == "text"]
        assert "".join(text_chunks).startswith("final answer")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMountOnUIApp:
    def test_ui_app_can_host_gateway(self, stack):
        from runtime.platform.ui import create_app

        app = create_app(journal=stack.journal, registry=stack.registry)
        app.include_router(create_openai_router(stack))
        client = TestClient(app)

        # Implementation note.
        assert client.get("/api/status").status_code == 200
        # Implementation note.
        assert client.get("/v1/models").status_code == 200

        # Implementation note.
        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "list"}],
            },
        )
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestOpenAISDKCompat:
    def test_response_matches_chatcompletion_model(self, client):
        """Implementation note."""
        data = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "list"}],
            },
        ).json()

        # Implementation note.
        required = {"id", "object", "created", "model", "choices", "usage"}
        assert required.issubset(data.keys())

        choice = data["choices"][0]
        assert set(choice.keys()) >= {"index", "message", "finish_reason"}
        assert set(choice["message"].keys()) >= {"role", "content"}
