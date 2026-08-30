"""Tests for slices #1, #2, #3 of the production-polish batch:

* §1: ``safety.guard_overrides`` per-spec yaml override.
* §2: ``CheckpointMirror`` retry + circuit breaker.
* §3: resume_cli writes ``task_resumed`` event before driving runner.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from runtime.core.cerebrum import resume_cli
from runtime.core.cerebrum.checkpoint_mirror import (
    CheckpointMirror,
    _CircuitBreaker,
)
from runtime.core.cerebrum.react_loop import (
    _disabled_guards_from_yaml,
    _reset_disabled_set_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECHO_DISABLED_GUARDS", raising=False)
    _reset_disabled_set_for_tests()
    yield tmp_path
    _reset_disabled_set_for_tests()


# ══════════════════════════════════════════════════════════════════
# §1 — guard_overrides yaml
# ══════════════════════════════════════════════════════════════════


class TestGuardOverridesYaml:
    def test_override_false_adds_to_disabled(
        self,
        _isolated_cwd: Path,
    ) -> None:
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n  disabled_guards: []\n  guard_overrides:\n    magic-number guard: false\n",
            encoding="utf-8",
        )
        result = _disabled_guards_from_yaml()
        assert "magic-number guard" in result

    def test_override_true_removes_from_disabled(
        self,
        _isolated_cwd: Path,
    ) -> None:
        # Blanket disabled list says off; override says on (True wins).
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n"
            "  disabled_guards:\n    - weak-test guard\n"
            "  guard_overrides:\n"
            "    weak-test guard: true\n",
            encoding="utf-8",
        )
        result = _disabled_guards_from_yaml()
        assert "weak-test guard" not in result

    def test_overrides_combine_with_disabled_list(
        self,
        _isolated_cwd: Path,
    ) -> None:
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n"
            "  disabled_guards:\n    - guard-a\n"
            "  guard_overrides:\n"
            "    guard-b: false\n"
            "    guard-c: true\n",
            encoding="utf-8",
        )
        result = _disabled_guards_from_yaml()
        assert result == frozenset({"guard-a", "guard-b"})

    def test_non_bool_override_ignored(self, _isolated_cwd: Path) -> None:
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n  guard_overrides:\n    guard-a: 'maybe'\n    guard-b: 42\n",
            encoding="utf-8",
        )
        result = _disabled_guards_from_yaml()
        assert result == frozenset()

    def test_overrides_not_dict_silent(self, _isolated_cwd: Path) -> None:
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n  guard_overrides: not-a-dict\n",
            encoding="utf-8",
        )
        assert _disabled_guards_from_yaml() == frozenset()


# ══════════════════════════════════════════════════════════════════
# §2 — CheckpointMirror retry + circuit breaker
# ══════════════════════════════════════════════════════════════════


class _FlakyClient:
    """Client that fails N times then succeeds."""

    def __init__(self, fails_before_success: int) -> None:
        self.fails_before_success = fails_before_success
        self.calls = 0
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.calls <= self.fails_before_success:
            raise RuntimeError(f"flake {self.calls}")

    def set(self, k, v):
        self._maybe_fail()
        self.kv[k] = v

    def get(self, k):
        self._maybe_fail()
        return self.kv.get(k)

    def sadd(self, k, *m):
        self._maybe_fail()
        self.sets.setdefault(k, set()).update(m)
        return len(m)

    def srem(self, k, *m):
        self._maybe_fail()
        s = self.sets.get(k, set())
        for x in m:
            s.discard(x)

    def smembers(self, k):
        self._maybe_fail()
        return list(self.sets.get(k, set()))

    def delete(self, *keys):
        self._maybe_fail()
        for k in keys:
            self.kv.pop(k, None)


class _AlwaysFailClient:
    def set(self, *a, **kw):
        raise RuntimeError("always")

    def get(self, *a, **kw):
        raise RuntimeError("always")

    def sadd(self, *a, **kw):
        raise RuntimeError("always")

    def srem(self, *a, **kw):
        raise RuntimeError("always")

    def smembers(self, *a, **kw):
        raise RuntimeError("always")

    def delete(self, *a, **kw):
        raise RuntimeError("always")


class TestRetry:
    def test_succeeds_on_retry(self) -> None:
        client = _FlakyClient(fails_before_success=1)
        m = CheckpointMirror(client, retry_sleep_s=0.0)
        # First attempt fails, second succeeds.
        assert m.put("t", {"i": 1}) is True
        # Each public call invokes retry; client.calls counts
        # individual underlying ops. Just check it eventually wrote.
        assert m.get("t") == {"i": 1}

    def test_gives_up_after_max_attempts(self) -> None:
        m = CheckpointMirror(_AlwaysFailClient(), retry_sleep_s=0.0)
        assert m.put("t", {"i": 1}) is False

    def test_sleep_invoked_between_retries(self) -> None:
        sleeps: list[float] = []
        client = _FlakyClient(fails_before_success=1)
        m = CheckpointMirror(
            client,
            retry_sleep_s=0.1,
            sleep_fn=lambda s: sleeps.append(s),
        )
        m.put("t", {"i": 1})
        assert sleeps == [0.1]


class TestCircuitBreaker:
    def test_breaker_opens_after_threshold(self) -> None:
        b = _CircuitBreaker(threshold=3, cooldown_s=10, clock=lambda: 0.0)
        assert b.is_open() is False
        b.record_failure()
        b.record_failure()
        assert b.is_open() is False
        b.record_failure()
        assert b.is_open() is True

    def test_breaker_resets_on_success(self) -> None:
        b = _CircuitBreaker(threshold=2, cooldown_s=10, clock=lambda: 0.0)
        b.record_failure()
        b.record_success()
        b.record_failure()  # back to 1, not at threshold
        assert b.is_open() is False

    def test_breaker_cooldown_lets_calls_through(self) -> None:
        time_now = [0.0]
        b = _CircuitBreaker(
            threshold=1,
            cooldown_s=5,
            clock=lambda: time_now[0],
        )
        b.record_failure()
        assert b.is_open() is True
        time_now[0] = 6.0  # past cooldown
        assert b.is_open() is False

    def test_mirror_quiet_while_breaker_open(self) -> None:
        m = CheckpointMirror(
            _AlwaysFailClient(),
            retry_sleep_s=0.0,
            breaker_threshold=1,
            breaker_cooldown_s=10,
        )
        # First put fails, breaker opens (threshold=1).
        assert m.put("t", {"i": 1}) is False
        # Subsequent calls short-circuit — they should not raise.
        assert m.put("t", {"i": 2}) is False
        assert m.get("t") is None
        assert m.list_tasks() == []


# ══════════════════════════════════════════════════════════════════
# §3 — resume telemetry
# ══════════════════════════════════════════════════════════════════


class TestResumeTelemetry:
    def test_resume_writes_task_resumed_event(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "j.jsonl"
        # Seed a non-final checkpoint event.
        import json as _json

        path.write_text(
            _json.dumps(
                {
                    "event_type": "react_checkpoint",
                    "task_id": "task-z",
                    "ts": "2026-06-02T10:00:00",
                    "iteration_completed": 5,
                    "max_iterations": 100,
                    "current_phase": "implement",
                    "has_final_answer": False,
                    "steps_snapshot": [],
                    "working_set_snapshot": [],
                    "progress_summary": "Working on it.",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        captured: dict[str, object] = {}

        class _RecordingJournal:
            def write_task_resumed(self, **kw):
                captured["kw"] = kw

        # The journal_loader returns our recording journal; the
        # stack_builder yields a stack with that journal attached.
        rec_journal = _RecordingJournal()

        rc = resume_cli._resume_task(
            "task-z",
            journal_path=path,
            journal_loader=lambda p: rec_journal,
            stack_builder=lambda **kw: (
                SimpleNamespace(),
                SimpleNamespace(),
                kw["journal"],
            ),
            runner=lambda *a, **kw: SimpleNamespace(final_answer="ok"),
        )
        assert rc == 0
        assert captured["kw"]["task_id"] == "task-z"
        assert "resume_cli/" in captured["kw"]["resumed_by"]

    def test_resume_telemetry_failure_does_not_break_resume(
        self,
        tmp_path: Path,
    ) -> None:
        # Journal raises on write_task_resumed — runner must still run.
        path = tmp_path / "j.jsonl"
        import json as _json

        path.write_text(
            _json.dumps(
                {
                    "event_type": "react_checkpoint",
                    "task_id": "t",
                    "ts": "2026-06-02T10:00:00",
                    "iteration_completed": 1,
                    "max_iterations": 10,
                    "has_final_answer": False,
                    "steps_snapshot": [],
                    "working_set_snapshot": [],
                    "progress_summary": "",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        class _BoomJournal:
            def write_task_resumed(self, **kw):
                raise RuntimeError("audit pipe down")

        runner_called: list[bool] = []
        rc = resume_cli._resume_task(
            "t",
            journal_path=path,
            journal_loader=lambda p: _BoomJournal(),
            stack_builder=lambda **kw: (
                SimpleNamespace(),
                SimpleNamespace(),
                kw["journal"],
            ),
            runner=lambda *a, **kw: runner_called.append(True),
        )
        # Audit failure is swallowed; resume proceeds.
        assert rc == 0
        assert runner_called == [True]
