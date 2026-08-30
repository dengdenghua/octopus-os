"""Tests for the cross-thread history skills (history_search / history_read).

Uses a real in-memory ThreadStateStore (no path / no per_agent_base) injected
via ``set_default_thread_store``, so nothing touches disk.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from runtime.execution.suckers import history_skill as hs
from runtime.memory.threads.store import ThreadStateStore


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=None).isoformat() + "Z"


NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=30)
RECENT = NOW - timedelta(days=2)


@pytest.fixture
def store(monkeypatch):
    """In-memory store with two threads at different points in time."""
    st = ThreadStateStore(index_enabled=False)

    old = st.create(
        values={
            "title": "Redis cache tuning",
            "messages": [
                {"role": "user", "content": "how do I tune the redis eviction policy"},
                {"role": "assistant", "content": "use allkeys-lru for a pure cache"},
            ],
        },
    )
    new = st.create(
        values={
            "title": "Postgres index plan",
            "messages": [
                {"role": "user", "content": "why is this query doing a seq scan"},
                {"role": "assistant", "content": "the planner ignores the index here"},
            ],
        },
    )
    # Backdate directly: create() always stamps "now".
    st._threads[old["thread_id"]]["created_at"] = _iso(OLD)
    st._threads[old["thread_id"]]["updated_at"] = _iso(OLD)
    st._threads[new["thread_id"]]["created_at"] = _iso(RECENT)
    st._threads[new["thread_id"]]["updated_at"] = _iso(RECENT)

    monkeypatch.setattr(hs, "_DEFAULT_STORE", st)
    monkeypatch.setattr(hs, "_current_thread_id", lambda: None)
    st.old_id = old["thread_id"]  # type: ignore[attr-defined]
    st.new_id = new["thread_id"]  # type: ignore[attr-defined]
    return st


# ── search: keyword ──────────────────────────────────────────


def test_search_by_keyword_matches_message_body(store) -> None:
    res = hs._history_search(query="eviction policy")
    assert res["count"] == 1
    assert res["threads"][0]["thread_id"] == store.old_id
    assert "eviction" in res["threads"][0]["snippet"]


def test_search_by_keyword_matches_title(store) -> None:
    res = hs._history_search(query="postgres")
    assert res["count"] == 1
    assert res["threads"][0]["title"] == "Postgres index plan"


def test_search_no_match_returns_empty(store) -> None:
    res = hs._history_search(query="kubernetes")
    assert res["count"] == 0
    assert res["threads"] == []
    assert "widen" in res["next_action"]


def test_search_without_query_lists_all(store) -> None:
    res = hs._history_search()
    assert res["count"] == 2


# ── search: date range ───────────────────────────────────────


def test_days_back_excludes_old_thread(store) -> None:
    res = hs._history_search(days_back=7)
    assert res["count"] == 1
    assert res["threads"][0]["thread_id"] == store.new_id


def test_end_date_excludes_recent_thread(store) -> None:
    cutoff = (NOW - timedelta(days=10)).strftime("%Y-%m-%d")
    res = hs._history_search(end_date=cutoff)
    assert res["count"] == 1
    assert res["threads"][0]["thread_id"] == store.old_id


def test_explicit_window_selects_one(store) -> None:
    res = hs._history_search(
        start_date=(NOW - timedelta(days=4)).strftime("%Y-%m-%d"),
        end_date=NOW.strftime("%Y-%m-%d"),
    )
    assert res["count"] == 1
    assert res["threads"][0]["thread_id"] == store.new_id


def test_end_date_is_inclusive_of_whole_day(store) -> None:
    """A thread updated at 14:00 must match end_date == that same day."""
    same_day = RECENT.strftime("%Y-%m-%d")
    res = hs._history_search(start_date=same_day, end_date=same_day)
    assert res["count"] == 1
    assert res["threads"][0]["thread_id"] == store.new_id


def test_bad_date_returns_error_not_raise(store) -> None:
    res = hs._history_search(start_date="last tuesday")
    assert res["count"] == 0
    assert "unparseable start_date" in res["error"]


def test_inverted_window_returns_error(store) -> None:
    res = hs._history_search(start_date="2026-08-04", end_date="2026-08-01")
    assert res["count"] == 0
    assert "after end_date" in res["error"]


# ── search: current-thread exclusion ─────────────────────────


def test_current_thread_excluded_by_default(store, monkeypatch) -> None:
    monkeypatch.setattr(hs, "_current_thread_id", lambda: store.new_id)
    res = hs._history_search()
    assert [t["thread_id"] for t in res["threads"]] == [store.old_id]


def test_include_current_overrides_exclusion(store, monkeypatch) -> None:
    monkeypatch.setattr(hs, "_current_thread_id", lambda: store.new_id)
    res = hs._history_search(include_current=True)
    assert res["count"] == 2


# ── read ─────────────────────────────────────────────────────


def test_read_returns_messages(store) -> None:
    res = hs._history_read(thread_id=store.old_id)
    assert res["title"] == "Redis cache tuning"
    assert res["count"] == 2
    assert res["messages"][0]["role"] == "user"
    assert "eviction" in res["messages"][0]["content"]


def test_read_missing_id_returns_error(store) -> None:
    res = hs._history_read()
    assert res["count"] == 0
    assert "required" in res["error"]


def test_read_unknown_id_returns_error(store) -> None:
    res = hs._history_read(thread_id="nope")
    assert res["count"] == 0
    assert "not found" in res["error"]


def test_read_truncates_long_content(store) -> None:
    long_thread = store.create(
        values={"title": "Long", "messages": [{"role": "user", "content": "x" * 5000}]}
    )
    res = hs._history_read(thread_id=long_thread["thread_id"])
    assert res["messages"][0]["truncated"] is True
    assert len(res["messages"][0]["content"]) == hs._PREVIEW_CHARS


# ── no-store degradation ─────────────────────────────────────


def test_no_store_returns_error_not_raise(monkeypatch) -> None:
    monkeypatch.setattr(hs, "_DEFAULT_STORE", None)
    monkeypatch.setattr(hs, "_FALLBACK_STORE", None)
    monkeypatch.setattr(hs, "_resolve_store", lambda: None)
    assert hs._history_search(query="x")["error"] == "no thread store available"
    assert hs._history_read(thread_id="x")["error"] == "no thread store available"


# ── registration ─────────────────────────────────────────────


def test_register_history_skill_adds_two() -> None:
    from runtime.execution.suckers.registry import SkillRegistry

    reg = SkillRegistry()
    assert hs.register_history_skill(reg) == 2
    assert {"history_search", "history_read"} <= set(reg.all_names())
    assert reg.get("history_search").handler is hs._history_search
    assert reg.get("history_read").handler is hs._history_read


def test_memory_skills_bundle_includes_history() -> None:
    """register_memory_skills must wire the history skills in and count them."""
    from runtime.execution.suckers.memory_skills import register_memory_skills
    from runtime.execution.suckers.registry import SkillRegistry

    reg = SkillRegistry()
    count = register_memory_skills(reg)
    names = set(reg.all_names())
    assert {"history_search", "history_read"} <= names
    assert {"remember", "recall"} <= names  # existing skills still registered
    assert count == 14  # 12 core memory skills + 2 history skills

