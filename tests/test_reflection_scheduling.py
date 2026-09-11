"""Implementation note."""

from __future__ import annotations

from pathlib import Path

# ═══════════════════════════════════════════════════════════
# _register_reflection_tasks
# ═══════════════════════════════════════════════════════════


def _stub_stack(planner_type: str = "llm"):
    """Implementation note."""
    import json

    from runtime.platform.config import (
        AgentConfig,
        PlannerConfig,
        build_from_config,
    )

    if planner_type == "llm":
        cfg = AgentConfig(
            planner=PlannerConfig(
                type="llm",
                model="mock/r",
                mock_response=json.dumps({"nodes": []}),
            ),
        )
    else:
        cfg = AgentConfig(planner=PlannerConfig(type="static"))
    return build_from_config(cfg)


class TestReflectionTaskRegistration:
    def test_llm_planner_registers_5_reflection_tasks(self):
        from runtime.adapters.scheduler import BackgroundRunner
        from runtime.cli import _register_reflection_tasks

        stack = _stub_stack(planner_type="llm")
        runner = BackgroundRunner()
        n = _register_reflection_tasks(runner, stack, interval_s=3600)
        # Implementation note.
        assert n == 5
        names = set(runner.task_names())
        assert "reflect_rules" in names
        assert "reflect_memories" in names
        assert "reflect_kg" in names
        assert "reflect_recipe" in names
        assert "reflect_skill_forge" in names

    def test_static_planner_registers_2_reflection_tasks(self):
        from runtime.adapters.scheduler import BackgroundRunner
        from runtime.cli import _register_reflection_tasks

        stack = _stub_stack(planner_type="static")
        runner = BackgroundRunner()
        n = _register_reflection_tasks(runner, stack, interval_s=3600)
        # Implementation note.
        assert n == 2
        names = set(runner.task_names())
        assert "reflect_workflow" in names
        assert "reflect_skill_forge" in names
        assert "reflect_rules" not in names  # Implementation note.

    def test_reflection_tasks_have_jitter(self):
        """Implementation note."""
        from runtime.adapters.scheduler import BackgroundRunner
        from runtime.cli import _register_reflection_tasks

        stack = _stub_stack(planner_type="llm")
        runner = BackgroundRunner()
        _register_reflection_tasks(runner, stack, interval_s=3600)
        for task in runner._tasks:
            assert task.jitter_s > 0, f"task {task.name} has jitter_s=0 · 雷群风险"

    def test_skill_forge_is_planner_agnostic(self):
        """Implementation note."""
        from runtime.adapters.scheduler import BackgroundRunner
        from runtime.cli import _register_reflection_tasks

        stack = _stub_stack(planner_type="static")
        runner = BackgroundRunner()
        _register_reflection_tasks(runner, stack, interval_s=3600)
        assert "reflect_skill_forge" in runner.task_names()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _write_cfg(tmp_path: Path, planner_type: str = "llm") -> Path:
    path = tmp_path / "cfg.yaml"
    if planner_type == "llm":
        path.write_text(
            "planner:\n"
            "  type: llm\n"
            "  model: mock/s\n"
            "  mock_response: '{\"nodes\":[]}'\n"
            "budget:\n"
            "  max_tokens: 5000\n"
            "  max_usd: 0.05\n",
            encoding="utf-8",
        )
    else:
        path.write_text(
            "planner:\n  type: static\nbudget:\n  max_tokens: 5000\n  max_usd: 0.05\n",
            encoding="utf-8",
        )
    return path


class TestServeReflectionScheduling:
    def test_learn_interval_zero_no_reflection_tasks(self, tmp_path: Path, monkeypatch):
        cfg = _write_cfg(tmp_path)
        import uvicorn

        from runtime import scheduler
        from runtime.cli import run_serve

        captured = {}
        real_cls = scheduler.BackgroundRunner

        def capture(*a, **kw):
            inst = real_cls(*a, **kw)
            captured["r"] = inst
            return inst

        monkeypatch.setattr(scheduler, "BackgroundRunner", capture)
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            color=False,
        )
        names = set(captured["r"].task_names())
        # Implementation note.
        assert not any(n.startswith("reflect_") for n in names)

    def test_learn_interval_positive_registers_llm_reflection(self, tmp_path: Path, monkeypatch):
        cfg = _write_cfg(tmp_path, planner_type="llm")
        import uvicorn

        from runtime import scheduler
        from runtime.cli import run_serve

        captured = {}
        real_cls = scheduler.BackgroundRunner

        def capture(*a, **kw):
            inst = real_cls(*a, **kw)
            captured["r"] = inst
            return inst

        monkeypatch.setattr(scheduler, "BackgroundRunner", capture)
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=8000,
            learn_interval_s=3600,
            color=False,
        )
        names = set(captured["r"].task_names())
        assert "reflect_rules" in names
        assert "reflect_memories" in names
        assert "reflect_kg" in names
        assert "reflect_recipe" in names
        assert "reflect_skill_forge" in names

    def test_learn_interval_positive_registers_static_reflection(self, tmp_path: Path, monkeypatch):
        cfg = _write_cfg(tmp_path, planner_type="static")
        import uvicorn

        from runtime import scheduler
        from runtime.cli import run_serve

        captured = {}
        real_cls = scheduler.BackgroundRunner

        def capture(*a, **kw):
            inst = real_cls(*a, **kw)
            captured["r"] = inst
            return inst

        monkeypatch.setattr(scheduler, "BackgroundRunner", capture)
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=8000,
            learn_interval_s=3600,
            color=False,
        )
        names = set(captured["r"].task_names())
        assert "reflect_workflow" in names
        assert "reflect_skill_forge" in names


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLoopIncludesSkillForge:
    def test_loop_mentions_forge_in_output(self, tmp_path: Path, capsys):
        """Implementation note."""
        from runtime.cli import run_loop
        from runtime.safety.recovery import skill_forge as sf_module

        real_forge_class = sf_module.SkillForge
        call_count = [0]

        class _SpyForge(real_forge_class):
            def run(self):
                call_count[0] += 1
                return super().run()

        import runtime.safety.recovery as reg_module

        original_export = reg_module.SkillForge
        reg_module.SkillForge = _SpyForge  # type: ignore[misc]
        try:
            cfg = _write_cfg(tmp_path, planner_type="llm")
            journal = tmp_path / "events.jsonl"
            run_loop(
                goal="probe",
                config_path=cfg,
                journal_path=journal,
                iterations=2,
                color=False,
            )
            # Implementation note.
            assert call_count[0] >= 2
        finally:
            reg_module.SkillForge = original_export
