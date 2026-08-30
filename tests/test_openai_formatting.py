"""
Unit tests for ``runtime/sensing/siphon/openai_formatting.py``.

These helpers used to live inside ``openai_gateway.py`` as private
functions · untested because they were hard to reach through the
gateway's fastapi surface. Extracting them let us pin their
behavior with pure-function tests · any refactor now has tight
regression coverage on shape and priority ordering.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from runtime.sensing.gateway.openai_formatting import (
    _pick_output_keys,
    _pick_preview_keys,
    _short,
    chat_completion_envelope,
    step_effective_success,
    summarize_step_for_stream,
)

# ═══════════════════════════════════════════════════════════
# _short
# ═══════════════════════════════════════════════════════════


class TestShort:
    def test_short_string_passthrough(self):
        assert _short("hi") == "hi"

    def test_long_string_truncated_with_ellipsis(self):
        # Single Unicode ellipsis occupies one char.
        out = _short("a" * 100, max_len=10)
        assert out.endswith("…")
        assert len(out) == 10

    def test_non_string_coerced(self):
        assert _short(42) == "42"
        assert _short({"k": 1}) == "{'k': 1}"

    def test_custom_max_len(self):
        out = _short("abcdef", max_len=4)
        assert len(out) == 4
        assert out.endswith("…")


# ═══════════════════════════════════════════════════════════
# _pick_preview_keys
# ═══════════════════════════════════════════════════════════


class TestPickPreviewKeys:
    def test_priority_keys_win(self):
        """`text` is in _ARGS_PRIORITY at position 0 · should come
        first even when declared last in the dict."""
        result = _pick_preview_keys({"junk": 1, "text": "hi"})
        assert result[0] == ("text", "hi")

    def test_fills_to_three(self):
        result = _pick_preview_keys(
            {
                "a": 1,
                "b": 2,
                "c": 3,
                "d": 4,
                "e": 5,
            }
        )
        assert len(result) == 3

    def test_respects_priority_order_among_multiple(self):
        result = _pick_preview_keys(
            {
                "url": "u",
                "text": "t",
                "path": "p",
            }
        )
        # Priority: text, path, url, ... → text first, path second, url third.
        keys = [k for k, _v in result]
        assert keys == ["text", "path", "url"]

    def test_empty_dict(self):
        assert _pick_preview_keys({}) == []

    def test_only_filler_no_priority(self):
        result = _pick_preview_keys({"foo": 1, "bar": 2})
        assert len(result) == 2
        assert {k for k, _v in result} == {"foo", "bar"}


# ═══════════════════════════════════════════════════════════
# _pick_output_keys
# ═══════════════════════════════════════════════════════════


class TestPickOutputKeys:
    def test_caps_at_two(self):
        result = _pick_output_keys(
            {
                "result": 1,
                "reply": 2,
                "answer": 3,
                "text": 4,
            }
        )
        assert len(result) == 2

    def test_skips_underscore_prefixed(self):
        """Underscore-prefixed keys are internal metadata · should
        never appear in the user-facing output summary."""
        result = _pick_output_keys({"_raw": "noise", "reply": "ok"})
        keys = [k for k, _v in result]
        assert "_raw" not in keys
        assert "reply" in keys

    def test_priority_over_non_priority(self):
        """`result` beats an unknown key even if the unknown one came
        first in the dict."""
        result = _pick_output_keys({"unknown": 1, "result": "r"})
        keys = [k for k, _v in result]
        assert keys[0] == "result"


# ═══════════════════════════════════════════════════════════
# summarize_step_for_stream
# ═══════════════════════════════════════════════════════════


def _mk_step(
    *,
    success: bool,
    sucker_id: str,
    args: dict | None = None,
    output: Any = None,
) -> Any:
    """Minimal Step stub · just enough for the formatter."""
    return SimpleNamespace(
        success=success,
        action=SimpleNamespace(sucker_id=sucker_id, args=args or {}),
        result=SimpleNamespace(output=output),
    )


class TestSummarizeStep:
    def test_success_marker_for_successful_step(self):
        step = _mk_step(success=True, sucker_id="list_cwd", args={"path": "."})
        out = summarize_step_for_stream(step)
        assert out.startswith("✓ list_cwd(")

    def test_failure_marker(self):
        step = _mk_step(success=False, sucker_id="list_cwd")
        assert summarize_step_for_stream(step).startswith("✗ list_cwd")

    def test_args_and_output_both_present(self):
        step = _mk_step(
            success=True,
            sucker_id="read_file",
            args={"path": "/a.txt"},
            output={"reply": "hello"},
        )
        out = summarize_step_for_stream(step)
        # Layout: "<marker> <skill>(<args>)  →  <output>"
        assert "read_file" in out
        assert "path=/a.txt" in out
        assert "reply=hello" in out
        assert " → " in out

    def test_args_only(self):
        step = _mk_step(
            success=True,
            sucker_id="list_cwd",
            args={"path": "."},
            output=None,
        )
        out = summarize_step_for_stream(step)
        assert "(" in out  # args block present
        assert " → " not in out  # no output arrow

    def test_output_only(self):
        step = _mk_step(
            success=True,
            sucker_id="count_words",
            args=None,
            output={"word_count": 42},
        )
        out = summarize_step_for_stream(step)
        assert "(" not in out.replace("()", "")  # no args parens
        assert "word_count=42" in out

    def test_neither_args_nor_output(self):
        step = _mk_step(success=True, sucker_id="ping")
        assert summarize_step_for_stream(step) == "✓ ping()"

    def test_string_output_uses_80_char_cap(self):
        long = "x" * 200
        step = _mk_step(
            success=True,
            sucker_id="tell",
            args=None,
            output=long,
        )
        out = summarize_step_for_stream(step)
        # The output portion is capped at 80 · marker + skill = small prefix.
        # Total line length bounded reasonably (< 120 chars, generous).
        assert len(out) < 120

    def test_error_payload_marks_step_as_failed(self):
        step = _mk_step(
            success=True,
            sucker_id="list_cwd",
            args={"path": "."},
            output={"error": "not found: ."},
        )
        out = summarize_step_for_stream(step)
        assert out.startswith("✗ list_cwd(")
        assert "error=not found: ." in out
        assert step_effective_success(step) is False


# ═══════════════════════════════════════════════════════════
# chat_completion_envelope
# ═══════════════════════════════════════════════════════════


class TestChatCompletionEnvelope:
    def test_required_openai_fields(self):
        env = chat_completion_envelope("hello", model="gpt-4")
        required = {
            "id",
            "object",
            "created",
            "model",
            "choices",
            "usage",
        }
        assert required.issubset(env.keys())
        assert env["object"] == "chat.completion"

    def test_choices_shape_matches_openai(self):
        env = chat_completion_envelope("hello", model="gpt-4")
        c0 = env["choices"][0]
        assert c0["index"] == 0
        assert c0["message"]["role"] == "assistant"
        assert c0["message"]["content"] == "hello"
        assert c0["finish_reason"] == "stop"

    def test_echo_meta_default_is_fallback(self):
        env = chat_completion_envelope("x", model="m")
        meta = env["echo"]
        assert meta["strategy"] == "direct_llm_fallback"
        assert meta["step_count"] == 0
        assert meta["success"] is True

    def test_agent_meta_populated_when_provided(self):
        agent = SimpleNamespace(agent_id="coder")
        env = chat_completion_envelope("x", model="m", agent=agent)
        assert env["echo"]["agent"] == "coder"

    def test_actor_meta_populated_when_provided(self):
        env = chat_completion_envelope("x", model="m", actor="u1")
        assert env["echo"]["actor"] == "u1"

    def test_extra_meta_merged(self):
        env = chat_completion_envelope(
            "x",
            model="m",
            extra={"custom_flag": True, "step_count": 99},
        )
        assert env["echo"]["custom_flag"] is True
        # extra can override defaults · by design, lets the caller
        # tag fallback with real info (e.g., planner_error string).
        assert env["echo"]["step_count"] == 99

    def test_id_is_prefixed(self):
        env = chat_completion_envelope("x", model="m")
        assert env["id"].startswith("chatcmpl-")

    def test_usage_fields_zeroed(self):
        """Fallback didn't count tokens · envelope reports zeros
        rather than None. OpenAI clients expect ints."""
        env = chat_completion_envelope("x", model="m")
        u = env["usage"]
        assert u["prompt_tokens"] == 0
        assert u["completion_tokens"] == 0
        assert u["total_tokens"] == 0
