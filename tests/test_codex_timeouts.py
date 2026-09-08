"""Realtime and background execution share timeout parsing and bounds."""
import pytest

from runtime.execution.codex_backend.role_runner import _timeout_s
from runtime.execution.codex_backend.timeouts import execution_timeout_s
from runtime.sensing.gateway.realtime_codex_backend import _turn_timeout_s


@pytest.mark.parametrize("raw, expected", [
    ("", 1800), ("45", 45), ("2", 30), ("999999", 14400),
    ("bad", 1800), ("nan", 1800), ("inf", 1800), ("-inf", 1800),
])
def test_entry_points_share_environment_policy(monkeypatch, raw, expected):
    monkeypatch.setenv("ECHO_CODEX_APP_SERVER_TIMEOUT", raw)
    assert _turn_timeout_s() == expected
    assert _timeout_s({}) == expected


def test_background_override_keeps_precedence(monkeypatch):
    monkeypatch.setenv("ECHO_CODEX_APP_SERVER_TIMEOUT", "120")
    assert _timeout_s({"timeout_s": 60}) == 60
    assert _turn_timeout_s() == 120
    assert execution_timeout_s(object()) == 1800
