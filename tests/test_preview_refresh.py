"""Implementation note."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.core.nerves.bus import TypedEventBus
from runtime.memory.journal import InMemoryJournal, PreviewRefreshEvent
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config
from runtime.platform.ui.app import create_app
from runtime.sensing.normalize.events import FileChanged
from runtime.sensing.normalize.preview_bridge import PreviewRefreshBridge


@pytest.fixture
def isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


# Implementation note.


class TestWritePreviewRefresh:
    def test_journal_accepts_preview_refresh(self) -> None:
        j = InMemoryJournal()
        j.write_preview_refresh(
            target="http://localhost:5173/preview",
            trigger_path="src/App.tsx",
            reason="src/App.tsx:modified",
        )
        events = [e for e in j.read_all() if isinstance(e, PreviewRefreshEvent)]
        assert len(events) == 1
        e = events[0]
        assert e.target == "http://localhost:5173/preview"
        assert e.trigger_path == "src/App.tsx"
        assert e.reason == "src/App.tsx:modified"
        assert e.event_type == "preview_refresh"


# Implementation note.


class TestPreviewRefreshBridge:
    def _build(self, **kwargs) -> tuple[TypedEventBus, InMemoryJournal, PreviewRefreshBridge]:
        bus = TypedEventBus()
        j = InMemoryJournal()
        bridge = PreviewRefreshBridge(
            bus=bus,
            journal=j,
            target="preview://test",
            **kwargs,
        )
        bridge.start()
        return bus, j, bridge

    def test_single_change_emits_one_refresh(self) -> None:
        bus, journal, bridge = self._build(debounce_ms=0)
        bus.publish(
            FileChanged(
                path="src/App.tsx",
                change_type="modified",
            )
        )
        refreshes = [e for e in journal.read_all() if isinstance(e, PreviewRefreshEvent)]
        assert len(refreshes) == 1
        assert refreshes[0].trigger_path == "src/App.tsx"
        assert refreshes[0].target == "preview://test"
        bridge.stop()

    def test_path_include_filters(self) -> None:
        bus, journal, bridge = self._build(
            debounce_ms=0,
            path_include=["*.tsx", "*.css"],
        )
        bus.publish(FileChanged(path="src/server.py", change_type="modified"))
        bus.publish(FileChanged(path="src/App.tsx", change_type="modified"))
        bus.publish(FileChanged(path="src/main.css", change_type="modified"))
        refreshes = [e for e in journal.read_all() if isinstance(e, PreviewRefreshEvent)]
        # Implementation note.
        paths = [r.trigger_path for r in refreshes]
        assert "src/server.py" not in paths
        assert "src/App.tsx" in paths
        assert "src/main.css" in paths
        bridge.stop()

    def test_path_exclude_wins_over_include(self) -> None:
        bus, journal, bridge = self._build(
            debounce_ms=0,
            path_include=["*.tsx"],
            path_exclude=["*App.tsx"],
        )
        bus.publish(FileChanged(path="src/App.tsx", change_type="modified"))
        bus.publish(FileChanged(path="src/Page.tsx", change_type="modified"))
        refreshes = [e for e in journal.read_all() if isinstance(e, PreviewRefreshEvent)]
        paths = [r.trigger_path for r in refreshes]
        assert "src/App.tsx" not in paths
        assert "src/Page.tsx" in paths
        bridge.stop()

    def test_debounce_collapses_burst(self) -> None:
        """Implementation note."""
        bus, journal, bridge = self._build(debounce_ms=100)
        for i in range(5):
            bus.publish(
                FileChanged(
                    path=f"src/File{i}.tsx",
                    change_type="modified",
                )
            )
        # Implementation note.
        immediate = [e for e in journal.read_all() if isinstance(e, PreviewRefreshEvent)]
        assert len(immediate) == 1
        # Implementation note.
        time.sleep(0.25)
        final = [e for e in journal.read_all() if isinstance(e, PreviewRefreshEvent)]
        assert len(final) == 2
        assert final[0].trigger_path == "src/File0.tsx"
        assert final[1].trigger_path == "src/File4.tsx"
        bridge.stop()

    def test_stop_is_idempotent(self) -> None:
        bus, journal, bridge = self._build(debounce_ms=0)
        bridge.stop()
        bridge.stop()  # Implementation note.
        # Implementation note.
        bus.publish(FileChanged(path="x.tsx", change_type="modified"))
        refreshes = [e for e in journal.read_all() if isinstance(e, PreviewRefreshEvent)]
        assert refreshes == []


# Implementation note.


@pytest.fixture
def client(isolated_cwd: Path) -> TestClient:
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/prev",
            mock_response='{"reasoning":"r","nodes":[]}',
        )
    )
    stack = build_from_config(cfg)
    app = create_app(
        journal=stack.journal,
        registry=stack.registry,
        stack=stack,
    )
    stack.journal.write_preview_refresh(
        target="preview://ch",
        trigger_path="pre.tsx",
        reason="seed",
    )
    return TestClient(app)


class TestPreviewStreamEndpoint:
    def test_route_registered(self, client: TestClient) -> None:
        routes = set(client.app.openapi()["paths"])
        assert "/api/preview/stream" in routes

    def test_journal_counts_preview_refresh(self, client: TestClient) -> None:
        r = client.get("/api/journal")
        assert r.status_code == 200
        assert r.json()["counts"].get("preview_refresh", 0) >= 1
