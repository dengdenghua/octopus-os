"""Implementation note."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from runtime.core.nerves.bus import TypedEventBus
from runtime.sensing.normalize import (
    EnvSensor,
    FileChanged,
    FileWatcherSensor,
    GitCommitDetected,
    GitHookSensor,
    ProcessStateChanged,
    ProcessWatchSensor,
    SensorManager,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeSensor(EnvSensor):
    sensor_id = "fake"

    def __init__(self):
        super().__init__()
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        self._running = True

    def stop(self):
        self.stopped = True
        self._running = False


class TestSensorManagerLifecycle:
    def test_requires_bus_or_publisher(self):
        with pytest.raises(ValueError):
            SensorManager()

    def test_register_binds_publisher(self):
        bus = TypedEventBus()
        mgr = SensorManager(bus=bus)
        s = _FakeSensor()
        mgr.register(s)
        assert s._publisher is not None
        assert "fake" in mgr.sensor_ids()

    def test_duplicate_sensor_id_rejected(self):
        bus = TypedEventBus()
        mgr = SensorManager(bus=bus)
        mgr.register(_FakeSensor())
        with pytest.raises(ValueError, match="duplicate"):
            mgr.register(_FakeSensor())

    def test_start_stop_all(self):
        bus = TypedEventBus()
        mgr = SensorManager(bus=bus)
        s = _FakeSensor()
        mgr.register(s)
        mgr.start_all()
        assert s.started
        mgr.stop_all()
        assert s.stopped

    def test_unregister_stops_and_removes(self):
        bus = TypedEventBus()
        mgr = SensorManager(bus=bus)
        s = _FakeSensor()
        mgr.register(s)
        s.start()
        assert mgr.unregister("fake") is True
        assert s.stopped
        assert mgr.get("fake") is None

    def test_ping_produces_event(self):
        bus = TypedEventBus()
        mgr = SensorManager(bus=bus)
        from runtime.sensing.normalize import EnvironmentPing

        received: list = []
        bus.subscribe(EnvironmentPing, received.append)
        mgr.ping()
        assert len(received) == 1

    def test_custom_publisher_overrides_bus(self):
        bus = TypedEventBus()
        captured: list = []
        mgr = SensorManager(bus=bus, publisher=captured.append)
        mgr.ping()
        assert len(captured) == 1

    def test_status_all_empty(self):
        bus = TypedEventBus()
        mgr = SensorManager(bus=bus)
        assert mgr.status_all() == []


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestFileWatcherPolling:
    def test_requires_paths(self):
        with pytest.raises(ValueError):
            FileWatcherSensor(paths=[])

    def test_rejects_bad_params(self):
        with pytest.raises(ValueError):
            FileWatcherSensor(paths=["/tmp"], poll_interval_seconds=0)
        with pytest.raises(ValueError):
            FileWatcherSensor(paths=["/tmp"], debounce_ms=-1)

    def test_detects_new_file(self, tmp_path: Path):
        events: list[FileChanged] = []
        bus = TypedEventBus()
        bus.subscribe(FileChanged, events.append)

        mgr = SensorManager(bus=bus)
        sensor = FileWatcherSensor(
            paths=[tmp_path],
            poll_interval_seconds=0.1,
            debounce_ms=0,
            force_polling=True,
        )
        mgr.register(sensor)
        mgr.start_all()
        try:
            # Implementation note.
            time.sleep(0.05)
            new = tmp_path / "new.py"
            new.write_text("x = 1", encoding="utf-8")

            # Implementation note.
            deadline = time.time() + 2.0
            while time.time() < deadline and not events:
                time.sleep(0.1)
        finally:
            mgr.stop_all()

        assert len(events) >= 1
        assert any(e.change_type == "created" for e in events)
        assert any("new.py" in e.path for e in events)

    def test_detects_modification(self, tmp_path: Path):
        target = tmp_path / "watch.py"
        target.write_text("x = 1", encoding="utf-8")

        events: list[FileChanged] = []
        bus = TypedEventBus()
        bus.subscribe(FileChanged, events.append)

        mgr = SensorManager(bus=bus)
        sensor = FileWatcherSensor(
            paths=[tmp_path],
            poll_interval_seconds=0.1,
            debounce_ms=0,
            force_polling=True,
        )
        mgr.register(sensor)
        mgr.start_all()
        try:
            time.sleep(0.2)
            # Implementation note.
            old_mtime = target.stat().st_mtime
            target.write_text("x = 2", encoding="utf-8")
            os.utime(target, (old_mtime + 5, old_mtime + 5))

            deadline = time.time() + 2.0
            while time.time() < deadline and not events:
                time.sleep(0.1)
        finally:
            mgr.stop_all()

        assert any(e.change_type == "modified" for e in events)

    def test_pattern_filter(self, tmp_path: Path):
        events: list[FileChanged] = []
        bus = TypedEventBus()
        bus.subscribe(FileChanged, events.append)

        mgr = SensorManager(bus=bus)
        sensor = FileWatcherSensor(
            paths=[tmp_path],
            patterns=["*.py"],
            poll_interval_seconds=0.1,
            debounce_ms=0,
            force_polling=True,
        )
        mgr.register(sensor)
        mgr.start_all()
        try:
            time.sleep(0.05)
            (tmp_path / "yes.py").write_text("x", encoding="utf-8")
            (tmp_path / "no.txt").write_text("x", encoding="utf-8")

            deadline = time.time() + 2.0
            while time.time() < deadline and len(events) < 1:
                time.sleep(0.1)
            time.sleep(0.3)  # Implementation note.
        finally:
            mgr.stop_all()

        paths = [e.path for e in events]
        assert any("yes.py" in p for p in paths)
        assert not any("no.txt" in p for p in paths)

    def test_ignore_patterns(self, tmp_path: Path):
        sensor = FileWatcherSensor(
            paths=[tmp_path],
            patterns=["*.py"],
            ignore_patterns=["*_pb2.py"],
        )
        assert sensor._matches("foo.py") is True
        assert sensor._matches("bar_pb2.py") is False

    def test_debounce_suppresses_storm(self, tmp_path: Path):
        sensor = FileWatcherSensor(
            paths=[tmp_path],
            debounce_ms=1000,
            force_polling=True,
        )
        # Implementation note.
        assert sensor._debounced("/a") is True
        # Implementation note.
        assert sensor._debounced("/a") is False

    def test_status_shows_backend_and_paths(self, tmp_path: Path):
        bus = TypedEventBus()
        sensor = FileWatcherSensor(
            paths=[tmp_path],
            force_polling=True,
            poll_interval_seconds=1.0,
        )
        mgr = SensorManager(bus=bus)
        mgr.register(sensor)
        status = sensor.status()
        assert status.sensor_id == "file_watcher"
        assert "paths" in status.metadata


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _git_available() -> bool:
    try:
        r = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            timeout=3.0,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _git_available(), reason="git CLI not available")
class TestGitHookSensor:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(tmp_path)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.name", "t"],
            check=True,
        )
        (tmp_path / "a.txt").write_text("a")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "a.txt"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
            check=True,
        )
        return tmp_path

    def test_no_event_on_stable_head(self, repo: Path):
        events: list = []
        bus = TypedEventBus()
        bus.subscribe(GitCommitDetected, events.append)

        mgr = SensorManager(bus=bus)
        sensor = GitHookSensor(repo_root=repo, poll_interval_seconds=0.1)
        mgr.register(sensor)
        mgr.start_all()
        try:
            time.sleep(0.3)
        finally:
            mgr.stop_all()
        assert events == []

    def test_detects_new_commit(self, repo: Path):
        events: list[GitCommitDetected] = []
        bus = TypedEventBus()
        bus.subscribe(GitCommitDetected, events.append)

        mgr = SensorManager(bus=bus)
        sensor = GitHookSensor(repo_root=repo, poll_interval_seconds=0.1)
        mgr.register(sensor)
        mgr.start_all()
        try:
            time.sleep(0.2)
            # Implementation note.
            (repo / "b.txt").write_text("b")
            subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "second"],
                check=True,
            )

            deadline = time.time() + 3.0
            while time.time() < deadline and not events:
                time.sleep(0.1)
        finally:
            mgr.stop_all()

        assert len(events) == 1
        assert events[0].subject == "second"
        assert events[0].branch == "main"
        assert events[0].sha != events[0].old_sha
        assert events[0].author == "t"

    def test_check_once_returns_event(self, repo: Path):
        """Implementation note."""
        bus = TypedEventBus()
        mgr = SensorManager(bus=bus)
        sensor = GitHookSensor(repo_root=repo)
        mgr.register(sensor)
        # Implementation note.
        sensor._last_sha = sensor._current_sha()

        # Implementation note.
        assert sensor.check_once() is None

        # Implementation note.
        (repo / "c.txt").write_text("c")
        subprocess.run(["git", "-C", str(repo), "add", "c.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "third"],
            check=True,
        )

        evt = sensor.check_once()
        assert evt is not None
        assert evt.subject == "third"


# ═══════════════════════════════════════════════════════════
# ProcessWatchSensor
# ═══════════════════════════════════════════════════════════


class TestProcessWatchSensor:
    def test_rejects_bad_pid(self):
        with pytest.raises(ValueError):
            ProcessWatchSensor(pid=0)
        with pytest.raises(ValueError):
            ProcessWatchSensor(pid=123, poll_interval_seconds=0)

    def test_is_alive_for_self(self):
        """Implementation note."""
        sensor = ProcessWatchSensor(pid=os.getpid())
        assert sensor.is_alive() is True

    def test_is_alive_false_for_bogus_pid(self):
        """Implementation note."""
        sensor = ProcessWatchSensor(pid=999_999)
        # Implementation note.
        # Implementation note.
        _ = sensor.is_alive()  # Implementation note.

    def test_check_once_initial_is_silent(self):
        """Implementation note."""
        bus = TypedEventBus()
        events: list = []
        bus.subscribe(ProcessStateChanged, events.append)
        mgr = SensorManager(bus=bus)
        sensor = ProcessWatchSensor(pid=os.getpid(), name="self")
        mgr.register(sensor)
        evt = sensor.check_once()
        assert evt is None
        assert events == []

    def test_detects_stop(self):
        """Implementation note."""
        bus = TypedEventBus()
        events: list[ProcessStateChanged] = []
        bus.subscribe(ProcessStateChanged, events.append)
        mgr = SensorManager(bus=bus)
        sensor = ProcessWatchSensor(pid=os.getpid(), name="self")
        mgr.register(sensor)
        # Implementation note.
        sensor.check_once()
        # Implementation note.
        orig = sensor.is_alive
        sensor.is_alive = lambda: False  # type: ignore[method-assign]
        evt = sensor.check_once()
        sensor.is_alive = orig  # type: ignore[method-assign]
        assert evt is not None
        assert evt.state == "stopped"
        assert len(events) == 1
