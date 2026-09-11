"""Tests for the ReAct resume CLI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.core.cerebrum import resume_cli


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


def _ckpt(
    task_id: str,
    *,
    ts: str = "2026-06-01T10:00:00",
    iteration: int = 5,
    max_iter: int = 100,
    phase: str = "implement",
    has_final: bool = False,
    steps: list[dict] | None = None,
    summary: str = "",
) -> dict:
    return {
        "event_type": "react_checkpoint",
        "task_id": task_id,
        "ts": ts,
        "iteration_completed": iteration,
        "max_iterations": max_iter,
        "current_phase": phase,
        "has_final_answer": has_final,
        "steps_snapshot": steps or [],
        "working_set_snapshot": [],
        "progress_summary": summary,
    }


# Arg parsing


class TestArgParsing:
    def test_list_subcommand(self) -> None:
        args = resume_cli._parse_args(["list"])
        assert args.cmd == "list"

    def test_show_requires_task_id(self) -> None:
        with pytest.raises(SystemExit):
            resume_cli._parse_args(["show"])

    def test_show_takes_task_id(self) -> None:
        args = resume_cli._parse_args(["show", "task-123"])
        assert args.cmd == "show"
        assert args.task_id == "task-123"

    def test_resume_subcommand_options(self) -> None:
        args = resume_cli._parse_args(
            [
                "resume",
                "task-123",
                "--planner-type",
                "llm",
                "--planner-model",
                "gpt-test",
                "--max-iterations",
                "9",
            ]
        )
        assert args.cmd == "resume"
        assert args.task_id == "task-123"
        assert args.planner_type == "llm"
        assert args.planner_model == "gpt-test"
        assert args.max_iterations == 9

    def test_journal_path_override(self, tmp_path: Path) -> None:
        args = resume_cli._parse_args(
            [
                "--journal-path",
                str(tmp_path / "j.jsonl"),
                "list",
            ]
        )
        assert args.journal_path == tmp_path / "j.jsonl"

    def test_no_subcommand_errors(self) -> None:
        # argparse should reject "no subcommand at all"
        with pytest.raises(SystemExit):
            resume_cli._parse_args([])


# Journal loading + grouping


class TestLoadAndGroup:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        events = resume_cli._load_journal_events(tmp_path / "missing.jsonl")
        assert events == []

    def test_loads_jsonl_records(self, tmp_path: Path) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("a"), _ckpt("b")])
        events = resume_cli._load_journal_events(path)
        assert len(events) == 2
        assert {e["task_id"] for e in events} == {"a", "b"}

    def test_skips_garbage_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "j.jsonl"
        path.write_text(
            json.dumps(_ckpt("a")) + "\nthis is not json\n" + json.dumps(_ckpt("b")) + "\n",
            encoding="utf-8",
        )
        events = resume_cli._load_journal_events(path)
        assert len(events) == 2

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "j.jsonl"
        path.write_text(
            json.dumps(_ckpt("a")) + "\n\n\n" + json.dumps(_ckpt("b")) + "\n",
            encoding="utf-8",
        )
        assert len(resume_cli._load_journal_events(path)) == 2

    def test_groups_by_task(self) -> None:
        events = [
            _ckpt("a", ts="2026-06-01T10:00:00"),
            _ckpt("a", ts="2026-06-01T10:05:00"),
            _ckpt("b", ts="2026-06-01T10:02:00"),
            {"event_type": "other", "task_id": "z"},  # ignored
        ]
        by_task = resume_cli._checkpoints_by_task(events)
        assert set(by_task) == {"a", "b"}
        assert len(by_task["a"]) == 2
        assert len(by_task["b"]) == 1

    def test_skips_events_without_task_id(self) -> None:
        events = [{"event_type": "react_checkpoint"}]  # no task_id
        assert resume_cli._checkpoints_by_task(events) == {}


# Resumable filter


class TestResumable:
    def test_only_non_final_returned(self) -> None:
        by_task = {
            "done": [_ckpt("done", iteration=10, has_final=True)],
            "still": [_ckpt("still", iteration=3, has_final=False)],
        }
        out = resume_cli._resumable_tasks(by_task)
        ids = [tid for tid, _ in out]
        assert ids == ["still"]

    def test_uses_latest_checkpoint(self) -> None:
        by_task = {
            "a": [
                _ckpt("a", ts="2026-06-01T10:00:00", iteration=2),
                _ckpt("a", ts="2026-06-01T10:05:00", iteration=5),
            ],
        }
        out = resume_cli._resumable_tasks(by_task)
        assert out[0][1]["iteration_completed"] == 5

    def test_sorted_by_ts(self) -> None:
        by_task = {
            "newer": [_ckpt("newer", ts="2026-06-01T11:00:00")],
            "older": [_ckpt("older", ts="2026-06-01T09:00:00")],
        }
        out = resume_cli._resumable_tasks(by_task)
        ids = [tid for tid, _ in out]
        assert ids == ["older", "newer"]

    def test_empty_history_skipped(self) -> None:
        by_task = {"empty": []}
        assert resume_cli._resumable_tasks(by_task) == []

    def test_final_at_latest_drops_task(self) -> None:
        # Earlier checkpoint non-final, latest is final: drop.
        by_task = {
            "wrap": [
                _ckpt("wrap", ts="2026-06-01T10:00:00", has_final=False),
                _ckpt("wrap", ts="2026-06-01T10:05:00", has_final=True),
            ],
        }
        assert resume_cli._resumable_tasks(by_task) == []


# Render


class TestRender:
    def test_list_empty(self) -> None:
        out = resume_cli._render_list([])
        assert "No resumable" in out

    def test_list_includes_each_task(self) -> None:
        out = resume_cli._render_list(
            [
                ("task-aaa", _ckpt("task-aaa", iteration=3, max_iter=20, phase="design")),
                ("task-bbb", _ckpt("task-bbb", iteration=8, max_iter=20)),
            ]
        )
        assert "task-aaa" in out
        assert "task-bbb" in out
        assert "Resumable react tasks: 2" in out

    def test_show_missing_task(self) -> None:
        out = resume_cli._render_show("nope", None)
        assert "No checkpoint" in out
        assert "nope" in out

    def test_show_full_detail(self) -> None:
        ckpt = _ckpt(
            "tid",
            iteration=7,
            max_iter=50,
            phase="implement",
            summary="Read file foo.py.\nIdentified bug at line 42.",
            steps=[
                {
                    "iteration": 7,
                    "thought": "Need to verify the fix.",
                    "action": 'exec_shell({"command": "pytest"})',
                    "observation": "===== 5 passed =====",
                },
            ],
        )
        out = resume_cli._render_show("tid", ckpt)
        assert "Task: tid" in out
        assert "7" in out  # iteration
        assert "implement" in out
        assert "verify the fix" in out
        assert "pytest" in out
        assert "5 passed" in out
        assert "Read file foo.py" in out

    def test_show_truncates_long_lines(self) -> None:
        long_action = "x" * 500
        ckpt = _ckpt(
            "tid",
            steps=[{"iteration": 1, "thought": "", "action": long_action, "observation": ""}],
        )
        out = resume_cli._render_show("tid", ckpt)
        # 120-char cap on action line ensures no full 500-char dump
        for line in out.splitlines():
            assert len(line) < 200


# main() integration


class TestMain:
    def test_list_runs_clean(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("alive")])
        rc = resume_cli.main(["--journal-path", str(path), "list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alive" in out

    def test_show_runs_clean(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("hot", iteration=42)])
        rc = resume_cli.main(
            [
                "--journal-path",
                str(path),
                "show",
                "hot",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Task: hot" in out
        assert "42" in out

    def test_show_unknown_task(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("real")])
        rc = resume_cli.main(
            [
                "--journal-path",
                str(path),
                "show",
                "fake",
            ]
        )
        assert rc == 0
        assert "No checkpoint" in capsys.readouterr().out

    def test_missing_journal_handled_gracefully(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = resume_cli.main(
            [
                "--journal-path",
                str(tmp_path / "missing.jsonl"),
                "list",
            ]
        )
        assert rc == 0
        assert "No resumable" in capsys.readouterr().out

    def test_top_level_exception_returns_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def boom(*a, **kw):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(resume_cli, "_load_journal_events", boom)
        path = tmp_path / "j.jsonl"
        path.touch()
        rc = resume_cli.main(["--journal-path", str(path), "list"])
        assert rc == 1
        assert "ERROR" in capsys.readouterr().err


class TestResumeTask:
    def test_missing_task_returns_3(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("real")])

        rc = resume_cli._resume_task(
            "missing",
            journal_path=path,
            runner=lambda *a, **kw: None,
            stack_builder=lambda **kw: (object(), object(), kw["journal"]),
            journal_loader=lambda p: object(),
        )

        assert rc == 3
        assert "No checkpoints" in capsys.readouterr().out

    def test_final_task_returns_3(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("done", has_final=True)])

        rc = resume_cli._resume_task(
            "done",
            journal_path=path,
            runner=lambda *a, **kw: None,
            stack_builder=lambda **kw: (object(), object(), kw["journal"]),
            journal_loader=lambda p: object(),
        )

        assert rc == 3
        assert "already has a final answer" in capsys.readouterr().out

    def test_resume_wraps_cli_stack_and_passes_resume_task_id(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(
            path,
            [
                _ckpt("task-123", summary="Keep going from here."),
            ],
        )
        captured: dict[str, object] = {}

        def fake_stack_builder(**kwargs):
            captured["builder_kwargs"] = kwargs
            return (
                SimpleNamespace(name="planner"),
                SimpleNamespace(name="executor"),
                kwargs["journal"],
            )

        def fake_runner(stack, intent, agent, **kwargs):
            captured["stack"] = stack
            captured["intent"] = intent
            captured["agent"] = agent
            captured["runner_kwargs"] = kwargs
            return SimpleNamespace(final_answer="ok")

        rc = resume_cli._resume_task(
            "task-123",
            journal_path=path,
            planner_type="static",
            planner_model="mock/planner",
            max_iterations=7,
            runner=fake_runner,
            stack_builder=fake_stack_builder,
            journal_loader=lambda p: SimpleNamespace(path=p),
        )

        assert rc == 0
        stack = captured["stack"]
        assert stack.planner.name == "planner"
        assert stack.executor.name == "executor"
        assert stack.journal.path == path
        assert captured["runner_kwargs"] == {
            "max_iterations": 7,
            "resume_task_id": "task-123",
        }
        assert captured["intent"].raw == "Keep going from here."
        assert captured["intent"].intent_type == "task"
