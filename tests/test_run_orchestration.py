"""Deterministic orchestration loop (``run_orchestration``).

Fan out N workers per round, split + dedupe findings, loop until dry (or the
spawn budget runs out), optionally vote-verify each finding. The control flow
is code, so it's deterministic and unit-testable: the loop/dedup logic is
tested by mocking the two sub-skills; the budget gate is tested end-to-end
through the real ``_call_agent_parallel`` with a mocked ``call_subagent``.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from runtime.execution.suckers import delegation_budget as db
from runtime.execution.suckers import delegation_skills as ds


@pytest.fixture(autouse=True)
def _reset_budget_state():
    from runtime.execution.subagents.bridge import (
        set_sub_agent_runner,
        set_subagent_registry,
    )

    db._TURN_DELEGATIONS.clear()
    db._TURN_FAILED_FINGERPRINTS.clear()
    set_sub_agent_runner(None)
    set_subagent_registry(None)
    yield
    db._TURN_DELEGATIONS.clear()
    db._TURN_FAILED_FINGERPRINTS.clear()
    set_sub_agent_runner(None)
    set_subagent_registry(None)


# ── pure helpers ─────────────────────────────────────────────────


def test_split_findings_strips_markers_keeps_content_digits():
    # bullets + space-delimited enumerators ("2.", "(3)") stripped; NONE dropped
    assert ds._split_findings("- a\n2. b\n\nNONE\n* c\n(3) d") == [
        "a",
        "b",
        "c",
        "d",
    ]
    assert ds._split_findings("nothing\nN/A") == []
    # a finding whose CONTENT starts with a number must survive intact — the
    # marker regex requires a space after the enumerator, so a decimal or a
    # leading count is never mangled.
    assert ds._split_findings("3 retries observed\n10ms latency\n3.14s p99") == [
        "3 retries observed",
        "10ms latency",
        "3.14s p99",
    ]


def test_split_findings_caps_per_worker():
    many = "\n".join(f"item {i}" for i in range(100))
    assert len(ds._split_findings(many)) == ds._ORCH_MAX_FINDINGS_PER_WORKER


def test_split_findings_drops_markdown_noise():
    # the exact noise shape a real model emitted around its list (observed live):
    # a horizontal rule, a bold section label, an ATX heading.
    raw = (
        "---\n"
        "**Critical issues**\n"
        "## Heading\n"
        '`parseInt("")` returns NaN with no exception\n'
        "**Always** validate the radix argument\n"  # bold WORD inside → kept
        "*note*\n"  # whole-line emphasis label → dropped
    )
    assert ds._split_findings(raw) == [
        '`parseInt("")` returns NaN with no exception',
        "**Always** validate the radix argument",
    ]


def test_dedupe_findings_normalises_and_tracks_seen():
    seen: set[str] = set()
    assert ds._dedupe_findings(["A", "a ", "B"], seen) == ["A", "B"]
    assert ds._dedupe_findings(["B", "C"], seen) == ["C"]


def test_coerce_roles():
    assert ds._coerce_roles("researcher") == ["researcher"]
    assert ds._coerce_roles(["a", "b"]) == ["a", "b"]
    assert ds._coerce_roles(["a", "a", "b"]) == ["a", "b"]  # dedupe, order kept
    assert ds._coerce_roles([]) == ["researcher"]
    assert ds._coerce_roles(None) == ["researcher"]  # None is not a role
    assert ds._coerce_roles(["", "  ", "x"]) == ["x"]


# ── loop logic (sub-skills mocked) ───────────────────────────────


def _fake_parallel_seq(rounds_outputs: list[list[str]]):
    calls = {"i": 0}

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        idx = calls["i"]
        calls["i"] += 1
        outs = rounds_outputs[idx] if idx < len(rounds_outputs) else []
        return {
            "ok": True,
            "successes": [{"output": o, "agent_id": "researcher"} for o in outs],
            "failures": [],
            "success_count": len(outs),
        }

    return fake


def test_two_rounds_collect_union(monkeypatch):
    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["a\nb"], ["c"]]))
    r = ds._run_orchestration(goal="g", n=1, rounds=2, patience=2)
    assert r["collected"] == ["a", "b", "c"]
    assert r["rounds_run"] == 2
    assert r["stopped_reason"] == "rounds"


def test_dry_stop(monkeypatch):
    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["a"], [], ["c"]]))
    r = ds._run_orchestration(goal="g", n=1, rounds=3, patience=0)
    assert r["collected"] == ["a"]
    assert r["rounds_run"] == 2
    assert r["stopped_reason"] == "dry"


def test_subagent_failures_are_reported_not_masked(monkeypatch):
    """A total sub-agent crash must surface in the return — not be swallowed
    into ``stopped_reason == "dry"`` / ``count == 0`` (the masking that made a
    real all-crash read as "nothing found")."""

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "successes": [],
            "failures": [
                {
                    "role": "reviewer",
                    "agent_id": "reviewer",
                    "error": "exceeded round cap (5) without converging",
                    "error_type": "round_cap_exceeded",
                }
            ],
            "success_count": 0,
        }

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    r = ds._run_orchestration(goal="g", n=1, rounds=2, patience=1)
    assert r["collected"] == []
    assert r["count"] == 0
    assert r["stopped_reason"] == "dry"  # loop-level reason unchanged
    # two rounds, one failure each → raw count 2, de-duplicated to one summary
    assert r["failure_count"] == 2
    assert r["failures"] == [
        {
            "role": "reviewer",
            "error": "exceeded round cap (5) without converging",
            "error_type": "round_cap_exceeded",
        }
    ]
    assert "SUBAGENT FAILURES" in r["note"]
    assert "round cap" in r["note"]


def test_no_failures_leaves_failure_fields_empty(monkeypatch):
    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["a"]]))
    r = ds._run_orchestration(goal="g", n=1, rounds=1)
    assert r["failure_count"] == 0
    assert r["failures"] == []
    assert r["note"] == ""


def test_dedupe_across_rounds(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_call_agent_parallel",
        _fake_parallel_seq([["a\nb"], ["a\nb"], ["c"]]),
    )
    r = ds._run_orchestration(goal="g", n=1, rounds=3, patience=1)
    assert r["collected"] == ["a", "b", "c"]
    assert r["fresh_per_round"] == [2, 0, 1]
    assert r["rounds_run"] == 3


def test_heterogeneous_roles_rotate_across_workers(monkeypatch):
    captured: dict[str, Any] = {}

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        captured["specs"] = specs
        return {
            "ok": True,
            "successes": [{"output": "x", "agent_id": "r"}],
            "success_count": 1,
        }

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    ds._run_orchestration(
        goal="g",
        agent_id=["researcher", "explorer", "critic"],
        n=3,
        rounds=1,
    )
    assert [s["agent_id"] for s in captured["specs"]] == [
        "researcher",
        "explorer",
        "critic",
    ]


def test_roles_rotate_when_workers_exceed_roles(monkeypatch):
    captured: dict[str, Any] = {}

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        captured["specs"] = specs
        return {"ok": True, "successes": [{"output": "x"}], "success_count": 1}

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    # ["a","a","b"] dedupes to [a,b]; n=3 rotates a, b, a
    ds._run_orchestration(goal="g", agent_id=["a", "a", "b"], n=3, rounds=1)
    assert [s["agent_id"] for s in captured["specs"]] == ["a", "b", "a"]


def test_verify_drops_minority(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_call_agent_parallel",
        _fake_parallel_seq([["keep-me\ndrop-me"]]),
    )

    def fake_vote(question: str = "", **_kw: Any) -> dict[str, Any]:
        verdict = "drop" if "drop-me" in question else "keep"
        return {"ok": True, "verdict": verdict}

    monkeypatch.setattr(ds, "_call_agent_vote", fake_vote)
    r = ds._run_orchestration(goal="g", n=1, rounds=1, verify=True, max_spawns=20)
    assert r["collected"] == ["keep-me", "drop-me"]
    assert r["confirmed"] == ["keep-me"]
    assert r["verified"] is True


def test_verify_budget_limits_how_many_checked(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_call_agent_parallel",
        _fake_parallel_seq([["a\nb\nc\nd"]]),
    )
    calls = {"n": 0}
    lock = threading.Lock()

    def fake_vote(question: str = "", **_kw: Any) -> dict[str, Any]:
        with lock:
            calls["n"] += 1
        return {"ok": True, "verdict": "keep"}

    monkeypatch.setattr(ds, "_call_agent_vote", fake_vote)
    # max_spawns=7 → affordable = 7 // 3 = 2 findings verified; the other 2 are
    # kept but flagged unverified (deterministic which two: the first two).
    r = ds._run_orchestration(goal="g", n=1, rounds=1, verify=True, max_spawns=7)
    assert calls["n"] == 2
    assert r["unverified"] == 2
    assert r["count"] == 4  # all kept (2 verified-keep + 2 unverified)


def test_parallel_verify_preserves_order(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_call_agent_parallel",
        _fake_parallel_seq([["keep1\ndrop1\nkeep2"]]),
    )

    def fake_vote(question: str = "", **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "verdict": "drop" if "drop1" in question else "keep"}

    monkeypatch.setattr(ds, "_call_agent_vote", fake_vote)
    r = ds._run_orchestration(goal="g", n=1, rounds=1, verify=True, max_spawns=30)
    assert r["collected"] == ["keep1", "drop1", "keep2"]
    assert r["confirmed"] == ["keep1", "keep2"]  # order kept, drop1 removed


def test_missing_goal_errors():
    r = ds._run_orchestration(goal="")
    assert r["ok"] is False
    assert "required" in (r["error"] or "")


def test_total_findings_cap(monkeypatch):
    calls = {"i": 0}

    def fake(specs: Any = None, **_kw: Any) -> dict[str, Any]:
        rnd = calls["i"]
        calls["i"] += 1
        out = "\n".join(f"r{rnd}-item{j}" for j in range(50))  # 50 fresh/round
        return {
            "ok": True,
            "successes": [{"output": out, "agent_id": "researcher"}],
            "success_count": 1,
        }

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    r = ds._run_orchestration(goal="g", n=1, rounds=10, patience=3)
    assert r["count"] == ds._ORCH_MAX_FINDINGS_TOTAL
    assert r["stopped_reason"] == "cap"


# ── budget gate (real _call_agent_parallel, mocked call_subagent) ─


def test_budget_stops_the_loop(monkeypatch):
    counter = {"i": 0}
    lock = threading.Lock()

    def fake_cs(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        with lock:
            counter["i"] += 1
            i = counter["i"]
        return {"agent_id": agent_id, "output": f"finding-{i}", "success": True, "error": None}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_cs)
    monkeypatch.setattr("runtime.execution.subagents.bridge.call_subagent", fake_cs)

    # max_spawns=4, n=3: round1 charges 3 (room), round2 charges 3 (3<4 room),
    # round3 has no room → budget-stop. Every finding is unique so it never
    # dry-stops first.
    r = ds._run_orchestration(goal="g", n=3, rounds=5, patience=3, max_spawns=4)
    assert r["stopped_reason"] == "budget"
    assert r["rounds_run"] == 2
    assert r["budget_used"] >= 4


# ── full-stack composition (real parallel + real vote + real envelope) ─


def test_full_stack_find_and_verify(monkeypatch):
    """run_orchestration → real _call_agent_parallel → real _call_agent_vote →
    real orchestration_budget_scope, mocking ONLY call_subagent at the bottom.
    Proves the three pieces built this session actually compose: workers find
    two candidates, the vote panel keeps the strong one and drops the weak."""

    def fake_cs(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        # The ballot prompt is keyed off its panel framing (not the literal
        # "VERDICT", which moved into the JSON-schema instruction that the real
        # call_subagent — mocked away here — would append).
        if "voter on a panel" in prompt:  # a ballot from call_agent_vote
            verdict = "drop" if "weak finding" in prompt else "keep"
            out = f"VERDICT: {verdict}\nREASON: test"
        else:  # a finder prompt
            out = "strong finding\nweak finding"
        return {
            "agent_id": agent_id,
            "output": out,
            "success": True,
            "error": None,
            "codename": "X",
        }

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_cs)
    monkeypatch.setattr("runtime.execution.subagents.bridge.call_subagent", fake_cs)

    r = ds._run_orchestration(
        goal="enumerate findings",
        n=2,
        rounds=1,
        verify=True,
        max_spawns=20,
    )

    assert r["ok"] is True
    assert r["collected"] == ["strong finding", "weak finding"]
    # the vote gate dropped the weak one
    assert r["confirmed"] == ["strong finding"]
    assert r["verified"] is True
    # 2 finders + 2 findings * 3 voters = 8 spawns charged to the one envelope
    assert r["budget_used"] == 8


def test_orchestration_consumes_structured_findings(monkeypatch):
    """Finders return a schema-validated ``{"findings": [...]}`` array; the loop
    consumes the structured list rather than newline-splitting the text. A
    finding that contains its own newline survives intact — line-splitting would
    have shredded it into two."""

    def fake_cs(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        parsed = {"findings": ["multi\nline finding", "second finding"]}
        return {
            "agent_id": agent_id,
            "output": json.dumps(parsed),
            "parsed": parsed,
            "schema_ok": True,
            "success": True,
            "error": None,
            "codename": "X",
        }

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_cs)
    monkeypatch.setattr("runtime.execution.subagents.bridge.call_subagent", fake_cs)

    r = ds._run_orchestration(
        goal="enumerate findings",
        n=1,
        rounds=1,
        verify=False,
        max_spawns=10,
    )
    assert r["ok"] is True
    # structured array preserved verbatim (the embedded newline is NOT split)
    assert r["collected"] == ["multi\nline finding", "second finding"]


def test_orchestration_falls_back_to_line_split(monkeypatch):
    """When a finder reply has no ``parsed`` (schema disabled or failed), the
    loop still parses one-finding-per-line — no worse than before."""

    def fake_cs(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        return {
            "agent_id": agent_id,
            "output": "alpha\nbeta",  # plain text, no parsed key
            "success": True,
            "error": None,
            "codename": "X",
        }

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_cs)
    monkeypatch.setattr("runtime.execution.subagents.bridge.call_subagent", fake_cs)

    r = ds._run_orchestration(
        goal="enumerate findings",
        n=1,
        rounds=1,
        verify=False,
        max_spawns=10,
    )
    assert r["ok"] is True
    assert r["collected"] == ["alpha", "beta"]


# ── synthesis (P1: fan-out → vote-gate → SYNTHESIZE) ──────────────


def _fake_parallel_split(finder_outputs, synth_output):
    """Fake that returns ``finder_outputs`` for a discovery spec and
    ``synth_output`` for the closing synthesizer (detected by its prompt)."""
    state = {"round": 0, "synth_calls": 0}

    def fake(specs=None, **_kw):
        spec = (specs or [{}])[0]
        prompt = str(spec.get("prompt", ""))
        if "CONFIRMED FINDINGS" in prompt:  # the synthesizer
            state["synth_calls"] += 1
            return {
                "ok": True,
                "successes": [{"output": synth_output, "agent_id": "researcher"}],
                "success_count": 1,
            }
        idx = state["round"]
        state["round"] += 1
        outs = finder_outputs[idx] if idx < len(finder_outputs) else []
        return {
            "ok": True,
            "successes": [{"output": o, "agent_id": "researcher"} for o in outs],
            "success_count": len(outs),
        }

    fake.state = state
    return fake


def test_synthesize_folds_findings_into_one_answer(monkeypatch):
    fake = _fake_parallel_split([["alpha\nbeta"]], "SYNTH: alpha + beta")
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    r = ds._run_orchestration(goal="g", n=1, rounds=1, synthesize=True, max_spawns=20)
    assert r["collected"] == ["alpha", "beta"]
    assert r["synthesis"] == "SYNTH: alpha + beta"
    assert fake.state["synth_calls"] == 1


def test_synthesizer_prompt_carries_the_confirmed_findings(monkeypatch):
    seen = {}

    def fake(specs=None, **_kw):
        prompt = str((specs or [{}])[0].get("prompt", ""))
        if "CONFIRMED FINDINGS" in prompt:
            seen["prompt"] = prompt
            return {"ok": True, "successes": [{"output": "s"}], "success_count": 1}
        return {"ok": True, "successes": [{"output": "alpha\nbeta"}], "success_count": 1}

    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    ds._run_orchestration(goal="my-goal", n=1, rounds=1, synthesize=True, max_spawns=20)
    assert "alpha" in seen["prompt"] and "beta" in seen["prompt"]
    assert "my-goal" in seen["prompt"]


def test_no_synthesis_by_default(monkeypatch):
    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["alpha\nbeta"]]))
    r = ds._run_orchestration(goal="g", n=1, rounds=1)
    assert r["synthesis"] == ""


def test_synthesize_accepts_string_true(monkeypatch):
    fake = _fake_parallel_split([["x"]], "S")
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    r = ds._run_orchestration(goal="g", n=1, rounds=1, synthesize="true", max_spawns=20)
    assert fake.state["synth_calls"] == 1
    assert r["synthesis"] == "S"


def test_synthesize_skipped_when_nothing_confirmed(monkeypatch):
    fake = _fake_parallel_split([[]], "S")  # finders find nothing
    monkeypatch.setattr(ds, "_call_agent_parallel", fake)
    r = ds._run_orchestration(goal="g", n=1, rounds=1, synthesize=True, patience=0)
    assert r["confirmed"] == []
    assert fake.state["synth_calls"] == 0
    assert r["synthesis"] == ""


# ── blackboard coordination (harness-enforced stigmergy) ──────────


def test_orchestration_seeds_and_publishes_on_blackboard(monkeypatch):
    from runtime.memory.runtime_state.blackboard import get_blackboard

    turn = "turn-bb-coord-1"
    monkeypatch.setattr(ds, "_resolve_session_and_turn", lambda: (None, turn))
    fp = ds._compute_fingerprint("run_orchestration", "g")
    key = f"orchestration.findings.{fp}"
    get_blackboard(turn).write(key, ["prior-finding"], writer="sibling")

    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["new-finding"]]))
    r = ds._run_orchestration(goal="g", n=1, rounds=1, patience=2)

    assert r["shared"] is True
    assert r["inherited"] == 1
    assert "prior-finding" in r["collected"]  # seeded from the shared board
    assert "new-finding" in r["collected"]  # newly discovered
    # the union is republished for the rest of the turn
    assert set(get_blackboard(turn).read(key)) >= {"prior-finding", "new-finding"}


def test_second_orchestration_builds_on_the_first(monkeypatch):
    turn = "turn-bb-coord-2"
    monkeypatch.setattr(ds, "_resolve_session_and_turn", lambda: (None, turn))

    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["A"]]))
    r1 = ds._run_orchestration(goal="shared-goal", n=1, rounds=1, patience=2)
    assert r1["inherited"] == 0 and "A" in r1["collected"]

    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["B"]]))
    r2 = ds._run_orchestration(goal="shared-goal", n=1, rounds=1, patience=2)
    assert r2["inherited"] == 1  # inherited A from the first run
    assert "A" in r2["collected"] and "B" in r2["collected"]


def test_no_blackboard_outside_a_turn_is_a_noop(monkeypatch):
    # no Session → no turn → no board; behaviour identical to before
    monkeypatch.setattr(ds, "_resolve_session_and_turn", lambda: (None, None))
    monkeypatch.setattr(ds, "_call_agent_parallel", _fake_parallel_seq([["x"]]))
    r = ds._run_orchestration(goal="g", n=1, rounds=1, patience=2)
    assert r["shared"] is False
    assert r["inherited"] == 0
    assert r["collected"] == ["x"]

