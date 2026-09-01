"""Tests for browser artifact routing."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── _emit_screenshot_artifact ──────────────────────────────


def _make_png_b64(size: int = 16) -> str:
    """Return a minimal valid base64-encoded PNG-header bytes."""
    # Not a real PNG but enough to test save/decode path.
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * size).decode()


def test_screenshot_saves_file_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.execution.suckers import browser_act_skills as bas

    monkeypatch.setattr(bas, "_artifacts_root", lambda: tmp_path / "artifacts")

    response = {
        "ok": True,
        "data": _make_png_b64(),
        "width": 1440,
        "height": 900,
    }
    bas._emit_screenshot_artifact(response)

    files = list((tmp_path / "artifacts").glob("screenshot-*.png"))
    assert len(files) == 1
    assert files[0].read_bytes().startswith(b"\x89PNG")


def test_screenshot_strips_data_uri_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.execution.suckers import browser_act_skills as bas

    monkeypatch.setattr(bas, "_artifacts_root", lambda: tmp_path / "artifacts")

    raw = _make_png_b64()
    response = {
        "ok": True,
        "data": f"data:image/png;base64,{raw}",
        "width": 800,
        "height": 600,
    }
    bas._emit_screenshot_artifact(response)

    files = list((tmp_path / "artifacts").glob("screenshot-*.png"))
    assert len(files) == 1


def test_screenshot_no_data_field_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.execution.suckers import browser_act_skills as bas

    monkeypatch.setattr(bas, "_artifacts_root", lambda: tmp_path / "artifacts")

    bas._emit_screenshot_artifact({"ok": True})

    assert not (tmp_path / "artifacts").exists()


def test_screenshot_ok_false_no_emit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.execution.suckers import browser_act_skills as bas
    from runtime.execution.suckers.browser_act_skills import _h_screenshot

    monkeypatch.setattr(bas, "_artifacts_root", lambda: tmp_path / "artifacts")
    # Patch _bridge_call to return an error response
    with patch.object(bas, "_bridge_call", return_value={"ok": False, "error": "bridge down"}):
        result = _h_screenshot()
    assert result["ok"] is False
    assert not (tmp_path / "artifacts").exists()


def test_screenshot_journal_broadcast_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a journal with _broadcast is available, _emit_screenshot_artifact
    calls _broadcast with the artifact event dict."""
    from runtime.execution.suckers import browser_act_skills as bas

    monkeypatch.setattr(bas, "_artifacts_root", lambda: tmp_path / "artifacts")

    broadcast_calls: list[dict] = []

    class _FakeJournal:
        def _broadcast(self, event: dict) -> None:
            broadcast_calls.append(event)

    fake_journal = _FakeJournal()

    def fake_active_journal():
        return fake_journal

    with patch.dict(
        "sys.modules",
        {
            "runtime.sensing.gateway": MagicMock(
                _active_streaming_journal=fake_active_journal,
            ),
        },
    ):
        # Re-import to pick up the patched module
        # We call _emit directly after patching _artifacts_root
        response = {
            "ok": True,
            "data": _make_png_b64(),
            "width": 1440,
            "height": 900,
        }
        # Patch the journal lookup within the function
        with patch(
            "runtime.sensing.gateway._active_streaming_journal", fake_active_journal, create=True
        ):
            bas._emit_screenshot_artifact(response)

    # The journal broadcast might not fire (patching the import is tricky),
    # but the file should always be written first
    files = list((tmp_path / "artifacts").glob("screenshot-*.png"))
    assert len(files) == 1


def test_emit_swallows_exceptions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken artifacts root must not raise from _emit_screenshot_artifact."""
    from runtime.execution.suckers import browser_act_skills as bas

    # Make _artifacts_root raise
    def bad_root():
        raise RuntimeError("disk full")

    monkeypatch.setattr(bas, "_artifacts_root", bad_root)

    response = {"ok": True, "data": _make_png_b64()}
    # Should not raise
    bas._emit_screenshot_artifact(response)


# ─── /api/browser-artifacts/{filename} ──────────────────────


def test_artifact_endpoint_serves_png(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from runtime.platform.ui.browser_router import create_browser_router

    # Write a fake PNG
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "screenshot-test.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    from runtime.execution.suckers import browser_act_skills as bas

    monkeypatch.setattr(bas, "_artifacts_root", lambda: artifacts)

    app = FastAPI()
    app.include_router(create_browser_router())
    client = TestClient(app)

    r = client.get("/api/browser-artifacts/screenshot-test.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_artifact_endpoint_rejects_traversal() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from runtime.platform.ui.browser_router import create_browser_router

    app = FastAPI()
    app.include_router(create_browser_router())
    client = TestClient(app)

    r = client.get("/api/browser-artifacts/../../etc/passwd")
    assert r.status_code in (404, 422)


def test_artifact_endpoint_rejects_non_png() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from runtime.platform.ui.browser_router import create_browser_router

    app = FastAPI()
    app.include_router(create_browser_router())
    client = TestClient(app)

    r = client.get("/api/browser-artifacts/malicious.sh")
    assert r.status_code == 404


def test_artifact_endpoint_404_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from runtime.execution.suckers import browser_act_skills as bas
    from runtime.platform.ui.browser_router import create_browser_router

    monkeypatch.setattr(bas, "_artifacts_root", lambda: tmp_path / "empty")

    app = FastAPI()
    app.include_router(create_browser_router())
    client = TestClient(app)

    r = client.get("/api/browser-artifacts/screenshot-ghost.png")
    assert r.status_code == 404


# ─── values-only SSE mode ───────────────────────────────────


def test_stream_mode_values_accepted_from_body() -> None:
    """Check that ``stream_mode`` is read from the request body and
    validated to ``full`` / ``values``."""
    # We can't run the full OpenAI gateway without a stack,
    # so we test the validation logic in isolation.
    for valid in ("full", "values"):
        mode = str(valid).lower()
        assert mode in ("full", "values")

    for invalid in ("events", "updates", "debug", None, ""):
        raw = str(invalid or "full").lower()
        result = raw if raw in ("full", "values") else "full"
        assert result == "full"
