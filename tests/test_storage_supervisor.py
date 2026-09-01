"""Opt-in co-launch of echo-storage (best-effort, mocked subprocess)."""

from __future__ import annotations

import runtime.sensing.gateway.storage_supervisor as ss


def test_autostart_gating(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_STORAGE_AUTOSTART", raising=False)
    assert ss._autostart_enabled() is False
    monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
    assert ss._autostart_enabled() is True


def test_resolve_explicit_cmd_appends_serve_and_port(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_STORAGE_CMD", "/opt/bin/echo-storage")
    monkeypatch.delenv("ECHO_STORAGE_URL", raising=False)
    assert ss.resolve_storage_command() == [
        "/opt/bin/echo-storage",
        "serve",
        "--port",
        "8767",
    ]


def test_resolve_explicit_cmd_keeps_existing_serve(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_STORAGE_CMD", "octo-store serve --port 9000")
    assert ss.resolve_storage_command() == ["octo-store", "serve", "--port", "9000"]


def test_resolve_port_comes_from_storage_url(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_STORAGE_CMD", "echo-storage")
    monkeypatch.setenv("ECHO_STORAGE_URL", "http://127.0.0.1:9999")
    assert ss.resolve_storage_command() == ["echo-storage", "serve", "--port", "9999"]


def test_disabled_does_not_spawn(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_STORAGE_AUTOSTART", raising=False)
    spawned = {"n": 0}
    monkeypatch.setattr(ss.subprocess, "Popen", lambda *a, **k: spawned.__setitem__("n", 1))
    assert ss.maybe_start_storage() == "disabled"
    assert spawned["n"] == 0


def test_explicit_user_start_bypasses_boot_autostart_gate(monkeypatch) -> None:
    ss._proc = None
    monkeypatch.delenv("ECHO_STORAGE_AUTOSTART", raising=False)
    monkeypatch.setattr(ss, "_already_up", lambda: False)
    monkeypatch.setattr(
        ss, "resolve_storage_command", lambda: ["/x/echo-storage", "serve", "--port", "8767"]
    )

    class _FakeProc:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(ss.subprocess, "Popen", lambda *_a, **_k: _FakeProc())
    monkeypatch.setattr(
        ss.threading, "Thread", lambda **_k: type("T", (), {"start": lambda self: None})()
    )

    assert ss.maybe_start_storage(force=True) == "started"
    ss._proc = None


def test_already_running_does_not_spawn(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
    monkeypatch.setattr(ss, "_already_up", lambda: True)
    spawned = {"n": 0}
    monkeypatch.setattr(ss.subprocess, "Popen", lambda *a, **k: spawned.__setitem__("n", 1))
    assert ss.maybe_start_storage() == "already_running"
    assert spawned["n"] == 0


def test_not_found_when_unresolvable(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
    monkeypatch.setattr(ss, "_already_up", lambda: False)
    monkeypatch.setattr(ss, "resolve_storage_command", lambda: None)
    assert ss.maybe_start_storage() == "not_found"


def test_spawns_with_resolved_cmd(monkeypatch) -> None:
    ss._proc = None
    monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
    monkeypatch.setattr(ss, "_already_up", lambda: False)
    monkeypatch.setattr(
        ss, "resolve_storage_command", lambda: ["/x/echo-storage", "serve", "--port", "8767"]
    )
    seen: dict = {}

    class _FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **_k):
        seen["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(ss.subprocess, "Popen", fake_popen)
    # don't run the real readiness thread
    monkeypatch.setattr(
        ss.threading, "Thread", lambda **_k: type("T", (), {"start": lambda self: None})()
    )
    assert ss.maybe_start_storage() == "started"
    assert seen["cmd"] == ["/x/echo-storage", "serve", "--port", "8767"]
    ss._proc = None


def test_stop_storage_terminates_child() -> None:
    class _FakeProc:
        def __init__(self):
            self.terminated = False
            self._alive = True

        def poll(self):
            return None if self._alive else 0

        def terminate(self):
            self.terminated = True
            self._alive = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self._alive = False

    p = _FakeProc()
    ss._proc = p
    ss.stop_storage()
    assert p.terminated is True
    assert ss._proc is None


def test_should_restart_decision() -> None:
    base = dict(
        autostart=True,
        proc_alive=False,
        resolvable=True,
        now=100.0,
        last_restart=0.0,
        backoff_s=30.0,
    )
    # down + autostart + resolvable + dead child + past backoff → relaunch
    assert ss._should_restart(up=False, **base) is True
    # storage is up → never
    assert ss._should_restart(up=True, **base) is False
    # we don't own its lifecycle → never
    assert ss._should_restart(up=False, **{**base, "autostart": False}) is False
    # our child is still (re)booting → give it a cycle
    assert ss._should_restart(up=False, **{**base, "proc_alive": True}) is False
    # command can't be resolved → can't relaunch
    assert ss._should_restart(up=False, **{**base, "resolvable": False}) is False
    # within the backoff window → hold off (no thrash)
    assert ss._should_restart(up=False, **{**base, "last_restart": 80.0}) is False


def test_storage_status_probes_on_demand_without_heartbeat(monkeypatch) -> None:
    monkeypatch.setattr(ss, "_heartbeat_started", False)
    with ss._status_lock:
        ss._state["heartbeat"] = False
    monkeypatch.setattr(ss, "_already_up", lambda: True)
    assert ss.storage_status()["up"] is True
    monkeypatch.setattr(ss, "_already_up", lambda: False)
    assert ss.storage_status()["up"] is False


def test_heartbeat_gated_on_autostart_and_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(ss, "_heartbeat_started", False)
    with ss._status_lock:
        ss._state["heartbeat"] = False
    started = {"n": 0}

    class _T:
        def __init__(self, **_k) -> None:
            pass

        def start(self) -> None:
            started["n"] += 1

    monkeypatch.setattr(ss.threading, "Thread", _T)
    # autostart off → no supervision thread
    monkeypatch.delenv("ECHO_STORAGE_AUTOSTART", raising=False)
    ss.start_storage_heartbeat()
    assert started["n"] == 0
    # autostart on → starts exactly once, repeat calls are no-ops
    monkeypatch.setenv("ECHO_STORAGE_AUTOSTART", "1")
    ss.start_storage_heartbeat()
    ss.start_storage_heartbeat()
    assert started["n"] == 1
    monkeypatch.setattr(ss, "_heartbeat_started", False)

