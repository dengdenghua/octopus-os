"""
Direct-LLM budget_commit contract · 2026-04-24

Bug #2 (2026-04-24 browser regression): Cost tab showed 0 tokens even
after chat replies consumed LLM tokens. Root cause: direct-LLM /
reflex path doesn't go through ``ToolExecutor.commit()``, so
``budget_commit`` is never written to the journal, and the
``/api/budget/summary`` aggregator (which Cost tab reads) has nothing
to sum.

Fix: add ``_commit_direct_llm_cost`` helper that mints a
``budget_commit`` event after the direct-LLM / synthesis LLM call.
These tests pin the helper so future refactors can't silently break
the Cost tab again.
"""

from __future__ import annotations

from typing import Any

from runtime.memory.journal import InMemoryJournal
from runtime.sensing.gateway.openai_gateway import _commit_direct_llm_cost


class _FakeStack:
    def __init__(self) -> None:
        self.journal = InMemoryJournal()


class _FakeAgent:
    def __init__(self, agent_id: str = "test_agent") -> None:
        self.agent_id = agent_id


def _count_budget_commits(stack: _FakeStack) -> int:
    return len(list(stack.journal.read_by_type("budget_commit")))


# ─── 1. Happy path · tokens > 0 · emits event ─────────────────


def test_commit_writes_budget_event_when_tokens_nonzero() -> None:
    stack = _FakeStack()
    agent = _FakeAgent()
    _commit_direct_llm_cost(
        stack,
        {"input_tokens": 22, "output_tokens": 33},
        agent,
    )
    events = list(stack.journal.read_by_type("budget_commit"))
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "budget_commit"
    assert ev.cost.tokens_in == 22
    assert ev.cost.tokens_out == 33
    assert ev.actor == "arms/test_agent"
    assert ev.reason == "direct_llm_fallback"


# ─── 2. Skip zero · don't pollute journal with empty entries ──


def test_commit_skips_zero_tokens() -> None:
    """Reflex / cache-hit paths call with 0/0 tokens · we shouldn't
    pollute the Cost tab with meaningless 0-token rows."""
    stack = _FakeStack()
    _commit_direct_llm_cost(
        stack,
        {"input_tokens": 0, "output_tokens": 0},
        None,
    )
    assert _count_budget_commits(stack) == 0


def test_commit_skips_missing_usage() -> None:
    stack = _FakeStack()
    _commit_direct_llm_cost(stack, None, None)
    _commit_direct_llm_cost(stack, {}, None)
    assert _count_budget_commits(stack) == 0


# ─── 3. Best-effort · failure doesn't propagate ──────────────


def test_commit_swallows_journal_exceptions() -> None:
    """A broken journal must not break chat replies. Any exception
    from write_budget is caught and logged (not raised)."""

    class _BrokenJournal:
        def write_budget(self, *_a: Any, **_kw: Any) -> None:
            raise RuntimeError("disk on fire")

    class _BrokenStack:
        journal = _BrokenJournal()

    # Must not raise
    _commit_direct_llm_cost(
        _BrokenStack(),
        {"input_tokens": 10, "output_tokens": 20},
        None,
    )


def test_commit_handles_missing_stack_journal() -> None:
    class _EmptyStack:
        pass

    _commit_direct_llm_cost(
        _EmptyStack(),
        {"input_tokens": 10, "output_tokens": 20},
        None,
    )
    # Should not raise; nothing to assert · the absence of crash IS
    # the assertion.


# ─── 4. Default actor · no agent attached ────────────────────


def test_commit_uses_default_actor_when_no_agent() -> None:
    stack = _FakeStack()
    _commit_direct_llm_cost(
        stack,
        {"input_tokens": 5, "output_tokens": 5},
        None,
    )
    events = list(stack.journal.read_by_type("budget_commit"))
    assert events[0].actor == "arms/direct_llm"


# ─── 5. Custom reason · useful for path attribution ──────────


def test_commit_threads_custom_reason() -> None:
    """``reason`` lets Cost tab / audit logs distinguish direct_llm
    vs synthesize_reply vs streaming · all share the same helper
    but different paths pass different reasons."""
    stack = _FakeStack()
    _commit_direct_llm_cost(
        stack,
        {"input_tokens": 1, "output_tokens": 1},
        None,
        reason="synthesize_reply",
    )
    ev = next(iter(stack.journal.read_by_type("budget_commit")))
    assert ev.reason == "synthesize_reply"


# ─── 6. Integration · /api/budget/summary sees our commit ────


def test_budget_summary_sees_direct_llm_cost() -> None:
    """Write a few commits, then call api_budget_summary's same
    aggregation logic · confirm tokens flow end-to-end."""
    stack = _FakeStack()
    _commit_direct_llm_cost(
        stack,
        {"input_tokens": 100, "output_tokens": 200},
        None,
    )
    _commit_direct_llm_cost(
        stack,
        {"input_tokens": 50, "output_tokens": 75},
        None,
        reason="synthesize_reply",
    )

    # Replicate /api/budget/summary aggregation (cheap)
    total_tokens = 0
    for ev in stack.journal.read_by_type("budget_commit"):
        total_tokens += ev.cost.tokens_in + ev.cost.tokens_out
    assert total_tokens == 100 + 200 + 50 + 75
