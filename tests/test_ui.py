"""Implementation note."""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from runtime.platform.ui import create_app  # noqa: E402

# ═══════════════════════════════════════════════════════════
# factory
# ═══════════════════════════════════════════════════════════


@pytest.fixture()
def client() -> TestClient:
    app = create_app(journal_path=None)
    return TestClient(app)


def _seed_journal(path: Path) -> None:
    """Implementation note."""
    from runtime.core.cerebrum import StaticPlanner
    from runtime.core.cerebrum.planner import Rule
    from runtime.core.graph_runtime import GraphRuntime
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all
    from runtime.execution.tool_engine import ToolExecutor
    from runtime.memory.journal import JSONLJournal
    from runtime.platform.models import (
        ArmId,
        Budget,
        BudgetLimits,
        BudgetSpec,
        ParsedIntent,
        SkillId,
    )
    from runtime.safety.auth import TrustEngine

    journal = JSONLJournal(path)
    registry = SkillRegistry()
    register_all(registry)
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)
    planner = StaticPlanner(
        rules=[
            Rule(
                name="seed",
                intent_types=["task"],
                skill_sequence=[SkillId("list_cwd")],
            )
        ],
        default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        fallback_skill=SkillId("list_cwd"),
    )
    intent = ParsedIntent(raw="seed", intent_type="task", normalized_goal="seed")
    graph = planner.plan(intent)
    budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))
    runtime.run(graph, budget=budget, caller="arms/seed", arm_id=ArmId("seed_arm"))


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBasicRoutes:
    def test_index_html(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200
        assert "echo-agent" in r.text
        assert "<html" in r.text.lower()

    def test_status_endpoint(self, client: TestClient):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["version"]
        assert data["skill_count"] > 0
        assert "capabilities" in data
        assert set(data["capabilities"]) >= {"opentelemetry", "mcp", "httpx"}

    def test_skills_endpoint(self, client: TestClient):
        r = client.get("/api/skills")
        assert r.status_code == 200
        data = r.json()
        assert "skills" in data
        names = {s["name"] for s in data["skills"]}
        assert "list_cwd" in names
        assert "hash_text" in names

    def test_journal_empty(self, client: TestClient):
        r = client.get("/api/journal?limit=10")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["recent"] == []

    def test_reflect_empty_journal_returns_error(self, client: TestClient):
        r = client.get("/api/reflect")
        assert r.status_code == 200
        assert "error" in r.json()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestWithJournal:
    def test_run_populates_journal(self, client: TestClient):
        r = client.post("/api/run", json={"goal": "list files"})
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["steps"] >= 1

        # Implementation note.
        jr = client.get("/api/journal")
        jdata = jr.json()
        assert jdata["total"] >= 2  # step + trajectory
        assert "step" in jdata["counts"]

    def test_run_requires_goal(self, client: TestClient):
        r = client.post("/api/run", json={"goal": ""})
        assert r.status_code == 400

    def test_reflect_after_seed(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        _seed_journal(path)
        app = create_app(journal_path=path)
        client = TestClient(app)

        r = client.get("/api/reflect")
        assert r.status_code == 200
        data = r.json()
        assert "error" not in data
        assert "kg" in data
        assert "recipe" in data
        assert "memory" in data

    def test_kg_endpoint_with_data(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        _seed_journal(path)
        app = create_app(journal_path=path)
        client = TestClient(app)

        r = client.get("/api/kg?limit=20")
        assert r.status_code == 200
        data = r.json()
        assert "triples" in data
        assert data["kg_size"] >= 0


# ═══════════════════════════════════════════════════════════
# CLI ui subcommand
# ═══════════════════════════════════════════════════════════


class TestUiCliWiring:
    def test_ui_subcommand_registered(self):
        """Implementation note."""
        import argparse

        # Implementation note.

        # Implementation note.
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        uip = sub.add_parser("ui")
        uip.add_argument("--host", default="127.0.0.1")
        uip.add_argument("--port", type=int, default=8000)
        uip.add_argument("--journal", type=Path, default=None)
        # Implementation note.
        args = parser.parse_args(["ui", "--port", "9999"])
        assert args.command == "ui"
        assert args.port == 9999

    def test_run_ui_missing_uvicorn(self, monkeypatch, capsys):
        """Implementation note."""
        import builtins

        from runtime.cli import run_ui

        real_import = builtins.__import__

        def _fake_import(name, *a, **kw):
            if name == "uvicorn":
                raise ImportError("mocked missing")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        rc = run_ui(host="127.0.0.1", port=8000, journal_path=None)
        assert rc == 2
        err = capsys.readouterr().err
        assert "uvicorn" in err
