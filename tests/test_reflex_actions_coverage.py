"""Dense coverage for reflex action runners (audit Q-05)."""

from __future__ import annotations

import subprocess
import sys
import types
import urllib.error
from types import SimpleNamespace

from runtime.core.nerves.reflex.actions import (
    ActionResult,
    ActionSpec,
    _run_exec,
    _run_mqtt,
    _run_webhook,
    execute_action,
)

# ─── ActionSpec ───────────────────────────────────────────────


def test_action_spec_from_entry() -> None:
    assert ActionSpec.from_entry(None) is None
    assert ActionSpec.from_entry({}) is None
    spec = ActionSpec.from_entry(
        {"webhook": {"url": "https://x"}, "exec": {"cmd": "ls"}, "mqtt": {"broker": "b"}}
    )
    assert spec.webhook["url"] == "https://x"
    assert spec.exec["cmd"] == "ls"
    assert spec.mqtt["broker"] == "b"
    assert ActionSpec.from_entry({"webhook": "not-dict"}) is None


# ─── webhook ──────────────────────────────────────────────────


def test_run_webhook(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=1.0: _ctx(_RespLike(204)))
    res = _run_webhook("r1", {"url": "https://8.8.8.8/hook", "method": "GET"})
    assert res.success is True
    assert "HTTP 204" in res.detail

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=1.0: _ctx(_RespLike(500)))
    res = _run_webhook("r1", {"url": "https://8.8.8.8/hook", "body": {"a": 1}})
    assert res.success is False
    assert "HTTP 500" in res.detail

    def _http_error(*a, **kw):
        raise urllib.error.HTTPError("https://8.8.8.8", 429, "Too Many", None, None)

    monkeypatch.setattr("urllib.request.urlopen", _http_error)
    res = _run_webhook("r1", {"url": "https://8.8.8.8/hook"})
    assert res.success is False
    assert "HTTPError" in res.error

    def _generic(*a, **kw):
        raise OSError("dns failed")

    monkeypatch.setattr("urllib.request.urlopen", _generic)
    res = _run_webhook("r1", {"url": "https://8.8.8.8/hook"})
    assert res.success is False
    assert "OSError" in res.error

    res = _run_webhook("r1", {"url": ""})
    assert res.error == "missing url"


class _Ctx:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *exc):
        return False


class _RespLike:
    def __init__(self, status):
        self.status = status


def _ctx(resp):
    return _Ctx(resp)


# ─── exec ─────────────────────────────────────────────────────


def test_run_exec(monkeypatch) -> None:
    res = _run_exec("r1", {"cmd": ""})
    assert res.error == "missing cmd"

    def _ok(args, **kw):
        return SimpleNamespace(returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr("subprocess.run", _ok)
    res = _run_exec("r1", {"cmd": "echo hi"})
    assert res.success is True
    assert res.detail == "exit=0 done"

    def _fail(args, **kw):
        return SimpleNamespace(returncode=2, stdout="", stderr="boom")

    monkeypatch.setattr("subprocess.run", _fail)
    res = _run_exec("r1", {"cmd": "false"})
    assert res.success is False
    assert "boom" in res.detail

    # shell=True path passes the raw string through
    seen = {}

    def _shell(args, **kw):
        seen["args"] = args
        return SimpleNamespace(returncode=0, stdout="x" * 200, stderr="")

    monkeypatch.setattr("subprocess.run", _shell)
    res = _run_exec("r1", {"cmd": "echo a | cat", "shell": True})
    assert res.success is True
    assert seen["args"] == "echo a | cat"
    assert "…" in res.detail  # trimmed

    def _timeout(args, **kw):
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr("subprocess.run", _timeout)
    res = _run_exec("r1", {"cmd": "sleep 100"})
    assert res.success is False
    assert "TimeoutExpired" in res.error


# ─── mqtt ─────────────────────────────────────────────────────


def test_run_mqtt_not_installed(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", None)
    monkeypatch.setitem(sys.modules, "paho.mqtt.publish", None)
    res = _run_mqtt("r1", {"broker": "b", "topic": "t"})
    assert "paho-mqtt not installed" in res.error


def test_run_mqtt_validation(monkeypatch) -> None:
    fake_publish = types.ModuleType("paho.mqtt.publish")
    fake_publish.single = lambda **kw: None
    fake_client = SimpleNamespace(ssl=SimpleNamespace(PROTOCOL_TLS_CLIENT=5))
    fake_pkg = types.ModuleType("paho")
    fake_mqtt = types.ModuleType("paho.mqtt")
    monkeypatch.setitem(sys.modules, "paho", fake_pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt", fake_mqtt)
    monkeypatch.setitem(sys.modules, "paho.mqtt.publish", fake_publish)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", fake_client)

    assert _run_mqtt("r1", {"topic": "t"}).error == "missing broker"
    assert _run_mqtt("r1", {"broker": "b"}).error == "missing topic"

    res = _run_mqtt(
        "r1",
        {
            "broker": "localhost:1884",
            "topic": "t",
            "qos": 9,
            "retain": True,
            "payload": {"x": 1},
            "username": "u",
            "password": "p",
            "tls": True,
            "client_id": "cid",
            "timeout_ms": 1000,
        },
    )
    assert res.success is True
    assert "localhost:1884" in res.detail

    def _bad_payload(**kw):
        raise TypeError("not serializable")

    fake_publish.single = _bad_payload
    res = _run_mqtt("r1", {"broker": "b", "topic": "t", "payload": object()})
    assert res.success is False
    assert "not JSON-serializable" in res.error


# ─── execute_action ───────────────────────────────────────────


def test_execute_action_order(monkeypatch) -> None:
    assert execute_action(None, "r1") == []

    calls = []

    def _wh(rule_id, cfg):
        calls.append("wh")
        return ActionResult(rule_id, "webhook", True, 1.0)

    def _mq(rule_id, cfg):
        calls.append("mq")
        return ActionResult(rule_id, "mqtt", True, 1.0)

    def _ex(rule_id, cfg):
        calls.append("ex")
        return ActionResult(rule_id, "exec", True, 1.0)

    monkeypatch.setattr("runtime.core.nerves.reflex.actions._run_webhook", _wh)
    monkeypatch.setattr("runtime.core.nerves.reflex.actions._run_mqtt", _mq)
    monkeypatch.setattr("runtime.core.nerves.reflex.actions._run_exec", _ex)

    spec = ActionSpec(webhook={"url": "x"}, mqtt={"broker": "b", "topic": "t"}, exec={"cmd": "ls"})
    out = execute_action(spec, "r1")
    assert calls == ["wh", "mq", "ex"]
    assert len(out) == 3

    # Exceptions inside runners are swallowed defensively.
    monkeypatch.setattr(
        "runtime.core.nerves.reflex.actions._run_webhook",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = execute_action(ActionSpec(webhook={"url": "x"}), "r1")
    assert out == []


def test_run_webhook_ssrf_guard_blocks_private() -> None:
    """SSRF 防护 (audit C4): 指向内网/私网的 webhook 被 url_guard 拒绝."""
    res = _run_webhook("r1", {"url": "http://127.0.0.1:8080/hook", "method": "GET"})
    assert res.success is False
    assert "url_guard rejected" in res.error

    # allow_private 显式开启时不再被 url_guard 拦截（配置所有者负责该端点）；
    # 此处未 mock urlopen，连接本地端口会得到 OSError，但绝不是 url_guard。
    res2 = _run_webhook(
        "r1", {"url": "http://127.0.0.1:8080/hook", "method": "GET", "allow_private": True}
    )
    assert "url_guard" not in res2.error

