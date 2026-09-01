"""Unit tests for ``runtime.core.cerebrum.leader``.

Spins up a real LeaderProcess on a temp UDS path. No subprocess —
the leader runs in a background thread within the test process so
failures surface as normal assertion errors instead of timeouts.

Uses ``/tmp`` short paths because macOS limits AF_UNIX socket paths
to 104 bytes — pytest's default ``tmp_path`` is often longer.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from runtime.core.cerebrum.leader import (
    LeaderAlreadyRunning,
    LeaderClient,
    LeaderError,
    LeaderNotRunning,
    LeaderProcess,
    LeaderState,
    _pid_alive,
)


def _short_tmp_dir() -> Path:
    """A short-lived per-test dir under /tmp to dodge macOS UDS limits."""
    path = Path("/tmp") / f"echo-leader-test-{os.getpid()}-{time.time_ns()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def short_tmp() -> Path:
    path = _short_tmp_dir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def leader_socket(short_tmp: Path) -> Path:
    return short_tmp / "s.sock"


@pytest.fixture()
def leader_pid(short_tmp: Path) -> Path:
    return short_tmp / "s.pid"


@pytest.fixture()
def running_leader(leader_socket: Path, leader_pid: Path) -> LeaderProcess:
    """Start a non-blocking leader; teardown stops it."""
    proc = LeaderProcess(socket_path=leader_socket, pid_path=leader_pid)
    proc.start(blocking=False)
    # Wait briefly for the socket to come up.
    deadline = time.time() + 1.0
    while time.time() < deadline and not leader_socket.exists():
        time.sleep(0.01)
    if not leader_socket.exists():
        proc.stop()
        pytest.fail("leader socket did not come up")
    yield proc
    proc.stop()


# ── PID file management ─────────────────────────────────────


def test_pid_alive_for_current_process() -> None:
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_for_dead_process() -> None:
    # pid 2**31-1 is very unlikely to exist on a normal test box.
    assert _pid_alive(2_147_483_647) is False


def test_pid_alive_for_invalid_pid() -> None:
    assert _pid_alive(-1) is False
    assert _pid_alive(0) is False


def test_leader_writes_pid_file(running_leader: LeaderProcess, leader_pid: Path) -> None:
    assert leader_pid.exists()
    pid_in_file = int(leader_pid.read_text().strip())
    assert pid_in_file == os.getpid()


# ── Single-instance enforcement ─────────────────────────────


def test_second_leader_fails_with_already_running(
    running_leader: LeaderProcess,
    leader_socket: Path,
    leader_pid: Path,
) -> None:
    second = LeaderProcess(socket_path=leader_socket, pid_path=leader_pid)
    with pytest.raises(LeaderAlreadyRunning):
        second.start(blocking=False)
    second.stop()


def test_stale_pid_file_is_reclaimed(short_tmp: Path) -> None:
    socket_path = short_tmp / "s.sock"
    pid_path = short_tmp / "s.pid"
    # Write a stale pid file pointing at a definitely-dead pid.
    pid_path.write_text("2_000_000")
    proc = LeaderProcess(socket_path=socket_path, pid_path=pid_path)
    proc.start(blocking=False)
    try:
        # Pid file should now point at OUR process.
        assert int(pid_path.read_text().strip()) == os.getpid()
    finally:
        proc.stop()


# ── Client / server round-trip ──────────────────────────────


def test_client_status_returns_snapshot(running_leader: LeaderProcess, leader_socket: Path) -> None:
    with LeaderClient.connect(leader_socket) as client:
        snapshot = client.call("status", {})
    assert snapshot["pid"] == os.getpid()
    assert snapshot["protocol_version"] == 1
    assert "uptime_seconds" in snapshot
    assert snapshot["task_status"] == {}


def test_client_ping(running_leader: LeaderProcess, leader_socket: Path) -> None:
    with LeaderClient.connect(leader_socket) as client:
        result = client.call("ping", {})
    assert result == {"pong": True, "pid": os.getpid()}


def test_client_pause_and_resume(
    running_leader: LeaderProcess,
    leader_socket: Path,
) -> None:
    with LeaderClient.connect(leader_socket) as client:
        paused = client.call("pause", {"task_id": "task-1"})
        assert paused == {"ok": True, "task_id": "task-1", "status": "paused"}

        resumed = client.call("resume", {"task_id": "task-1"})
        assert resumed == {"ok": True, "task_id": "task-1", "status": "running"}

        snapshot = client.call("status", {})
        assert snapshot["task_status"]["task-1"] == "running"


def test_client_set_task_status_validates_status(
    running_leader: LeaderProcess,
    leader_socket: Path,
) -> None:
    with (
        LeaderClient.connect(leader_socket) as client,
        pytest.raises(LeaderError, match="invalid status"),
    ):
        client.call("set_task_status", {"task_id": "t", "status": "bogus"})


def test_client_set_task_status_requires_task_id(
    running_leader: LeaderProcess,
    leader_socket: Path,
) -> None:
    with (
        LeaderClient.connect(leader_socket) as client,
        pytest.raises(LeaderError, match="task_id required"),
    ):
        client.call("set_task_status", {"status": "running"})


def test_client_method_not_found(
    running_leader: LeaderProcess,
    leader_socket: Path,
) -> None:
    with (
        LeaderClient.connect(leader_socket) as client,
        pytest.raises(LeaderError, match="unknown method"),
    ):
        client.call("nonexistent_method", {})


# ── Client connection failures ──────────────────────────────


def test_connect_raises_when_socket_missing(short_tmp: Path) -> None:
    with pytest.raises(LeaderNotRunning):
        LeaderClient.connect(short_tmp / "missing.sock")


def test_connect_raises_when_socket_path_has_no_listener(
    short_tmp: Path,
) -> None:
    # Create the file but no one is listening on it.
    socket_path = short_tmp / "lonely.sock"
    socket_path.touch()
    with pytest.raises(LeaderNotRunning):
        LeaderClient.connect(socket_path)


# ── State broadcast ─────────────────────────────────────────


def test_register_handler_extends_protocol(
    running_leader: LeaderProcess,
    leader_socket: Path,
) -> None:
    running_leader.register_handler(
        "echo",
        lambda params: {"echoed": params},
    )
    with LeaderClient.connect(leader_socket) as client:
        result = client.call("echo", {"foo": "bar"})
    assert result == {"echoed": {"foo": "bar"}}


def test_register_handler_rejects_duplicates(
    running_leader: LeaderProcess,
) -> None:
    with pytest.raises(ValueError, match="already registered"):
        running_leader.register_handler("status", lambda _p: None)


# ── LeaderState ─────────────────────────────────────────────


def test_leader_state_snapshot_shape() -> None:
    state = LeaderState()
    snap = state.snapshot()
    assert snap["protocol_version"] == 1
    assert snap["pid"] == os.getpid()
    assert snap["task_status"] == {}
    assert snap["client_count"] == 0

