"""Session-reference projection — dsh byte-bounded current-surface snapshot."""

from __future__ import annotations

import json

import pytest

from runtime.execution.tool_engine.session_projection import (
    is_compact_checkpoint_source,
    project_session_conversation,
    retain_session_reference,
    stringify_tag_safe_json,
    truncate_with_notice,
)


def _user(text: str, *, source: dict | None = None, checkpoint: bool = False):
    return {
        "type": "user/message",
        "data": {
            "source": source
            or ({"kind": "user"} if not checkpoint else {"kind": "plugin", "plugin": "compact"}),
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant(text: str):
    return {
        "type": "assistant/message",
        "data": {"message": {"content": [{"type": "text", "text": text}]}},
    }


def _tool_result():
    return {
        "type": "tool/result",
        "data": {"content": [{"type": "text", "text": "tool noise"}], "tool": "x"},
    }


def _retain(events, *, max_bytes, session_id="s1", label="agent session"):
    return retain_session_reference(
        events,
        session_id=session_id,
        label=label,
        max_bytes=max_bytes,
    )


# ═══════════════════════════════════════════════════════════
# projection
# ═══════════════════════════════════════════════════════════


def test_project_keeps_user_assistant_skips_tools_and_injected() -> None:
    events = [
        _user("hi"),
        _assistant("hello"),
        _tool_result(),
        _user("injected", source={"kind": "plugin", "plugin": "web"}),
        _user(""),  # empty text dropped
        {
            "type": "assistant/message",
            "data": {"message": {"content": [{"type": "image", "source": {}}]}},
        },
    ]
    items = project_session_conversation(events)
    assert [i.role for i in items] == ["user", "assistant"]
    assert [i.text for i in items] == ["hi", "hello"]
    assert all(not i.checkpoint for i in items)


def test_project_keeps_compact_checkpoint_user() -> None:
    items = project_session_conversation([_user("checkpoint", checkpoint=True)])
    assert len(items) == 1
    assert items[0].checkpoint is True
    assert items[0].role == "user"


def test_is_compact_checkpoint_source() -> None:
    assert is_compact_checkpoint_source({"kind": "plugin", "plugin": "compact"}) is True
    assert is_compact_checkpoint_source({"kind": "plugin", "plugin": "web"}) is False
    assert is_compact_checkpoint_source({"kind": "user"}) is False
    assert is_compact_checkpoint_source(None) is False


# ═══════════════════════════════════════════════════════════
# retention
# ═══════════════════════════════════════════════════════════


def test_retain_within_budget_keeps_everything() -> None:
    events = [_user("a"), _assistant("b"), _user("c")]
    data, stats = _retain(events, max_bytes=100000)
    assert data.conversation == [
        {"role": "user", "text": "a"},
        {"role": "assistant", "text": "b"},
        {"role": "user", "text": "c"},
    ]
    assert stats.original_messages == 3
    assert stats.retained_messages == 3
    assert stats.omitted_messages == 0
    assert stats.omitted_bytes == 0
    assert stats.truncated is False
    assert stats.compacted is False


def test_retain_drops_oldest_non_checkpoint_keeps_newest() -> None:
    events: list = []
    for i in range(20):
        events.append(_user(f"ask {i}"))
        events.append(_assistant(f"ans {i}"))
    data, stats = _retain(events, max_bytes=600)
    assert stats.omitted_messages > 0
    assert stats.truncated is True
    # newest conversation item survives
    assert data.conversation[-1] == {"role": "assistant", "text": "ans 19"}
    assert len(data.conversation) == stats.retained_messages
    assert "ask 0" not in [c["text"] for c in data.conversation]


def test_retain_never_drops_checkpoint() -> None:
    events = [_user("checkpoint-summary", checkpoint=True)] + [
        e for i in range(20) for e in [_user(f"ask {i}"), _assistant(f"ans {i}")]
    ]
    data, stats = _retain(events, max_bytes=400)
    texts = [c["text"] for c in data.conversation]
    assert "checkpoint-summary" in texts


def test_retain_truncates_longest_message_with_notice() -> None:
    events = [_user("A" * 500)]
    data, stats = _retain(events, max_bytes=300)
    assert stats.omitted_bytes > 0
    assert stats.truncated is True
    serialized = stringify_tag_safe_json(data)
    assert len(serialized.encode("utf-8")) <= 300
    assert "UTF-8 bytes …" in serialized
    # a single message is never dropped whole — it is head/tail-truncated.
    assert stats.omitted_messages == 0
    assert stats.retained_messages == 1


def test_retain_none_when_fixed_fields_cannot_fit() -> None:
    events = [_user("hi"), _assistant("yo")]
    assert _retain(events, max_bytes=40) is None


def test_retain_validates_max_bytes() -> None:
    with pytest.raises(ValueError):
        _retain([_user("hi")], max_bytes=-1)


def test_retain_utf8_boundary_no_replacement_char() -> None:
    events = [_assistant("🧪" * 200)]
    data, stats = _retain(events, max_bytes=200)
    assert stats.omitted_bytes > 0
    serialized = stringify_tag_safe_json(data)
    assert "\ufffd" not in serialized
    assert len(serialized.encode("utf-8")) <= 200


# ═══════════════════════════════════════════════════════════
# serialization + truncation
# ═══════════════════════════════════════════════════════════


def test_stringify_tag_safe_json_escapes_lt() -> None:
    value = {"conversation": [{"role": "user", "text": "a <b> and <tag>"}]}
    out = stringify_tag_safe_json(value)
    assert "<" not in out
    assert json.loads(out) == value


def test_truncate_with_notice_within_budget() -> None:
    result = truncate_with_notice("short", 100)
    assert result.text == "short"
    assert result.omitted_bytes == 0


def test_truncate_with_notice_fits_budget() -> None:
    result = truncate_with_notice("x" * 500, 120)
    assert len(result.text.encode("utf-8")) <= 120
    assert result.omitted_bytes == 500 - result.text.count("x")
    assert "UTF-8 bytes …" in result.text


def test_truncate_with_notice_preserves_utf8() -> None:
    result = truncate_with_notice("🧪" * 100, 100)
    assert "\ufffd" not in result.text
    assert len(result.text.encode("utf-8")) <= 100

