"""prior_grounding_text extraction from conversation history.

The assembly step must carry tool observations from EARLIER turns of the
thread into the guard context, so research guards can tell "sourced in a
previous turn" apart from "fabricated now" (regression: thread
txhjBkLKtmrjdfdJp0FQhN falsely flagged turn-1 facts reused in the turn-2
report).
"""

from __future__ import annotations

from runtime.core.cerebrum._react_prompt_assembly_state import _extract_prior_observations


def test_extracts_only_observation_user_messages() -> None:
    history = [
        {"role": "user", "content": "你帮我找下这3个数据"},
        {"role": "assistant", "content": "我这就去查。"},
        {
            "role": "user",
            "content": 'Observation: {"ok": true, "results": [{"title": "X", "url": "https://a.com"}]}',
        },
        {
            "role": "user",
            "content": "Observation: Global Market Insights: 2025 智能床垫市场 17.6 亿美元",
        },
        {"role": "assistant", "content": "更新：已确认 17.6 亿美元。"},
    ]
    out = _extract_prior_observations(history)
    assert out.startswith("Observation:")
    assert "17.6 亿美元" in out
    assert "你帮我找下" not in out
    assert "已确认" not in out


def test_skips_non_string_and_non_observation() -> None:
    history = [
        {"role": "user", "content": 123},  # type: ignore[list-item]
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "Observation: only this one"},
        "not-a-dict",
    ]
    assert _extract_prior_observations(history) == "Observation: only this one"


def test_handles_empty_or_non_list() -> None:
    assert _extract_prior_observations(None) == ""
    assert _extract_prior_observations([]) == ""
    assert _extract_prior_observations("nope") == ""

