"""Dense coverage for vision config resolution + OpenAI-compatible call (audit Q-05)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import runtime.sensing.gateway.computer_vision as cv


def _patch_model_file(monkeypatch, tmp_path, payload) -> None:
    target = tmp_path / "custom_models.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cv, "Path", lambda *a, **kw: target)


def test_load_custom_model(tmp_path, monkeypatch) -> None:
    _patch_model_file(monkeypatch, tmp_path, {"v1": {"model": "gpt-4o"}})
    assert cv._load_custom_model("v1") == {"model": "gpt-4o"}
    assert cv._load_custom_model("missing") is None
    assert cv._load_custom_model("") is None

    # Bad JSON
    target = tmp_path / "custom_models.json"
    target.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(cv, "Path", lambda *a, **kw: target)
    assert cv._load_custom_model("v1") is None

    # Non-dict payload / entry
    target.write_text("[]", encoding="utf-8")
    assert cv._load_custom_model("v1") is None
    target.write_text(json.dumps({"v1": ["not-a-dict"]}), encoding="utf-8")
    assert cv._load_custom_model("v1") is None

    # Missing file
    monkeypatch.setattr(cv, "Path", lambda *a, **kw: tmp_path / "nope.json")
    assert cv._load_custom_model("v1") is None


def test_vision_model_config_custom(tmp_path, monkeypatch) -> None:
    _patch_model_file(
        monkeypatch,
        tmp_path,
        {
            "v1": {
                "provider": "OpenAI",
                "models": ["gpt-4o", ""],
                "base_url": "https://api.openai.com/v1/",
                "api_key": "k",
                "default_headers": {"X-1": "a"},
            }
        },
    )
    cfg = cv._vision_model_config("v1")
    assert cfg["model"] == "gpt-4o"
    assert cfg["provider"] == "openai"
    assert cfg["base_url"] == "https://api.openai.com/v1/"
    assert cfg["default_headers"] == {"X-1": "a"}

    # Legacy single-model entry
    _patch_model_file(monkeypatch, tmp_path, {"v2": {"model": "legacy-vision"}})
    cfg = cv._vision_model_config("v2")
    assert cfg["model"] == "legacy-vision"

    # No upstream anywhere -> fall back to the requested id
    _patch_model_file(monkeypatch, tmp_path, {"v3": {"models": []}})
    cfg = cv._vision_model_config("v3")
    assert cfg["model"] == "v3"


def test_vision_model_config_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ECHO_COMPUTER_VISION_MODEL", raising=False)
    monkeypatch.delenv("ECHO_COMPUTER_VISION_BASE_URL", raising=False)
    monkeypatch.delenv("ECHO_COMPUTER_VISION_API_KEY", raising=False)
    monkeypatch.delenv("ECHO_COMPUTER_VISION_UPSTREAM_MODEL", raising=False)
    monkeypatch.setattr(cv, "Path", lambda *a, **kw: tmp_path / "missing.json")
    assert cv._vision_model_config("") is None

    monkeypatch.setenv("ECHO_COMPUTER_VISION_MODEL", "m1")
    monkeypatch.setenv("ECHO_COMPUTER_VISION_BASE_URL", "https://x")
    monkeypatch.setenv("ECHO_COMPUTER_VISION_API_KEY", "key")
    cfg = cv._vision_model_config("")
    assert cfg["provider"] == "openai"
    assert cfg["model"] == "m1"
    assert cfg["api_key"] == "key"


def test_call_openai_vision_validation(tmp_path, monkeypatch) -> None:
    def _fail(*a, **kw):
        raise AssertionError("httpx should not be called")

    monkeypatch.setattr(cv, "httpx", SimpleNamespace(post=_fail))
    # httpx unavailable -> 503
    monkeypatch.setattr(cv, "HTTPX_AVAILABLE", False)
    with pytest.raises(HTTPException) as ei:
        cv._call_openai_vision(
            config={"provider": "openai", "base_url": "https://x", "model": "gpt-4o"},
            goal="g",
            data_url="d",
        )
    assert ei.value.status_code == 503

    monkeypatch.setattr(cv, "HTTPX_AVAILABLE", True)
    with pytest.raises(HTTPException) as ei:
        cv._call_openai_vision(config={"provider": "bogus"}, goal="g", data_url="d")
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei:
        cv._call_openai_vision(
            config={"provider": "openai", "base_url": "", "model": ""}, goal="g", data_url="d"
        )
    assert ei.value.status_code == 400


def test_call_openai_vision_success_and_errors(tmp_path, monkeypatch) -> None:
    class _Resp:
        def __init__(self, status_code=200, data=None):
            self.status_code = status_code
            self._data = data

        def json(self):
            return self._data

    calls = {}

    def _post(url, **kw):
        calls["url"] = url
        calls["headers"] = kw["headers"]
        calls["payload"] = kw["json"]
        return _Resp(200, {"choices": [{"message": {"content": '{"actions":[]}'}}]})

    monkeypatch.setattr(cv, "httpx", SimpleNamespace(post=_post))
    monkeypatch.setattr(cv, "HTTPX_AVAILABLE", True)
    cfg = {
        "provider": "openai",
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o",
        "api_key": "sk-x",
        "default_headers": {"X-Extra": "1"},
    }
    out = cv._call_openai_vision(config=cfg, goal="click the button", data_url="data:img")
    assert out == '{"actions":[]}'
    assert calls["url"] == "https://api.example.com/v1/chat/completions"
    assert calls["headers"]["Authorization"] == "Bearer sk-x"
    assert calls["headers"]["X-Extra"] == "1"
    assert calls["payload"]["messages"][0]["content"][1]["image_url"]["url"] == "data:img"
    assert "click the button" in calls["payload"]["messages"][0]["content"][0]["text"]

    # Content as a list of parts
    monkeypatch.setattr(
        cv,
        "httpx",
        SimpleNamespace(
            post=lambda *a, **kw: _Resp(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "A"},
                                    {"type": "text", "text": "B"},
                                ]
                            }
                        }
                    ]
                },
            )
        ),
    )
    out = cv._call_openai_vision(config=cfg, goal="g", data_url="d")
    assert out == "A\nB"

    # Upstream HTTP error
    monkeypatch.setattr(cv, "httpx", SimpleNamespace(post=lambda *a, **kw: _Resp(429, {})))
    with pytest.raises(HTTPException) as ei:
        cv._call_openai_vision(config=cfg, goal="g", data_url="d")
    assert ei.value.status_code == 429

    # Network failure
    def _boom(*a, **kw):
        raise OSError("refused")

    monkeypatch.setattr(cv, "httpx", SimpleNamespace(post=_boom))
    with pytest.raises(HTTPException) as ei:
        cv._call_openai_vision(config=cfg, goal="g", data_url="d")
    assert ei.value.status_code == 502

    # Non-JSON response
    class _BadJson:
        status_code = 200

        def json(self):
            raise json.JSONDecodeError("bad", "doc", 0)

    monkeypatch.setattr(cv, "httpx", SimpleNamespace(post=lambda *a, **kw: _BadJson()))
    with pytest.raises(HTTPException) as ei:
        cv._call_openai_vision(config=cfg, goal="g", data_url="d")
    assert ei.value.status_code == 502

    # No choices / empty content
    monkeypatch.setattr(
        cv, "httpx", SimpleNamespace(post=lambda *a, **kw: _Resp(200, {"choices": []}))
    )
    with pytest.raises(HTTPException) as ei:
        cv._call_openai_vision(config=cfg, goal="g", data_url="d")
    assert ei.value.status_code == 502

    monkeypatch.setattr(
        cv,
        "httpx",
        SimpleNamespace(
            post=lambda *a, **kw: _Resp(200, {"choices": [{"message": {"content": "  "}}]})
        ),
    )
    with pytest.raises(HTTPException) as ei:
        cv._call_openai_vision(config=cfg, goal="g", data_url="d")
    assert ei.value.status_code == 502

