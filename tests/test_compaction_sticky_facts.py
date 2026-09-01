"""Tests for ``extract_sticky_facts`` in compaction.

Compaction summarises old turns into one paragraph. Sticky facts —
explicit user-stated constraints / prefs / decisions — should survive
that summarisation as a separate, never-collapsed bullet list.
"""

from __future__ import annotations

from runtime.memory.threads.compaction import extract_sticky_facts


def test_empty_input_returns_empty() -> None:
    assert extract_sticky_facts([]) == []


def test_constraint_phrase_extracted() -> None:
    msgs = [{"role": "user", "content": "用 React 框架, 必须 4 空格缩进"}]
    facts = extract_sticky_facts(msgs)
    assert len(facts) >= 1
    # Should be tagged with one of the labels and contain part of the
    # original phrase
    assert any("React" in f or "缩进" in f for f in facts)


def test_question_form_skipped() -> None:
    """A question is a request, not a fact — must not be extracted."""
    msgs = [{"role": "user", "content": "应该用 React 吗?"}]
    assert extract_sticky_facts(msgs) == []


def test_chinese_question_form_skipped() -> None:
    msgs = [{"role": "user", "content": "我们是否必须用 4 空格缩进?"}]
    assert extract_sticky_facts(msgs) == []


def test_too_short_message_skipped() -> None:
    msgs = [
        {"role": "user", "content": "ok"},
        {"role": "user", "content": "好"},
        {"role": "user", "content": "yes"},
    ]
    assert extract_sticky_facts(msgs) == []


def test_capped_at_max() -> None:
    msgs = [{"role": "user", "content": f"必须做事情第{i}项, 这是个长内容"} for i in range(50)]
    facts = extract_sticky_facts(msgs)
    # _STICKY_FACTS_MAX is 12 in the impl
    assert len(facts) <= 12


def test_assistant_messages_ignored() -> None:
    msgs = [
        {"role": "assistant", "content": "必须 用户必须 4 空格缩进"},
        {"role": "user", "content": "ok"},
    ]
    assert extract_sticky_facts(msgs) == []


def test_dedup() -> None:
    msgs = [
        {"role": "user", "content": "必须 用 4 空格缩进, 这是约束"},
        {"role": "user", "content": "必须 用 4 空格缩进, 这是约束"},
    ]
    facts = extract_sticky_facts(msgs)
    assert len(facts) == 1


def test_ban_pattern_extracted() -> None:
    msgs = [
        {"role": "user", "content": "不要 在 commit 里加 Co-Authored-By 这种 footer"},
    ]
    facts = extract_sticky_facts(msgs)
    assert len(facts) == 1
    assert "[ban]" in facts[0]


def test_preference_pattern_extracted() -> None:
    msgs = [
        {"role": "user", "content": "我喜欢 用 type hints, 总是写 explicit return type"},
    ]
    facts = extract_sticky_facts(msgs)
    assert len(facts) >= 1
