"""Implementation note."""

from __future__ import annotations

from runtime.sensing.model_router import MockModelRouter, ModelRequest
from runtime.sensing.model_router.models import Message


def _req(text: str, model: str = "mock/test") -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=[Message(role="user", content=text)],
    )


class TestFixedResponse:
    def test_returns_fixed_string(self):
        r = MockModelRouter(response="hi")
        resp = r.call(_req("anything"))
        assert resp.text == "hi"

    def test_tokens_and_cost_populated(self):
        r = MockModelRouter(response="abcdef", default_input_tokens=10)
        resp = r.call(_req("anything"))
        assert resp.input_tokens == 10
        assert resp.output_tokens >= 1
        assert resp.cost.tokens_in == 10


class TestResponseFn:
    def test_fn_receives_request(self):
        r = MockModelRouter(response_fn=lambda req: f"got-{len(req.messages)}")
        resp = r.call(_req("hi"))
        assert resp.text == "got-1"


class TestByModel:
    def test_routes_by_model_name(self):
        r = MockModelRouter(responses_by_model={"haiku": "fast", "sonnet": "smart"})
        assert r.call(_req("x", model="haiku")).text == "fast"
        assert r.call(_req("x", model="sonnet")).text == "smart"


class TestCallLog:
    def test_logs_all_calls(self):
        r = MockModelRouter(response="x")
        r.call(_req("a"))
        r.call(_req("b"))
        assert len(r.call_log) == 2
        assert r.call_log[0].messages[-1].content == "a"


class TestDefaultEcho:
    def test_no_response_configured_echoes(self):
        r = MockModelRouter()
        resp = r.call(_req("hello world"))
        assert "hello world" in resp.text
