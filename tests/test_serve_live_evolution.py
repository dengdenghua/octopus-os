"""Implementation note."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config  # noqa: E402
from runtime.safety.experiments import PromptOptimizer, PromptVariant  # noqa: E402
from runtime.sensing.gateway import create_openai_router  # noqa: E402

# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════


def _write_cfg(tmp_path: Path, planner: str = "llm") -> Path:
    path = tmp_path / "cfg.yaml"
    if planner == "llm":
        path.write_text(
            "planner:\n"
            "  type: llm\n"
            "  model: mock/srv\n"
            '  mock_response: \'{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{"path":"."}}]}\'\n'
            "budget:\n  max_tokens: 5000\n  max_usd: 0.05\n",
            encoding="utf-8",
        )
    else:
        path.write_text(
            "planner:\n  type: static\nbudget:\n  max_tokens: 5000\n  max_usd: 0.05\n",
            encoding="utf-8",
        )
    return path


def _write_variants(tmp_path: Path) -> Path:
    path = tmp_path / "variants.yaml"
    path.write_text(
        "variants:\n"
        "  - name: baseline\n"
        "    system_prompt_suffix: ''\n"
        "    weight: 1.0\n"
        "  - name: careful\n"
        "    system_prompt_suffix: 'Be careful.'\n"
        "    weight: 1.0\n",
        encoding="utf-8",
    )
    return path


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestGatewayWithOptimizer:
    def test_response_includes_variant_name(self, tmp_path):
        cfg = AgentConfig(
            planner=PlannerConfig(
                type="llm",
                model="mock/g",
                mock_response=json.dumps(
                    {
                        "reasoning": "r",
                        "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                    }
                ),
            )
        )
        stack = build_from_config(cfg)
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="A", system_prompt_suffix="", weight=1.0),
                PromptVariant(name="B", system_prompt_suffix="X.", weight=1.0),
            ],
        )

        app = FastAPI()
        app.include_router(create_openai_router(stack, prompt_optimizer=opt))
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "list"}],
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "variant" in data["echo"]
        assert data["echo"]["variant"] in {"A", "B"}

    def test_variant_stats_accumulate(self, tmp_path):
        cfg = AgentConfig(
            planner=PlannerConfig(
                type="llm",
                model="mock/g",
                mock_response=json.dumps(
                    {
                        "reasoning": "r",
                        "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                    }
                ),
            )
        )
        stack = build_from_config(cfg)
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="A", system_prompt_suffix="", weight=1.0),
                PromptVariant(name="B", system_prompt_suffix="X.", weight=1.0),
            ],
        )

        app = FastAPI()
        app.include_router(create_openai_router(stack, prompt_optimizer=opt))
        client = TestClient(app)

        for i in range(10):
            client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": f"task-{i}"}],
                },
            )

        total = sum(opt._splitter.stats[n].assignments for n in opt.variant_names)
        assert total == 10
        # Implementation note.
        hit = [n for n in opt.variant_names if opt._splitter.stats[n].assignments > 0]
        assert len(hit) >= 1  # Implementation note.

    def test_optimizer_none_no_variant_field(self):
        cfg = AgentConfig(
            planner=PlannerConfig(
                type="llm",
                model="mock/g",
                mock_response=json.dumps(
                    {
                        "reasoning": "r",
                        "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                    }
                ),
            )
        )
        stack = build_from_config(cfg)
        app = FastAPI()
        app.include_router(create_openai_router(stack))  # Implementation note.
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "list"}],
            },
        )
        assert r.status_code == 200
        # Implementation note.
        assert "variant" not in r.json()["echo"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestServePromptEvolution:
    def test_variants_yaml_missing_gracefully(self, tmp_path, monkeypatch, capsys):
        """Implementation note."""
        import uvicorn
        from runtime.cli import run_serve

        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        rc = run_serve(
            config_path=_write_cfg(tmp_path),
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            prompt_variants_path=tmp_path / "nope.yaml",
            evolve_interval_s=0,
            mutator_model="mock/m",
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "not found" in out or "skipping" in out

    def test_static_planner_skips_evolution_gracefully(self, tmp_path, monkeypatch, capsys):
        import uvicorn
        from runtime.cli import run_serve

        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        rc = run_serve(
            config_path=_write_cfg(tmp_path, planner="static"),
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            prompt_variants_path=_write_variants(tmp_path),
            evolve_interval_s=60,
            mutator_model="mock/m",
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        # Implementation note.
        assert "LLMPlanner" in out or "skipping" in out

    def test_evolve_interval_registers_scheduler_task(self, tmp_path, monkeypatch):
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
            config_path=_write_cfg(tmp_path),
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            prompt_variants_path=_write_variants(tmp_path),
            evolve_interval_s=3600,
            mutator_model="mock/m",
            color=False,
        )
        assert "prompt_evolve" in captured["r"].task_names()

    def test_variants_only_no_evolver_skipped(self, tmp_path, monkeypatch):
        """Implementation note."""
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
            config_path=_write_cfg(tmp_path),
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            prompt_variants_path=_write_variants(tmp_path),
            evolve_interval_s=0,  # Implementation note.
            mutator_model="mock/m",
            color=False,
        )
        assert "prompt_evolve" not in captured["r"].task_names()

    def test_serve_output_shows_variants(self, tmp_path, monkeypatch, capsys):
        import uvicorn
        from runtime.cli import run_serve

        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        run_serve(
            config_path=_write_cfg(tmp_path),
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            prompt_variants_path=_write_variants(tmp_path),
            evolve_interval_s=0,
            mutator_model="mock/m",
            color=False,
        )
        out = capsys.readouterr().out
        assert "prompt A/B" in out
        assert "baseline" in out
        assert "careful" in out
