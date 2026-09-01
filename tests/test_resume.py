"""Implementation note."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import register_all
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import (
    InMemoryJournal,
    JSONLJournal,
    resume_info,
)
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    SkillId,
    TaskGraph,
    TaskId,
    TaskNode,
)
from runtime.safety.auth import TrustEngine

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _stack():
    reg = SkillRegistry()
    register_all(reg)
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=reg,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    return reg, journal, executor


def _graph(n: int, skill: str = "list_cwd") -> TaskGraph:
    return TaskGraph(
        nodes=[
            TaskNode(
                node_id=f"n{i}",
                skill_ref=SkillId(skill),
                args_template={"path": "."},
            )
            for i in range(n)
        ],
        edges=[],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        strategy="resume_test",
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestResumeInfoBasics:
    def test_no_events_returns_none(self):
        assert resume_info(InMemoryJournal(), uuid4()) is None

    def test_task_started_only(self):
        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=3, strategy="s")
        info = resume_info(j, tid)
        assert info is not None
        assert info.total_nodes == 3
        assert info.completed_nodes == []
        assert info.resume_from_index == 0
        assert info.is_resumable is True

    def test_partial_progress(self):
        """Implementation note."""
        _, journal, executor = _stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        # Implementation note.
        # Implementation note.
        # Implementation note.
        # Implementation note.
        graph = _graph(2)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        info = resume_info(journal, graph.task_id)
        assert info is not None
        assert len(info.completed_nodes) == 2
        assert info.outputs_by_node["n0"] is not None
        assert info.outputs_by_node["n1"] is not None
        assert info.task_terminated is True  # Implementation note.
        assert info.task_success is True

    def test_crash_mid_task_resumable(self):
        """Implementation note."""
        from runtime.platform.models import ExecutionResult, Step, ToolCall

        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=5, strategy="s")
        for i in range(2):
            call = ToolCall(caller="arms/a", sucker_id="list_cwd", args={})
            step = Step(
                step_id=i,
                node_id=f"n{i}",
                action=call,
                result=ExecutionResult(
                    call_id=call.call_id,
                    status="success",
                    output={"path": f"/tmp/{i}"},
                ),
            )
            j.write_step(tid, ArmId("a"), step)

        info = resume_info(j, tid)
        assert info.total_nodes == 5
        assert len(info.completed_nodes) == 2
        assert info.resume_from_index == 2
        assert info.is_resumable is True
        assert info.outputs_by_node["n0"] == {"path": "/tmp/0"}

    def test_failed_step_stops_accumulation(self):
        """Implementation note."""
        from runtime.platform.models import ExecutionResult, Step, ToolCall

        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=3, strategy="s")

        def _mk(i, ok):
            call = ToolCall(caller="arms/a", sucker_id="list_cwd", args={})
            return Step(
                step_id=i,
                node_id=f"n{i}",
                action=call,
                result=ExecutionResult(
                    call_id=call.call_id,
                    status="success" if ok else "failed",
                ),
            )

        # step0 success · step1 failed
        j.write_step(tid, ArmId("a"), _mk(0, True))
        j.write_step(tid, ArmId("a"), _mk(1, False))

        info = resume_info(j, tid)
        assert len(info.completed_nodes) == 1
        assert info.resume_from_index == 1

    def test_retry_success_replaces_earlier_failed_step(self):
        """Implementation note."""
        from runtime.platform.models import ExecutionResult, Step, ToolCall

        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=3, strategy="s")

        def _mk(i, ok, *, output=None):
            call = ToolCall(caller="arms/a", sucker_id="list_cwd", args={})
            return Step(
                step_id=i,
                node_id=f"n{i}",
                action=call,
                result=ExecutionResult(
                    call_id=call.call_id,
                    status="success" if ok else "failed",
                    output=output,
                ),
            )

        j.write_step(tid, ArmId("a"), _mk(0, True, output={"path": "/tmp/0"}))
        j.write_step(tid, ArmId("a"), _mk(1, False))
        j.write_step(tid, ArmId("a"), _mk(1, True, output={"path": "/tmp/1"}))

        info = resume_info(j, tid)
        assert len(info.completed_nodes) == 2
        assert info.resume_from_index == 2
        assert info.outputs_by_node["n1"] == {"path": "/tmp/1"}
        assert info.is_resumable is True

    def test_json_string_output_is_decoded_for_resume_seed(self):
        """Implementation note."""
        from runtime.platform.models import ExecutionResult, Step, ToolCall

        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=2, strategy="s")
        call = ToolCall(caller="arms/a", sucker_id="list_cwd", args={})
        step = Step(
            step_id=0,
            node_id="n0",
            action=call,
            result=ExecutionResult(
                call_id=call.call_id,
                status="success",
                output='{"path": "hello world", "items": ["a", "b"]}',
            ),
        )
        j.write_step(tid, ArmId("a"), step)

        info = resume_info(j, tid)
        assert info.outputs_by_node["n0"] == {
            "path": "hello world",
            "items": ["a", "b"],
        }
        assert info.completed_nodes[0].output["path"] == "hello world"

    def test_terminated_not_resumable(self):
        _, journal, executor = _stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph(1)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=1000, usd=0.01),
        )
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        info = resume_info(journal, graph.task_id)
        # Implementation note.
        assert info.task_terminated is True
        assert info.is_resumable is False


# ═══════════════════════════════════════════════════════════
# GraphRuntime resume_from + outputs_seed
# ═══════════════════════════════════════════════════════════


class TestGraphRuntimeResume:
    def test_resume_from_skips_first_n(self):
        _, journal, executor = _stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph(3)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        traj = rt.run(
            graph,
            budget=budget,
            caller="arms/x",
            arm_id=ArmId("x"),
            resume_from=2,
            outputs_seed={
                "n0": {"path": "/preloaded/0"},
                "n1": {"path": "/preloaded/1"},
            },
        )
        # Implementation note.
        assert len(traj.steps) == 1
        assert traj.steps[0].node_id == "n2"

    def test_outputs_seed_supplies_template_refs(self):
        """Implementation note."""
        _, journal, executor = _stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = TaskGraph(
            nodes=[
                TaskNode(node_id="n0", skill_ref=SkillId("list_cwd"), args_template={"path": "."}),
                TaskNode(node_id="n1", skill_ref=SkillId("list_cwd"), args_template={"path": "."}),
                TaskNode(
                    node_id="n2",
                    skill_ref=SkillId("count_words"),
                    args_template={"text": "{n0.path}"},
                ),
            ],
            edges=[],
            budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        traj = rt.run(
            graph,
            budget=budget,
            caller="arms/x",
            arm_id=ArmId("x"),
            resume_from=2,
            outputs_seed={
                "n0": {"path": "hello world"},
                "n1": {"path": "."},
            },
        )
        assert len(traj.steps) == 1
        # Implementation note.
        out = traj.steps[0].result.output
        assert out is not None
        assert out.get("words") == 2  # Implementation note.


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _write_cfg(tmp_path: Path, *, single_node: bool = True) -> Path:
    """Implementation note."""
    path = tmp_path / "cfg.yaml"
    if single_node:
        mock = '{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{"path":"."}}]}'
    else:
        mock = (
            '{"reasoning":"r","nodes":['
            '{"skill":"list_cwd","args":{"path":"."}},'
            '{"skill":"list_cwd","args":{"path":"."}}'
            "]}"
        )
    path.write_text(
        "planner:\n"
        "  type: llm\n"
        "  model: mock/resume\n"
        f"  mock_response: '{mock}'\n"
        "budget:\n"
        "  max_tokens: 5000\n"
        "  max_usd: 0.05\n",
        encoding="utf-8",
    )
    return path


def _crashed_journal(tmp_path: Path, *, total_nodes: int, completed: int) -> tuple[Path, TaskId]:
    """Implementation note."""
    from runtime.platform.models import ExecutionResult, Step, ToolCall

    path = tmp_path / "events.jsonl"
    j = JSONLJournal(path)
    tid = TaskId(uuid4())
    j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=total_nodes, strategy="default_list")
    for i in range(completed):
        call = ToolCall(caller="arms/a", sucker_id="list_cwd", args={"path": "."})
        step = Step(
            step_id=i,
            node_id=f"n{i}",
            action=call,
            result=ExecutionResult(
                call_id=call.call_id,
                status="success",
                output={"path": f"/fake/{i}", "items": [], "count": 0},
            ),
        )
        j.write_step(tid, ArmId("a"), step)
    return path, tid


class TestCLIResume:
    def test_dry_run_prints_diagnostic(self, tmp_path: Path, capsys):
        cfg = _write_cfg(tmp_path)
        jpath, tid = _crashed_journal(tmp_path, total_nodes=1, completed=0)

        from runtime.cli import run_resume

        rc = run_resume(
            task_id=str(tid),
            journal_path=jpath,
            goal="list cwd",
            config_path=cfg,
            dry_run=True,
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        # i18n dict uses ``Completed: 0/1`` capitalized · match
        # case-insensitive so both forms pass.
        out_lower = out.lower()
        assert "resume" in out_lower
        assert "completed: 0/1" in out_lower
        assert "resumable: true" in out_lower
        assert "dry-run" in out_lower

    def test_nonexistent_task_id_returns_2(self, tmp_path: Path, capsys):
        cfg = _write_cfg(tmp_path)
        jpath, _ = _crashed_journal(tmp_path, total_nodes=1, completed=0)

        from runtime.cli import run_resume

        rc = run_resume(
            task_id=str(uuid4()),  # Implementation note.
            journal_path=jpath,
            goal="x",
            config_path=cfg,
            dry_run=True,
            color=False,
        )
        assert rc == 2
        assert "no events" in capsys.readouterr().err

    def test_missing_journal_returns_2(self, tmp_path: Path, capsys):
        cfg = _write_cfg(tmp_path)
        from runtime.cli import run_resume

        rc = run_resume(
            task_id=str(uuid4()),
            journal_path=tmp_path / "nope.jsonl",
            goal="x",
            config_path=cfg,
            dry_run=True,
            color=False,
        )
        assert rc == 2

    def test_real_resume_continues_from_checkpoint(self, tmp_path: Path, capsys):
        """Implementation note."""
        cfg = _write_cfg(tmp_path)
        jpath, tid = _crashed_journal(tmp_path, total_nodes=1, completed=0)

        from runtime.cli import run_resume

        rc = run_resume(
            task_id=str(tid),
            journal_path=jpath,
            goal="list cwd",
            config_path=cfg,
            dry_run=False,
            color=False,
        )
        # Implementation note.
        assert rc == 0
        out = capsys.readouterr().out
        assert "resume" in out
        # Implementation note.
        reopened = JSONLJournal(jpath)
        evs = reopened.read_all()
        types = [e.event_type for e in evs]
        assert "trajectory" in types  # Implementation note.

    def test_already_terminated_no_resume(self, tmp_path: Path, capsys):
        """Implementation note."""
        _, journal, executor = _stack()
        rt = GraphRuntime(executor=executor, journal=journal)
        graph = _graph(1)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=1000, usd=0.01),
        )
        rt.run(graph, budget=budget, caller="arms/x", arm_id=ArmId("x"))

        path = tmp_path / "events.jsonl"
        out = JSONLJournal(path)
        for e in journal.read_all():
            out.write(e)

        cfg = _write_cfg(tmp_path)
        from runtime.cli import run_resume

        rc = run_resume(
            task_id=str(graph.task_id),
            journal_path=path,
            goal="list",
            config_path=cfg,
            dry_run=False,
            color=False,
        )
        assert rc == 0
        out_text = capsys.readouterr().out
        assert "already completed" in out_text or "nothing to resume" in out_text

    def test_prefix_args_template_mismatch_rejects_resume(self, tmp_path: Path, capsys):
        """Implementation note."""
        from runtime.platform.models import ExecutionResult, Step, ToolCall

        cfg = tmp_path / "cfg.yaml"
        mock = (
            '{"reasoning":"r","nodes":['
            '{"skill":"list_cwd","args":{"path":"./other"}},'
            '{"skill":"count_words","args":{"text":"hello"}}'
            "]}"
        )
        cfg.write_text(
            "planner:\n"
            "  type: llm\n"
            "  model: mock/resume\n"
            f"  mock_response: '{mock}'\n"
            "budget:\n"
            "  max_tokens: 5000\n"
            "  max_usd: 0.05\n",
            encoding="utf-8",
        )

        jpath = tmp_path / "events.jsonl"
        journal = JSONLJournal(jpath)
        tid = TaskId(uuid4())
        journal.write_task_started(tid, arm_id=ArmId("a"), total_nodes=2, strategy="s")
        call = ToolCall(caller="arms/a", sucker_id="list_cwd", args={"path": "."})
        step = Step(
            step_id=0,
            node_id="n0",
            action=call,
            args_template={"path": "."},
            result=ExecutionResult(
                call_id=call.call_id,
                status="success",
                output={"path": "."},
            ),
        )
        journal.write_step(tid, ArmId("a"), step)

        from runtime.cli import run_resume

        rc = run_resume(
            task_id=str(tid),
            journal_path=jpath,
            goal="list cwd then count words",
            config_path=cfg,
            dry_run=False,
            color=False,
        )
        assert rc == 1
        assert "args_template diverges" in capsys.readouterr().err
