"""Tests for the operator kill-switch — disabling guards by label.

Two layers:

* ``evaluate_guards(disabled_labels=...)`` — programmatic skip.
* ``ECHO_DISABLED_GUARDS`` env var — runtime knob the ReAct loop
  reads via ``_disabled_guard_labels``.

Critical invariant: a disabled guard must NOT record telemetry —
disabling means "this firing didn't actually block anything", and
counting it would poison the digest the evolver consumes.
"""

from __future__ import annotations

import pytest
from runtime.core.cerebrum.react_guards import (
    GuardContext,
    GuardSpec,
    evaluate_guards,
)
from runtime.core.cerebrum.react_loop import _disabled_guard_labels
from runtime.core.cerebrum.react_types import ReActStep


def _step(iteration: int, *, action: str = "") -> ReActStep:
    return ReActStep(iteration=iteration, action=action)


def _always_fires(_ctx: GuardContext) -> str | None:
    return "boom"


def _never_fires(_ctx: GuardContext) -> str | None:
    return None


@pytest.fixture
def fake_registry() -> list[GuardSpec]:
    return [
        GuardSpec(label="guard-a", category="security", invoke=_always_fires),
        GuardSpec(label="guard-b", category="code-smell", invoke=_always_fires),
        GuardSpec(label="guard-c", category="test-quality", invoke=_never_fires),
    ]


# ══════════════════════════════════════════════════════════════════
# evaluate_guards(disabled_labels=...)
# ══════════════════════════════════════════════════════════════════


class TestDisabledLabels:
    def test_disabled_skipped(self, fake_registry: list[GuardSpec]) -> None:
        ctx = GuardContext(
            steps=[_step(1)],
            final_answer="x",
            is_code_mode=True,
        )
        # Without disabling: first guard fires.
        assert evaluate_guards(ctx, registry=fake_registry) == ("guard-a", "boom")
        # Disable the first; the SECOND guard should now fire.
        hit = evaluate_guards(
            ctx,
            registry=fake_registry,
            disabled_labels={"guard-a"},
        )
        assert hit == ("guard-b", "boom")

    def test_all_firing_disabled_returns_none(
        self,
        fake_registry: list[GuardSpec],
    ) -> None:
        ctx = GuardContext(
            steps=[_step(1)],
            final_answer="x",
            is_code_mode=True,
        )
        hit = evaluate_guards(
            ctx,
            registry=fake_registry,
            disabled_labels={"guard-a", "guard-b"},
        )
        # guard-c never fires → no hit at all.
        assert hit is None

    def test_disabled_does_not_record_telemetry(
        self,
        fake_registry: list[GuardSpec],
    ) -> None:
        ctx = GuardContext(
            steps=[_step(1)],
            final_answer="x",
            is_code_mode=True,
        )
        recorded: list[tuple[str, str]] = []
        # guard-a is disabled, guard-b will fire and SHOULD be recorded.
        hit = evaluate_guards(
            ctx,
            registry=fake_registry,
            recorder=lambda lab, cat, _msg: recorded.append((lab, cat)),
            disabled_labels={"guard-a"},
        )
        assert hit == ("guard-b", "boom")
        # Only guard-b is recorded; guard-a is NOT.
        assert recorded == [("guard-b", "code-smell")]

    def test_empty_disabled_set_is_noop(
        self,
        fake_registry: list[GuardSpec],
    ) -> None:
        ctx = GuardContext(
            steps=[_step(1)],
            final_answer="x",
            is_code_mode=True,
        )
        assert evaluate_guards(
            ctx,
            registry=fake_registry,
            disabled_labels=set(),
        ) == ("guard-a", "boom")

    def test_none_disabled_is_noop(self, fake_registry: list[GuardSpec]) -> None:
        ctx = GuardContext(
            steps=[_step(1)],
            final_answer="x",
            is_code_mode=True,
        )
        assert evaluate_guards(
            ctx,
            registry=fake_registry,
            disabled_labels=None,
        ) == ("guard-a", "boom")

    def test_unknown_disabled_labels_ignored(
        self,
        fake_registry: list[GuardSpec],
    ) -> None:
        # Operator typos shouldn't be silent failures, but the SAFE
        # fallback is "ignore the typo, run normally" — better than
        # crashing the loop. Verify that path.
        ctx = GuardContext(
            steps=[_step(1)],
            final_answer="x",
            is_code_mode=True,
        )
        hit = evaluate_guards(
            ctx,
            registry=fake_registry,
            disabled_labels={"nonexistent-guard"},
        )
        assert hit == ("guard-a", "boom")


# ══════════════════════════════════════════════════════════════════
# ECHO_DISABLED_GUARDS env var
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_kill_switch_cache():
    """Reset the cached last-seen disabled set between tests."""
    from runtime.core.cerebrum.react_loop import _reset_disabled_set_for_tests

    _reset_disabled_set_for_tests()
    yield
    _reset_disabled_set_for_tests()


class TestEnvVarParsing:
    def test_unset_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ECHO_DISABLED_GUARDS", raising=False)
        assert _disabled_guard_labels() == frozenset()

    def test_blank_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "   ")
        assert _disabled_guard_labels() == frozenset()

    def test_single_label(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "magic-number guard")
        assert _disabled_guard_labels() == frozenset({"magic-number guard"})

    def test_multiple_labels_comma_separated(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "ECHO_DISABLED_GUARDS",
            "magic-number guard,long-function guard",
        )
        assert _disabled_guard_labels() == frozenset(
            {
                "magic-number guard",
                "long-function guard",
            }
        )

    def test_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "ECHO_DISABLED_GUARDS",
            " magic-number guard , long-function guard ,, ",
        )
        assert _disabled_guard_labels() == frozenset(
            {
                "magic-number guard",
                "long-function guard",
            }
        )

    def test_re_read_on_each_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Operators may flip the env at runtime; the loop must pick up
        # changes on the next turn rather than caching the value.
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "guard-a")
        assert _disabled_guard_labels() == frozenset({"guard-a"})
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "guard-b")
        assert _disabled_guard_labels() == frozenset({"guard-b"})
        monkeypatch.delenv("ECHO_DISABLED_GUARDS")
        assert _disabled_guard_labels() == frozenset()


# ══════════════════════════════════════════════════════════════════
# Audit trail — when the disabled set changes we leave a record
# ══════════════════════════════════════════════════════════════════


class TestKillSwitchAudit:
    def test_logs_warning_on_first_non_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "guard-a")
        with caplog.at_level("WARNING", logger="runtime.core.cerebrum.react_loop"):
            _disabled_guard_labels()
        assert any("ECHO_DISABLED_GUARDS changed" in r.message for r in caplog.records)

    def test_idempotent_no_log_when_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "guard-a")
        _disabled_guard_labels()  # First call — emits.
        caplog.clear()
        with caplog.at_level("WARNING", logger="runtime.core.cerebrum.react_loop"):
            for _ in range(5):
                _disabled_guard_labels()  # Same value 5x — no further emits.
        kill_switch_logs = [
            r for r in caplog.records if "ECHO_DISABLED_GUARDS changed" in r.message
        ]
        assert kill_switch_logs == []

    def test_logs_on_each_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level("WARNING", logger="runtime.core.cerebrum.react_loop"):
            monkeypatch.setenv("ECHO_DISABLED_GUARDS", "guard-a")
            _disabled_guard_labels()
            monkeypatch.setenv("ECHO_DISABLED_GUARDS", "guard-b")
            _disabled_guard_labels()
            monkeypatch.delenv("ECHO_DISABLED_GUARDS")
            _disabled_guard_labels()
        kill_switch_logs = [
            r for r in caplog.records if "ECHO_DISABLED_GUARDS changed" in r.message
        ]
        # 3 distinct states → 3 emissions.
        assert len(kill_switch_logs) == 3

    def test_no_log_when_starts_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Process boots with no kill-switch set — silent first call.
        monkeypatch.delenv("ECHO_DISABLED_GUARDS", raising=False)
        with caplog.at_level("WARNING", logger="runtime.core.cerebrum.react_loop"):
            for _ in range(3):
                _disabled_guard_labels()
        kill_switch_logs = [
            r for r in caplog.records if "ECHO_DISABLED_GUARDS changed" in r.message
        ]
        assert kill_switch_logs == []

    def test_message_includes_added_and_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("ECHO_DISABLED_GUARDS", "guard-a,guard-b")
        _disabled_guard_labels()  # Establish pre-state.
        caplog.clear()
        with caplog.at_level("WARNING", logger="runtime.core.cerebrum.react_loop"):
            monkeypatch.setenv("ECHO_DISABLED_GUARDS", "guard-b,guard-c")
            _disabled_guard_labels()
        kill_switch_logs = [
            r for r in caplog.records if "ECHO_DISABLED_GUARDS changed" in r.message
        ]
        assert len(kill_switch_logs) == 1
        msg = kill_switch_logs[0].message
        assert "guard-c" in msg  # added
        assert "guard-a" in msg  # removed
