"""Tests for runtime/memory/users/distill.py — the bucket summary writer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from runtime.memory.users import user_store
from runtime.memory.users.distill import distill_user_memory

NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    return data_dir


def _seed_facts(facts: list[dict]) -> None:
    memory = user_store.empty_memory()
    memory["facts"] = facts
    user_store.write_memory(memory)


def _fact(content: str, *, days_old: int = 1, category: str = "profile") -> dict:
    return {
        "id": content[:8],
        "content": content,
        "category": category,
        "confidence": 0.9,
        "createdAt": (NOW - timedelta(days=days_old)).isoformat(),
        "source": "chat",
        "scope": "global",
        "agent_id": "",
        "project": "",
    }


class _MockRouter:
    default_model = "mock/model"

    def __init__(self, text: str = "LLM 摘要") -> None:
        self.text = text
        self.calls = 0

    def call(self, req):  # noqa: ANN001 — mock
        self.calls += 1

        class _Resp:
            text = ""

        resp = _Resp()
        resp.text = self.text
        return resp


class _ExplodingRouter:
    default_model = "mock/model"

    def call(self, req):  # noqa: ANN001 — mock
        raise ConnectionError("llm down")


def test_heuristic_fills_work_and_personal_buckets(memory_home: Path) -> None:
    _seed_facts(
        [
            _fact("用户负责部署 pipeline 的代码审查", category="work"),
            _fact("用户喜欢简洁的回复风格", category="preference"),
        ]
    )

    result = distill_user_memory(now=NOW)

    assert result["ok"] is True
    memory = json.loads((memory_home / "user_memory.json").read_text(encoding="utf-8"))
    assert "部署" in memory["user"]["workContext"]["summary"]
    assert "简洁" in memory["user"]["personalContext"]["summary"]
    assert result["used_llm"] is False


def test_top_of_mind_uses_most_recent_facts(memory_home: Path) -> None:
    _seed_facts([_fact(f"事实 {i}", days_old=10 - i) for i in range(8)])

    distill_user_memory(now=NOW)

    memory = json.loads((memory_home / "user_memory.json").read_text(encoding="utf-8"))
    top = memory["user"]["topOfMind"]["summary"]
    assert "事实 7" in top
    assert "事实 0" not in top  # older than the 5-fact window


def test_history_buckets_split_by_age(memory_home: Path) -> None:
    _seed_facts(
        [
            _fact("最近的事", days_old=10),
            _fact("半年前的事", days_old=200),
            _fact("很久以前的事", days_old=800),
        ]
    )

    distill_user_memory(now=NOW)

    memory = json.loads((memory_home / "user_memory.json").read_text(encoding="utf-8"))
    history = memory["history"]
    assert "最近的事" in history["recentMonths"]["summary"]
    assert "半年前的事" in history["earlierContext"]["summary"]
    assert "很久以前的事" in history["longTermBackground"]["summary"]


def test_llm_path_compresses_buckets(memory_home: Path) -> None:
    _seed_facts([_fact("用户负责部署 pipeline", category="work")])
    router = _MockRouter("用户做部署相关工作")

    result = distill_user_memory(router, now=NOW)

    memory = json.loads((memory_home / "user_memory.json").read_text(encoding="utf-8"))
    assert memory["user"]["workContext"]["summary"] == "用户做部署相关工作"
    assert result["used_llm"] is True
    assert router.calls >= 1


def test_llm_failure_falls_back_to_heuristic(memory_home: Path) -> None:
    _seed_facts([_fact("用户负责部署 pipeline", category="work")])

    result = distill_user_memory(_ExplodingRouter(), now=NOW)

    memory = json.loads((memory_home / "user_memory.json").read_text(encoding="utf-8"))
    assert "部署" in memory["user"]["workContext"]["summary"]
    assert result["used_llm"] is False


def test_no_facts_writes_nothing_and_stays_ok(memory_home: Path) -> None:
    result = distill_user_memory(now=NOW)

    assert result["ok"] is True
    assert result["buckets_written"] == 0
    assert not (memory_home / "user_memory.json").exists()


def test_unclassifiable_facts_still_feed_top_of_mind(memory_home: Path) -> None:
    _seed_facts([_fact("xyzzy 无关键词内容", category="context")])

    distill_user_memory(now=NOW)

    memory = json.loads((memory_home / "user_memory.json").read_text(encoding="utf-8"))
    assert "xyzzy" in memory["user"]["topOfMind"]["summary"]
    assert memory["user"]["workContext"]["summary"] == ""

