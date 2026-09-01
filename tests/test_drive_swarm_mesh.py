"""_drive_swarm_mesh — auto-selecting swarm driver (mesh vs team) + fallback."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from runtime.platform.models import BudgetSpec, SkillId, TaskGraph, TaskNode
from runtime.platform.models.pipeline import WorkflowEdge
from runtime.sensing.gateway import realtime_team_stream as mod


class _Emitter:
    def __init__(self) -> None:
        self.calls: list = []

    async def notify(self, method, params):
        self.calls.append((method, params))


class _Log:
    def item_started(self, *a):
        pass

    def item_completed(self, *a):
        pass


def _graph(nodes, edges):
    return TaskGraph(
        nodes=[TaskNode(node_id=n, skill_ref=SkillId("x"), args_template={}) for n in nodes],
        edges=[WorkflowEdge(from_node=a, to_node=b) for a, b in edges],
        budget=BudgetSpec(tokens=1, usd=0.1),
        task_type="mixed",
    )


def _to_thread_seq(*returns):
    """Fake asyncio.to_thread: yield each value for successive calls; an
    Exception value is raised."""
    state = {"i": 0}

    async def fake(fn, *a):
        i = min(state["i"], len(returns) - 1)
        state["i"] += 1
        val = returns[i]
        if isinstance(val, Exception):
            raise val
        return val

    return fake


# ── _graph_favors_mesh: the auto-select decision ─────────────────


def test_graph_favors_mesh_decision() -> None:
    assert mod._graph_favors_mesh(_graph(["a", "b"], [])) is False  # too small
    assert mod._graph_favors_mesh(_graph(["a", "b", "c"], [])) is True  # 3 parallel
    assert (
        mod._graph_favors_mesh(  # 3-node chain
            _graph(["a", "b", "c"], [("a", "b"), ("b", "c")])
        )
        is False
    )


# ── mesh path: parallel graph runs the swarm + emits ─────────────


def test_parallel_graph_runs_mesh_and_emits(monkeypatch) -> None:
    parallel = _graph(["a", "b", "c"], [])  # favors mesh
    result = SimpleNamespace(
        arm_results=[
            SimpleNamespace(arm_id="builtin_arm", status="success", reason="ok"),
            SimpleNamespace(arm_id="code_intel_arm", status="success", reason="done"),
        ]
    )
    monkeypatch.setattr(asyncio, "to_thread", _to_thread_seq(parallel, (result, 5)))
    turn = SimpleNamespace(thread_id="th", id="t1", items=[])
    intent = SimpleNamespace(user_context={})
    asyncio.run(
        mod._drive_swarm_mesh(
            SimpleNamespace(), turn, _Log(), _Emitter(), intent, text="g", topology_id="topo"
        ),
    )
    assert len(turn.items) == 1  # all succeeded → just the plain summary
    assert "Ran 2 agents in parallel" in turn.items[0].text
    assert "2 done" in turn.items[0].text


def test_failed_agent_surfaces_a_plain_line(monkeypatch) -> None:
    parallel = _graph(["a", "b", "c"], [])
    result = SimpleNamespace(
        arm_results=[
            SimpleNamespace(arm_id="x", status="success", reason=""),
            SimpleNamespace(arm_id="y", status="failed", reason="timeout"),
        ]
    )
    monkeypatch.setattr(asyncio, "to_thread", _to_thread_seq(parallel, (result, 3)))
    turn = SimpleNamespace(thread_id="th", id="t1", items=[])
    intent = SimpleNamespace(user_context={})
    asyncio.run(
        mod._drive_swarm_mesh(
            SimpleNamespace(), turn, _Log(), _Emitter(), intent, text="g", topology_id="topo"
        ),
    )
    bodies = [it.text for it in turn.items]
    assert any("couldn't finish" in b and "timeout" in b for b in bodies)
    assert any("Ran 2 agents in parallel" in b and "1 need a look" in b for b in bodies)


# ── auto-select: small/sequential graph routes to the team ───────


def test_small_graph_routes_to_team(monkeypatch) -> None:
    small = _graph(["a", "b"], [])  # too small → team
    monkeypatch.setattr(asyncio, "to_thread", _to_thread_seq(small))
    team = {"called": None}

    async def fake_team(runtime, turn, log, emitter, intent, *, text, topology_id):
        team["called"] = topology_id

    monkeypatch.setattr(mod, "_drive_team_topology", fake_team)
    turn = SimpleNamespace(thread_id="th", id="t1", items=[])
    intent = SimpleNamespace(user_context={})
    asyncio.run(
        mod._drive_swarm_mesh(
            SimpleNamespace(), turn, _Log(), _Emitter(), intent, text="g", topology_id="topo-7"
        ),
    )
    assert team["called"] == "topo-7"  # delegated to the sequential team
    assert turn.items == []  # no mesh items emitted


# ── fallback: a mesh fault never breaks the turn ─────────────────


def test_mesh_fault_falls_back_to_react(monkeypatch) -> None:
    parallel = _graph(["a", "b", "c"], [])
    monkeypatch.setattr(
        asyncio,
        "to_thread",
        _to_thread_seq(parallel, RuntimeError("boom")),
    )
    monkeypatch.setattr(mod, "GatewayApprovalProvider", lambda *a, **k: object())
    react = {"called": False}

    async def fake_drive_react(*a, **k):
        react["called"] = True

    runtime = SimpleNamespace(
        _trace_store=None,
        _wrap_with_policy=lambda p: p,
        _resolve_agent=lambda *a, **k: None,
        _drive_react=fake_drive_react,
    )
    turn = SimpleNamespace(thread_id="th", id="t1", items=[])
    intent = SimpleNamespace(user_context={})
    asyncio.run(
        mod._drive_swarm_mesh(
            runtime, turn, _Log(), _Emitter(), intent, text="g", topology_id="topo"
        ),
    )
    assert react["called"] is True


# ── per-turn override: the 集群/蜂群 UI pick wins over the auto-decision ──


def test_serve_mesh_0_forces_team_even_for_parallel(monkeypatch) -> None:
    """集群: serve_mesh="0" routes to the sequential team even when the graph
    would otherwise favor the mesh."""
    monkeypatch.delenv("ECHO_SERVE_MESH", raising=False)
    parallel = _graph(["a", "b", "c"], [])  # would favor mesh
    monkeypatch.setattr(asyncio, "to_thread", _to_thread_seq(parallel))
    team = {"called": None}

    async def fake_team(runtime, turn, log, emitter, intent, *, text, topology_id):
        team["called"] = topology_id

    monkeypatch.setattr(mod, "_drive_team_topology", fake_team)
    turn = SimpleNamespace(thread_id="th", id="t1", items=[])
    intent = SimpleNamespace(user_context={"serve_mesh": "0"})
    asyncio.run(
        mod._drive_swarm_mesh(
            SimpleNamespace(), turn, _Log(), _Emitter(), intent, text="g", topology_id="topo-c"
        ),
    )
    assert team["called"] == "topo-c"  # forced to the cluster (sequential) team
    assert turn.items == []


def test_serve_mesh_1_forces_mesh_even_for_small(monkeypatch) -> None:
    """蜂群: serve_mesh="1" runs the mesh even for a small/sequential graph that
    would otherwise route to the team."""
    monkeypatch.delenv("ECHO_SERVE_MESH", raising=False)
    small = _graph(["a", "b"], [])  # too small → would go to team
    result = SimpleNamespace(
        arm_results=[
            SimpleNamespace(arm_id="x", status="success", reason="ok"),
        ]
    )
    monkeypatch.setattr(asyncio, "to_thread", _to_thread_seq(small, (result, 2)))
    turn = SimpleNamespace(thread_id="th", id="t1", items=[])
    intent = SimpleNamespace(user_context={"serve_mesh": "1"})
    asyncio.run(
        mod._drive_swarm_mesh(
            SimpleNamespace(), turn, _Log(), _Emitter(), intent, text="g", topology_id="topo"
        ),
    )
    assert any("parallel" in it.text for it in turn.items)  # mesh actually ran


def test_budget_scales_with_graph_size() -> None:
    """④ 预算随节点数动态伸缩, 单节点用基线, 大图放大并封顶."""
    single = _graph(["a"], [])
    t1, u1 = mod._budget_for_graph(single)
    assert t1 == 140_000  # 100k + 40k*1
    assert u1 == 1.5

    big = _graph(["a", "b", "c", "d", "e"], [])
    t5, u5 = mod._budget_for_graph(big)
    assert t5 == 300_000  # 100k + 40k*5
    assert u5 == 3.5

    # 封顶: 100+ 节点也不超过 800k / 8.0
    huge = _graph([f"n{i}" for i in range(200)], [])
    th, uh = mod._budget_for_graph(huge)
    assert th == 800_000
    assert uh == 8.0

