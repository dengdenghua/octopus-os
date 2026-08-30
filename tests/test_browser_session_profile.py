"""Signed-in browser: agent session worker uses a persistent profile on opt-in."""

from __future__ import annotations

from pathlib import Path

import pytest

_sp = pytest.importorskip("playwright.sync_api")

from runtime.execution.suckers.browser_session_worker import _default_page_factory  # noqa: E402


class _FakeBrowserOrCtx:
    pages: list = []

    def new_context(self):
        return self

    def new_page(self):
        return "PAGE"

    def close(self):
        pass


class _FakeChromium:
    def __init__(self, calls):
        self.calls = calls

    def launch(self, **kw):
        self.calls["launch"] = kw
        return _FakeBrowserOrCtx()

    def launch_persistent_context(self, **kw):
        self.calls["persistent"] = kw
        return _FakeBrowserOrCtx()


class _FakePW:
    def __init__(self, calls):
        self.chromium = _FakeChromium(calls)

    def stop(self):
        pass


class _FakeSP:
    def __init__(self, calls):
        self.calls = calls

    def start(self):
        return _FakePW(self.calls)


def test_persistent_profile_when_env_set(monkeypatch, tmp_path: Path) -> None:
    calls: dict = {}
    monkeypatch.setattr(_sp, "sync_playwright", lambda: _FakeSP(calls))
    monkeypatch.setenv("ECHO_BROWSER_PROFILE", str(tmp_path / "prof"))
    page, close = _default_page_factory(headless=True)
    assert "persistent" in calls and "launch" not in calls  # signed-in path
    assert calls["persistent"]["user_data_dir"] == str(tmp_path / "prof")
    close()


def test_stateless_launch_by_default(monkeypatch) -> None:
    calls: dict = {}
    monkeypatch.setattr(_sp, "sync_playwright", lambda: _FakeSP(calls))
    monkeypatch.delenv("ECHO_BROWSER_PROFILE", raising=False)
    page, close = _default_page_factory(headless=True)
    assert "launch" in calls and "persistent" not in calls  # default unchanged
    close()

