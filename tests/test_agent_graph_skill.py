"""``call_agent_graph`` · declarative DAG with server-side fan-in.

The behaviour that matters is the one the other delegation skills cannot do: a
node consuming an upstream node's output WITHOUT that text passing through the
lead's context. So the central assertion is on the prompt a downstream worker
actually receives, captured at the ``_call_agent_parallel`` boundary.

Validation is fail-closed by design: a cyclic or dangling graph must be
rejected before any spawn, because a partially-scheduled graph would spend real
budget on a topology the caller never described.
"""

from __future__ import annotations

from typing import Any

from runtime.execution.suckers import delegation_skills as ds
from runtime.execution.suckers._delegation_skills_graph import (
    _MAX_GRAPH_NODES,
    _coerce_graph_nodes,
    _plan_layers,
    _resolve_node_prompt,
)


def _fake_parallel(monkeypatch: Any, outputs: dict[str, str] | None = None) -> dict[str, Any]:
    """Echo one success per spec, keyed by the node id carried in ``bb_key``."""
    seen: dict[str, Any] = {"batches": []}

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        batch = list(specs or [])
        seen["batches"].append(batch)
        successes = []
        for i, spec in enumerate(batch):
            node_id = str(spec.get("bb_key") or "")
            text = (outputs or {}).get(node_id, f"out-{node_id}")
            successes.append(
                {
                    "output": text,
                    "agent_id": spec.get("agent_id"),
                    "bb_key": node_id,
                    "spec_index": i,
                    "success": True,
                }
            )
        return {"ok": True, "successes": successes, "failures": [], "success_count": len(batch)}

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    return seen


# ── the point of the skill: fan-in without a context round-trip ────


def test_downstream_node_receives_the_upstream_output(monkeypatch: Any) -> None:
    seen = _fake_parallel(monkeypatch, {"a": "ALPHA-FINDING"})
    r = ds._run_agent_graph(
        nodes=[
            {"id": "a", "prompt": "investigate"},
            {"id": "b", "prompt": "given {a.output}, decide", "depends_on": ["a"]},
        ]
    )
    assert r["ok"] is True
    downstream = seen["batches"][1][0]
    assert downstream["prompt"] == "given ALPHA-FINDING, decide"


def test_diamond_folds_two_lanes_into_one_node(monkeypatch: Any) -> None:
    """The shape ``call_agent_parallel`` cannot express."""
    seen = _fake_parallel(monkeypatch, {"left": "L", "right": "R"})
    r = ds._run_agent_graph(
        nodes=[
            {"id": "left", "prompt": "lane one"},
            {"id": "right", "prompt": "lane two"},
            {
                "id": "join",
                "prompt": "reconcile {left.output} with {right.output}",
                "depends_on": ["left", "right"],
            },
        ]
    )
    assert r["order"] == [["left", "right"], ["join"]]
    assert seen["batches"][0] and len(seen["batches"][0]) == 2, "lanes did not run concurrently"
    assert seen["batches"][1][0]["prompt"] == "reconcile L with R"
    assert r["terminal"] == ["out-join"]


def test_schema_field_reference_resolves(monkeypatch: Any) -> None:
    """``{a.output.field}`` needs the parsed object, not the raw text."""
    seen: dict[str, Any] = {"batches": []}

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        batch = list(specs or [])
        seen["batches"].append(batch)
        node_id = str(batch[0].get("bb_key") or "")
        succ: dict[str, Any] = {"output": "raw", "bb_key": node_id, "success": True}
        if node_id == "a":
            succ["parsed"] = {"verdict": "KEEP"}
        return {"ok": True, "successes": [succ], "failures": [], "success_count": 1}

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    ds._run_agent_graph(
        nodes=[
            {"id": "a", "prompt": "judge", "output_schema": {"type": "object"}},
            {"id": "b", "prompt": "act on {a.output.verdict}", "depends_on": ["a"]},
        ]
    )
    assert seen["batches"][1][0]["prompt"] == "act on KEEP"


def test_independent_nodes_share_one_layer(monkeypatch: Any) -> None:
    seen = _fake_parallel(monkeypatch)
    r = ds._run_agent_graph(
        nodes=[{"id": "a", "prompt": "x"}, {"id": "b", "prompt": "y"}, {"id": "c", "prompt": "z"}]
    )
    assert len(seen["batches"]) == 1, "independent nodes were serialised"
    assert r["layers_run"] == 1


# ── fail closed before spending budget ────────────────────────────


def test_cycle_is_rejected_before_any_spawn(monkeypatch: Any) -> None:
    seen = _fake_parallel(monkeypatch)
    r = ds._run_agent_graph(
        nodes=[
            {"id": "a", "prompt": "x", "depends_on": ["b"]},
            {"id": "b", "prompt": "y", "depends_on": ["a"]},
        ]
    )
    assert r["ok"] is False
    assert "cycle" in r["error"]
    assert seen["batches"] == [], "spawned despite an invalid graph"


def test_partial_cycle_does_not_run_its_acyclic_prefix(monkeypatch: Any) -> None:
    """``_topo_layers`` silently DROPS nodes it cannot schedule, so without the
    count check a graph with a cycle would run its healthy prefix and report ok.
    """
    seen = _fake_parallel(monkeypatch)
    r = ds._run_agent_graph(
        nodes=[
            {"id": "root", "prompt": "fine"},
            {"id": "a", "prompt": "x", "depends_on": ["b"]},
            {"id": "b", "prompt": "y", "depends_on": ["a"]},
        ]
    )
    assert r["ok"] is False
    assert seen["batches"] == []


def test_unknown_dependency_is_rejected() -> None:
    _, err = _coerce_graph_nodes([{"id": "a", "prompt": "x", "depends_on": ["ghost"]}])
    assert "unknown node" in err


def test_self_dependency_is_rejected() -> None:
    _, err = _coerce_graph_nodes([{"id": "a", "prompt": "x", "depends_on": ["a"]}])
    assert "depends on itself" in err


def test_duplicate_ids_are_rejected() -> None:
    _, err = _coerce_graph_nodes([{"id": "a", "prompt": "x"}, {"id": "a", "prompt": "y"}])
    assert "duplicate" in err


def test_node_without_prompt_is_rejected() -> None:
    _, err = _coerce_graph_nodes([{"id": "a"}])
    assert "no prompt" in err


def test_node_ceiling_is_enforced() -> None:
    too_many = [{"id": f"n{i}", "prompt": "x"} for i in range(_MAX_GRAPH_NODES + 1)]
    _, err = _coerce_graph_nodes(too_many)
    assert "too many nodes" in err


def test_missing_nodes_returns_error() -> None:
    assert "required" in ds._run_agent_graph()["error"]


def test_json_string_nodes_are_accepted() -> None:
    """LLM callers pass array args as JSON strings as often as real lists."""
    nodes, err = _coerce_graph_nodes('[{"id": "a", "prompt": "x"}]')
    assert err == ""
    assert nodes[0]["id"] == "a"


# ── failure propagation ───────────────────────────────────────────


def test_node_is_skipped_when_its_upstream_failed(monkeypatch: Any) -> None:
    """Spawning it anyway would spend budget on an unresolved placeholder."""

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        batch = list(specs or [])
        node_id = str(batch[0].get("bb_key") or "")
        return {
            "ok": True,
            "successes": [],
            "failures": [{"bb_key": node_id, "error": "boom", "agent_id": "researcher"}],
            "success_count": 0,
        }

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    r = ds._run_agent_graph(
        nodes=[
            {"id": "a", "prompt": "x"},
            {"id": "b", "prompt": "use {a.output}", "depends_on": ["a"]},
        ]
    )
    assert r["nodes"]["a"]["ok"] is False
    assert r["nodes"]["b"]["skipped"] is True
    assert "upstream" in r["nodes"]["b"]["error"]


def test_unresolved_reference_never_reaches_a_worker() -> None:
    """A literal ``{a.output}`` arriving as text is a silent wrong answer."""
    resolved, err = _resolve_node_prompt("use {ghost.output}", {})
    assert "unresolved" in err
    assert resolved == "use {ghost.output}"


def test_layers_are_derived_not_declared() -> None:
    nodes, _ = _coerce_graph_nodes(
        [
            {"id": "c", "prompt": "z", "depends_on": ["a", "b"]},
            {"id": "a", "prompt": "x"},
            {"id": "b", "prompt": "y"},
        ]
    )
    layers, err = _plan_layers(nodes)
    assert err == ""
    # Declaration order is c,a,b — execution order must still put c last.
    assert [sorted(nodes[i]["id"] for i in layer) for layer in layers] == [["a", "b"], ["c"]]


# ── budget: a graph is one delegation, not N ──────────────────────


def test_graph_costs_one_against_the_per_turn_cap(monkeypatch: Any) -> None:
    """Charging per node would double-count: the internal spawn budget already
    bounds width, and the per-turn cap exists to stop unbounded ORCHESTRATIONS.
    Matches run_orchestration / run_pipeline.
    """
    charged: list[Any] = []
    monkeypatch.setattr(ds, "_record_delegation", lambda *a, **k: charged.append(a))
    _fake_parallel(monkeypatch)
    ds._run_agent_graph(
        nodes=[
            {"id": "a", "prompt": "x"},
            {"id": "b", "prompt": "y", "depends_on": ["a"]},
            {"id": "c", "prompt": "z", "depends_on": ["b"]},
        ]
    )
    assert len(charged) == 1, f"3-node graph charged {len(charged)} delegations"


def test_exhausted_turn_cap_refuses_the_graph(monkeypatch: Any) -> None:
    seen = _fake_parallel(monkeypatch)
    monkeypatch.setattr(ds, "_check_absolute_cap", lambda *a, **k: (99, False))
    r = ds._run_agent_graph(nodes=[{"id": "a", "prompt": "x"}])
    assert r["ok"] is False
    assert "exhausted" in r["error"]
    assert seen["batches"] == [], "spawned despite an exhausted cap"

