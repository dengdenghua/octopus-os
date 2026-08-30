"""Tests for runtime.core.cerebrum.token_juicer.

Each test isolates one compression pass and verifies (a) the pass
fires when expected, (b) it doesn't strip protected sentinels, and
(c) JuiceStats correctly accounts before/after sizes.
"""

from __future__ import annotations

import pytest

from runtime.core.cerebrum.token_juicer import (
    JuiceStats,
    is_enabled,
    juice,
)


def test_empty_input_passes_through() -> None:
    out, stats = juice("")
    assert out == ""
    assert stats == JuiceStats(0, 0, ())


def test_short_plain_text_unchanged() -> None:
    out, stats = juice("hello world")
    assert out == "hello world"
    assert stats.passes == ()
    assert stats.saved == 0


def test_html_pass_strips_tags_keeps_visible_text() -> None:
    raw = (
        "<html><body>"
        "<p>Hello <b>world</b></p>"
        "<script>evil()</script>"
        "<style>.x{color:red}</style>"
        "<p>second line</p>"
        "</body></html>"
    )
    out, stats = juice(
        raw, enable_url=False, enable_dedup=False, enable_array=False, enable_cap=False
    )
    assert "evil()" not in out, out
    assert "color:red" not in out, out
    assert "Hello world" in out, out
    assert "second line" in out, out
    assert "html" in stats.passes
    assert stats.after < stats.before


def test_url_shortening_collapses_long_urls() -> None:
    raw = "see https://example.com/very/long/path?a=" + "x" * 200 + " for details"
    out, stats = juice(
        raw, enable_html=False, enable_dedup=False, enable_array=False, enable_cap=False
    )
    assert "<example.com/" in out, out
    assert "x" * 200 not in out
    assert "for details" in out
    assert "url" in stats.passes


def test_url_shortening_leaves_short_urls_alone() -> None:
    raw = "go to https://example.com/page now"
    out, stats = juice(
        raw, enable_html=False, enable_dedup=False, enable_array=False, enable_cap=False
    )
    assert out == raw
    assert "url" not in stats.passes


def test_dedup_collapses_repeated_lines() -> None:
    raw = "starting\n" + "warning: x\n" * 8 + "done"
    out, stats = juice(
        raw, enable_html=False, enable_url=False, enable_array=False, enable_cap=False
    )
    assert "× 8 times" in out, out
    assert "starting" in out
    assert "done" in out
    assert "dedup" in stats.passes


def test_dedup_leaves_short_runs_alone() -> None:
    raw = "a\nb\nb\nb\nc"  # 3 b's — under threshold of 4
    out, stats = juice(
        raw, enable_html=False, enable_url=False, enable_array=False, enable_cap=False
    )
    assert out == raw
    assert "dedup" not in stats.passes


def test_prefix_dedup_preserves_heterogeneous_short_list() -> None:
    """A run of 12+ DISTINCT short lines (a todo list, test results, a
    grep-across-files inventory) must survive intact — every line is
    data the model may need to reason over, even when lines share a
    common prefix like `src/`. Regression for the removed prefix-run
    collapse, which guessed that prefix-sharing meant redundancy."""
    distinct = "\n".join(f"item-{i}: {chr(97 + i)}value" for i in range(14))
    out, _stats = juice(
        distinct,
        enable_html=False,
        enable_url=False,
        enable_array=False,
        enable_cap=False,
    )
    # No line dropped — all 14 distinct lines survive.
    for i in range(14):
        assert f"item-{i}:" in out, f"line item-{i} was dropped: {out!r}"
    assert "omitted" not in out


def test_grep_like_distinct_matches_preserved() -> None:
    """The exact shape the old pass targeted — many grep hits sharing
    `src/module/file_N.py:` — is now preserved, because each match is a
    distinct result, not redundant repetition."""
    grep_like = "\n".join(f"src/module/file_{i}.py: match found" for i in range(20))
    out, stats = juice(
        grep_like,
        enable_html=False,
        enable_url=False,
        enable_array=False,
        enable_cap=False,
    )
    for i in range(20):
        assert f"file_{i}.py:" in out, f"grep hit file_{i} dropped: {out!r}"
    assert "dedup" not in stats.passes


def test_dedup_still_collapses_exact_duplicate_lines() -> None:
    """Exact-duplicate runs (≥4 identical lines) are still collapsed —
    that's lossless and remains the dedup pass's job."""
    raw = "start\n" + "warning: deprecated\n" * 9 + "end"
    out, stats = juice(
        raw,
        enable_html=False,
        enable_url=False,
        enable_array=False,
        enable_cap=False,
    )
    assert "× 9 times" in out, out
    assert "start" in out and "end" in out
    assert "dedup" in stats.passes


def test_array_trim_collapses_long_lists() -> None:
    body = ", ".join(f'{{"id": {i}, "v": "x"}}' for i in range(30))
    raw = f"prefix [{body}] suffix"
    out, stats = juice(
        raw, enable_html=False, enable_url=False, enable_dedup=False, enable_cap=False
    )
    assert "more items omitted" in out
    assert "prefix" in out and "suffix" in out
    assert "array" in stats.passes


def test_hard_cap_keeps_head_and_tail() -> None:
    raw = "HEAD" + "x" * 10000 + "TAIL"
    out, stats = juice(
        raw,
        max_chars=500,
        enable_html=False,
        enable_url=False,
        enable_dedup=False,
        enable_array=False,
    )
    assert "HEAD" in out
    assert "TAIL" in out
    assert "已压缩中段" in out
    assert len(out) < len(raw)
    assert "cap" in stats.passes


def test_protected_sentinel_tool_failure_survives_cap() -> None:
    """If hard cap would eat the tail (containing `(工具失败)` sentinel)
    we revert to original. The model sees more tokens, but the loop's
    retry/error semantics stay correct."""
    raw = "HEAD" + "x" * 10000 + "(工具失败) status=timeout"
    out, _stats = juice(raw, max_chars=200)
    # Original returned because sentinel was in tail beyond cap.
    assert "(工具失败) status=timeout" in out


def test_protected_parallel_batch_header_survives() -> None:
    """[1/3 read_file] style headers must reach the model so it
    knows which observation belongs to which call."""
    raw = (
        "[1/3 read_file]\n"
        + ("a" * 50 + "\n") * 6
        + "[2/3 read_file]\n"
        + ("b" * 50 + "\n") * 6
        + "[3/3 read_file]\nresult"
    )
    out, _stats = juice(raw, max_chars=400)
    assert "[1/3 read_file]" in out
    assert "[3/3 read_file]" in out


def test_combined_passes_compose() -> None:
    """Realistic scrape output: HTML body + duplicate warning lines +
    a long URL. All three passes should fire and stats reflect both
    before and after."""
    raw = (
        "<html><body>"
        + "<p>Article title</p>"
        + "<p>Body paragraph.</p>"
        + "</body></html>\n"
        + "warning: deprecated\n" * 6
        + "see https://very.long.example.com/path/"
        + "y" * 200
    )
    out, stats = juice(raw)
    assert "Article title" in out
    assert "Body paragraph." in out
    assert "× 6 times" in out
    assert "<very.long.example.com/" in out
    assert set(stats.passes) >= {"html", "dedup", "url"}


def test_is_enabled_defaults_on_and_respects_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TokenJuice is ON by default after validation; operators can opt out
    with the explicit false spellings."""
    monkeypatch.delenv("ECHO_TOKEN_JUICE", raising=False)
    assert is_enabled() is True
    monkeypatch.setenv("ECHO_TOKEN_JUICE", "1")
    assert is_enabled() is True
    monkeypatch.setenv("ECHO_TOKEN_JUICE", "true")
    assert is_enabled() is True
    monkeypatch.setenv("ECHO_TOKEN_JUICE", "on")
    assert is_enabled() is True
    monkeypatch.setenv("ECHO_TOKEN_JUICE", "0")
    assert is_enabled() is False
    monkeypatch.setenv("ECHO_TOKEN_JUICE", "false")
    assert is_enabled() is False
    monkeypatch.setenv("ECHO_TOKEN_JUICE", "off")
    assert is_enabled() is False
    # Only explicit false spellings disable the validated default.
    monkeypatch.setenv("ECHO_TOKEN_JUICE", "maybe")
    assert is_enabled() is True


def test_cjk_text_preserved() -> None:
    """Never strip CJK / emoji grapheme-by-grapheme.
    Verify that compression on text containing only CJK doesn't mangle it."""
    raw = "今天天气真好" * 200
    out, _stats = juice(raw, max_chars=400)
    # Either kept whole (if under cap after passes) or head+tail
    # form — both must remain valid CJK without partial decoding.
    assert "今天天气真好" in out
    out.encode("utf-8")  # would raise on broken surrogate pair


def test_react_loop_compresses_observation_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring proof: when ECHO_TOKEN_JUICE=1, the observation that
    actually reaches the next LLM round through messages.append is
    the compressed version, not the raw one. Default-off path is
    covered by the existing 110 react_loop tests staying green."""
    from typing import Any

    from runtime.core.cerebrum.react_loop import stream_react_loop

    # Build a verbose HTML observation by giving the model an action
    # that returns one. We reuse the test harness's existing fakes.
    from tests.test_react_loop import (
        _build_stack_with_executor,
        _CapturingRouter,
        _intent,
    )

    monkeypatch.setenv("ECHO_TOKEN_JUICE", "1")

    # Two-iter scripted router: first iter runs an exec_shell that
    # the test fixture handles; second iter gives Final Answer. We
    # patch the executor to return a long HTML observation so juicer
    # has something to compress.
    class _HtmlExecutor:
        """Stand-in executor that returns an HTML-heavy observation."""

        def __init__(self) -> None:
            from runtime.execution.suckers import Skill, SkillRegistry
            from runtime.execution.tool_engine import ToolExecutor
            from runtime.safety.auth import TrustEngine

            reg = SkillRegistry()
            reg.register(
                Skill(
                    name="fetch_html",
                    description="Returns a verbose HTML page.",
                    trusted_source="builtin://fetch_html",
                    handler=lambda url="x": {
                        "url": url,
                        "html": (
                            "<html><body>"
                            + "<p>Useful sentence.</p>"
                            + "<script>tracking()</script>" * 30
                            + "</body></html>"
                        ),
                    },
                ),
                verify_tests=False,
            )
            self.real = ToolExecutor(
                registry=reg,
                immunity=TrustEngine(
                    trusted_sources=["builtin://*"],
                    unknown_policy="allow",
                ),
            )
            self.registry = self.real.registry
            self.journal = self.real.journal

        def execute_step(self, *args: Any, **kwargs: Any) -> Any:
            return self.real.execute_step(*args, **kwargs)

    router = _CapturingRouter(
        [
            'Thought: fetch the page\nAction: fetch_html({"url": "x"})\n',
            "Final Answer: 已完成",
        ]
    )
    stack = _build_stack_with_executor(router)
    stack.executor = _HtmlExecutor()

    intent = _intent("compress test")
    intent.user_context["auto_approve"] = True

    # Drain the loop. After Iter 1, the next router request will
    # contain the compressed observation in its messages.
    gen = stream_react_loop(stack, intent, agent=None, max_iterations=3)
    list(gen)  # exhaust

    # The router captured the message stream of the second call;
    # check the user message containing "Observation:".
    second_request_messages = router.requests[1].messages
    obs_messages = [
        m
        for m in second_request_messages
        if isinstance(m.content, str) and m.content.startswith("Observation:")
    ]
    assert obs_messages, "no Observation: message reached the second LLM call"
    obs_text = obs_messages[0].content
    # Compressed: <script> blocks are gone, but the useful sentence
    # survives.
    assert "tracking()" not in obs_text, "<script> body leaked into prompt — juicer didn't engage"
    assert "Useful sentence." in obs_text
