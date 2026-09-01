"""Sub-agent dispatch should honor ``extra_denied_paths`` so the
parent can tighten what its researcher / critic / explorer is
allowed to read for that one sub-call.

This is the multi-agent permission separation contract: even when
the user's global denylist allows a path, the parent can pin
additional restrictions just for THIS sub-agent's run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runtime.execution.subagents import bridge
from runtime.safety.auth import path_denylist as pdn
from runtime.safety.auth.path_guard import check_path


@pytest.fixture(autouse=True)
def _clear_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ECHO_PATH_DENYLIST_PATH", str(tmp_path / "dl.json"))
    pdn._TURN_DENYLIST.set(())
    yield
    pdn._TURN_DENYLIST.set(())


def _restore_runner(orig):
    bridge._RUNNER = orig


def test_extra_denied_paths_apply_during_subagent_run(tmp_path: Path) -> None:
    """A path passed via ``extra_denied_paths`` is blocked from
    inside the sub-agent's runner — but unblocked again after the
    call returns. This is the per-turn permission scoping contract."""
    secret = tmp_path / "secrets" / "config.env"
    secret.parent.mkdir()
    secret.write_text("API_KEY=x")

    captured: dict[str, object] = {}

    def _runner(prompt, *, subagent_name, context):
        # Inside the sub-agent's run: secret should be blocked by the
        # ad-hoc denylist the parent just pushed.
        captured["inside"] = check_path(str(secret)).allow
        return "done"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        # Before: parent can read it (no denylist applies).
        assert check_path(str(secret)).allow is True

        bridge.call_subagent(
            agent_id="custom_explorer",
            role="researcher",
            prompt="explore",
            extra_denied_paths=[str(tmp_path / "secrets")],
        )

        # During: blocked.
        assert captured["inside"] is False

        # After: parent can read it again — restriction was scoped to
        # the sub-agent run via push/pop.
        assert check_path(str(secret)).allow is True
    finally:
        _restore_runner(orig)


def test_no_extra_paths_means_no_extra_restrictions(tmp_path: Path) -> None:
    """Default behaviour: when ``extra_denied_paths`` is omitted,
    the sub-agent inherits the parent's full read access (subject
    to the global denylist + sandbox)."""
    secret = tmp_path / "data" / "ok.txt"
    secret.parent.mkdir()
    secret.write_text("ok")

    captured: dict[str, object] = {}

    def _runner(prompt, *, subagent_name, context):
        captured["inside"] = check_path(str(secret)).allow
        return "ok"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        bridge.call_subagent(
            agent_id="custom_explorer",
            role="researcher",
            prompt="explore",
        )
        assert captured["inside"] is True
    finally:
        _restore_runner(orig)


def test_extra_denied_paths_restored_on_runner_exception(tmp_path: Path) -> None:
    """Even when the runner raises, the denylist must pop back so a
    parent isn't left with stale restrictions in its own turn."""
    secret = tmp_path / "x" / "y.txt"
    secret.parent.mkdir()
    secret.write_text("y")

    def _runner(prompt, *, subagent_name, context):
        raise RuntimeError("boom")

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        bridge.call_subagent(
            agent_id="custom_x",
            role="researcher",
            prompt="x",
            extra_denied_paths=[str(tmp_path / "x")],
        )
        # Parent's view is unrestricted again.
        assert check_path(str(secret)).allow is True
    finally:
        _restore_runner(orig)
