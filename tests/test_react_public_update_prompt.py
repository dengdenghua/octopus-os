from __future__ import annotations

from runtime.core.cerebrum.react_types import (
    REACT_OBSERVATION_FOLLOWUP,
    REACT_SYSTEM_PROMPT_BASE,
)


def test_public_update_is_required_after_observation_before_more_tools() -> None:
    assert "后续继续调用工具时也必填" in REACT_SYSTEM_PROMPT_BASE
    assert "收到 Observation 后若还要 Action" in REACT_SYSTEM_PROMPT_BASE
    assert "必须先输出一条 Update:" in REACT_OBSERVATION_FOLLOWUP


def test_observation_followup_allows_direct_final_and_rejects_empty_status() -> None:
    assert "证据已经足够，直接输出 Final Answer" in REACT_OBSERVATION_FOLLOWUP
    assert "不要写空状态" in REACT_OBSERVATION_FOLLOWUP
    assert "不要复述工具名、参数或内部协议" in REACT_OBSERVATION_FOLLOWUP


def test_generic_clarification_is_not_public_progress() -> None:
    """A pure clarification request ("请说明您需要我处理的具体内容") is an
    answer-lane message, not an Update: progress checkpoint. Surfacing it as
    the first visible commentary is the "先泛化一句、让人感觉敷衍" experience
    (thread txhjBkLKtmrjdfdJp0FQhN), so the checkpoint channel drops it."""
    from runtime.core.cerebrum.react_public_updates import _safe_public_update

    assert (
        _safe_public_update("Update: 请说明您需要我处理的具体内容，我将据此进行核对或调整。") == ""
    )
    assert _safe_public_update("请提供更多信息。") == ""
    assert _safe_public_update("请详细描述您的需求。") == ""
    assert _safe_public_update("您需要我处理什么内容？") == ""
    assert _safe_public_update("我需要您告诉我具体想让我做什么。") == ""


def test_concrete_update_still_streams() -> None:
    from runtime.core.cerebrum.react_public_updates import _safe_public_update

    concrete = (
        "我会分三路并行查证：智能床垫全球市场规模、传统床/床品全球市场规模、"
        "温度影响睡眠的科学依据，然后汇总成带来源的数据清单。"
    )
    assert _safe_public_update(concrete) == concrete
    assert _safe_public_update("已定位到证据源，下一步核对内容。") != ""
    # A real plan that merely ends in a conditional ask still streams.
    assert _safe_public_update("先核对配置，如果缺数据请告诉我。") != ""
    # A directive that asks the model-side action (not clarification) stays.
    assert _safe_public_update("请查看附件中的配置。") != ""

