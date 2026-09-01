"""Cache-stability invariant tests for the system prompt.

Two consecutive turns with the same shape (mode + workspace +
agent + project_rules) must produce a system prompt with
**identical bytes** so the LLM provider's prompt cache hits.

Anything per-turn (date, output_style, memory recall, camouflage
A/B variant) MUST live outside the system prompt — in a synthetic
prepended user message — or the cache prefix breaks every turn.
"""

from __future__ import annotations

import hashlib

from runtime.core.cerebrum.stable_prompt import (
    StablePromptBuilder,
    render_volatile_as_user_message,
)

# ── builder unit tests ────────────────────────────────────────


def test_empty_builder_returns_empty_strings() -> None:
    b = StablePromptBuilder()
    sys_text, vol_text = b.compose()
    assert sys_text == ""
    assert vol_text == ""


def test_only_stable_no_volatile() -> None:
    b = StablePromptBuilder()
    b.add_stable("base", "BASE PROMPT")
    b.add_stable("ws", "workspace block")
    sys_text, vol_text = b.compose()
    assert sys_text == "BASE PROMPT\n\nworkspace block"
    assert vol_text == ""


def test_stable_and_volatile_separated() -> None:
    b = StablePromptBuilder()
    b.add_stable("base", "STABLE")
    b.add_volatile("date", "today is 2026-05-31")
    sys_text, vol_text = b.compose()
    assert sys_text == "STABLE"
    assert "today is" in vol_text
    assert "today" not in sys_text


def test_empty_strings_skipped() -> None:
    b = StablePromptBuilder()
    b.add_stable("a", "")
    b.add_stable("b", "   ")
    b.add_stable("c", "real")
    b.add_volatile("d", "")
    sys_text, vol_text = b.compose()
    assert sys_text == "real"
    assert vol_text == ""


def test_insertion_order_preserved() -> None:
    b = StablePromptBuilder()
    b.add_stable("c", "third")
    b.add_stable("a", "first")
    b.add_stable("b", "second")
    sys_text, _ = b.compose()
    assert sys_text == "third\n\nfirst\n\nsecond"


def test_insert_stable_first() -> None:
    """SOUL injection prepends — keep that explicit."""
    b = StablePromptBuilder()
    b.add_stable("base", "BASE")
    b.insert_stable_first("soul", "SOUL")
    sys_text, _ = b.compose()
    assert sys_text == "SOUL\n\nBASE"


# ── byte-stability invariant ──────────────────────────────────


def test_same_inputs_same_hash() -> None:
    """Two builders with the same stable inputs in the same order
    must produce byte-equal output. This is THE cache invariant."""

    def make() -> StablePromptBuilder:
        b = StablePromptBuilder()
        b.add_stable("base", "REACT BASE")
        b.add_stable("workspace", "/home/x")
        b.add_stable("code-mode", "code mode block")
        return b

    h1 = make().compute_stable_hash()
    h2 = make().compute_stable_hash()
    assert h1 == h2


def test_volatile_differences_do_not_break_stable_hash() -> None:
    """Volatile additions don't shift the stable hash — that's the
    whole point of the split."""
    b1 = StablePromptBuilder()
    b1.add_stable("base", "REACT")
    b1.add_volatile("date", "2026-05-31")

    b2 = StablePromptBuilder()
    b2.add_stable("base", "REACT")
    b2.add_volatile("date", "2026-06-01")

    assert b1.compute_stable_hash() == b2.compute_stable_hash()
    # But volatile text obviously differs
    _, v1 = b1.compose()
    _, v2 = b2.compose()
    assert v1 != v2


def test_stable_addition_breaks_hash() -> None:
    """Adding a stable section must change the hash — verifies hash
    isn't trivially equal."""
    b1 = StablePromptBuilder()
    b1.add_stable("base", "REACT")

    b2 = StablePromptBuilder()
    b2.add_stable("base", "REACT")
    b2.add_stable("extra", "something new")

    assert b1.compute_stable_hash() != b2.compute_stable_hash()


def test_label_only_diff_does_not_change_hash() -> None:
    """Hash is over content, not labels — so renaming a label
    doesn't break cache."""
    b1 = StablePromptBuilder()
    b1.add_stable("foo", "content")

    b2 = StablePromptBuilder()
    b2.add_stable("bar", "content")

    assert b1.compute_stable_hash() == b2.compute_stable_hash()


# ── volatile wrapping ─────────────────────────────────────────


def test_volatile_wrapper_skips_empty() -> None:
    assert render_volatile_as_user_message("") == ""
    assert render_volatile_as_user_message("   ") == ""


def test_volatile_wrapper_marks_per_turn_status() -> None:
    out = render_volatile_as_user_message("today is X")
    assert "<turn-context>" in out
    assert "today is X" in out
    assert "per-turn signals" in out
    assert "</turn-context>" in out


# ── react_loop integration: assert no per-turn drift ─────────


def _system_text_from_run(thinking: bool = False, recall: str | None = None) -> str:
    """Helper: run react_loop with a minimal stack, return system msg content.

    We don't actually invoke the model; the _CapturingRouter in
    test_react_loop captures the request before send.
    """
    # Lightweight reuse of the helpers from test_react_loop.
    from tests.test_react_loop import (  # type: ignore[import-not-found]
        _CapturingRouter,
        _FakeStack,
        _intent,
        run_react_loop,
    )

    router = _CapturingRouter(["Final Answer: done"])
    intent = _intent("test goal")
    if thinking:
        from runtime.core.cerebrum.thinking_mode import build_thinking_plan

        intent.user_context["thinking_plan"] = build_thinking_plan(
            intent.normalized_goal,
            mode="react",
        ).to_dict()
    if recall is not None:
        intent.user_context["mock_recall"] = recall

    run_react_loop(_FakeStack(router), intent, agent=None)
    # System message is messages[0].
    sys_msg = router.requests[0].messages[0]
    assert sys_msg.role == "system"
    return sys_msg.content if isinstance(sys_msg.content, str) else ""


def test_system_prompt_unchanged_when_thinking_plan_added() -> None:
    """The thinking_plan is per-turn → must not appear in the
    cached system prompt. Two turns (with vs without) yield the
    same system bytes."""
    sys_no = _system_text_from_run(thinking=False)
    sys_yes = _system_text_from_run(thinking=True)
    assert (
        hashlib.sha256(sys_no.encode("utf-8")).hexdigest()
        == hashlib.sha256(sys_yes.encode("utf-8")).hexdigest()
    ), (
        "Adding a thinking_plan should NOT change the system prompt — "
        "it's a per-turn signal and should ride in a prepended user "
        "message instead. If this fails, the cache prefix is broken."
    )


def test_system_prompt_does_not_contain_current_date() -> None:
    """Current date is per-turn (changes daily) → MUST not appear
    in the system prompt or the cache breaks every midnight."""
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    sys_text = _system_text_from_run()
    assert today not in sys_text, (
        f"Current date ({today}) leaked into the system prompt. "
        "Move it to volatile (prepended user message) or the prompt "
        "cache prefix breaks every day at midnight."
    )
