from runtime.core.cerebrum.react_guards import _answer_item_count_guard


def test_rejects_one_paragraph_when_three_points_were_requested() -> None:
    message = _answer_item_count_guard(
        "只读分析六个文件，最后给出三点结论。",
        "结论清晰：协议、适配和活性模块职责分明。",
    )

    assert message is not None
    assert "requested 3 distinct points" in message


def test_accepts_exact_numbered_shape_in_chinese() -> None:
    assert (
        _answer_item_count_guard(
            "请用三点回答。",
            "1. 协议稳定。\n2. 状态归并清晰。\n3. 流式健康可观测。",
        )
        is None
    )


def test_accepts_english_bullets_for_requested_findings() -> None:
    assert (
        _answer_item_count_guard(
            "Provide 3 findings from the source files.",
            "- Protocol is typed.\n- State is immutable.\n- Streaming is observable.",
        )
        is None
    )


def test_does_not_force_a_list_without_an_explicit_count() -> None:
    assert _answer_item_count_guard("概括这些文件。", "职责边界清晰。") is None

