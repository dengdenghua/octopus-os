import pytest

from runtime.execution.codex_backend.account import _normalize_rate_window
from runtime.execution.codex_backend.types import ProtocolError


@pytest.mark.parametrize("used", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_quota_is_rejected_instead_of_claiming_remaining_capacity(used):
    with pytest.raises(ProtocolError, match="finite"):
        _normalize_rate_window({"usedPercent": used, "windowDurationMins": 300, "resetsAt": 0})


def test_real_zero_usage_is_preserved():
    result = _normalize_rate_window({"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 0})
    assert result["remaining_percent"] == 100


def test_absent_window_stays_absent():
    assert _normalize_rate_window(None) is None
