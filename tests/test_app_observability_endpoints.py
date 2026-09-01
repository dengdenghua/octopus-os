"""
Integration tests for the observability endpoint group.

Baseline for extracting these 6 endpoints out of ``app.py``:

    GET  /api/journal    · event counts + recent tail
    GET  /api/reflect    · run all 6 reflection producers
    GET  /api/kg         · query the knowledge graph
    GET  /api/progress   · task progress snapshots
    GET  /api/stream     · SSE live feed of journal events
    POST /api/run        · static-planner probe run

Design note
-----------

The ``/api/reflect`` test is intentionally loose · we only assert
the response shape has the 6 producer keys. Asserting specific
counts would tie the test to emergent behavior (how many rules
the extractor actually finds in a trivially-empty journal), which
churns without us learning anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config
from runtime.platform.ui.app import create_app


@pytest.fixture
def isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def stack(isolated_cwd: Path):
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/ob",
            mock_response='{"reasoning":"r","nodes":[]}',
        ),
    )
    return build_from_config(cfg)


@pytest.fixture
def client(stack, isolated_cwd: Path) -> TestClient:
    app = create_app(
        journal=stack.journal,
        registry=stack.registry,
        stack=stack,
    )
    return TestClient(app)


# ═══════════════════════════════════════════════════════════
# /api/journal
# ═══════════════════════════════════════════════════════════


class TestJournalEndpoint:
    def test_empty_journal_shape(self, client: TestClient) -> None:
        r = client.get("/api/journal")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["counts"] == {}
        assert data["recent"] == []

    def test_respects_limit(self, client: TestClient) -> None:
        # Just verify the query param is plumbed · no events yet so
        # limit doesn't matter for count, but 400 bounds must hold.
        r = client.get("/api/journal?limit=5")
        assert r.status_code == 200
        r = client.get("/api/journal?limit=0")
        assert r.status_code == 422
        r = client.get("/api/journal?limit=9999")
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════
# /api/reflect
# ═══════════════════════════════════════════════════════════


class TestReflectEndpoint:
    def test_empty_journal_returns_error_hint(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/reflect")
        assert r.status_code == 200
        assert r.json().get("error") == "journal empty · run something first"

    def test_populated_journal_returns_all_producers(
        self,
        client: TestClient,
    ) -> None:
        """Once the journal has events, reflect returns all 6
        producer keys. We drive events by hitting /api/run."""
        run_r = client.post("/api/run", json={"goal": "probe"})
        assert run_r.status_code == 200

        r = client.get("/api/reflect")
        assert r.status_code == 200
        data = r.json()
        required = {
            "skill_forge",
            "rule_extractor",
            "kg",
            "memory",
            "workflow",
            "recipe",
        }
        assert required.issubset(data.keys()), (
            f"reflect response missing keys: {required - data.keys()}"
        )


# ═══════════════════════════════════════════════════════════
# /api/kg
# ═══════════════════════════════════════════════════════════


class TestKgEndpoint:
    def test_empty_returns_zero(self, client: TestClient) -> None:
        r = client.get("/api/kg")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 0
        assert data["kg_size"] == 0
        assert data["triples"] == []


# ═══════════════════════════════════════════════════════════
# /api/progress
# ═══════════════════════════════════════════════════════════


class TestProgressEndpoint:
    def test_empty_returns_no_tasks(self, client: TestClient) -> None:
        r = client.get("/api/progress")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 0
        assert data["running"] == 0
        assert data["tasks"] == []

    def test_unknown_task_id_returns_404(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/progress?task_id=nope")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# /api/run
# ═══════════════════════════════════════════════════════════


class TestRunEndpoint:
    def test_runs_static_probe(self, client: TestClient) -> None:
        r = client.post("/api/run", json={"goal": "hello"})
        assert r.status_code == 200
        data = r.json()
        assert "success" in data
        assert "steps" in data
        assert "tokens_spent" in data
        assert "usd_spent" in data

    def test_empty_goal_rejected(self, client: TestClient) -> None:
        r = client.post("/api/run", json={"goal": ""})
        assert r.status_code == 400
        r = client.post("/api/run", json={})
        assert r.status_code == 400


class TestEvolutionStatusEndpoint:
    """Implementation note."""

    def test_fresh_stack_returns_zero_counts(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/evolution/status")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        assert data["rules_count"] == 0
        assert data["memories_count"] == 0
        assert data["trajectories"]["total"] == 0
        assert data["trajectories"]["react_loop"] == 0

    def test_shape_includes_full_sections_and_trajectory_stats(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/evolution/status")
        data = r.json()
        assert "rules_section" in data
        assert "memories_section" in data
        assert "trajectories" in data
        assert "react_loop_failures" in data["trajectories"]

    def test_delete_rule_removes_one_bullet(
        self,
        client: TestClient,
        stack,
    ) -> None:
        """Implementation note."""
        stack.planner.learned_rules_section = (
            "LEARNED MITIGATIONS (from past failures):\n"
            "  - [HIGH·5x] rule A → fix A\n"
            "  - [MID·3x] rule B → fix B"
        )
        r = client.delete("/api/evolution/rules/0")
        assert r.status_code == 200
        data = r.json()
        assert "rule A" in data["dropped"]
        assert data["remaining"] == 1
        # Implementation note.
        s = client.get("/api/evolution/status").json()
        assert s["rules_count"] == 1
        assert len(s["rules_lines"]) == 1
        assert "rule B" in s["rules_lines"][0]

    def test_delete_rule_out_of_range_returns_404(
        self,
        client: TestClient,
        stack,
    ) -> None:
        stack.planner.learned_rules_section = ""
        r = client.delete("/api/evolution/rules/99")
        assert r.status_code == 404

    def test_delete_memory_removes_one_bullet(
        self,
        client: TestClient,
        stack,
    ) -> None:
        stack.planner.learned_memories_section = (
            "CONSOLIDATED MEMORIES (past pattern stats):\n"
            "  - [HOT] react_arm/react_loop · 5 runs · 80% success\n"
            "  - [WARM] code_arm/plan · 3 runs · 100% success"
        )
        r = client.delete("/api/evolution/memories/1")
        assert r.status_code == 200
        assert "code_arm/plan" in r.json()["dropped"]
        s = client.get("/api/evolution/status").json()
        assert s["memories_count"] == 1

    def test_status_exposes_react_variants(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/evolution/status")
        data = r.json()
        assert "react_variants" in data
        # Implementation note.
        assert isinstance(data["react_variants"], list)

    def test_counts_rules_after_react_failure(
        self,
        client: TestClient,
        stack,
    ) -> None:
        """Implementation note."""
        from runtime.core.cerebrum.react_loop import run_react_loop
        from runtime.execution.suckers import Skill
        from runtime.platform.models import ParsedIntent

        # Implementation note.
        def _boom() -> None:
            raise RuntimeError("kaboom")

        stack.registry.register(
            Skill(
                name="boom",
                description="test failure skill",
                trusted_source="builtin://boom",
                handler=_boom,
            ),
            verify_tests=False,
        )

        # Implementation note.
        from dataclasses import dataclass

        @dataclass
        class _Resp:
            text: str

        script = [
            "Thought: 调 boom\nAction: boom()\n",
            "Final Answer: 放弃",
        ]
        call_count = [0]

        def _fake_call(req):  # noqa: ANN001, ARG001
            i = call_count[0]
            call_count[0] += 1
            return _Resp(text=script[i % len(script)])

        def _fake_stream(req):  # noqa: ANN001
            from runtime.sensing.model_router.models import (
                CostEntry,
                ModelResponse,
                ModelStreamEvent,
            )

            resp = _fake_call(req)
            if resp.text:
                yield ModelStreamEvent(type="text_delta", delta=resp.text)
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text=resp.text, model="test", cost=CostEntry()),
            )

        stack.planner.router = type(
            "R",
            (),
            {
                "call": staticmethod(_fake_call),
                "call_stream": staticmethod(_fake_stream),
            },
        )()

        run_react_loop(
            stack,
            ParsedIntent(
                raw="go",
                intent_type="task",
                normalized_goal="go",
                user_context={},
            ),
            agent=None,
            max_iterations=3,
        )

        r = client.get("/api/evolution/status")
        data = r.json()
        assert data["trajectories"]["react_loop"] >= 1
        # Implementation note.
        # Implementation note.
        # Implementation note.
        # Implementation note.
