"""Tests for runtime.memory.skills_lib.ambient_suggestions_scheduler."""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from runtime.memory.skills_lib.ambient_suggestions_scheduler import (
    AmbientScheduler,
    AmbientSchedulerConfig,
    get_ambient_scheduler,
)
from runtime.platform import feature_flags as ff

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_flag_snapshot() -> Iterator[None]:
    """Isolate each test's feature-flag state so env monkeypatches do
    not bleed between tests."""
    original = dict(ff._SPECS)
    yield
    ff._SPECS.clear()
    ff._SPECS.update(original)
    ff._SNAPSHOT = None
    ff._FILE_PATH = None


@pytest.fixture(autouse=True)
def _reset_scheduler_singleton() -> Iterator[None]:
    """Reset the module-level singleton before and after every test.

    The scheduler is a classic process-wide singleton; without this
    ``stop``/``start`` state leaks across tests.
    """
    with AmbientScheduler._instance_lock:
        AmbientScheduler._instance = None
    yield
    inst = AmbientScheduler._instance
    if inst is not None:
        with contextlib.suppress(Exception):
            inst.stop(timeout=2.0)
    with AmbientScheduler._instance_lock:
        AmbientScheduler._instance = None


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a fake project root with an ``agents/`` tree.

    Points ``ECHO_HOME`` at it so ``_project_root()`` resolves
    there without touching the real repo.
    """
    (tmp_path / "agents").mkdir()
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    # Also clear any env vars that could override flag defaults.
    monkeypatch.delenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", raising=False)
    monkeypatch.delenv(
        "ECHO_FF_UI_AMBIENT_SUGGESTIONS_INTERVAL_SEC",
        raising=False,
    )
    ff._SNAPSHOT = None
    return tmp_path


def _write_scores(
    workspace: Path,
    agent_id: str,
    *,
    turn_count: int = 5,
    age_days: int = 0,
    include_malformed: bool = False,
) -> Path:
    """Create an ``agents/<id>/agent-core/.scores.jsonl`` with
    ``turn_count`` synthetic entries aged ``age_days`` days ago."""
    agent_core = workspace / "agents" / agent_id / "agent-core"
    agent_core.mkdir(parents=True, exist_ok=True)
    path = agent_core / ".scores.jsonl"
    ts = datetime.now(UTC) - timedelta(days=age_days)
    # Match ``record_turn_score``: naive local isoformat with seconds.
    ts_str = ts.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    lines: list[str] = []
    for i in range(turn_count):
        lines.append(
            json.dumps(
                {
                    "ts": ts_str,
                    "agent_id": agent_id,
                    "score": 1.0,
                    "reason": "success",
                    "soul_hash": "deadbeef",
                    "thread_id": f"t-{i}",
                    "turn_id": f"u-{i}",
                }
            )
        )
    if include_malformed:
        lines.append("{not-json at all}")
        lines.append("")  # blank line
        lines.append("garbage")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _enable_flag(monkeypatch: pytest.MonkeyPatch, value: str = "1") -> None:
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", value)
    ff._SNAPSHOT = None


# ═══════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════


def test_tick_once_returns_expected_keys(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    sched = get_ambient_scheduler()
    with patch(
        "runtime.memory.skills_lib.ambient_suggestions.generate_suggestions",
    ) as gen:
        gen.return_value = {"added": 0, "error": None}
        out = sched.tick_once()
    assert set(out.keys()) == {
        "agents_processed",
        "errors",
        "suggestions_added",
    }
    assert isinstance(out["agents_processed"], int)
    assert isinstance(out["errors"], list)
    assert isinstance(out["suggestions_added"], int)


def test_tick_once_noop_when_flag_off(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_AMBIENT_SUGGESTIONS", "0")
    ff._SNAPSHOT = None
    _write_scores(workspace, "coder", turn_count=5)
    sched = get_ambient_scheduler()
    with patch(
        "runtime.memory.skills_lib.ambient_suggestions.generate_suggestions",
    ) as gen:
        gen.return_value = {"added": 2, "error": None}
        out = sched.tick_once()
    assert out["agents_processed"] == 0
    assert out["suggestions_added"] == 0
    assert gen.call_count == 0


def test_tick_once_iterates_active_agents(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    _write_scores(workspace, "coder", turn_count=5, age_days=0)
    _write_scores(workspace, "general", turn_count=4, age_days=1)
    # Too old — should NOT be active.
    _write_scores(workspace, "stale", turn_count=10, age_days=30)
    # Too few turns — should NOT be active.
    _write_scores(workspace, "quiet", turn_count=2, age_days=0)
    # Underscore-prefixed shared dir should be skipped.
    _write_scores(workspace, "_shared", turn_count=5, age_days=0)

    sched = get_ambient_scheduler()
    with patch(
        "runtime.memory.skills_lib.ambient_suggestions.generate_suggestions",
    ) as gen:
        gen.return_value = {"added": 1, "error": None}
        out = sched.tick_once()

    processed_ids = sorted(c.args[1] for c in gen.call_args_list)
    assert processed_ids == ["coder", "general"]
    assert out["agents_processed"] == 2
    assert out["suggestions_added"] == 2


def test_tick_once_caps_at_max_agents_per_tick(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    for i in range(5):
        _write_scores(workspace, f"agent{i}", turn_count=4)

    sched = get_ambient_scheduler()
    sched._config = AmbientSchedulerConfig(max_agents_per_tick=2)
    with patch(
        "runtime.memory.skills_lib.ambient_suggestions.generate_suggestions",
    ) as gen:
        gen.return_value = {"added": 0, "error": None}
        out = sched.tick_once()
    assert out["agents_processed"] == 2
    assert gen.call_count == 2


def test_per_agent_failure_does_not_stop_loop(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    _write_scores(workspace, "boom", turn_count=5)
    _write_scores(workspace, "fine", turn_count=5)

    def fake(project, agent_id, **_kw):
        if agent_id == "boom":
            raise RuntimeError("llm exploded")
        return {"added": 1, "error": None}

    sched = get_ambient_scheduler()
    with patch(
        "runtime.memory.skills_lib.ambient_suggestions.generate_suggestions",
        side_effect=fake,
    ) as gen:
        out = sched.tick_once()

    called = sorted(c.args[1] for c in gen.call_args_list)
    assert called == ["boom", "fine"]
    assert out["agents_processed"] == 2
    assert out["suggestions_added"] == 1
    # The boom agent should have produced an error entry.
    assert any(e.get("agent_id") == "boom" for e in out["errors"])


def test_start_then_stop_returns_quickly(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    sched = get_ambient_scheduler()
    start = time.monotonic()
    sched.start(
        AmbientSchedulerConfig(
            interval_sec=60,
            initial_delay_sec=30,  # never hit before we stop
            enabled=True,
        )
    )
    sched.stop(timeout=2.0)
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, f"start+stop took {elapsed:.2f}s"
    # Thread should be fully cleaned up.
    assert sched._thread is None or not sched._thread.is_alive()


def test_start_honors_initial_delay_no_tick_before_stop(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    _write_scores(workspace, "coder", turn_count=5)

    sched = get_ambient_scheduler()
    with patch(
        "runtime.memory.skills_lib.ambient_suggestions.generate_suggestions",
    ) as gen:
        gen.return_value = {"added": 0, "error": None}
        # Huge initial delay — the loop's wait should cover it, and
        # stop() should cancel it before any tick fires.
        sched.start(
            AmbientSchedulerConfig(
                interval_sec=5,
                initial_delay_sec=30,
                enabled=True,
            )
        )
        # Sleep a hair to let the thread enter its wait.
        time.sleep(0.2)
        sched.stop(timeout=2.0)
    assert gen.call_count == 0, "generate_suggestions must not be called before initial_delay"


def test_interval_flag_env_resolves(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ECHO_FF_UI_AMBIENT_SUGGESTIONS_INTERVAL_SEC",
        "900",
    )
    ff._SNAPSHOT = None
    sched = get_ambient_scheduler()
    assert sched._current_interval() == 900
    # Below-floor values should clamp to 60 (busy-loop guard).
    monkeypatch.setenv(
        "ECHO_FF_UI_AMBIENT_SUGGESTIONS_INTERVAL_SEC",
        "5",
    )
    ff._SNAPSHOT = None
    assert sched._current_interval() == 60


def test_singleton_returns_same_instance() -> None:
    a = get_ambient_scheduler()
    b = get_ambient_scheduler()
    c = AmbientScheduler.get()
    assert a is b is c


def test_does_not_crash_on_malformed_scores_lines(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    _write_scores(
        workspace,
        "coder",
        turn_count=5,
        include_malformed=True,
    )
    sched = get_ambient_scheduler()
    with patch(
        "runtime.memory.skills_lib.ambient_suggestions.generate_suggestions",
    ) as gen:
        gen.return_value = {"added": 0, "error": None}
        out = sched.tick_once()
    # coder still passes the activity threshold on the 5 good lines.
    assert out["agents_processed"] == 1
    assert gen.call_count == 1
