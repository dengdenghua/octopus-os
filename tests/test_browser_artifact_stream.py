"""Tests for browser artifact streaming via the journal."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from runtime.execution.suckers.browser_act_skills import (
    _emit_screenshot_artifact,
)
from runtime.memory.journal import BrowserArtifactEvent, InMemoryJournal
from runtime.memory.journal.journal import _parse_event


@pytest.fixture
def artifacts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_artifacts_root`` at tmp so file writes don't pollute
    the real data dir."""
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    # The app_paths() lookup caches via ``_reset_module_state`` in
    # conftest, but we want a concrete dir — poke ECHO_DATA_DIR.
    return tmp_path / "browser_artifacts"


def _png_bytes() -> bytes:
    # Minimal valid PNG header — don't need a real image, just bytes
    # that base64-decode cleanly.
    return b"\x89PNG\r\n\x1a\n" + b"dummy" * 10


def _bridge_response(caption: str = "initial page") -> dict:
    b64 = base64.b64encode(_png_bytes()).decode("ascii")
    return {
        "data": b64,
        "width": 1440,
        "height": 900,
        "caption": caption,
    }


# ─── file write (no regression) ─────────────────────────────


def test_emit_writes_file_even_without_stream(
    artifacts_root: Path,
):
    _emit_screenshot_artifact(_bridge_response())
    # A screenshot-*.png was written.
    hits = list(artifacts_root.glob("screenshot-*.png"))
    assert len(hits) == 1
    assert hits[0].read_bytes().startswith(b"\x89PNG")


# ─── journal mirror ─────────────────────────────────────────


class _FakeSession:
    def __init__(self, journal):
        self.metadata = {"journal": journal}


def test_emit_writes_browser_artifact_event_to_journal(
    artifacts_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    journal = InMemoryJournal()
    session = _FakeSession(journal)

    import runtime.platform.process.session as _sess

    monkeypatch.setattr(_sess, "current_session", lambda: session)

    _emit_screenshot_artifact(_bridge_response(caption="search results"))
    events = journal.read_all()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, BrowserArtifactEvent)
    assert ev.kind == "screenshot"
    assert ev.caption == "search results"
    assert ev.width == 1440
    assert ev.height == 900
    assert ev.mime_type == "image/png"
    assert ev.url.startswith("/api/browser-artifacts/screenshot-")
    assert ev.filename.startswith("screenshot-")
    assert ev.filename.endswith(".png")


def test_emit_without_session_still_writes_file(
    artifacts_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """When no session is bound (standalone skill test), file is
    saved but no journal event is fired."""
    import runtime.platform.process.session as _sess

    monkeypatch.setattr(_sess, "current_session", lambda: None)

    _emit_screenshot_artifact(_bridge_response())
    hits = list(artifacts_root.glob("screenshot-*.png"))
    assert len(hits) == 1


def test_emit_without_data_is_noop(artifacts_root: Path):
    _emit_screenshot_artifact({"data": ""})
    # No file written, no exception.
    assert not artifacts_root.exists() or not list(artifacts_root.glob("screenshot-*.png"))


def test_emit_strips_data_uri_prefix(
    artifacts_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Browser bridges sometimes send ``data:image/png;base64,...``."""
    journal = InMemoryJournal()
    session = _FakeSession(journal)
    import runtime.platform.process.session as _sess

    monkeypatch.setattr(_sess, "current_session", lambda: session)

    b64 = base64.b64encode(_png_bytes()).decode("ascii")
    _emit_screenshot_artifact({"data": f"data:image/png;base64,{b64}", "caption": "x"})
    ev = journal.read_all()[0]
    assert isinstance(ev, BrowserArtifactEvent)
    assert ev.caption == "x"


# ─── serialization round-trip ───────────────────────────────


def test_event_round_trips_through_jsonl():
    ev = BrowserArtifactEvent(
        kind="screenshot",
        url="/api/browser-artifacts/foo.png",
        filename="foo.png",
        caption="",
        mime_type="image/png",
        width=800,
        height=600,
        thread_id="t-1",
    )
    parsed = _parse_event(ev.model_dump_json())
    assert isinstance(parsed, BrowserArtifactEvent)
    assert parsed.filename == "foo.png"
    assert parsed.width == 800


# ─── ContextVar emitter still fires ─────────────────────────


def test_emit_calls_active_artifact_emitter(
    artifacts_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The ContextVar emitter path is the legacy/fast channel. Journal
    mirror should NOT replace it — both are expected to fire.
    """
    from runtime.execution.suckers.browser_act_skills import (
        _ACTIVE_ARTIFACT_EMITTER,
    )

    captured: list[dict] = []
    token = _ACTIVE_ARTIFACT_EMITTER.set(lambda ev: captured.append(ev))
    try:
        _emit_screenshot_artifact(_bridge_response())
    finally:
        _ACTIVE_ARTIFACT_EMITTER.reset(token)

    assert len(captured) == 1
    assert captured[0]["type"] == "artifact"
    assert captured[0]["kind"] == "screenshot"
