"""Implementation note."""

from __future__ import annotations

import json
from typing import Any

import pytest

from runtime.sensing.model_router import (
    GeminiModelRouter,
    GeminiRouterError,
    Message,
    ModelRequest,
)

# ═══════════════════════════════════════════════════════════
# Mock httpx client
# ═══════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(
        self,
        *,
        status_code: int = 200,
        body: Any = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text or (json.dumps(body) if body else "")

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, resp: _FakeResp | None = None) -> None:
        self.resp = resp
        self.calls: list[dict[str, Any]] = []

    def post(self, url, params=None, json=None, headers=None, **_kw):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "json": json,
                "headers": headers,
            }
        )
        return self.resp or _FakeResp(body=_default_ok())


def _default_ok(text: str = "hello") -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
        },
    }


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestURLAndPayload:
    def test_url_includes_model(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(
            api_key="KEY",
            default_model="gemini-2.5-flash",
            client=http,
        )
        r.call(
            ModelRequest(
                model="gemini-2.5-pro",
                messages=[Message(role="user", content="hi")],
            )
        )
        assert http.calls[0]["url"].endswith(
            "/models/gemini-2.5-pro:generateContent",
        )

    def test_default_model_when_request_empty(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(
            api_key="KEY",
            default_model="gemini-2.5-flash",
            client=http,
        )
        r.call(
            ModelRequest(
                model="",
                messages=[Message(role="user", content="x")],
            )
        )
        assert "gemini-2.5-flash" in http.calls[0]["url"]

    def test_api_key_in_query_and_header(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="MYKEY", client=http)
        r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="x")],
            )
        )
        call = http.calls[0]
        assert call["params"] == {"key": "MYKEY"}
        assert call["headers"]["x-goog-api-key"] == "MYKEY"

    def test_no_api_key_omits_params(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="", client=http)
        r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="x")],
            )
        )
        call = http.calls[0]
        assert call["params"] is None
        assert "x-goog-api-key" not in (call["headers"] or {})


# ═══════════════════════════════════════════════════════════
# role / system mapping
# ═══════════════════════════════════════════════════════════


class TestMessageMapping:
    def test_system_goes_to_systemInstruction(self):  # noqa: N802 — checks Gemini API field name
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="k", client=http)
        r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[
                    Message(role="system", content="be concise"),
                    Message(role="user", content="hi"),
                ],
            )
        )
        body = http.calls[0]["json"]
        assert "systemInstruction" in body
        assert body["systemInstruction"]["parts"][0]["text"] == "be concise"
        # Implementation note.
        roles = [c["role"] for c in body["contents"]]
        assert "system" not in roles

    def test_multiple_system_messages_joined(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="k", client=http)
        r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[
                    Message(role="system", content="first"),
                    Message(role="system", content="second"),
                    Message(role="user", content="hi"),
                ],
            )
        )
        body = http.calls[0]["json"]
        txt = body["systemInstruction"]["parts"][0]["text"]
        assert "first" in txt and "second" in txt

    def test_assistant_role_mapped_to_model(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="k", client=http)
        r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[
                    Message(role="user", content="q1"),
                    Message(role="assistant", content="a1"),
                    Message(role="user", content="q2"),
                ],
            )
        )
        contents = http.calls[0]["json"]["contents"]
        assert [c["role"] for c in contents] == ["user", "model", "user"]

    def test_generation_config_from_request(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="k", client=http)
        r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="hi")],
                temperature=0.9,
                max_tokens=1024,
            )
        )
        cfg = http.calls[0]["json"]["generationConfig"]
        assert cfg["temperature"] == 0.9
        assert cfg["maxOutputTokens"] == 1024


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestImages:
    def test_image_attached_to_last_user_part(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="k", client=http)
        r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="what's in this?")],
                images_b64=["iVBORw0KGgo=AAAA"],
            )
        )
        contents = http.calls[0]["json"]["contents"]
        parts = contents[-1]["parts"]
        # Implementation note.
        has_image = any(
            "inlineData" in p and p["inlineData"]["mimeType"] == "image/png" for p in parts
        )
        assert has_image

    def test_multiple_images_all_attached(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="k", client=http)
        r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="compare")],
                images_b64=["img1-b64", "img2-b64"],
            )
        )
        parts = http.calls[0]["json"]["contents"][-1]["parts"]
        images = [p for p in parts if "inlineData" in p]
        assert len(images) == 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestResponseParse:
    def test_basic_text_and_tokens(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok(text="answer")))
        r = GeminiModelRouter(api_key="k", client=http)
        resp = r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="q")],
            )
        )
        assert resp.text == "answer"
        assert resp.input_tokens == 10
        assert resp.output_tokens == 5
        assert resp.provider == "gemini"
        assert resp.model == "gemini-2.5-flash"

    def test_multi_part_text_concatenated(self):
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "part1 "},
                            {"text": "part2"},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
            },
        }
        http = _FakeClient(resp=_FakeResp(body=body))
        r = GeminiModelRouter(api_key="k", client=http)
        resp = r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="q")],
            )
        )
        assert resp.text == "part1 part2"

    def test_finish_reason_max_tokens_normalized(self):
        body = _default_ok()
        body["candidates"][0]["finishReason"] = "MAX_TOKENS"
        http = _FakeClient(resp=_FakeResp(body=body))
        r = GeminiModelRouter(api_key="k", client=http)
        resp = r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="q")],
            )
        )
        assert resp.finish_reason == "length"

    def test_empty_candidates_raises(self):
        http = _FakeClient(resp=_FakeResp(body={"candidates": []}))
        r = GeminiModelRouter(api_key="k", client=http)
        with pytest.raises(GeminiRouterError, match="no candidates"):
            r.call(
                ModelRequest(
                    model="gemini-2.5-flash",
                    messages=[Message(role="user", content="q")],
                )
            )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestCost:
    def test_default_pricing_used_when_no_config(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        r = GeminiModelRouter(api_key="k", client=http)
        resp = r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="q")],
            )
        )
        # 10 input, 5 output · default 1.25e-7/5e-7 → 10*1.25e-7 + 5*5e-7 = 3.75e-6
        assert resp.cost.usd > 0
        assert resp.cost.usd < 0.0001  # Implementation note.

    def test_custom_pricing_per_1k(self):
        http = _FakeClient(resp=_FakeResp(body=_default_ok()))
        # 1000 input · 500 output · flash: (0.10, 0.40) per 1k
        body = _default_ok()
        body["usageMetadata"] = {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 500,
        }
        http.resp = _FakeResp(body=body)
        r = GeminiModelRouter(
            api_key="k",
            client=http,
            pricing_per_1k={"gemini-2.5-flash": (0.10, 0.40)},
        )
        resp = r.call(
            ModelRequest(
                model="gemini-2.5-flash",
                messages=[Message(role="user", content="q")],
            )
        )
        # 1000/1000 * 0.10 + 500/1000 * 0.40 = 0.10 + 0.20 = 0.30
        assert resp.cost.usd == pytest.approx(0.30, rel=0.01)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestErrors:
    def test_http_4xx(self):
        http = _FakeClient(resp=_FakeResp(status_code=403, text="forbidden"))
        r = GeminiModelRouter(api_key="k", client=http)
        with pytest.raises(GeminiRouterError, match="http_403"):
            r.call(
                ModelRequest(
                    model="gemini-2.5-flash",
                    messages=[Message(role="user", content="q")],
                )
            )

    def test_invalid_json(self):
        class _BadJson:
            status_code = 200
            text = "garbage"

            def json(self):
                raise ValueError("not json")

        class _H:
            def post(self, *a, **kw):
                return _BadJson()

        r = GeminiModelRouter(api_key="k", client=_H())
        with pytest.raises(GeminiRouterError, match="invalid_json"):
            r.call(
                ModelRequest(
                    model="gemini-2.5-flash",
                    messages=[Message(role="user", content="q")],
                )
            )

    def test_network_error_wrapped(self):
        class _Boom:
            def post(self, *a, **kw):
                raise RuntimeError("timeout")

        r = GeminiModelRouter(api_key="k", client=_Boom())
        with pytest.raises(GeminiRouterError, match="http_error"):
            r.call(
                ModelRequest(
                    model="gemini-2.5-flash",
                    messages=[Message(role="user", content="q")],
                )
            )


# ═══════════════════════════════════════════════════════════
# env var loading
# ═══════════════════════════════════════════════════════════


class TestEnvVar:
    def test_reads_gemini_api_key_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "from-env")
        r = GeminiModelRouter()
        assert r.api_key == "from-env"

    def test_custom_env_var_name(self, monkeypatch):
        monkeypatch.setenv("MY_GEMINI", "x")
        r = GeminiModelRouter(env_var_name="MY_GEMINI")
        assert r.api_key == "x"

    def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "env")
        r = GeminiModelRouter(api_key="explicit")
        assert r.api_key == "explicit"
