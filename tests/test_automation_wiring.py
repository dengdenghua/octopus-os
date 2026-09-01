"""Regression tests for automation-stack wiring fixes.

* computer_use_loop is now registered at serve time (app.py) from a
  router-backed VisionPlanner, so the desktop_operator_arm's
  ``computer_use_loop`` allowlist entry actually resolves. Previously only
  the demo server registered it.
"""

from __future__ import annotations


def test_computer_use_loop_registers_from_router_planner():
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.computer_use_loop import (
        ModelRouterVisionPlanner,
        register_computer_use_loop,
    )
    from runtime.sensing.model_router import MockModelRouter

    reg = SkillRegistry()
    planner = ModelRouterVisionPlanner(router=MockModelRouter())
    n = register_computer_use_loop(reg, planner)

    assert n == 1
    assert reg.has("computer_use_loop")


def test_desktop_operator_arm_references_a_registrable_loop():
    # The preset arm allowlists computer_use_loop; this guards that the name
    # the arm references is exactly the one the registrar registers.
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.computer_use_loop import (
        ModelRouterVisionPlanner,
        register_computer_use_loop,
    )
    from runtime.sensing.model_router import MockModelRouter

    reg = SkillRegistry()
    register_computer_use_loop(reg, ModelRouterVisionPlanner(router=MockModelRouter()))
    assert "computer_use_loop" in reg.all_names()


# ── browser_router now honours require_auth (was unauthenticated) ──


def _browser_client(**kwargs):
    import pytest

    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.platform.ui.browser_router import create_browser_router

    app = FastAPI()
    app.include_router(create_browser_router(**kwargs))
    return TestClient(app)


def test_browser_endpoints_open_by_default():
    # Default (require_auth off) → _resolve_actor is a no-op → local preview
    # unchanged. A browser endpoint must NOT 401.
    c = _browser_client()
    assert c.get("/api/browser/config").status_code != 401


def test_browser_endpoints_enforce_auth_when_enabled():
    # require_auth on + an identity store + no bearer token → 401 across the
    # router (previously these endpoints had no auth at all).
    c = _browser_client(require_auth=True, identity_store=object())
    assert c.get("/api/browser/config").status_code == 401


# ── vision-loop semantic grounding (window list into the planner prompt) ──


class _RecordingRouter:
    """Captures the ModelRequest the planner sends and returns a done action."""

    def __init__(self) -> None:
        self.last = None

    def call(self, request):
        self.last = request

        class _R:
            text = '{"action": "done"}'

        return _R()


def test_window_grounding_returns_str_and_never_raises():
    from runtime.execution.suckers.desktop_grounding import window_grounding

    out = window_grounding()
    # macOS: a non-empty window list; other platforms / no perms: "".
    assert isinstance(out, str)


def test_ax_control_grounding_returns_str_and_never_raises():
    from runtime.execution.suckers.desktop_grounding import ax_control_grounding

    # macOS+trusted: actionable AX controls of the frontmost app; otherwise "".
    # Must never raise into the vision loop regardless of platform/permission.
    assert isinstance(ax_control_grounding(), str)


def test_combined_grounding_merges_best_effort_parts():
    from runtime.execution.suckers.desktop_grounding import combined_grounding

    out = combined_grounding()
    assert isinstance(out, str)  # window list + AX controls, each best-effort


# ── browser skills route through the 3-track resolver (EXT>ELEC>PW) ──


class _FakeTrack:
    """A recording higher-priority backend for the resolver.

    It intentionally implements only the public BrowserBackend protocol, not
    adapter-private helpers such as ``_call``.
    """

    def __init__(self, *, fail_navigate: bool = False, track=None):
        from runtime.execution.suckers.browser_backend import Track

        self.track = track or Track.ELECTRON
        self.calls = []
        self.fail_navigate = fail_navigate

    def available(self):
        return True

    def _record(self, action, payload, *, ok=True, error=""):
        from runtime.execution.suckers.browser_backend import BrowserResult

        self.calls.append((action, dict(payload)))
        response = {"ok": ok, "action": action}
        if error:
            response["error"] = error
        response.update(payload)
        return BrowserResult.from_track(self.track, response)

    def navigate(self, url):
        return self._record(
            "navigate",
            {"url": url},
            ok=not self.fail_navigate,
            error="nav failed" if self.fail_navigate else "",
        )

    def click(self, selector):
        return self._record("click", {"selector": selector})

    def type(self, selector, text, *, clear=False):
        return self._record("type", {"selector": selector, "text": text, "clear": clear})

    def scroll(self, *, selector=None, delta_y=0):
        return self._record("scroll", {"selector": selector, "delta_y": delta_y})

    def wait(self, selector, *, timeout_ms=10_000):
        return self._record("wait", {"selector": selector, "timeout_ms": timeout_ms})

    def state(self, *, max_items=30):
        return self._record("state", {"max_items": max_items})

    def extract(self):
        return self._record("extract", {})

    def screenshot(self, path="", *, full_page=False):
        return self._record("screenshot", {"path": path, "full_page": full_page})


def test_with_page_navigate_then_acts_on_higher_track(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs

    fake = _FakeTrack()
    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [fake])
    # PW closure must NOT run — the higher track serves it.
    out = bs._with_page(
        None,
        lambda p: {"pw": True},
        verb="click",
        payload={"selector": "#go"},
        url="http://h.test",
    )
    assert ("navigate", {"url": "http://h.test"}) in fake.calls  # navigate first
    assert ("click", {"selector": "#go"}) in fake.calls  # then act
    assert out.get("action") == "click" and out.get("pw") is None


def test_navigate_verb_has_no_separate_prenavigate(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs

    fake = _FakeTrack()
    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [fake])
    bs._with_page(
        None,
        lambda p: {"pw": True},
        verb="navigate",
        payload={"url": "http://h.test"},
        url="http://h.test",
    )
    assert fake.calls == [("navigate", {"url": "http://h.test"})]


def test_higher_track_navigation_failure_stops_followup_action(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs

    fake = _FakeTrack(fail_navigate=True)
    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [fake])

    out = bs._with_page(
        None,
        lambda p: {"pw": True},
        verb="click",
        payload={"selector": "#go"},
        url="http://h.test",
    )

    assert fake.calls == [("navigate", {"url": "http://h.test"})]
    assert out["ok"] is False
    assert out["error"] == "nav failed"
    assert out.get("pw") is None


def test_falls_back_to_pw_when_no_higher_track(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs

    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [])
    # no higher track available → None, so _with_page uses the Playwright path
    assert bs._dispatch_higher_track("click", {"selector": "#x"}, url="http://h") is None


def test_unavailable_higher_track_is_skipped(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs

    class _Down(_FakeTrack):
        def available(self):
            return False

    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [_Down()])
    assert bs._dispatch_higher_track("click", {"selector": "#x"}) is None


def test_explicit_track_preference_selects_requested_available_backend(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs
    from runtime.execution.suckers.browser_backend import Track
    from runtime.platform.process.session import Session, session_scope

    extension = _FakeTrack(track=Track.EXTENSION)
    electron = _FakeTrack(track=Track.ELECTRON)
    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [extension, electron])

    with session_scope(
        Session(metadata={"browser_track_preference": "electron"}),
    ):
        out = bs._dispatch_higher_track("state", {"max_items": 10})

    assert out is not None
    assert out["track"] == "electron"
    assert out["browser_track_preference_satisfied"] is True
    assert electron.calls == [("state", {"max_items": 10})]
    assert extension.calls == []


def test_chrome_playwright_fallback_emits_auditable_track_receipt() -> None:
    from runtime.execution.suckers import browser_skills as bs
    from runtime.execution.suckers.browser_backend import Track
    from runtime.platform.process.session import Session, session_scope

    with session_scope(
        Session(
            metadata={
                "browser_track_preference": "extension",
                "browser_session_policy": "thread_native_external_chrome",
            },
        ),
    ):
        out = bs._annotate_browser_track_result(
            {"url": "https://example.test"},
            served_track=Track.PLAYWRIGHT,
        )

    assert out["track"] == "playwright"
    assert out["browser_track_preference"] == "extension"
    assert out["browser_track_preference_satisfied"] is False
    assert out["browser_track_fallback"] == {
        "requested": "extension",
        "served": "playwright",
        "reason": "extension_unavailable",
    }


def test_browser_state_uses_current_higher_track_without_url(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs

    fake = _FakeTrack()
    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [fake])

    out = bs._browser_state()

    assert out["action"] == "state"
    assert fake.calls == [("state", {"max_items": 30})]


def test_browser_get_prefers_live_browser_over_headless(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs

    class _TextTrack(_FakeTrack):
        def extract(self):
            from runtime.execution.suckers.browser_backend import BrowserResult, Track

            self.calls.append(("extract", {}))
            return BrowserResult.from_track(
                Track.ELECTRON,
                {
                    "ok": True,
                    "url": "https://live-browser.test",
                    "title": "Live browser",
                    "text": "signed-in page body",
                },
            )

    fake = _TextTrack()
    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [fake])
    monkeypatch.setattr(bs, "_check_url_safe", lambda _url, _allow_private: None)
    monkeypatch.setattr(
        bs,
        "sync_playwright",
        lambda: (_ for _ in ()).throw(AssertionError("headless must not start")),
    )

    out = bs._browser_get(url="https://live-browser.test")

    assert fake.calls == [
        ("navigate", {"url": "https://live-browser.test"}),
        ("extract", {}),
    ]
    assert out["track"] == "electron"
    assert out["content"] == "signed-in page body"
    assert out["length"] == len("signed-in page body")


def test_browser_find_uses_higher_track_extract_without_url(monkeypatch):
    from runtime.execution.suckers import browser_skills as bs

    class _TextTrack(_FakeTrack):
        def extract(self):
            from runtime.execution.suckers.browser_backend import BrowserResult, Track

            self.calls.append(("extract", {}))
            return BrowserResult.from_track(
                Track.EXTENSION,
                {
                    "ok": True,
                    "url": "https://signed-in.test",
                    "title": "Signed in",
                    "text": "alpha beta gamma beta",
                },
            )

    fake = _TextTrack()
    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [fake])

    out = bs._browser_find(text="beta")

    assert out["url"] == "https://signed-in.test"
    assert out["count"] == 2
    assert fake.calls == [("extract", {})]


def test_browser_current_tab_screenshot_materializes_higher_track_data(
    monkeypatch,
    tmp_path,
):
    from runtime.execution.suckers import browser_skills as bs

    class _ScreenshotTrack(_FakeTrack):
        def screenshot(self, path="", *, full_page=False):
            from runtime.execution.suckers.browser_backend import BrowserResult, Track

            self.calls.append(("screenshot", {"path": path, "full_page": full_page}))
            return BrowserResult.from_track(
                Track.EXTENSION,
                {
                    "ok": True,
                    "track": "extension",
                    "dataUrl": "data:image/png;base64,iVBORw0KGgo=",
                },
            )

    fake = _ScreenshotTrack()
    monkeypatch.setattr(bs, "_higher_track_backends", lambda: [fake])
    shot = tmp_path / "current.png"

    out = bs._browser_screenshot(path=str(shot))

    assert out["track"] == "extension"
    assert out["path"] == str(shot)
    assert shot.read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_planner_injects_grounding_into_prompt(tmp_path):
    from runtime.execution.suckers.computer_use_loop import ModelRouterVisionPlanner

    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG\r\n")
    rec = _RecordingRouter()
    planner = ModelRouterVisionPlanner(
        router=rec,
        grounding=lambda: "On-screen windows:\n- Finder @ (0,0) 800x600",
    )
    planner.next_action(goal="open a file", screenshot_path=str(shot), history=[])

    user_msg = rec.last.messages[1].content
    assert "On-screen windows" in user_msg
    assert "Finder" in user_msg


def test_planner_pure_pixel_without_grounding(tmp_path):
    from runtime.execution.suckers.computer_use_loop import ModelRouterVisionPlanner

    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG\r\n")
    rec = _RecordingRouter()
    planner = ModelRouterVisionPlanner(router=rec)  # grounding=None (default)
    planner.next_action(goal="open a file", screenshot_path=str(shot), history=[])

    assert "On-screen windows" not in rec.last.messages[1].content


def test_planner_grounding_failure_is_swallowed(tmp_path):
    from runtime.execution.suckers.computer_use_loop import ModelRouterVisionPlanner

    def _boom() -> str:
        raise RuntimeError("grounding blew up")

    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG\r\n")
    planner = ModelRouterVisionPlanner(router=_RecordingRouter(), grounding=_boom)
    # A failing grounding hook must NOT break the planner step.
    out = planner.next_action(goal="g", screenshot_path=str(shot), history=[])
    assert isinstance(out, dict)

