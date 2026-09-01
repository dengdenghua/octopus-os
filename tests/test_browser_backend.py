"""Unified BrowserBackend seam: result shape, Protocol conformance,
and the track-priority resolver.

These pin the abstraction the three automation tracks (Playwright,
Electron webview, extension relay) will plug into. The real adapters
need their runtimes to verify; the seam itself is runtime-free.
"""

from runtime.execution.suckers.browser_backend import (
    TRACK_PRIORITY,
    BrowserBackend,
    BrowserResult,
    Track,
    resolve_backend,
)
from runtime.execution.suckers.browser_backends_mock import MockBrowserBackend


class TestBrowserResult:
    def test_from_track_success(self):
        r = BrowserResult.from_track(Track.MOCK, {"ok": True, "url": "x"})
        assert r.ok is True
        assert r.track is Track.MOCK
        assert r.error is None
        assert r.data == {"url": "x"}
        assert r.raw == {"ok": True, "url": "x"}

    def test_from_track_failure_extracts_error(self):
        r = BrowserResult.from_track(Track.PLAYWRIGHT, {"ok": False, "error": "boom"})
        assert r.ok is False
        assert r.error == "boom"

    def test_failure_without_error_gets_placeholder(self):
        r = BrowserResult.from_track(Track.ELECTRON, {"ok": False})
        assert r.ok is False
        assert r.error == "unknown_error"


class TestProtocolConformance:
    def test_mock_satisfies_protocol(self):
        backend = MockBrowserBackend()
        # runtime_checkable Protocol — structural check.
        assert isinstance(backend, BrowserBackend)

    def test_actions_record_and_return_results(self):
        backend = MockBrowserBackend()
        assert backend.navigate("https://x").ok
        assert backend.click("#go").ok
        assert backend.type("#q", "hi", clear=True).ok
        assert backend.scroll(delta_y=100).ok
        assert backend.wait("#done").ok
        assert backend.state().ok
        assert backend.extract().ok
        names = [c[0] for c in backend.calls]
        assert names == [
            "navigate",
            "click",
            "type",
            "scroll",
            "wait",
            "state",
            "extract",
        ]

    def test_navigate_updates_state_url(self):
        backend = MockBrowserBackend()
        backend.navigate("https://example.com")
        assert backend.state().data["url"] == "https://example.com"


class TestResolver:
    def test_priority_order_is_extension_first(self):
        assert TRACK_PRIORITY[0] is Track.EXTENSION
        assert TRACK_PRIORITY[-1] is Track.PLAYWRIGHT

    def test_picks_highest_priority_available(self):
        ext = _stub(Track.EXTENSION, available=True)
        electron = _stub(Track.ELECTRON, available=True)
        playwright = _stub(Track.PLAYWRIGHT, available=True)
        chosen = resolve_backend([playwright, electron, ext])
        assert chosen is ext

    def test_skips_unavailable_backends(self):
        ext = _stub(Track.EXTENSION, available=False)
        electron = _stub(Track.ELECTRON, available=True)
        chosen = resolve_backend([ext, electron])
        assert chosen is electron

    def test_returns_none_when_nothing_available(self):
        ext = _stub(Track.EXTENSION, available=False)
        assert resolve_backend([ext]) is None

    def test_prefer_overrides_priority_when_available(self):
        ext = _stub(Track.EXTENSION, available=True)
        playwright = _stub(Track.PLAYWRIGHT, available=True)
        chosen = resolve_backend([ext, playwright], prefer=Track.PLAYWRIGHT)
        assert chosen is playwright

    def test_prefer_falls_back_when_unavailable(self):
        ext = _stub(Track.EXTENSION, available=True)
        playwright = _stub(Track.PLAYWRIGHT, available=False)
        chosen = resolve_backend([ext, playwright], prefer=Track.PLAYWRIGHT)
        assert chosen is ext

    def test_mock_only_reachable_via_prefer(self):
        # Mock is not in TRACK_PRIORITY, so default resolution ignores
        # it — tests opt in explicitly.
        mock = MockBrowserBackend()
        assert resolve_backend([mock]) is None
        assert resolve_backend([mock], prefer=Track.MOCK) is mock


def _stub(track: Track, *, available: bool) -> MockBrowserBackend:
    backend = MockBrowserBackend(available=available)
    backend.track = track  # type: ignore[misc]
    return backend
