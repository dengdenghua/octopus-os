"""Implementation note."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.execution.suckers.browser_skills import (
    BROWSER_SKILL_NAMES,
    _browser_click,
    _browser_navigate,
    _browser_screenshot,
    _browser_scroll,
    _browser_type,
    _browser_wait,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status


class _FakeLocator:
    def __init__(self, page: _FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        self._page.scroll_calls.append(("selector", self._selector))


class _FakePage:
    def __init__(
        self,
        *,
        title: str = "page title",
        url: str = "https://example.com/",
        goto_raises: Exception | None = None,
        click_raises: Exception | None = None,
        fill_raises: Exception | None = None,
        wait_raises: Exception | None = None,
    ) -> None:
        self._title = title
        self.url = url
        self._goto_raises = goto_raises
        self._click_raises = click_raises
        self._fill_raises = fill_raises
        self._wait_raises = wait_raises
        # Implementation note.
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.presses: list[tuple[str, str]] = []
        self.waits: list[tuple[str, str]] = []
        self.evaluations: list[str] = []
        self.scroll_calls: list[tuple[str, Any]] = []
        self.wait_ms_calls: list[int] = []
        self.screenshots: list[dict[str, Any]] = []

    def goto(self, url: str, timeout: int = 0, wait_until: str = "") -> _FakeResponse:
        if self._goto_raises is not None:
            raise self._goto_raises
        self.url = url
        return _FakeResponse(200)

    def title(self) -> str:
        return self._title

    def wait_for_timeout(self, ms: int) -> None:
        self.wait_ms_calls.append(ms)

    def click(self, selector: str, timeout: int = 0) -> None:
        if self._click_raises is not None:
            raise self._click_raises
        self.clicks.append(selector)

    def fill(self, selector: str, text: str, timeout: int = 0) -> None:
        if self._fill_raises is not None:
            raise self._fill_raises
        self.fills.append((selector, text))

    def press(self, selector: str, key: str, timeout: int = 0) -> None:
        self.presses.append((selector, key))

    def wait_for_selector(
        self,
        selector: str,
        state: str = "visible",
        timeout: int = 0,
    ) -> None:
        if self._wait_raises is not None:
            raise self._wait_raises
        self.waits.append((selector, state))

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    def evaluate(self, js: str) -> None:
        self.evaluations.append(js)

    def screenshot(self, path: str = "", full_page: bool = False) -> None:
        self.screenshots.append({"path": path, "full_page": full_page})
        # Implementation note.
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)


# ═══════════════════════════════════════════════════════════
# browser_navigate
# ═══════════════════════════════════════════════════════════


class TestNavigate:
    def test_returns_final_url_and_title(self):
        page = _FakePage(title="hello", url="https://a/")
        r = _browser_navigate(url="https://a/", page=page)
        assert "error" not in r
        assert r["status_code"] == 200
        assert r["title"] == "hello"
        assert r["url"] == "https://a/"

    def test_missing_url(self):
        r = _browser_navigate(url="", page=_FakePage())
        assert "error" in r

    def test_nav_error(self):
        page = _FakePage(goto_raises=RuntimeError("boom"))
        r = _browser_navigate(url="https://a/", page=page)
        assert "error" in r
        assert "nav_error" in r["error"]

    def test_wait_ms_invoked(self):
        page = _FakePage()
        _browser_navigate(url="https://a/", page=page, wait_ms=250)
        assert page.wait_ms_calls == [250]


# ═══════════════════════════════════════════════════════════
# browser_click
# ═══════════════════════════════════════════════════════════


class TestClick:
    def test_click_records_selector(self):
        page = _FakePage()
        r = _browser_click(
            url="https://a/",
            selector="#btn",
            page=page,
        )
        assert "error" not in r
        assert r["clicked"] == "#btn"
        assert r["final_url"] == "https://a/"
        assert page.clicks == ["#btn"]

    def test_missing_selector(self):
        r = _browser_click(url="https://a/", selector="", page=_FakePage())
        assert "error" in r

    def test_click_raises_reports_error(self):
        page = _FakePage(click_raises=RuntimeError("not clickable"))
        r = _browser_click(
            url="https://a/",
            selector="#btn",
            page=page,
        )
        assert "error" in r
        assert "click_error" in r["error"]

    def test_wait_after_ms(self):
        page = _FakePage()
        _browser_click(
            url="https://a/",
            selector="#btn",
            page=page,
            wait_after_ms=100,
        )
        assert 100 in page.wait_ms_calls


# ═══════════════════════════════════════════════════════════
# browser_type
# ═══════════════════════════════════════════════════════════


class TestType:
    def test_fills_input(self):
        page = _FakePage()
        r = _browser_type(
            url="https://a/",
            selector="#user",
            text="alice",
            page=page,
        )
        assert "error" not in r
        assert r["filled"] == "#user"
        assert r["text_len"] == 5
        # Implementation note.
        assert page.fills == [("#user", ""), ("#user", "alice")]

    def test_clear_first_false_skips_clear(self):
        page = _FakePage()
        _browser_type(
            url="https://a/",
            selector="#user",
            text="bob",
            clear_first=False,
            page=page,
        )
        assert page.fills == [("#user", "bob")]

    def test_press_enter(self):
        page = _FakePage()
        _browser_type(
            url="https://a/",
            selector="#q",
            text="query",
            press_enter=True,
            page=page,
        )
        assert page.presses == [("#q", "Enter")]

    def test_missing_selector(self):
        r = _browser_type(
            url="https://a/",
            selector="",
            text="x",
            page=_FakePage(),
        )
        assert "error" in r

    def test_fill_raises_reports_error(self):
        page = _FakePage(fill_raises=RuntimeError("readonly"))
        r = _browser_type(
            url="https://a/",
            selector="#x",
            text="y",
            page=page,
        )
        assert "error" in r
        assert "type_error" in r["error"]


# ═══════════════════════════════════════════════════════════
# browser_scroll
# ═══════════════════════════════════════════════════════════


class TestScroll:
    def test_scroll_to_selector(self):
        page = _FakePage()
        r = _browser_scroll(
            url="https://a/",
            to_selector="#footer",
            page=page,
        )
        assert "error" not in r
        assert r["scrolled_to"] == {"selector": "#footer"}
        assert page.scroll_calls == [("selector", "#footer")]

    def test_scroll_to_y(self):
        page = _FakePage()
        r = _browser_scroll(url="https://a/", to_y=500, page=page)
        assert "error" not in r
        assert r["scrolled_to"] == {"y": 500}
        assert page.evaluations == ["window.scrollTo(0, 500)"]

    def test_must_provide_exactly_one_target(self):
        r = _browser_scroll(url="https://a/", page=_FakePage())
        assert "error" in r
        r = _browser_scroll(
            url="https://a/",
            to_selector="#x",
            to_y=100,
            page=_FakePage(),
        )
        assert "error" in r


# ═══════════════════════════════════════════════════════════
# browser_wait
# ═══════════════════════════════════════════════════════════


class TestWait:
    def test_wait_for_visible(self):
        page = _FakePage()
        r = _browser_wait(
            url="https://a/",
            selector="#ready",
            state="visible",
            page=page,
        )
        assert "error" not in r
        assert r["waited_for"] == "#ready"
        assert r["state"] == "visible"
        assert page.waits == [("#ready", "visible")]

    def test_wait_for_hidden(self):
        page = _FakePage()
        _browser_wait(
            url="https://a/",
            selector=".spinner",
            state="hidden",
            page=page,
        )
        assert page.waits == [(".spinner", "hidden")]

    def test_invalid_state_rejected(self):
        r = _browser_wait(
            url="https://a/",
            selector="#x",
            state="floating",
            page=_FakePage(),
        )
        assert "error" in r

    def test_missing_selector(self):
        r = _browser_wait(url="https://a/", selector="", page=_FakePage())
        assert "error" in r

    def test_timeout_returns_flag(self):
        page = _FakePage(wait_raises=TimeoutError("element never appeared"))
        r = _browser_wait(
            url="https://a/",
            selector="#never",
            page=page,
        )
        assert r.get("timed_out") is True


# ═══════════════════════════════════════════════════════════
# browser_screenshot
# ═══════════════════════════════════════════════════════════


class TestScreenshot:
    def test_saves_file(self, tmp_path: Path):
        out = tmp_path / "shot.png"
        page = _FakePage()
        r = _browser_screenshot(
            url="https://a/",
            path=str(out),
            sandbox_dir=str(tmp_path),
            page=page,
        )
        assert "error" not in r
        assert r["size_bytes"] > 0
        assert out.exists()
        assert page.screenshots[0]["path"] == str(out)

    def test_full_page_flag_forwarded(self, tmp_path: Path):
        page = _FakePage()
        _browser_screenshot(
            url="https://a/",
            path=str(tmp_path / "s.png"),
            full_page=True,
            sandbox_dir=str(tmp_path),
            page=page,
        )
        assert page.screenshots[0]["full_page"] is True

    def test_missing_path(self):
        r = _browser_screenshot(
            url="https://a/",
            path="",
            page=_FakePage(),
        )
        assert "error" in r

    def test_path_outside_sandbox_rejected(self, tmp_path: Path):
        other = tmp_path / "other"
        other.mkdir()
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        r = _browser_screenshot(
            url="https://a/",
            path=str(other / "s.png"),
            sandbox_dir=str(sandbox),
            page=_FakePage(),
        )
        assert "error" in r
        assert "path_blocked" in r["error"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSSRFGuard:
    def test_private_ip_rejected_when_live(self, monkeypatch):
        """Implementation note."""
        r = _browser_navigate(url="http://127.0.0.1/admin")
        assert "error" in r
        assert r.get("blocked") is True

    def test_metadata_endpoint_rejected(self):
        r = _browser_navigate(url="http://169.254.169.254/latest/meta-data/")
        assert "error" in r
        assert r.get("blocked") is True

    def test_file_scheme_rejected(self):
        r = _browser_click(
            url="file:///etc/passwd",
            selector="#x",
        )
        assert "error" in r
        assert r.get("blocked") is True


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRegistration:
    def test_all_new_skills_listed(self):
        for name in [
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_scroll",
            "browser_wait",
            "browser_screenshot",
        ]:
            assert name in BROWSER_SKILL_NAMES

    def test_register_count_is_eight(self):
        pytest.importorskip("playwright")
        from runtime.execution.suckers import SkillRegistry
        from runtime.execution.suckers.browser_skills import (
            register_browser_skills,
        )

        reg = SkillRegistry()
        n = register_browser_skills(reg)
        # The browser skill family expanded over time (browser_find /
        # browser_state were added later); pin against the canonical
        # name list rather than a hard-coded count.
        assert n == len(BROWSER_SKILL_NAMES)
        for name in BROWSER_SKILL_NAMES:
            assert reg.has(name)
