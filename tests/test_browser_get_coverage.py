"""Dense coverage for browser_get with a stubbed page (audit Q-05)."""

from __future__ import annotations

from runtime.execution.suckers import browser_skills as bs


class _FakePage:
    def __init__(self, title="T", text="body text", url="http://page"):
        self._title = title
        self._text = text
        self.url = url
        self.frames = []

    def goto(self, url, **kw):
        self.url = url
        return type("R", (), {"status": 200})()

    def title(self):
        return self._title

    def inner_text(self, sel):
        return self._text

    def wait_for_timeout(self, ms):
        pass


def test_browser_get_with_page(monkeypatch) -> None:
    page = _FakePage()
    out = bs._browser_get("http://example.com", page=page)
    assert "content" in out
    assert "body text" in out["content"]
    assert out["title"] == "T"
    assert out["status_code"] == 200
    assert out["track"] == "playwright"


def test_browser_get_missing_url_no_page(monkeypatch) -> None:
    monkeypatch.setattr(bs, "_has_agent_browser_session", lambda: False)
    out = bs._browser_get("", page=None)
    assert "missing url" in out.get("error", "")


def test_browser_get_nav_error() -> None:
    class _BoomPage(_FakePage):
        def goto(self, url, **kw):
            raise RuntimeError("boom")

    out = bs._browser_get("http://x", page=_BoomPage())
    assert "nav_error" in out.get("error", "")

