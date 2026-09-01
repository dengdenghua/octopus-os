"""Endpoint coverage for the browser router session/config/relay surface (audit Q-05)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import runtime.platform.ui.browser_router as br_mod
from runtime.platform.ui.browser_router import create_browser_router


class _FakeLocator:
    def __init__(self, selector: str) -> None:
        self.selector = selector

    def scroll_into_view_if_needed(self, *a, **kw) -> None:
        return None

    def inner_text(self, *a, **kw) -> str:
        return "hello body"


class _FakeMouse:
    def click(self, x: int, y: int) -> None:
        return None

    def dblclick(self, x: int, y: int) -> None:
        return None


class _FakeKeyboard:
    def press(self, key: str) -> None:
        return None


class _FakePage:
    url = "https://example.com/"

    def title(self) -> str:
        return "Fake Title"

    def __init__(self) -> None:
        self.click = lambda *a, **kw: None
        self.fill = lambda *a, **kw: None
        self.hover = lambda *a, **kw: None
        self.wait_for_selector = lambda *a, **kw: None
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.set_viewport_size = lambda *a, **kw: None

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(selector)

    def evaluate(self, js: str) -> list[dict[str, str]]:
        return [
            {
                "tag": "button",
                "role": "",
                "name": "Go",
                "text": "Go",
                "selector": "#go",
            }
        ]


class _StubBackend(br_mod._BrowserBackend):
    """Real in-memory session center; browser/fs-facing calls stubbed."""

    _ext_path: Path | None = None

    def __init__(self, *, extension_path: Path | None = None, **kw):
        super().__init__(**kw)
        if extension_path is not None:
            self._ext_path = extension_path

    def _detect_browsers(self):
        return [{"name": "chromium", "executable": "/fake/chromium", "version": "120.0"}]

    def _resolve_browser_extension_path(self):
        assert self._ext_path is not None
        return self._ext_path

    def _ensure_real_browser_session(self, session):
        return session.get("page") is not None

    def _close_real_browser_session(self, session):
        return None

    def _navigate_browser_session(self, session, url):
        session["current_url"] = url
        session["current_title"] = url
        return {"ok": True, "url": url, "title": url}

    def _move_browser_history(self, session, delta):
        return {"ok": True, "url": "", "title": ""}

    def _reload_browser_session(self, session):
        return {"ok": True, "url": "", "title": ""}

    def _browser_screenshot_payload(self, session):
        return {"ok": True, "format": "png", "base64": "aW1hZ2U=", "width": 10, "height": 10}

    def _queue_browser_replay_case(self, replay_case, *, reason="", priority=""):
        return {"queued": 1, "schema": "echo.browser_replay_queue.v1"}

    def _persist_browser_policy(self):
        return None


@pytest.fixture()
def client(monkeypatch, tmp_path):
    ext = tmp_path / "extension"
    ext.mkdir()
    (ext / "manifest.json").write_text("{}", encoding="utf-8")
    (ext / "bookmarklet.js").write_text("window.__relay=1;", encoding="utf-8")
    _StubBackend._ext_path = ext
    monkeypatch.setattr(br_mod, "_BrowserBackend", _StubBackend)
    monkeypatch.setattr(br_mod, "open_extension_folder", lambda p: {"opened": True, "path": str(p)})
    app = FastAPI()
    app.include_router(create_browser_router())
    return TestClient(app), ext


def _c(client) -> TestClient:
    return client[0]


def _ensure(client, session_id: str = "s1") -> None:
    r = _c(client).post("/api/browser/session/ensure", json={"session_id": session_id})
    assert r.status_code == 200


def test_launch_ensure_viewport(client) -> None:
    c = _c(client)
    assert c.post("/api/browser/launch", json={}).status_code == 400
    r = c.post("/api/browser/launch", json={"session_id": "s1"})
    assert r.status_code == 200
    assert r.json()["status"] == "launched"

    r = c.post("/api/browser/session/ensure", json={"session_id": "s2", "headless": True})
    assert r.status_code == 200
    assert r.json()["status"] == "ready"

    r = c.post(
        "/api/browser/session/viewport", json={"session_id": "s2", "width": 800, "height": 600}
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Empty session_id defaults to "default" rather than erroring.
    assert c.post("/api/browser/session/viewport", json={"session_id": ""}).status_code == 200


def test_session_reset(client) -> None:
    c = _c(client)
    _ensure(client, "s3")
    r = c.post("/api/browser/session/reset", json={"session_id": "s3"})
    assert r.status_code == 200
    assert r.json()["status"] == "closed"

    _ensure(client, "s3")
    r = c.post("/api/browser/session/reset", json={"session_id": "s3", "relaunch": True})
    assert r.status_code == 200
    assert r.json()["status"] == "ready"

    # Empty session_id defaults to "default".
    assert c.post("/api/browser/session/reset", json={"session_id": ""}).status_code == 200


def test_clear_browser_data_closes_managed_sessions(client, monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    c = _c(client)
    _ensure(client, "clear-one")
    _ensure(client, "clear-two")

    response = c.post("/api/browser/data/clear")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["closed_sessions"] == 2
    assert c.get("/api/browser/sessions").json()["count"] == 0


def test_navigate_validation(client) -> None:
    c = _c(client)
    assert c.post("/api/browser/navigate", json={"url": "https://example.com"}).status_code == 400
    assert c.post("/api/browser/navigate", json={"session_id": "s1"}).status_code == 400
    r = c.post(
        "/api/browser/navigate",
        json={"session_id": "s1", "url": "https://example.com"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_actions_without_page(client) -> None:
    c = _c(client)
    _ensure(client)
    assert c.post("/api/browser/action", json={}).status_code == 400
    assert c.post("/api/browser/action", json={"action": "click"}).status_code == 400
    for action in ("back", "forward", "reload"):
        r = c.post("/api/browser/action", json={"session_id": "s1", "action": action})
        assert r.status_code == 200, (action, r.text)
        assert r.json()["ok"] is True
    # Without a real page the dispatch falls through and records the action.
    r = c.post("/api/browser/action", json={"session_id": "s1", "action": "fly"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def _page_client(client):
    c = _c(client)
    _ensure(client)

    _ = c.app.state  # not used; sessions live in the backend instance
    # Reach into the router's session registry via the sessions endpoint.
    return c


def test_actions_with_page(client, monkeypatch) -> None:
    c = _c(client)
    _ensure(client)
    page = _FakePage()

    # Grab the backend session object so we can attach a fake page.
    _ = br_mod._BrowserBackend  # patched class; instance lives in router closure.
    # The TestClient app holds no reference; instead, drive via a page-aware
    # backend by pre-seeding the session through launch + page injection.

    # Simplest: monkeypatch _ensure_real_browser_session to attach a page.
    def _attach(self, session):
        session["page"] = page
        return True

    monkeypatch.setattr(_StubBackend, "_ensure_real_browser_session", _attach)

    base = {"session_id": "s1"}
    # selector-required actions
    for action in ("click", "type", "hover", "wait"):
        assert c.post("/api/browser/action", json={**base, "action": action}).status_code == 400
    # coordinate-required actions
    for action in ("click_at", "double_click_at"):
        assert c.post("/api/browser/action", json={**base, "action": action}).status_code == 400
    # scroll needs selector or y
    assert c.post("/api/browser/action", json={**base, "action": "scroll"}).status_code == 400
    # unsupported action is rejected when a page is attached
    assert c.post("/api/browser/action", json={**base, "action": "fly"}).status_code == 400

    ok_cases = [
        {"action": "click", "selector": "#go"},
        {"action": "type", "selector": "#in", "text": "hi"},
        {"action": "hover", "selector": "#go"},
        {"action": "click_at", "x": 10, "y": 20},
        {"action": "double_click_at", "x": 10, "y": 20},
        {"action": "wait", "selector": "#go"},
        {"action": "press", "key": "Enter"},
        {"action": "scroll", "selector": "#go"},
        {"action": "scroll", "y": 100},
        {"action": "aria"},
    ]
    for body in ok_cases:
        r = c.post("/api/browser/action", json={**base, **body})
        assert r.status_code == 200, (body, r.text)
        assert r.json()["ok"] is True


def test_action_failure_records_replay_evidence(client, monkeypatch) -> None:
    c = _c(client)
    _ensure(client)
    page = _FakePage()

    def _boom(*a, **kw):
        raise RuntimeError("browser exploded")

    page.click = _boom

    def _attach(self, session):
        session["page"] = page
        return True

    monkeypatch.setattr(_StubBackend, "_ensure_real_browser_session", _attach)
    r = c.post(
        "/api/browser/action",
        json={"session_id": "s1", "action": "click", "selector": "#go"},
    )
    assert r.status_code == 500
    body = r.json()
    assert "replay_evidence" in body.get("detail", {})


def test_read_endpoints(client) -> None:
    c = _c(client)
    _ensure(client)
    # Fresh session: no actions recorded yet.
    r = c.get("/api/browser/session/replay-case", params={"session_id": "s1"})
    assert r.status_code == 200
    assert r.json()["replay_ready"] is False
    assert c.get("/api/browser/screenshot/base64", params={"session_id": "s1"}).status_code == 200
    assert c.get("/api/browser/page-info", params={"session_id": "s1"}).status_code == 200
    r = c.get("/api/browser/extract-text", params={"session_id": "s1"})
    assert r.status_code == 200
    assert "text" in r.json()
    r = c.get("/api/browser/action-log", params={"session_id": "s1"})
    assert r.status_code == 200
    assert isinstance(r.json()["actions"], list)


def test_replay_case_queue(client) -> None:
    c = _c(client)
    _ensure(client)
    assert (
        c.post("/api/browser/session/replay-case/queue", json={"session_id": "s1"}).status_code
        == 409
    )
    c.post("/api/browser/action", json={"session_id": "s1", "action": "press", "key": "Enter"})
    r = c.post(
        "/api/browser/session/replay-case/queue",
        json={"session_id": "s1", "reason": "regression", "priority": "P0"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_close_and_config(client) -> None:
    c = _c(client)
    _ensure(client)
    assert c.post("/api/browser/close", json={"session_id": "s1"}).status_code == 200
    assert c.post("/api/browser/close", json={}).status_code == 400

    r = c.get("/api/browser/config")
    assert r.status_code == 200
    assert r.json()["connection_mode"] == "playwright"

    assert c.put("/api/browser/config", json={"cdp_port": "not-an-int"}).status_code == 400
    assert c.put("/api/browser/config", json={"connection_mode": "bogus"}).status_code == 400
    r = c.put(
        "/api/browser/config",
        json={
            "max_open_tabs": 5,
            "connection_mode": "cdp",
            "headless": False,
            "relay_allowed_hosts": ["*.example.com"],
            "relay_blocked_hosts": ["bad.example.com"],
            "relay_require_allowlist": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["connection_mode"] == "cdp"
    assert r.json()["relay_require_allowlist"] is True


def test_relay_status_heartbeat_result(client) -> None:
    c = _c(client)
    r = c.get("/api/browser/relay/status")
    assert r.status_code == 200
    body = r.json()
    assert "connected" in body and "site_policy" in body and "control" in body

    r = c.post(
        "/api/browser/relay/heartbeat",
        json={"extension_version": "1.2.3", "active_tab": {"url": "https://x/", "title": "X"}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    assert c.post("/api/browser/relay/result", json={}).status_code == 400
    r = c.post("/api/browser/relay/result", json={"id": "cmd-1", "result": {"ok": True}})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_relay_control_actions(client) -> None:
    c = _c(client)
    r = c.post(
        "/api/browser/relay/control", json={"action": "interrupt", "reason": "user stepped in"}
    )
    assert r.status_code == 200
    assert r.json()["interrupt"]["reason"] == "user stepped in"
    assert r.json()["control"]["blocked"] is True

    r = c.post("/api/browser/relay/control", json={"action": "resume"})
    assert r.status_code == 200
    assert r.json()["control"]["blocked"] is False

    r = c.post("/api/browser/relay/control", json={"action": "status"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    assert c.post("/api/browser/relay/control", json={"action": "bogus"}).status_code == 400


def test_relay_command_policy_and_timeout(client) -> None:
    c = _c(client)
    # Extension not connected -> 409
    assert c.post("/api/browser/relay/command", json={"action": "extract"}).status_code == 409
    # Connect it via heartbeat
    c.post("/api/browser/relay/heartbeat", json={})
    assert c.post("/api/browser/relay/command", json={}).status_code == 400
    assert (
        c.post(
            "/api/browser/relay/command", json={"action": "extract", "timeout_seconds": "oops"}
        ).status_code
        == 400
    )

    # Site policy: allowlist blocks unknown hosts.
    c.put(
        "/api/browser/config",
        json={"relay_require_allowlist": True, "relay_allowed_hosts": ["ok.example.com"]},
    )
    r = c.post(
        "/api/browser/relay/command",
        json={"action": "navigate", "url": "https://evil.example.com", "timeout_seconds": 0.2},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["site_policy"]["decision"] == "block"

    # Human interrupt blocks commands.
    c.post("/api/browser/relay/control", json={"action": "interrupt"})
    r = c.post("/api/browser/relay/command", json={"action": "extract", "timeout_seconds": 0.2})
    assert r.status_code == 409

    c.post("/api/browser/relay/control", json={"action": "resume"})
    # Allowlisted URL passes the site policy; no extension answers -> 504.
    r = c.post(
        "/api/browser/relay/command",
        json={
            "action": "extract",
            "url": "https://ok.example.com/",
            "timeout_seconds": 0.2,
        },
    )
    assert r.status_code == 504


def test_bookmarklet(client) -> None:
    c = _c(client)
    assert c.get("/api/browser/relay/bookmarklet.js").status_code == 200
    assert (
        c.get(
            "/api/browser/relay/bookmarklet-poll", params={"callback": "bad-callback"}
        ).status_code
        == 400
    )
    r = c.get(
        "/api/browser/relay/bookmarklet-poll",
        params={"callback": "window.__cb", "url": "https://x/", "title": "X"},
    )
    assert r.status_code == 200
    assert "window.__cb(" in r.text

    assert (
        c.post(
            "/api/browser/relay/bookmarklet-result",
            content=b"not-json",
            headers={"content-type": "application/json"},
        ).status_code
        == 400
    )
    r = c.post(
        "/api/browser/relay/bookmarklet-result",
        content=b'{"id": "c2", "result": {"ok": true}}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200


def test_extension_endpoints(client) -> None:
    c, _ext = client
    r = c.get("/api/browser/extension-path")
    assert r.status_code == 200
    assert r.json()["exists"] is True
    r = c.post("/api/browser/open-extension-folder")
    assert r.status_code == 200
    assert r.json()["opened"] is True


def test_artifact_not_found(client) -> None:
    c = _c(client)
    assert c.get("/api/browser-artifacts/not-there.png").status_code == 404
    assert c.get("/api/browser-artifacts/../evil.png").status_code == 404


def test_relay_ws(client) -> None:
    c = _c(client)
    with c.websocket_connect("/api/browser/relay/ws") as ws:
        ws.send_json({"type": "heartbeat"})
        ws.send_json({"type": "result", "id": "w1", "result": {"ok": True}})
        ws.receive_json()


