"""Tests for keyword → topology auto-dispatch.

As of 2026-05-31 auto-dispatch is **disabled by default**. The single-
agent ReAct loop and the swarm path are kept as separate operating
modes — users opt into swarm by setting ``topology_id`` explicitly
(UI selector, ``deep-research-swarm`` skill, or API param).

Operators who want the old keyword-classifier behaviour can re-enable
it by setting ``user_ctx["enable_auto_topology"] = True`` on the turn
context. These tests cover both the off-by-default contract and the
opt-in path.
"""

from __future__ import annotations

from runtime.protocol.items import TurnParams
from runtime.sensing.gateway.realtime_cerebrum import _should_default_topology


def _make_params(*, metadata: dict | None = None, **overrides) -> TurnParams:
    """Build a TurnParams suitable for dispatch tests."""
    base: dict = {"thread_id": "t-test"}
    if metadata is not None:
        base["input"] = [{"type": "text", "text": "x", "metadata": metadata}]
    else:
        base["input"] = []
    base.update(overrides)
    return TurnParams(**base)


# ── default: never auto-dispatch ─────────────────────────────


def test_default_does_not_dispatch_even_on_strong_keyword() -> None:
    """Without explicit opt-in, even an unmistakable swarm keyword
    stays single-agent. This keeps the user's chosen mode (single
    agent) intact and avoids silent capability mismatches with
    upstream models that don't support native ``tools``."""
    p = _make_params()
    assert _should_default_topology("做一个激光雕刻的调研报告", p) is None
    assert _should_default_topology("Run a deep research on X", p) is None
    assert _should_default_topology("帮我代码评审这个 PR", p) is None
    assert _should_default_topology("debug this stack trace please", p) is None


def test_explicit_topology_id_passes_through_unchanged() -> None:
    """Explicit topology beats everything — that's how users opt
    into swarm now."""
    p = _make_params(topology_id="custom_team_v3")
    assert _should_default_topology("做调研", p) is None  # caller already set, no further dispatch


def test_chat_mode_disables_dispatch() -> None:
    p = _make_params(metadata={"context": {"mode": "chat"}})
    assert _should_default_topology("做调研报告", p) is None


def test_disable_auto_topology_flag_locks_off() -> None:
    """Operators who built tooling on top of the old behaviour and
    want it locked off forever can set this flag to make the answer
    None even if a future opt-in slipped in."""
    p = _make_params(
        metadata={
            "context": {"disable_auto_topology": True, "enable_auto_topology": True},
        }
    )
    assert _should_default_topology("做调研报告", p) is None


def test_disable_via_inner_metadata() -> None:
    p = _make_params(
        metadata={
            "context": {"metadata": {"disable_auto_topology": True}},
        }
    )
    assert _should_default_topology("做调研报告", p) is None


def test_unrelated_message_stays_none() -> None:
    p = _make_params()
    assert _should_default_topology("你好", p) is None
    assert _should_default_topology("帮我写个 hello world", p) is None
    assert _should_default_topology("今天天气怎么样", p) is None


def test_empty_message() -> None:
    p = _make_params()
    assert _should_default_topology("", p) is None
    assert _should_default_topology("   ", p) is None
    assert _should_default_topology(None, p) is None  # type: ignore[arg-type]


# ── opt-in path: enable_auto_topology re-enables keyword classifier ─


def test_opt_in_enables_research_keyword() -> None:
    p = _make_params(metadata={"context": {"enable_auto_topology": True}})
    assert _should_default_topology("做一个激光雕刻的调研报告", p) == "research_swarm_v1"


def test_opt_in_via_inner_metadata() -> None:
    p = _make_params(
        metadata={
            "context": {"metadata": {"enable_auto_topology": True}},
        }
    )
    assert _should_default_topology("Run a deep research on X", p) == "research_swarm_v1"


def test_opt_in_enables_code_review_keyword() -> None:
    p = _make_params(metadata={"context": {"enable_auto_topology": True}})
    assert _should_default_topology("帮我代码评审这个 PR", p) == "code_review_team_v1"


def test_opt_in_enables_debug_keyword() -> None:
    p = _make_params(metadata={"context": {"enable_auto_topology": True}})
    assert _should_default_topology("debug this stack trace please", p) == "debug_team_v1"


def test_opt_in_enables_refactor_keyword() -> None:
    p = _make_params(metadata={"context": {"enable_auto_topology": True}})
    assert _should_default_topology("refactor this module to be smaller", p) == "refactor_pair_v1"


def test_opt_in_review_pr_specificity_beats_refactor() -> None:
    """'review the refactor PR' mentions both — code-review wins
    even under opt-in mode (priority order is preserved)."""
    p = _make_params(metadata={"context": {"enable_auto_topology": True}})
    assert (
        _should_default_topology(
            "code review the refactor PR I just opened",
            p,
        )
        == "code_review_team_v1"
    )


def test_opt_in_unrelated_message_still_none() -> None:
    """Even with opt-in, messages that don't match any keyword
    pattern fall back to single-agent."""
    p = _make_params(metadata={"context": {"enable_auto_topology": True}})
    assert _should_default_topology("你好", p) is None


def test_opt_in_chat_mode_still_disables() -> None:
    """Chat mode short-circuits regardless of the auto-topology flag."""
    p = _make_params(metadata={"context": {"mode": "chat", "enable_auto_topology": True}})
    assert _should_default_topology("做调研报告", p) is None
