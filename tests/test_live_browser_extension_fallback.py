from __future__ import annotations

import json
from typing import Any

from runtime.execution.suckers import browser_act_skills as browser_act
from runtime.execution.suckers import browser_backends
from runtime.execution.suckers.browser_backend import BrowserResult, Track
from runtime.platform.process.session import Session, session_scope


class _FakeExtensionBackend:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]], *, available: bool = True):
        self._calls = calls
        self._available = available

    def available(self) -> bool:
        self._calls.append(("available", {}))
        return self._available

    def _result(self, action: str, payload: dict[str, Any]) -> BrowserResult:
        self._calls.append((action, payload))
        return BrowserResult.from_track(
            Track.EXTENSION,
            {"ok": True, "track": "extension", "action": action, **payload},
        )

    def click(self, selector: str) -> BrowserResult:
        return self._result("click", {"selector": selector})

    def type(self, selector: str, text: str, *, clear: bool = False) -> BrowserResult:
        return self._result("type", {"selector": selector, "text": text, "clear": clear})

    def scroll(self, *, selector: str | None = None, delta_y: int = 0) -> BrowserResult:
        return self._result("scroll", {"selector": selector, "delta_y": delta_y})

    def wait(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserResult:
        return self._result("wait", {"selector": selector, "timeout_ms": timeout_ms})

    def navigate(self, url: str) -> BrowserResult:
        return self._result("navigate", {"url": url})

    def extract(self) -> BrowserResult:
        return self._result("extract", {"text": "Alpha needle Omega", "url": "https://x"})

    def screenshot(self) -> BrowserResult:
        return self._result("screenshot", {"dataUrl": "data:image/png;base64,AA=="})

    def state(self, *, max_items: int = 30) -> BrowserResult:
        return self._result(
            "state",
            {"max_items": max_items, "url": "https://x", "title": "X"},
        )


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_bridge_status_requires_authenticated_active_webview(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    (tmp_path / "bridge.json").write_text(
        json.dumps({"port": 18234, "token": "desktop-secret"}),
        encoding="utf-8",
    )
    seen: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return _FakeResponse({"ok": True, "activeWebContentsId": 73})

    monkeypatch.setattr(browser_act.urllib_request, "urlopen", fake_urlopen)

    assert browser_act._electron_webview_available() is True
    assert seen == {
        "url": "http://127.0.0.1:18234/status",
        "authorization": "Bearer desktop-secret",
        "timeout": browser_act._BRIDGE_STATUS_TIMEOUT,
    }


def test_live_browser_keeps_electron_priority_and_never_duplicates_failed_action(
    monkeypatch,
) -> None:
    extension_calls: list[tuple[str, dict[str, Any]]] = []
    bridge_calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(browser_act, "_electron_webview_available", lambda: True)
    monkeypatch.setattr(
        browser_backends,
        "ExtensionBackend",
        lambda: _FakeExtensionBackend(extension_calls),
    )

    def failed_bridge(action: str, params: dict[str, Any]) -> dict[str, Any]:
        bridge_calls.append((action, params))
        return {"ok": False, "error": "selector not found"}

    monkeypatch.setattr(browser_act, "_bridge_call", failed_bridge)

    assert browser_act._h_click("#save") == {
        "ok": False,
        "error": "selector not found",
    }
    assert bridge_calls == [("click", {"selector": "#save"})]
    assert extension_calls == []


def test_live_browser_click_falls_back_to_extension_before_any_mutation(monkeypatch) -> None:
    extension_calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(browser_act, "_electron_webview_available", lambda: False)
    monkeypatch.setattr(
        browser_backends,
        "ExtensionBackend",
        lambda: _FakeExtensionBackend(extension_calls),
    )
    monkeypatch.setattr(
        browser_act,
        "_bridge_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Electron action must not run after extension selection")
        ),
    )

    result = browser_act._h_click("#save")

    assert result["ok"] is True
    assert result["track"] == "extension"
    assert result["live_browser_fallback"] == {
        "from": "electron",
        "to": "extension",
        "reason": "electron_webview_unavailable",
    }
    assert extension_calls == [
        ("available", {}),
        ("click", {"selector": "#save"}),
    ]


def test_live_browser_fallback_reuses_operator_selected_tab_lease_input(monkeypatch) -> None:
    relay_calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(browser_act, "_electron_webview_available", lambda: False)

    def fake_relay_request(
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10,
    ) -> dict[str, Any]:
        del timeout_seconds
        relay_calls.append((method, path, body))
        return {"connected": True} if path == "/status" else {"ok": True}

    monkeypatch.setattr(browser_backends, "_browser_relay_request", fake_relay_request)

    with session_scope(
        Session(
            metadata={
                "automation_target": {
                    "kind": "browser_tab",
                    "source": "browser_relay",
                    "id": "73",
                    "url": "https://selected.example/path",
                    "title": "Selected tab",
                }
            }
        )
    ):
        result = browser_act._h_click("#confirm")

    assert result["ok"] is True
    assert relay_calls == [
        ("GET", "/status", None),
        (
            "POST",
            "/command",
            {
                "action": "click",
                "selector": "#confirm",
                "target_tab_id": "73",
                "target_tab_url": "https://selected.example/path",
                "target_tab_title": "Selected tab",
            },
        ),
    ]


def test_live_browser_find_and_current_url_use_read_only_extension_contract(monkeypatch) -> None:
    extension_calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(browser_act, "_electron_webview_available", lambda: False)
    monkeypatch.setattr(
        browser_backends,
        "ExtensionBackend",
        lambda: _FakeExtensionBackend(extension_calls),
    )

    found = browser_act._h_find(text="needle")
    current = browser_act._h_current_url()

    assert found["count"] == 1
    assert found["matches"][0]["snippet"] == "Alpha needle Omega"
    assert found["track"] == "extension"
    assert current == {
        "ok": True,
        "url": "https://x",
        "title": "X",
        "track": "extension",
        "live_browser_fallback": {
            "from": "electron",
            "to": "extension",
            "reason": "electron_webview_unavailable",
        },
    }


def test_live_browser_execute_js_never_expands_to_extension(monkeypatch) -> None:
    monkeypatch.setattr(
        browser_act,
        "_extension_fallback_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("execute-js must remain Electron-only")
        ),
    )
    monkeypatch.setattr(
        browser_act,
        "_bridge_call",
        lambda action, params: {"ok": False, "error": f"{action} unavailable", **params},
    )

    result = browser_act._h_execute_js("document.title")

    assert result == {
        "ok": False,
        "error": "execute-js unavailable",
        "code": "document.title",
    }

