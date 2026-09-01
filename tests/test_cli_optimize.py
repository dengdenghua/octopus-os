"""Implementation note."""

from __future__ import annotations

from pathlib import Path

# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════


def _write_llm_cfg(tmp_path: Path) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(
        "planner:\n"
        "  type: llm\n"
        "  model: mock/opt\n"
        '  mock_response: \'{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{"path":"."}}]}\'\n'
        "budget:\n"
        "  max_tokens: 5000\n"
        "  max_usd: 0.05\n",
        encoding="utf-8",
    )
    return path


def _write_static_cfg(tmp_path: Path) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(
        "planner:\n  type: static\nbudget:\n  max_tokens: 5000\n  max_usd: 0.05\n",
        encoding="utf-8",
    )
    return path


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestArgValidation:
    def test_missing_config_returns_2(self, tmp_path: Path, capsys):
        from runtime.cli import run_optimize

        rc = run_optimize(
            goal="probe",
            config_path=tmp_path / "nope.yaml",
            variants_path=None,
            journal_path=tmp_path / "events.jsonl",
            rounds=1,
            tasks_per_round=1,
            mutator_model="mock/m",
            mutator_response=None,
            max_variants=4,
            retire_min_uses=3,
            export_path=None,
            color=False,
        )
        assert rc == 2
        assert "config" in capsys.readouterr().err

    def test_static_planner_rejected(self, tmp_path: Path, capsys):
        from runtime.cli import run_optimize

        cfg = _write_static_cfg(tmp_path)
        rc = run_optimize(
            goal="probe",
            config_path=cfg,
            variants_path=None,
            journal_path=tmp_path / "events.jsonl",
            rounds=1,
            tasks_per_round=1,
            mutator_model="mock/m",
            mutator_response=None,
            max_variants=4,
            retire_min_uses=3,
            export_path=None,
            color=False,
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "LLM planner" in err


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBasicEvolution:
    def test_runs_to_completion(self, tmp_path: Path, capsys):
        from runtime.cli import run_optimize

        cfg = _write_llm_cfg(tmp_path)
        journal = tmp_path / "events.jsonl"
        rc = run_optimize(
            goal="list cwd",
            config_path=cfg,
            variants_path=None,
            journal_path=journal,
            rounds=2,
            tasks_per_round=2,
            mutator_model="mock/m",
            mutator_response="<suffix>try harder</suffix>",
            max_variants=4,
            retire_min_uses=3,
            export_path=None,
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        # Current i18n dict produces ``[Round 1/2]`` and
        # ``── Final Ranking ──`` · tests match case-insensitive
        # so both "round 1/2" and "Round 1/2" forms pass.
        out_lower = out.lower()
        assert "round 1/2" in out_lower
        assert "round 2/2" in out_lower
        assert "final ranking" in out_lower
        # Implementation note.
        assert "pool=" in out

    def test_journal_accumulates_across_rounds(self, tmp_path: Path):
        from runtime.cli import run_optimize
        from runtime.memory.journal import JSONLJournal

        cfg = _write_llm_cfg(tmp_path)
        journal = tmp_path / "events.jsonl"
        run_optimize(
            goal="list cwd",
            config_path=cfg,
            variants_path=None,
            journal_path=journal,
            rounds=2,
            tasks_per_round=2,
            mutator_model="mock/m",
            mutator_response="<suffix>improved</suffix>",
            max_variants=4,
            retire_min_uses=3,
            export_path=None,
            color=False,
        )
        j = JSONLJournal(journal)
        # Implementation note.
        total = len(j.read_all())
        assert total >= 4


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestVariantsFromYaml:
    def test_loads_initial_variants(self, tmp_path: Path, capsys):
        from runtime.cli import run_optimize

        variants_yaml = tmp_path / "variants.yaml"
        variants_yaml.write_text(
            "variants:\n"
            "  - name: baseline\n"
            "    system_prompt_suffix: ''\n"
            "    weight: 1.0\n"
            "  - name: careful\n"
            "    system_prompt_suffix: 'Be careful.'\n"
            "    weight: 1.0\n",
            encoding="utf-8",
        )
        cfg = _write_llm_cfg(tmp_path)
        rc = run_optimize(
            goal="list",
            config_path=cfg,
            variants_path=variants_yaml,
            journal_path=tmp_path / "events.jsonl",
            rounds=1,
            tasks_per_round=1,
            mutator_model="mock/m",
            mutator_response="<suffix>evolved</suffix>",
            max_variants=6,
            retire_min_uses=3,
            export_path=None,
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "baseline" in out
        assert "careful" in out


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestExport:
    def test_export_yaml_is_reloadable(self, tmp_path: Path):
        from runtime.cli import run_optimize
        from runtime.safety.experiments import load_variants_from_yaml

        cfg = _write_llm_cfg(tmp_path)
        export_path = tmp_path / "winners.yaml"
        rc = run_optimize(
            goal="list",
            config_path=cfg,
            variants_path=None,
            journal_path=tmp_path / "events.jsonl",
            rounds=2,
            tasks_per_round=2,
            mutator_model="mock/m",
            mutator_response="<suffix>go faster</suffix>",
            max_variants=6,
            retire_min_uses=3,
            export_path=export_path,
            color=False,
        )
        assert rc == 0
        assert export_path.exists()
        # Implementation note.
        reloaded = load_variants_from_yaml(export_path)
        assert len(reloaded) >= 1
        names = {v.name for v in reloaded}
        assert "baseline" in names


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMutatorIntegration:
    def test_mutator_adds_new_variant_to_pool(self, tmp_path: Path, capsys):
        """Implementation note."""
        from uuid import uuid4

        from runtime.cli import run_optimize
        from runtime.memory.journal import JSONLJournal
        from runtime.platform.models import (
            ArmId,
            ExecutionResult,
            Step,
            TaskId,
            ToolCall,
            Trajectory,
            TrajectoryOutcome,
        )

        # Implementation note.
        journal_path = tmp_path / "events.jsonl"
        j = JSONLJournal(journal_path)
        for _ in range(3):
            call = ToolCall(caller="arms/a", sucker_id="read_file", args={})
            step = Step(
                step_id=0,
                node_id="n0",
                action=call,
                result=ExecutionResult(
                    call_id=call.call_id,
                    status="failed",
                    error_type="timeout",
                ),
            )
            j.write_trajectory(
                Trajectory(
                    task_id=TaskId(uuid4()),
                    arm_id=ArmId("a"),
                    recipe_id="preseeded",  # Implementation note.
                    steps=[step],
                    outcome=TrajectoryOutcome(success=False),
                )
            )

        cfg = _write_llm_cfg(tmp_path)
        run_optimize(
            goal="list",
            config_path=cfg,
            variants_path=None,
            journal_path=journal_path,
            rounds=2,
            tasks_per_round=2,
            mutator_model="mock/m",
            mutator_response="<suffix>improved-by-mutator</suffix>",
            max_variants=6,
            retire_min_uses=20,  # Implementation note.
            export_path=None,
            color=False,
        )
        out = capsys.readouterr().out
        has_mutation = "mutated=" in out or "pool=2" in out or "pool=3" in out or "pool=4" in out
        assert has_mutation, f"no evidence of mutation in output:\n{out[-500:]}"


# ═══════════════════════════════════════════════════════════
# CLI argparse wire
# ═══════════════════════════════════════════════════════════


class TestCLIWire:
    def test_cli_dispatches_to_run_optimize(self, tmp_path: Path, capsys):
        from runtime.cli import main

        cfg = _write_llm_cfg(tmp_path)
        rc = main(
            [
                "--no-color",
                "optimize",
                "list stuff",
                "--config",
                str(cfg),
                "--journal",
                str(tmp_path / "events.jsonl"),
                "--rounds",
                "1",
                "--tasks-per-round",
                "1",
                "--mutator-model",
                "mock/m",
                "--mutator-response",
                "<suffix>x</suffix>",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "optimize" in out
        # See note in test_runs_to_completion · dict says ``[Round 1/1]``.
        assert "round 1/1" in out.lower()
