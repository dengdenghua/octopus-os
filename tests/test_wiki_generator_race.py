"""Audit P-05: wiki generation uses an atomic test-and-set under the lock.

Two concurrent requests must never both spawn the generator subprocess;
a second request while one is running gets rejected.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from runtime.sensing.gateway import wiki_router as wr


def _wait_running_cleared(timeout_s: float = 5.0) -> None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end and wr._STATE.running:
        time.sleep(0.02)


def test_generator_test_and_set_prevents_double_start(monkeypatch) -> None:
    release = threading.Event()
    subprocess_started = threading.Event()
    calls: list[int] = []

    def fake_run(*args, **kwargs):
        calls.append(1)
        subprocess_started.set()
        release.wait(5)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(wr.subprocess, "run", fake_run)
    wr._STATE.running = False
    try:
        assert wr._run_generator() is True  # first caller acquires the slot
        assert wr._run_generator() is False  # second caller is rejected
        assert subprocess_started.wait(3), "first worker never reached the subprocess"
        assert len(calls) == 1, "generator subprocess started twice"
        release.set()
        _wait_running_cleared()
        assert wr._STATE.running is False
        # After completion a fresh start is allowed again.
        assert wr._run_generator() is True
    finally:
        release.set()
        _wait_running_cleared()

