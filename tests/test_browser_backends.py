"""Real BrowserBackend adapters — mapping logic via stub transports.

These verify each adapter maps the seven verbs to the correct track
action/payload and normalises the result, without Electron, a
Playwright install, or a connected extension. Live end-to-end behaviour
needs those runtimes and is out of scope.
"""

import json

from runtime.execution.suckers import browser_backends
from runtime.execution.suckers.browser_backend import (
    BrowserBackend,
    Track,
    resolve_backend,
)
from runtime.execution.suckers.browser_backends import (
    ElectronBackend,
    ExtensionBackend,
    PlaywrightBackend,
)
from runtime.platform.process.session import Session, session_scope


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _Recorder:
    """Stub transport: records (action, payload), returns scripted dict."""

    def __init__(self, response=None):
        self.calls = []
        self._response = response or {"ok": True}

    def __call__(self, action, payload):
        self.calls.append((action, payload))
        return dict(self._response)


class TestElectronBackend:
    def test_satisfies_protocol(self):
        assert isinstance(ElectronBackend(_Recorder()), BrowserBackend)

    def test_verb_action_mapping(self):
        rec = _Recorder()
        b = ElectronBackend(rec, available_probe=lambda: True)
        b.navigate("https://x")
        b.click("#go")
        b.type("#q", "hi", clear=True)
        b.scroll(delta_y=120)
        b.wait("#done", timeout_ms=5000)
        b.extract()
        b.screenshot()
        actions = [a for a, _ in rec.calls]
        assert actions == [
            "navigate",
            "click",
            "type",
            "scroll",
            "wait",
            "extract",
            "screenshot",
        ]
        # Payload shape per track contract.
        assert rec.calls[1][1] == {"selector": "#go"}
        assert rec.calls[2][1] == {"selector": "#q", "text": "hi", "clear": True}
        assert rec.calls[3][1] == {"deltaY": 120}
        assert rec.calls[4][1] == {"selector": "#done", "timeout": 5000}

    def test_failure_result_normalized(self):
        b = ElectronBackend(_Recorder({"ok": False, "error": "no bridge"}))
        r = b.click("#x")
        assert r.ok is False
        assert r.track is Track.ELECTRON
        assert r.error == "no bridge"

    def test_available_probe(self):
        assert ElectronBackend(_Recorder(), available_probe=lambda: False).available() is False
        assert ElectronBackend(_Recorder(), available_probe=lambda: True).available() is True

    def test_default_availability_requires_live_bridge(self, monkeypatch):
        from runtime.execution.suckers import browser_act_skills

        monkeypatch.setattr(browser_act_skills, "_bridge_status", lambda: None)
        assert ElectronBackend().available() is False

        monkeypatch.setattr(
            browser_act_skills,
            "_bridge_status",
            lambda: {"ok": True, "browser_ready": True},
        )
        assert ElectronBackend().available() is True


class TestPlaywrightBackend:
    def test_threads_current_url_through_actions(self):
        rec = _Recorder({"final_url": "https://x/page"})
        b = PlaywrightBackend(rec, available_probe=lambda: True, start_url="about:blank")
        b.navigate("https://x")
        # navigate set current_url, then handler reported final_url.
        b.click("#go")
        nav_payload = rec.calls[0][1]
        click_payload = rec.calls[1][1]
        assert nav_payload == {"url": "https://x"}
        # click operates on the post-navigate url.
        assert click_payload["url"] == "https://x/page"
        assert click_payload["selector"] == "#go"

    def test_type_uses_clear_first_param_name(self):
        rec = _Recorder({"url": "u"})
        b = PlaywrightBackend(rec, available_probe=lambda: True)
        b.type("#q", "hi", clear=True)
        assert rec.calls[0][1]["clear_first"] is True

    def test_scroll_param_names(self):
        rec = _Recorder({"url": "u"})
        b = PlaywrightBackend(rec, available_probe=lambda: True)
        b.scroll(selector="#foot", delta_y=300)
        payload = rec.calls[0][1]
        assert payload["to_selector"] == "#foot"
        assert payload["to_y"] == 300

    def test_no_error_key_means_success(self):
        # Playwright handlers omit "ok"; success is "no error key".
        b = PlaywrightBackend(_Recorder({"clicked": True, "title": "X"}))
        r = b.click("#x")
        assert r.ok is True

    def test_error_key_means_failure(self):
        b = PlaywrightBackend(_Recorder({"error": "selector not found"}))
        r = b.click("#missing")
        assert r.ok is False
        assert r.error == "selector not found"


class TestExtensionBackend:
    def test_relay_request_uses_configured_gateway_token_without_echoing_it(self, monkeypatch):
        seen = {}
        token = "sk-internal-browser-relay"
        monkeypatch.setenv("ECHO_BROWSER_RELAY_TOKEN", token)

        def fake_urlopen(request, timeout):
            seen["authorization"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            return _FakeResponse({"connected": True})

        monkeypatch.setattr(browser_backends.urllib_request, "urlopen", fake_urlopen)

        result = browser_backends._browser_relay_request("GET", "/status")

        assert seen == {
            "authorization": f"Bearer {token}",
            "timeout": browser_backends._BROWSER_RELAY_TIMEOUT_SECONDS,
        }
        assert result["connected"] is True
        assert result["browser_relay"]["auth_configured"] is True
        assert result["browser_relay"]["auth_configured_by"] == ("ECHO_BROWSER_RELAY_TOKEN")
        assert token not in str(result)

    def test_disconnected_default_relay_reports_unavailable(self, monkeypatch):
        def fake_request(method, path, body=None, *, timeout_seconds=10):
            assert method == "GET"
            assert path == "/status"
            return {
                "connected": False,
                "browser_relay": {"schema": "echo.browser_relay_bridge.v1"},
            }

        monkeypatch.setattr(
            "runtime.execution.suckers.browser_backends._browser_relay_request",
            fake_request,
        )

        b = ExtensionBackend()
        assert b.available() is False

    def test_default_relay_transport_maps_actions(self, monkeypatch):
        calls = []

        def fake_request(method, path, body=None, *, timeout_seconds=10):
            calls.append((method, path, body, timeout_seconds))
            if path == "/status":
                return {"connected": True}
            return {"ok": True, "url": "https://x"}

        monkeypatch.setattr(
            "runtime.execution.suckers.browser_backends._browser_relay_request",
            fake_request,
        )

        b = ExtensionBackend()
        assert b.available() is True
        result = b.navigate("https://x")

        assert result.ok is True
        assert result.track is Track.EXTENSION
        assert calls == [
            ("GET", "/status", None, 2),
            ("POST", "/command", {"action": "navigate", "url": "https://x"}, 10),
        ]

    def test_default_relay_transport_keeps_wait_deadlines_aligned(self, monkeypatch):
        calls = []

        def fake_request(method, path, body=None, *, timeout_seconds=10):
            calls.append((method, path, body, timeout_seconds))
            return {"ok": True}

        monkeypatch.setattr(
            "runtime.execution.suckers.browser_backends._browser_relay_request",
            fake_request,
        )

        result = ExtensionBackend().wait("#ready", timeout_ms=10_000)

        assert result.ok is True
        assert calls == [
            (
                "POST",
                "/command",
                {
                    "action": "wait",
                    "selector": "#ready",
                    "timeout": 10_000,
                    "timeout_seconds": 11.0,
                },
                13.0,
            )
        ]

    def test_default_relay_transport_honors_selected_tab(self, monkeypatch):
        calls = []

        def fake_request(method, path, body=None, *, timeout_seconds=10):
            calls.append((method, path, body, timeout_seconds))
            return {"ok": True}

        monkeypatch.setattr(
            "runtime.execution.suckers.browser_backends._browser_relay_request",
            fake_request,
        )

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
            result = ExtensionBackend().click("#confirm")

        assert result.ok is True
        assert calls[0][2] == {
            "action": "click",
            "selector": "#confirm",
            "target_tab_id": "73",
            "target_tab_url": "https://selected.example/path",
            "target_tab_title": "Selected tab",
        }

    def test_wired_transport_maps_actions(self):
        rec = _Recorder({"ok": True})
        b = ExtensionBackend(rec, available_probe=lambda: True)
        assert b.available() is True
        b.navigate("https://x")
        b.type("#q", "hi")
        b.screenshot()
        assert [a for a, _ in rec.calls] == ["navigate", "type", "screenshot"]


class TestResolverWithRealAdapters:
    def test_priority_picks_extension_over_playwright(self):
        ext = ExtensionBackend(_Recorder(), available_probe=lambda: True)
        play = PlaywrightBackend(_Recorder(), available_probe=lambda: True)
        chosen = resolve_backend([play, ext])
        assert chosen is ext

    def test_falls_through_to_playwright_when_others_down(self):
        ext = ExtensionBackend(available_probe=lambda: False)
        electron = ElectronBackend(_Recorder(), available_probe=lambda: False)
        play = PlaywrightBackend(_Recorder(), available_probe=lambda: True)
        chosen = resolve_backend([ext, electron, play])
        assert chosen is play

