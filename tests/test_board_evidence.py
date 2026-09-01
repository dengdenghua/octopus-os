"""Tests for the blackboard→event-log evidence bridge (P0-2 深化).

Covers:
  1. ``build_board_evidence_summary`` 纯函数：空 / 单轮 / 多轮 / 字符预算截断
  2. ``save_turn_blackboard`` + ``list_board_evidence`` 往返（Blackboard 实例 / dict）
  3. no-op 边界：无 turn_id / 空黑板 / 超预算截键
  4. 值长度截断（黑板值会进 LLM 上下文）
  5. 跨轮复用闭环：第一轮并行（黑板证据经 ``run_auto_parallel`` 落盘）→
     断线后 ``load_board_evidence`` 重建 → 合并进 ``build_thread_memory_summary``
     → 下一轮拆解的每个子任务 description 携带上一轮证据
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from runtime.core.cerebrum.agent_auto_parallel import (
    plan_auto_parallel,
    run_auto_parallel,
)
from runtime.memory.runtime_state.blackboard import Blackboard, reset_for_tests
from runtime.memory.threads.board_evidence import (
    build_board_evidence_summary,
    list_board_evidence,
    load_board_evidence,
    save_turn_blackboard,
)


@pytest.fixture(autouse=True)
def _clean_boards() -> None:
    reset_for_tests()
    yield
    reset_for_tests()


# ── 纯函数投影 ──────────────────────────────────────────────────────────────


def test_build_summary_empty() -> None:
    assert build_board_evidence_summary([]) == ""


def test_build_summary_single_turn() -> None:
    records = [{"turn_id": "t1", "ts": "x", "keys": {"A": "1", "B": "hello"}}]
    text = build_board_evidence_summary(records)
    assert text.startswith("<board-evidence>")
    assert "turn t1: A = 1" in text
    assert "turn t1: B = hello" in text
    assert text.endswith("</board-evidence>")


def test_build_summary_respects_limit_and_chars() -> None:
    records = [
        {"turn_id": f"t{i}", "ts": "", "keys": {f"k{i}": "v" * 30 for i in range(3)}}
        for i in range(6)
    ]
    text = build_board_evidence_summary(records, limit=2, max_chars=120)
    assert text.startswith("<board-evidence>")
    assert text.endswith("</board-evidence>")
    assert "turn t3:" not in text  # limit=2 只回看最近两轮（t4/t5），t3 及更早不出现
    assert len(text) <= 140  # 字符预算生效


def test_build_summary_skips_junk_records() -> None:
    records = [{"turn_id": "t1", "keys": "not-dict"}, "junk", None, {"no_turn": 1}]
    assert build_board_evidence_summary(records) == ""


# ── 落盘与读取往返 ──────────────────────────────────────────────────────────


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    board = Blackboard()
    board.write("研究结论", "市场 500 亿", writer="subagent-a")
    board.write("竞品", ["A", "B"], writer="subagent-b")

    saved = save_turn_blackboard("thr-1", "turn-1", board, logs_root=tmp_path)
    assert saved is not None
    assert saved["turn_id"] == "turn-1"
    assert saved["keys"]["研究结论"] == "市场 500 亿"

    records = list_board_evidence("thr-1", logs_root=tmp_path)
    assert len(records) == 1
    assert records[0]["keys"]["竞品"] == '["A", "B"]'

    text = load_board_evidence("thr-1", logs_root=tmp_path)
    assert "turn-1: 研究结论 = 市场 500 亿" in text


def test_save_accepts_plain_dict(tmp_path: Path) -> None:
    saved = save_turn_blackboard("thr-2", "turn-2", {"k": "v"}, logs_root=tmp_path)
    assert saved is not None
    records = list_board_evidence("thr-2", logs_root=tmp_path)
    assert records[0]["keys"] == {"k": "v"}


def test_save_noop_without_turn_or_empty_board(tmp_path: Path) -> None:
    assert save_turn_blackboard("", "turn-3", {"k": "v"}, logs_root=tmp_path) is None
    assert save_turn_blackboard("thr-3", "", {"k": "v"}, logs_root=tmp_path) is None
    assert save_turn_blackboard("thr-3", "turn-3", {}, logs_root=tmp_path) is None
    assert save_turn_blackboard("thr-3", "turn-3", None, logs_root=tmp_path) is None
    assert list_board_evidence("thr-3", logs_root=tmp_path) == []


def test_value_longer_than_limit_is_truncated(tmp_path: Path) -> None:
    board = Blackboard()
    board.write("big", "x" * 500)
    save_turn_blackboard("thr-4", "turn-4", board, logs_root=tmp_path)
    records = list_board_evidence("thr-4", logs_root=tmp_path)
    value = records[0]["keys"]["big"]
    assert value.endswith("...")
    assert len(value) <= 203


def test_record_over_budget_trims_keys_not_turn(tmp_path: Path) -> None:
    board = Blackboard()
    board.write("tiny", "ok")
    board.write("huge", "y" * 100_000)
    saved = save_turn_blackboard("thr-5", "turn-5", board, logs_root=tmp_path)
    assert saved is not None
    # 整轮保留（tiny 在），超预算键被裁掉
    assert saved["keys"].get("tiny") == "ok"
    records = list_board_evidence("thr-5", logs_root=tmp_path)
    assert records[0]["keys"].get("tiny") == "ok"


# ── run_auto_parallel 接线：turn_id 触发黑板证据落盘 ────────────────────────


def _parallel_plan() -> Any:
    plan = plan_auto_parallel(
        "请分别调研以下两个方向并给出各自结论：\n"
        "1. A 方向的当前进展与关键参与方\n"
        "2. B 方向的当前进展与关键参与方"
    )
    assert plan is not None and plan.should_parallelize()
    return plan


def test_run_auto_parallel_persists_board_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    plan = _parallel_plan()

    class _FakeResult:
        def __init__(self, task_id, status, result, subagent_name):
            self.task_id = task_id
            self.status = status
            self.result = result
            self.subagent_name = subagent_name

    class _FakeBatch:
        batch_id = "batch-evid"
        status = "completed"
        total_tasks = 2
        completed_tasks = 2
        error = None
        results = [
            _FakeResult("t1", "completed", "A 结论", "general-purpose"),
            _FakeResult("t2", "completed", "B 结论", "general-purpose"),
        ]

    class _FakeOrchestrator:
        def dispatch(self, tasks, **kwargs):
            return _FakeBatch()

        def get_batch(self, batch_id):
            return _FakeBatch()

    # 模拟并行子代理把中间发现写进本轮黑板
    from runtime.memory.runtime_state.blackboard import get_blackboard

    board = get_blackboard("turn-evid")
    board.write("findings.summary", "并行调研发现 X 与 Y", writer="subagent")

    with patch(
        "runtime.core.cerebrum.agent_auto_parallel.get_auto_parallel_orchestrator",
        return_value=_FakeOrchestrator(),
    ):
        result = run_auto_parallel(plan, thread_id="thr-evid", turn_id="turn-evid")
    assert result["success"] is True

    # 黑板证据已随 batch 落盘 → 断线后 load 可恢复
    text = load_board_evidence("thr-evid")
    assert "turn-evid: findings.summary = 并行调研发现 X 与 Y" in text


def test_run_auto_parallel_without_turn_id_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    plan = _parallel_plan()

    class _FakeBatch:
        batch_id = "batch-noop"
        status = "completed"
        total_tasks = 2
        completed_tasks = 2
        error = None
        results = []

    class _FakeOrchestrator:
        def dispatch(self, tasks, **kwargs):
            return _FakeBatch()

        def get_batch(self, batch_id):
            return _FakeBatch()

    with patch(
        "runtime.core.cerebrum.agent_auto_parallel.get_auto_parallel_orchestrator",
        return_value=_FakeOrchestrator(),
    ):
        result = run_auto_parallel(plan, thread_id="thr-noop")  # 不传 turn_id
    assert result["success"] is False  # 无可用输出 → 失败结果，但流程不炸
    assert (tmp_path / "data" / "threads" / "thr-noop.board.jsonl").exists() is False


# ── 跨轮复用闭环：上一轮证据喂给下一轮拆解 ─────────────────────────────────


def test_board_evidence_feeds_next_turn_decomposition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    # 第一轮：并行探索把证据写黑板并落盘
    board = Blackboard()
    board.write("market.size", "云计算 2025 约 500 亿美元", writer="researcher")
    board.write("competitor", "头部三家合计 60% 份额", writer="researcher")
    save_turn_blackboard("thr-cross", "turn-1", board)

    # 第二轮：拆解读取上一轮证据并喂给每个子任务
    from runtime.memory.threads.board_evidence import load_board_evidence

    evidence = load_board_evidence("thr-cross")
    assert "market.size" in evidence

    user_context = {
        "conversation_messages": [
            {"role": "user", "content": "继续完成云计算市场与竞品分析"},
            {"role": "assistant", "content": "好的，基于已有调研继续。"},
        ]
    }
    from runtime.core.cerebrum.agent_auto_parallel import build_thread_memory_summary

    memory = build_thread_memory_summary(user_context)
    combined = "\n\n".join(part for part in (memory, evidence) if part)

    plan = plan_auto_parallel(
        "请分别完成以下两个方向的深化分析：\n1. A 方向的补充调研与结论\n2. B 方向的补充调研与结论",
        context=combined,
    )
    assert plan is not None and plan.should_parallelize()
    for subtask in plan.subtasks:
        assert "market.size" in subtask.description or "云计算 2025" in subtask.description
        assert "<thread-memory>" in subtask.description or "<board-evidence>" in subtask.description

