"""Tests for shell exec network policy resolution.

The sandbox default is network DENIED. ``exec_shell`` honours the turn's
explicit ``sandboxPolicy.networkAccess`` declaration — never a scope-level
``network_policy`` default (which is "allow" outside plan mode and would
otherwise flip the confined-shell default to allowed, escaping the
sandbox's network policy).
"""

from __future__ import annotations

import pytest

from runtime.execution.suckers._write_skills_exec import _resolved_allow_network


def test_explicit_value_wins() -> None:
    assert _resolved_allow_network(True) is True
    assert _resolved_allow_network(False) is False


def test_no_session_defaults_to_deny() -> None:
    assert _resolved_allow_network(None) is False


class _Session:
    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata


@pytest.fixture()
def _bind_session(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.platform.process.session as session_mod

    def _set(session):
        monkeypatch.setattr(session_mod, "current_session", lambda: session)

    return _set


def test_declared_network_access_true_enables_network(_bind_session) -> None:
    sess = _Session({"sandbox_policy": {"type": "dangerFullAccess", "networkAccess": True}})
    _bind_session(sess)
    assert _resolved_allow_network(None) is True


def test_declared_network_access_false_stays_denied(_bind_session) -> None:
    sess = _Session({"sandbox_policy": {"type": "workspaceWrite", "networkAccess": False}})
    _bind_session(sess)
    assert _resolved_allow_network(None) is False


def test_missing_sandbox_policy_defaults_to_deny(_bind_session) -> None:
    sess = _Session({})
    _bind_session(sess)
    assert _resolved_allow_network(None) is False


def test_scope_network_policy_allow_does_not_auto_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: scope.network_policy defaults to "allow" outside plan
    # mode. It must NOT be consulted — otherwise every non-plan turn would
    # silently get network access and escape the sandbox default.
    import runtime.platform.process.scope as scope_mod
    import runtime.platform.process.session as session_mod

    class _Scope:
        network_policy = "allow"

    class _SessionWithScope:
        metadata = {}

    monkeypatch.setattr(session_mod, "current_session", lambda: _SessionWithScope())
    monkeypatch.setattr(scope_mod, "resolve_execution_scope", lambda sess: _Scope())
    assert _resolved_allow_network(None) is False

