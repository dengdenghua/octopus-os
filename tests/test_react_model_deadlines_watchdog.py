from runtime.core.cerebrum.react_model_deadlines import _reasoning_only_watchdog_s


def test_reasoning_only_watchdog_is_reserved_for_post_tool_rounds() -> None:
    assert _reasoning_only_watchdog_s(has_tool_evidence=False, recovery=False) is None
    assert _reasoning_only_watchdog_s(has_tool_evidence=True, recovery=False) == 600.0
    assert _reasoning_only_watchdog_s(has_tool_evidence=True, recovery=True) == 480.0

