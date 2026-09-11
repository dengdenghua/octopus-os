"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.browser_skills import (
    BROWSER_SKILL_NAMES,
    PLAYWRIGHT_AVAILABLE,
    _browser_extract,
    _browser_get,
    register_browser_skills,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status


class _FakeHandle:
    def __init__(self, text: str = "", attrs: dict[str, str] | None = None) -> None:
        self._text = text
        self._attrs = attrs or {}

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)


class _FakePage:
    def __init__(
        self,
        *,
        title: str = "t",
        body_text: str = "hello",
        handles: list[_FakeHandle] | None = None,
        goto_raises: Exception | None = None,
        url: str = "https://example.com/",
    ) -> None:
        self._title = title
        self._body = body_text
        self._handles = handles or []
        self._goto_raises = goto_raises
        self.url = url
        self.wait_calls: list[int] = []

    def goto(self, url: str, timeout: int = 0, wait_until: str = "") -> _FakeResponse:
        if self._goto_raises is not None:
            raise self._goto_raises
        self.url = url
        return _FakeResponse(200)

    def wait_for_timeout(self, ms: int) -> None:
        self.wait_calls.append(ms)

    def title(self) -> str:
        return self._title

    def inner_text(self, selector: str) -> str:
        assert selector == "body"
        return self._body

    def query_selector_all(self, selector: str) -> list[_FakeHandle]:
        return list(self._handles)


# ═══════════════════════════════════════════════════════════
# _browser_get
# ═══════════════════════════════════════════════════════════


class TestBrowserGet:
    def test_empty_url_returns_error(self):
        result = _browser_get(url="")
        assert "error" in result
        assert "missing" in result["error"]

    def test_happy_path_with_fake_page(self):
        page = _FakePage(title="Example", body_text="Hello World")
        r = _browser_get(url="https://example.com", page=page)
        assert r["title"] == "Example"
        assert r["content"] == "Hello World"
        assert r["length"] == 11
        assert r["truncated"] is False
        assert r["status_code"] == 200

    def test_text_truncated_at_max_bytes(self):
        page = _FakePage(body_text="x" * 200)
        r = _browser_get(url="https://a", page=page, max_bytes=50)
        assert r["truncated"] is True
        assert len(r["content"]) == 50
        assert r["length"] == 50

    def test_wait_ms_invokes_timeout(self):
        page = _FakePage()
        _browser_get(url="https://a", page=page, wait_ms=250)
        assert page.wait_calls == [250]

    def test_nav_failure_returns_error(self):
        page = _FakePage(goto_raises=RuntimeError("boom"))
        r = _browser_get(url="https://a", page=page)
        assert "error" in r
        assert "nav_error" in r["error"]

    def test_missing_playwright_without_page(self, monkeypatch):
        from runtime.execution.suckers import browser_skills

        monkeypatch.setattr(browser_skills, "PLAYWRIGHT_AVAILABLE", False)
        # Implementation note.
        monkeypatch.setattr(
            browser_skills,
            "_check_url_safe",
            lambda url, allow_private: None,
        )
        r = browser_skills._browser_get(url="https://a")
        assert "error" in r
        assert "playwright" in r["error"].lower()


# ═══════════════════════════════════════════════════════════
# _browser_extract
# ═══════════════════════════════════════════════════════════


class TestBrowserExtract:
    def test_missing_url(self):
        r = _browser_extract(url="", selector="a")
        assert "error" in r
        assert r["items"] == []

    def test_missing_selector(self):
        r = _browser_extract(url="https://a", selector="")
        assert "error" in r
        assert r["items"] == []

    def test_extract_inner_text(self):
        handles = [
            _FakeHandle(text="first"),
            _FakeHandle(text="second"),
            _FakeHandle(text="third"),
        ]
        page = _FakePage(handles=handles)
        r = _browser_extract(url="https://a", selector="a", page=page)
        assert r["count"] == 3
        assert r["items"] == ["first", "second", "third"]

    def test_extract_attr(self):
        handles = [
            _FakeHandle(attrs={"href": "/a"}),
            _FakeHandle(attrs={"href": "/b"}),
        ]
        page = _FakePage(handles=handles)
        r = _browser_extract(url="https://a", selector="a", attr="href", page=page)
        assert r["items"] == ["/a", "/b"]
        assert r["attr"] == "href"

    def test_limit_caps_results(self):
        handles = [_FakeHandle(text=str(i)) for i in range(10)]
        page = _FakePage(handles=handles)
        r = _browser_extract(url="https://a", selector="li", limit=3, page=page)
        assert r["count"] == 3

    def test_nav_error_returns_gracefully(self):
        page = _FakePage(goto_raises=TimeoutError("slow"))
        r = _browser_extract(url="https://a", selector="a", page=page)
        assert "error" in r
        assert r["items"] == []


# ═══════════════════════════════════════════════════════════
# registration
# ═══════════════════════════════════════════════════════════


class TestRegistration:
    def test_register_returns_zero_without_playwright(self, monkeypatch):
        from runtime.execution.suckers import browser_skills

        monkeypatch.setattr(browser_skills, "PLAYWRIGHT_AVAILABLE", False)
        reg = SkillRegistry()
        n = browser_skills.register_browser_skills(reg)
        assert n == 0
        assert not reg.has("browser_get")

    @pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
    def test_register_with_playwright(self):
        reg = SkillRegistry()
        n = register_browser_skills(reg)
        # Implementation note.
        # Implementation note.
        # Implementation note.
        assert n == len(BROWSER_SKILL_NAMES)
        for name in BROWSER_SKILL_NAMES:
            assert reg.has(name)
            s = reg.get(name)
            assert s.cost_profile == "high"
            assert s.trusted_source.startswith("skill://public/")

    @pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")
    def test_golden_tests_pass(self):
        """Implementation note."""
        reg = SkillRegistry()
        register_browser_skills(reg)
        s = reg.get("browser_get")
        assert s.tests  # golden case exists
