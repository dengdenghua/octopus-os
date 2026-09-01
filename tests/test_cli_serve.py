"""Implementation note."""

from __future__ import annotations

from pathlib import Path

from runtime.platform.i18n import set_lang

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _write_cfg(
    tmp_path: Path,
    *,
    intel_sources: list[dict] | None = None,
    planner_type: str = "llm",
) -> Path:
    lines = [
        "name: test-serve",
        "planner:",
        f"  type: {planner_type}",
    ]
    if planner_type == "llm":
        lines.append("  model: mock/serve")
        lines.append("  mock_response: '{\"nodes\":[]}'")
    lines.extend(
        [
            "budget:",
            "  max_tokens: 5000",
            "  max_usd: 0.05",
        ]
    )
    if intel_sources:
        lines.append("intel_sources:")
        for s in intel_sources:
            lines.append(f"  - source_id: {s['source_id']}")
            lines.append(f"    query: {s['query']!r}")
            lines.append(f"    max_results: {s.get('max_results', 3)}")
            lines.append(f"    frequency_seconds: {s.get('frequency_seconds', 3600)}")
    path = tmp_path / "cfg.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestServeBasics:
    def setup_method(self) -> None:
        set_lang("en")

    def test_serve_starts_and_stops_cleanly(self, tmp_path: Path, monkeypatch, capsys):
        cfg = _write_cfg(tmp_path)

        uvicorn_calls = []

        def fake_run(app, host, port, log_level, ws):
            uvicorn_calls.append({"host": host, "port": port, "ws": ws})

        import uvicorn

        monkeypatch.setattr(uvicorn, "run", fake_run)

        from runtime.cli import run_serve

        rc = run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=9090,
            learn_interval_s=0,
            color=False,
        )
        assert rc == 0
        assert len(uvicorn_calls) == 1
        assert uvicorn_calls[0]["port"] == 9090
        assert uvicorn_calls[0]["ws"] == "websockets-sansio"
        out = capsys.readouterr().out
        assert "http://127.0.0.1:9090" in out

    def test_missing_config_returns_2(self, tmp_path: Path, capsys):
        from runtime.cli import run_serve

        rc = run_serve(
            config_path=tmp_path / "nonexistent.yaml",
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            color=False,
        )
        assert rc == 2
        assert "config error" in capsys.readouterr().err

    def test_mock_planner_prints_warning_banner(self, tmp_path: Path, monkeypatch, capsys):
        """Accidental ``--config config.example.yaml`` (mock planner)
        must print a red warning at serve start · otherwise users see
        identical mock JSON every turn and can't tell why.
        Regression: 2026-04-24.
        """
        cfg = _write_cfg(tmp_path)  # default uses model=mock/serve
        import uvicorn

        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        from runtime.cli import run_serve

        rc = run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=9091,
            learn_interval_s=0,
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "MOCK PLANNER" in out
        assert "mock/serve" in out
        # mock_response='{"nodes":[]}' → "set"
        assert "mock_response=set" in out

    def test_static_planner_does_not_warn(self, tmp_path: Path, monkeypatch, capsys):
        """Static planner ignores ``planner.model`` entirely · the
        mock-planner guard must gate on ``type=='llm'`` to avoid
        false alarms on the default no-LLM dev config (which inherits
        schema default ``model='mock/planner'`` even though it's
        unused).
        """
        cfg = _write_cfg(tmp_path, planner_type="static")
        import uvicorn

        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        from runtime.cli import run_serve

        rc = run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=9092,
            learn_interval_s=0,
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "MOCK PLANNER" not in out


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIntelSchedulingIntegration:
    def test_intel_sources_registered_when_web_search_available(self, tmp_path: Path, monkeypatch):
        cfg = _write_cfg(
            tmp_path,
            intel_sources=[
                {"source_id": "s1", "query": "q1", "frequency_seconds": 3600},
                {"source_id": "s2", "query": "q2", "frequency_seconds": 7200},
            ],
        )

        import uvicorn
        from runtime.cli import run_serve

        # Implementation note.
        captured_runner = {}

        def spy_uvicorn_run(app, host, port, log_level, ws):
            # Implementation note.
            # Implementation note.
            # Implementation note.
            pass

        from runtime import scheduler

        real_runner_cls = scheduler.BackgroundRunner

        def capture_runner_cls(*args, **kwargs):
            inst = real_runner_cls(*args, **kwargs)
            captured_runner["runner"] = inst
            return inst

        monkeypatch.setattr(scheduler, "BackgroundRunner", capture_runner_cls)
        monkeypatch.setattr(uvicorn, "run", spy_uvicorn_run)

        run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            color=False,
        )

        runner = captured_runner["runner"]
        try:
            import httpx  # noqa: F401

            names = runner.task_names()
            assert "intel_s1" in names
            assert "intel_s2" in names
            assert "intelligence_subscriptions" in names
        except ImportError:
            # Implementation note.
            assert runner.task_names() == ["intelligence_subscriptions"]

    def test_learn_interval_registers_reflection_tasks(self, tmp_path: Path, monkeypatch):
        """Implementation note."""
        cfg = _write_cfg(tmp_path, planner_type="llm")

        import uvicorn
        from runtime import scheduler

        captured = {}
        real_cls = scheduler.BackgroundRunner

        def capture(*a, **kw):
            inst = real_cls(*a, **kw)
            captured["r"] = inst
            return inst

        monkeypatch.setattr(scheduler, "BackgroundRunner", capture)
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        from runtime.cli import run_serve

        run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=8000,
            learn_interval_s=60,
            color=False,
        )
        names = set(captured["r"].task_names())
        # Implementation note.
        for expected in (
            "reflect_rules",
            "reflect_memories",
            "reflect_kg",
            "reflect_recipe",
            "reflect_skill_forge",
        ):
            assert expected in names, f"missing reflection task: {expected}"

    def test_static_planner_uses_workflow_not_llm_reflections(self, tmp_path: Path, monkeypatch):
        """Implementation note."""
        cfg = _write_cfg(tmp_path, planner_type="static")

        import uvicorn
        from runtime import scheduler

        captured = {}
        real_cls = scheduler.BackgroundRunner

        def capture(*a, **kw):
            inst = real_cls(*a, **kw)
            captured["r"] = inst
            return inst

        monkeypatch.setattr(scheduler, "BackgroundRunner", capture)
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        from runtime.cli import run_serve

        run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=8000,
            learn_interval_s=60,
            color=False,
        )
        names = set(captured["r"].task_names())
        assert "reflect_workflow" in names  # Implementation note.
        assert "reflect_skill_forge" in names  # Implementation note.
        # Implementation note.
        assert "reflect_rules" not in names
        assert "reflect_recipe" not in names


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSharedJournal:
    def test_ui_receives_stack_journal(self, tmp_path: Path, monkeypatch):
        cfg = _write_cfg(tmp_path)

        import uvicorn
        from runtime import ui as ui_module

        captured_app = {}
        real_create = ui_module.create_app

        def spy_create_app(journal_path=None, **kwargs):
            captured_app["journal"] = kwargs.get("journal")
            captured_app["registry"] = kwargs.get("registry")
            return real_create(journal_path=journal_path, **kwargs)

        monkeypatch.setattr(ui_module, "create_app", spy_create_app)
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        from runtime.cli import run_serve

        run_serve(
            config_path=cfg,
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            color=False,
        )
        assert captured_app["journal"] is not None
        assert captured_app["registry"] is not None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestCreateAppInjection:
    def test_inject_journal_preserves_existing_events(self, tmp_path: Path):
        from uuid import uuid4

        from fastapi.testclient import TestClient
        from runtime.memory.journal import InMemoryJournal
        from runtime.platform.models import (
            ArmId,
            ExecutionResult,
            Step,
            TaskId,
            ToolCall,
            Trajectory,
            TrajectoryOutcome,
        )
        from runtime.platform.ui import create_app

        j = InMemoryJournal()
        # Implementation note.
        call = ToolCall(caller="arms/x", sucker_id="list_cwd", args={})
        j.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                steps=[
                    Step(
                        step_id=0,
                        node_id="n0",
                        action=call,
                        result=ExecutionResult(call_id=call.call_id, status="success"),
                    )
                ],
                outcome=TrajectoryOutcome(success=True),
            )
        )

        app = create_app(journal=j)
        client = TestClient(app)

        r = client.get("/api/journal")
        data = r.json()
        assert data["total"] >= 1  # Implementation note.
        assert "trajectory" in data["counts"]

    def test_inject_registry_respected(self):
        from fastapi.testclient import TestClient
        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.platform.ui import create_app

        # Implementation note.
        reg = SkillRegistry()
        reg.register(
            Skill(
                name="custom_probe",
                trusted_source="skill://test/custom_probe",
                handler=lambda **kw: {"ok": True},
            ),
            verify_tests=False,
        )
        app = create_app(registry=reg)
        client = TestClient(app)

        r = client.get("/api/skills")
        names = {s["name"] for s in r.json()["skills"]}
        # Custom-injected skill must be present. Note: ``create_app``
        # also pulls in market prompt skills via ``_register_public_prompt_skills``
        # at agent_world_router mount time, so the registry has the
        # injected skill PLUS whatever's on disk under all_skills/.
        # The contract here is "inject was respected" → custom_probe is
        # in the resulting registry; not "no other skills exist".
        assert "custom_probe" in names
