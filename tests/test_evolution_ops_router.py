from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
from uuid import uuid4  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.execution.suckers import (  # noqa: E402
    Skill,
    SkillRegistry,
    load_forged_skills_from_dir,
)
from runtime.memory.journal import (  # noqa: E402
    BudgetBreakerResetEvent,
    CurriculumGoalDecisionEvent,
    InMemoryJournal,
    McpProposalDecisionEvent,
    ProtocolDriftDecisionEvent,
    SkillProposalDecisionEvent,
)
from runtime.platform.models import (  # noqa: E402
    ArmId,
    CostEntry,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.sensing.gateway.evolution_ops_router import (  # noqa: E402
    create_evolution_ops_router,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_evolution_ops_router())
    return TestClient(app)


def _client_with_runtime() -> TestClient:
    journal = InMemoryJournal()
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="list_cwd",
            description="List cwd",
            trusted_source="builtin://list_cwd",
            handler=lambda: ".",
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="forged_list_then_read",
            description="Forged composite",
            trusted_source="skill://forged/demo",
            affinity=["forged"],
            handler=lambda: ".",
        ),
        verify_tests=False,
    )

    call = ToolCall(caller="test", sucker_id="list_cwd", args={})
    result = ExecutionResult(call_id=call.call_id, status="success")
    step = Step(step_id=0, node_id="n0", action=call, result=result)
    journal.write_trajectory(
        Trajectory(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("test"),
            strategy_id="react_loop",
            steps=[step],
            outcome=TrajectoryOutcome(success=True),
        )
    )

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(journal=journal, registry=registry),
    )
    return TestClient(app)


def _client_with_forge_candidate(
    *,
    forged_skill_dir=None,
) -> tuple[TestClient, SkillRegistry, InMemoryJournal]:
    journal = InMemoryJournal()
    registry = SkillRegistry()

    def _source_value(value: str = "hello") -> dict[str, str]:
        return {"content": value}

    def _uppercase(content: str = "") -> dict[str, str]:
        return {"content": content.upper()}

    registry.register(
        Skill(
            name="source_value",
            description="Source a value",
            trusted_source="builtin://source_value",
            handler=_source_value,
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="uppercase",
            description="Uppercase content",
            trusted_source="builtin://uppercase",
            handler=_uppercase,
        ),
        verify_tests=False,
    )

    for _ in range(3):
        first = ToolCall(
            caller="test",
            sucker_id="source_value",
            args={"value": "hello"},
        )
        second = ToolCall(
            caller="test",
            sucker_id="uppercase",
            args={"content": "hello"},
        )
        journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("test"),
                strategy_id="react_loop",
                steps=[
                    Step(
                        step_id=0,
                        node_id="n0",
                        action=first,
                        result=ExecutionResult(
                            call_id=first.call_id,
                            status="success",
                            output={"content": "hello"},
                        ),
                        args_template={"value": "hello"},
                    ),
                    Step(
                        step_id=1,
                        node_id="n1",
                        action=second,
                        result=ExecutionResult(
                            call_id=second.call_id,
                            status="success",
                            output={"content": "HELLO"},
                        ),
                        args_template={"content": "{n0.content}"},
                    ),
                ],
                outcome=TrajectoryOutcome(success=True),
            )
        )

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(
            journal=journal,
            registry=registry,
            forged_skill_dir=forged_skill_dir,
        ),
    )
    return TestClient(app), registry, journal


def _client_for_existing_runtime(
    journal: InMemoryJournal,
    registry: SkillRegistry,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(journal=journal, registry=registry),
    )
    return TestClient(app)


def _client_with_curriculum_failures() -> tuple[TestClient, InMemoryJournal]:
    journal = InMemoryJournal()
    registry = SkillRegistry()

    for _ in range(3):
        call = ToolCall(caller="test", sucker_id="read_file", args={"path": "x"})
        result = ExecutionResult(
            call_id=call.call_id,
            status="failed",
            error_type="FileNotFoundError",
        )
        step = Step(step_id=0, node_id="n0", action=call, result=result)
        journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("test"),
                strategy_id="react_loop",
                steps=[step],
                outcome=TrajectoryOutcome(success=False),
            )
        )

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(journal=journal, registry=registry),
    )
    return TestClient(app), journal


def _client_with_model_benchmark_data() -> TestClient:
    journal = InMemoryJournal()
    registry = SkillRegistry()

    def _record_task(
        *,
        model: str,
        success: bool,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> None:
        raw_task_id = uuid4()
        task_id = TaskId(raw_task_id)
        journal.write_token_usage(
            str(raw_task_id),
            iteration=1,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=cost_usd,
            model=model,
        )
        call = ToolCall(caller="test", sucker_id="noop", args={})
        result = ExecutionResult(call_id=call.call_id, status="success")
        journal.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("test"),
                strategy_id="react_loop",
                steps=[
                    Step(
                        step_id=0,
                        node_id="n0",
                        action=call,
                        result=result,
                    )
                ],
                outcome=TrajectoryOutcome(success=success),
            )
        )

    for _ in range(3):
        _record_task(
            model="model-good",
            success=True,
            tokens_in=1000,
            tokens_out=300,
            cost_usd=0.01,
        )
    _record_task(
        model="model-bad",
        success=True,
        tokens_in=900,
        tokens_out=250,
        cost_usd=0.02,
    )
    for _ in range(2):
        _record_task(
            model="model-bad",
            success=False,
            tokens_in=900,
            tokens_out=250,
            cost_usd=0.02,
        )

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(journal=journal, registry=registry),
    )
    return TestClient(app)


def _client_with_mcp_gap() -> tuple[TestClient, InMemoryJournal]:
    journal = InMemoryJournal()
    registry = SkillRegistry()

    for _ in range(3):
        call = ToolCall(
            caller="test",
            sucker_id="github_issue_search",
            args={"query": "repo issues"},
        )
        result = ExecutionResult(
            call_id=call.call_id,
            status="failed",
            error_type="SkillNotFound",
            output="no skill named github_issue_search; missing github MCP",
        )
        journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("test"),
                strategy_id="react_loop",
                steps=[
                    Step(
                        step_id=0,
                        node_id="n0",
                        action=call,
                        result=result,
                    )
                ],
                outcome=TrajectoryOutcome(success=False),
            )
        )

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(journal=journal, registry=registry),
    )
    return TestClient(app), journal


def _client_with_protocol_drift() -> tuple[TestClient, InMemoryJournal]:
    journal = InMemoryJournal()
    registry = SkillRegistry()

    for _ in range(2):
        call = ToolCall(
            caller="test",
            sucker_id="frontend_fetch",
            args={"url": "/api/cron/settings"},
        )
        result = ExecutionResult(
            call_id=call.call_id,
            status="failed",
            error_type="HTTPError",
            output="Stream failed: 404 on GET /api/cron/settings",
        )
        journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("test"),
                strategy_id="react_loop",
                steps=[
                    Step(
                        step_id=0,
                        node_id="n0",
                        action=call,
                        result=result,
                    )
                ],
                outcome=TrajectoryOutcome(success=False),
            )
        )

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(journal=journal, registry=registry),
    )
    return TestClient(app), journal


def _client_with_framework_experiments() -> TestClient:
    journal = InMemoryJournal()
    registry = SkillRegistry()

    def _record(*, recipe_id: str, success: bool) -> None:
        call = ToolCall(caller="test", sucker_id="noop", args={})
        result = ExecutionResult(
            call_id=call.call_id,
            status="success" if success else "failed",
            error_type=None if success else "AssertionError",
        )
        journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("test"),
                strategy_id="react_loop",
                recipe_id=recipe_id,
                steps=[
                    Step(
                        step_id=0,
                        node_id="n0",
                        action=call,
                        result=result,
                    )
                ],
                outcome=TrajectoryOutcome(success=success),
            )
        )

    for success in [True, False, False]:
        _record(recipe_id="recipe-alpha#__default__", success=success)
    for success in [True, True, True]:
        _record(recipe_id="recipe-alpha#team_router", success=success)

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(journal=journal, registry=registry),
    )
    return TestClient(app)


def _client_with_budget_events() -> tuple[TestClient, InMemoryJournal]:
    journal = InMemoryJournal()
    registry = SkillRegistry()
    for _ in range(2):
        journal.write_budget(
            "budget_commit",
            task_id=TaskId(uuid4()),
            actor="runtime",
            cost=CostEntry(tokens_in=100, tokens_out=50, usd=0.01),
        )
    for _ in range(5):
        journal.write_budget(
            "budget_squirt",
            task_id=TaskId(uuid4()),
            actor="runtime",
            reason="reserve rejected: circuit breaker open",
            cost=CostEntry(tokens_in=10, tokens_out=5, usd=0.001),
        )

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(journal=journal, registry=registry),
    )
    return TestClient(app), journal


def test_evolution_operator_console_fallback_endpoints() -> None:
    client = _client()

    budget = client.get("/api/evolution/budget/snapshot").json()
    assert budget["source"] == "journal"
    assert len(budget["components"]) >= 3
    assert budget["components"][0]["breaker"]["state"] == "closed"
    assert budget["components"][0]["usage"]["daily_used"] == 0

    assert client.get("/api/intel-evolution/skills/proposals").json() == []
    assert client.get("/api/intel-evolution/models/proposals").json() == []
    assert client.get("/api/intel-evolution/mcp/proposals").json() == []
    assert client.get("/api/evolution/curriculum/goals").json() == []
    assert client.get("/api/intel-evolution/frameworks/benchmarks").json() == []
    assert client.get("/api/intel-evolution/protocols/drift").json() == []
    assert client.get("/api/evolution/dispatch/snapshot").json() == {}


def test_evolution_operator_actions_return_explicit_noop_results() -> None:
    client = _client()

    reset = client.post(
        "/api/evolution/budget/breaker/reset",
        json={"component": "recipe_forge"},
    ).json()
    assert reset == {
        "ok": True,
        "component": "recipe_forge",
        "source": "journal",
    }

    scan = client.post("/api/intel-evolution/protocols/drift/scan", json={}).json()
    assert scan["ok"] is True
    assert scan["source"] == "journal"

    run = client.post("/api/evolution/forge/run?n_iter=2&eval_tasks=1").json()
    assert run["ok"] is False
    assert run["source"] == "fallback"
    assert "journal" in run["error"]


def test_budget_snapshot_is_derived_from_journal_and_reset_persists() -> None:
    client, journal = _client_with_budget_events()

    budget = client.get("/api/evolution/budget/snapshot").json()
    runtime = next(item for item in budget["components"] if item["name"] == "runtime")
    assert budget["source"] == "journal"
    assert budget["events"] == 7
    assert runtime["usage"]["daily_used"] == 7
    assert runtime["last_24h"]["success"] == 2
    assert runtime["last_24h"]["failure"] == 5
    assert runtime["last_24h"]["rejected_budget"] == 5
    assert runtime["last_24h"]["rejected_breaker"] == 5
    assert runtime["breaker"]["state"] == "open"
    assert runtime["breaker"]["consecutive_failures"] == 5
    assert runtime["cost"]["daily_tokens"] == 375

    reset = client.post(
        "/api/evolution/budget/breaker/reset",
        json={"component": "runtime", "reason": "manual test reset"},
    ).json()
    assert reset == {"ok": True, "component": "runtime", "source": "journal"}
    events = journal.read_by_type("budget_breaker_reset")
    assert len(events) == 1
    assert isinstance(events[0], BudgetBreakerResetEvent)
    assert events[0].component == "runtime"

    budget_after_reset = client.get("/api/evolution/budget/snapshot").json()
    runtime_after_reset = next(
        item for item in budget_after_reset["components"] if item["name"] == "runtime"
    )
    assert runtime_after_reset["breaker"]["state"] == "closed"
    assert runtime_after_reset["breaker"]["consecutive_failures"] == 0
    assert runtime_after_reset["last_reset_at"] is not None


def test_recipe_forge_alias_shapes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = _client()

    applied = client.get("/api/evolution/forge/applied").json()
    assert applied["applied"] is False
    assert applied["source"] == "gepa"
    assert applied["path"].endswith("forge_planner_addendum.md")
    assert client.get("/api/evolution/forge/runs").json()["runs"] == []
    assert client.get("/api/evolution/forge/addendums").json()["addendums"] == []
    assert client.get("/api/evolution/forge/recipes").json()["recipes"] == []

    status = client.get("/api/evolution/forge/auto-tick/status").json()
    assert status["enabled"] is False
    assert status["source"] == "gepa"

    stats = client.get("/api/evolution/forge/variants/react_loop/stats").json()
    assert stats["recipe_id"] == "react_loop"
    assert stats["variants"] == []
    assert stats["source"] == "gepa"

    csv_text = client.get("/api/evolution/forge/runs.csv").text
    assert csv_text.startswith("ts,iso_ts,trigger,recipe_id")


def test_recipe_forge_aliases_apply_and_manage_variants(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = _client()

    applied = client.post(
        "/api/evolution/forge/apply",
        json={
            "prompt": "Prefer compact plans.",
            "candidate_id": "cand-global",
            "avg_score": 0.91,
            "rationale": "test",
        },
    ).json()
    assert applied["ok"] is True
    assert applied["source"] == "gepa"
    assert applied["scope"] == "global"

    snapshot = client.get("/api/evolution/forge/applied").json()
    assert snapshot["applied"] is True
    assert "Prefer compact plans." in snapshot["content_preview"]

    recipe_id = "llm@alias_recipe"
    variant = client.post(
        "/api/evolution/forge/apply",
        json={
            "prompt": "Route ambiguous tasks to the team router.",
            "candidate_id": "cand-v1",
            "avg_score": 0.88,
            "rationale": "variant test",
            "target_recipe_id": recipe_id,
            "variant_id": "team_router",
            "variant_weight": 2,
        },
    ).json()
    assert variant["ok"] is True
    assert variant["scope"] == "variant"

    variants = client.get(f"/api/evolution/forge/variants/{recipe_id}").json()
    assert variants["manifest_present"] is True
    assert variants["source"] == "gepa"
    assert variants["variants"][0]["variant_id"] == "team_router"
    assert variants["variants"][0]["weight"] == 2
    addendums = client.get("/api/evolution/forge/addendums").json()["addendums"]
    assert not any("__team_router" in entry["path"] for entry in addendums)

    weights = client.post(
        f"/api/evolution/forge/variants/{recipe_id}/weights",
        json={"weights": {"team_router": 5}, "default_weight": 1},
    ).json()
    assert weights["ok"] is True
    assert weights["default_weight"] == 1
    assert weights["variants"][0]["weight"] == 5

    removed = client.delete(
        f"/api/evolution/forge/variants/{recipe_id}/team_router",
    ).json()
    assert removed["ok"] is True
    assert removed["deleted"] is True


def test_evolution_dashboard_endpoints_are_derived_from_runtime() -> None:
    client = _client_with_runtime()

    overview = client.get("/api/evolution/overview").json()
    assert overview["source"] == "journal"
    assert overview["skills"]["total"] == 2
    assert overview["skills"]["auto_extracted"] == 1
    assert overview["learning_events"] >= 1

    history = client.get("/api/evolution/skills/history").json()
    assert history[0]["skill_name"] == "list_cwd"
    assert history[0]["success_rate"] == 1.0

    perf = client.get("/api/evolution/skills/performance").json()
    by_name = {row["name"]: row for row in perf}
    assert by_name["list_cwd"]["usage_count"] == 1
    assert by_name["list_cwd"]["success_rate"] == 1.0
    assert by_name["forged_list_then_read"]["usage_count"] == 0

    curve = client.get("/api/evolution/learning-curve").json()
    assert curve[0]["success_rate"] == 1.0

    assert client.get("/api/evolution/memory/growth").status_code == 200
    assert client.get("/api/evolution/recommendations").status_code == 200
    sync = client.post("/api/evolution/learn-from-intel").json()
    assert sync["skills_created"] == []


def test_evolution_overview_reflects_intelligence_reports(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    (tmp_path / "intelligence.json").write_text(
        json.dumps(
            {
                "subscriptions": [
                    {"id": "sub-enabled", "enabled": True},
                    {"id": "sub-disabled", "enabled": False},
                ],
                "reports": [
                    {"id": "r1", "created_at": "2026-04-28T00:00:00+00:00"},
                    {"id": "r2", "created_at": "2026-04-29T00:00:00+00:00"},
                ],
            }
        ),
        encoding="utf-8",
    )

    overview = _client().get("/api/evolution/overview").json()

    assert overview["proactive_learning"]["enabled"] is True
    assert overview["proactive_learning"]["subscriptions"] == 2
    assert overview["proactive_learning"]["enabled_subscriptions"] == 1
    assert overview["proactive_learning"]["total_reports"] == 2
    assert overview["proactive_learning"]["last_report_at"] == ("2026-04-29T00:00:00+00:00")
    assert overview["learning_events"] >= 2


def test_framework_benchmarks_and_dispatch_are_derived_from_trajectories() -> None:
    client = _client_with_framework_experiments()

    rows = client.get("/api/intel-evolution/frameworks/benchmarks").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["base_model"] == "recipe-alpha"
    assert row["strategy_family"] == "react_loop"
    assert row["strategy_a"] == "__default__"
    assert row["strategy_b"] == "team_router"
    assert row["a_assigned"] == 3
    assert row["b_assigned"] == 3
    assert row["a_wins"] == 1
    assert row["b_wins"] == 3
    assert row["decision"] == "prefer_b"

    snap = client.get("/api/evolution/dispatch/snapshot").json()
    assert len(snap) == 1
    bucket = next(iter(snap.values()))
    assert bucket["skill_name"] == "recipe-alpha"
    assert bucket["a_assigned"] == 3
    assert bucket["b_assigned"] == 3
    assert bucket["a_reported"] == 3
    assert bucket["b_reported"] == 3
    assert bucket["outcomes"]["a_success"] == 1
    assert bucket["outcomes"]["b_success"] == 3


def test_learn_from_intel_runs_planner_learning_passes() -> None:
    class PlannerStub:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.learned_rules_section = ""
            self.learned_memories_section = ""

        def learn_from_journal(self, journal: InMemoryJournal) -> int:
            self.calls.append("rules")
            return 2

        def learn_memories_from_journal(self, journal: InMemoryJournal) -> int:
            self.calls.append("memories")
            return 3

        def learn_kg_from_journal(self, journal: InMemoryJournal) -> int:
            self.calls.append("kg")
            return 4

    journal = InMemoryJournal()
    registry = SkillRegistry()
    planner = PlannerStub()
    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(
            journal=journal,
            registry=registry,
            planner=planner,
        ),
    )
    client = TestClient(app)

    result = client.post("/api/evolution/learn-from-intel").json()
    assert result["ok"] is True
    assert result["source"] == "planner"
    assert result["planner_attached"] is True
    assert result["rules_learned"] == 2
    assert result["memories_stored"] == 3
    assert result["kg_triples"] == 4
    assert planner.calls == ["rules", "memories", "kg"]


def test_skillforge_proposals_can_be_promoted_or_rejected(tmp_path) -> None:
    client, registry, journal = _client_with_forge_candidate(
        forged_skill_dir=tmp_path,
    )

    proposals = client.get("/api/intel-evolution/skills/proposals?status=pending").json()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["topic"] == "SkillForge"
    assert proposal["status"] == "pending"
    assert proposal["source_sample_count"] == 3
    assert proposal["underlying_sequence"] == ["source_value", "uppercase"]
    assert proposal["name"].startswith("forged_source_value_then_uppercase_")

    approved = client.post(
        "/api/intel-evolution/skills/proposals/approve",
        json={"name": proposal["name"]},
    ).json()
    assert approved["ok"] is True
    assert approved["status"] == "promoted"
    assert registry.has(proposal["name"])
    assert (tmp_path / f"{proposal['name']}.md").exists()
    decision_events = journal.read_by_type("skill_proposal_decision")
    assert len(decision_events) == 1
    assert isinstance(decision_events[0], SkillProposalDecisionEvent)
    assert decision_events[0].decision == "promoted"
    assert client.get("/api/intel-evolution/skills/proposals?status=pending").json() == []

    loaded_registry = SkillRegistry()
    loaded_registry.register(registry.get("source_value"), verify_tests=False)
    loaded_registry.register(registry.get("uppercase"), verify_tests=False)
    loaded = load_forged_skills_from_dir(tmp_path, loaded_registry)
    assert proposal["name"] in loaded
    loaded_result = loaded_registry.get(proposal["name"]).handler(value="hello")
    assert loaded_result["success"] is True
    assert loaded_result["composite_output"]["n1"]["content"] == "HELLO"

    client, registry, journal = _client_with_forge_candidate()
    proposal = client.get("/api/intel-evolution/skills/proposals?status=pending").json()[0]
    rejected = client.post(
        "/api/intel-evolution/skills/proposals/reject",
        json={"name": proposal["name"]},
    ).json()
    assert rejected["ok"] is True
    assert rejected["status"] == "rejected"
    assert not registry.has(proposal["name"])
    decision_events = journal.read_by_type("skill_proposal_decision")
    assert decision_events[-1].decision == "rejected"
    assert client.get("/api/intel-evolution/skills/proposals?status=pending").json() == []
    restarted_client = _client_for_existing_runtime(journal, registry)
    assert restarted_client.get("/api/intel-evolution/skills/proposals?status=pending").json() == []


def test_curriculum_goals_are_derived_from_failures_and_decisions_persist() -> None:
    client, journal = _client_with_curriculum_failures()

    goals = client.get("/api/evolution/curriculum/goals?status=pending").json()
    assert len(goals) == 1
    goal = goals[0]
    assert goal["category"] == "skill_failure"
    assert goal["failure_count"] == 3
    assert goal["status"] == "pending"
    assert "read_file" in goal["keywords"]
    assert "FileNotFoundError" in goal["description"]

    cycle = client.post("/api/evolution/curriculum/cycle/run", json={}).json()
    assert cycle["ok"] is True
    assert cycle["created"] == 1
    assert cycle["source"] == "journal"

    decided = client.post(
        "/api/evolution/curriculum/goals/decide",
        json={"goal_id": goal["id"], "status": "in_progress"},
    ).json()
    assert decided["ok"] is True
    assert decided["status"] == "in_progress"

    decision_events = journal.read_by_type("curriculum_goal_decision")
    assert len(decision_events) == 1
    assert isinstance(decision_events[0], CurriculumGoalDecisionEvent)
    assert decision_events[0].cluster_key == goal["cluster_key"]
    assert decision_events[0].status == "in_progress"

    assert client.get("/api/evolution/curriculum/goals?status=pending").json() == []
    in_progress = client.get("/api/evolution/curriculum/goals?status=in_progress").json()
    assert len(in_progress) == 1
    assert in_progress[0]["id"] == goal["id"]

    restarted = _client_for_existing_runtime(journal, SkillRegistry())
    assert restarted.get("/api/evolution/curriculum/goals?status=pending").json() == []
    assert (
        restarted.get("/api/evolution/curriculum/goals?status=in_progress").json()[0]["id"]
        == goal["id"]
    )


def test_model_proposals_are_derived_from_token_usage_and_outcomes() -> None:
    client = _client_with_model_benchmark_data()

    proposals = client.get("/api/intel-evolution/models/proposals").json()
    assert len(proposals) == 2
    assert proposals[0]["model_label"] == "model-good"
    assert proposals[0]["status"] == "recommended"
    assert proposals[0]["task_count"] == 3
    assert proposals[0]["known_outcomes"] == 3
    assert proposals[0]["success_rate"] == 1.0
    assert "success_rate: 100%" in proposals[0]["benchmark_notes"]

    by_model = {row["model_label"]: row for row in proposals}
    assert by_model["model-bad"]["success_rate"] == pytest.approx(1 / 3)
    assert by_model["model-bad"]["status"] == "observed"

    run = client.post("/api/intel-evolution/models/benchmarks/run", json={}).json()
    assert run["ok"] is True
    assert run["created"] == 2
    assert run["source"] == "journal"
    assert run["proposals"][0]["model_label"] == "model-good"


def test_mcp_proposals_are_derived_from_capability_gaps_and_vetted() -> None:
    client, journal = _client_with_mcp_gap()

    proposals = client.get("/api/intel-evolution/mcp/proposals").json()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["server_name"] == "github"
    assert proposal["status"] == "pending_vet"
    assert proposal["failure_count"] == 3
    assert "server-github" in proposal["suggested_cmd"]

    vetted = client.post("/api/intel-evolution/mcp/proposals/vet", json={}).json()
    assert vetted["ok"] is True
    assert vetted["vetted"] == 1
    assert vetted["source"] == "journal"

    decision_events = journal.read_by_type("mcp_proposal_decision")
    assert len(decision_events) == 1
    assert isinstance(decision_events[0], McpProposalDecisionEvent)
    assert decision_events[0].server_name == "github"
    assert decision_events[0].status == "vetted"

    proposals = client.get("/api/intel-evolution/mcp/proposals").json()
    assert proposals[0]["status"] == "vetted"

    install = client.post(
        "/api/intel-evolution/mcp/proposals/install",
        json={"server_name": "github"},
    ).json()
    assert install["ok"] is True
    assert install["installed"] is False
    assert install["status"] == "install_requested"

    restarted = _client_for_existing_runtime(journal, SkillRegistry())
    restarted_proposals = restarted.get("/api/intel-evolution/mcp/proposals").json()
    assert restarted_proposals[0]["server_name"] == "github"
    assert restarted_proposals[0]["status"] == "install_requested"


def test_protocol_drift_and_repair_are_derived_from_contract_failures() -> None:
    client, journal = _client_with_protocol_drift()

    events = client.get("/api/intel-evolution/protocols/drift?acknowledged=false").json()
    assert len(events) >= 1
    drift = next(event for event in events if event["protocol_id"] == "http_api_contract")
    assert drift["protocol_id"] == "http_api_contract"
    assert drift["acknowledged"] is False
    assert drift["failure_count"] == 2
    assert "/api/cron/settings" in drift["summary"]

    scan = client.post("/api/intel-evolution/protocols/drift/scan", json={}).json()
    assert scan["ok"] is True
    assert scan["events"] == len(events)
    assert scan["source"] == "journal"

    repairs = client.get("/api/intel-evolution/protocols/repair/proposals?status=pending").json()
    repair = next(row for row in repairs if row["drift_event_id"] == drift["id"])
    assert repair["protocol_id"] == "http_api_contract"
    assert "endpoint" in repair["suggested_diff"].lower()

    sweep = client.post("/api/intel-evolution/protocols/repair/sweep", json={}).json()
    assert sweep["ok"] is True
    assert sweep["proposals"] == len(repairs)
    assert sweep["source"] == "journal"

    ack = client.post(
        f"/api/intel-evolution/protocols/drift/{drift['id']}/acknowledge",
        json={},
    ).json()
    assert ack["ok"] is True
    assert ack["acknowledged"] is True

    decision_events = journal.read_by_type("protocol_drift_decision")
    assert len(decision_events) == 1
    assert isinstance(decision_events[0], ProtocolDriftDecisionEvent)
    assert decision_events[0].drift_id == drift["id"]
    assert decision_events[0].status == "acknowledged"

    remaining = client.get("/api/intel-evolution/protocols/drift?acknowledged=false").json()
    assert all(row["id"] != drift["id"] for row in remaining)
    acknowledged = client.get("/api/intel-evolution/protocols/drift?acknowledged=true").json()
    assert any(row["id"] == drift["id"] for row in acknowledged)

    restarted = _client_for_existing_runtime(journal, SkillRegistry())
    restarted_remaining = restarted.get(
        "/api/intel-evolution/protocols/drift?acknowledged=false"
    ).json()
    assert all(row["id"] != drift["id"] for row in restarted_remaining)
