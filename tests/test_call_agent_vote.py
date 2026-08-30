"""Consensus / vote gate for ``call_agent_vote``.

Spawn N independent voters on the SAME question, parse each one's
``VERDICT`` line, and tally a majority (confidence + dissent). The pure
aggregation (``_extract_verdict`` / ``_tally_votes``) is tested directly;
the spawn path is exercised end-to-end with a mocked ``call_subagent``.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from runtime.execution.suckers import delegation_skills as ds


@pytest.fixture(autouse=True)
def _reset_runner_and_budget():
    from runtime.execution.subagents.bridge import (
        set_sub_agent_runner,
        set_subagent_registry,
    )
    from runtime.execution.suckers.delegation_budget import (
        _TURN_DELEGATIONS,
        _TURN_FAILED_FINGERPRINTS,
    )

    set_sub_agent_runner(None)
    set_subagent_registry(None)
    _TURN_DELEGATIONS.clear()
    _TURN_FAILED_FINGERPRINTS.clear()
    yield
    set_sub_agent_runner(None)
    set_subagent_registry(None)
    _TURN_DELEGATIONS.clear()
    _TURN_FAILED_FINGERPRINTS.clear()


def _patch_voters(monkeypatch, outputs: list[str]):
    """Each (concurrent) call_subagent pops the next canned output. Since
    votes are aggregated order-independently, which voter gets which output
    doesn't matter for the tally."""
    lock = threading.Lock()
    box = list(outputs)

    def _fake(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        with lock:
            out = box.pop(0) if box else "VERDICT: abstain"
        return {
            "agent_id": agent_id,
            "output": out,
            "success": True,
            "error": None,
            "codename": "Voter",
        }

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", _fake)
    monkeypatch.setattr(
        "runtime.execution.subagents.bridge.call_subagent",
        _fake,
    )


# ── pure: _coerce_vote_choices ───────────────────────────────────


def test_coerce_choices_forms():
    assert ds._coerce_vote_choices(None) is None
    assert ds._coerce_vote_choices([]) is None
    assert ds._coerce_vote_choices(["yes", "no"]) == ["yes", "no"]
    assert ds._coerce_vote_choices("yes, no") == ["yes", "no"]
    assert ds._coerce_vote_choices("yes|no") == ["yes", "no"]
    # de-dupe (case-insensitive), first spelling wins
    assert ds._coerce_vote_choices(["Yes", "yes", "No"]) == ["Yes", "No"]


# ── pure: _extract_verdict / _normalize_verdict ──────────────────


def test_extract_verdict_parses_verdict_and_reason():
    v, r = ds._extract_verdict("VERDICT: yes\nREASON: it reproduces", ["yes", "no"])
    assert v == "yes"
    assert r == "it reproduces"


def test_extract_verdict_normalises_sentence_to_choice():
    v, _ = ds._extract_verdict("VERDICT: Yes, definitely real", ["yes", "no"])
    assert v == "yes"


def test_extract_verdict_falls_back_to_first_line():
    v, _ = ds._extract_verdict("no — I cannot reproduce it", ["yes", "no"])
    assert v == "no"


def test_extract_verdict_unmappable_is_abstention():
    v, _ = ds._extract_verdict("VERDICT: maybe later", ["yes", "no"])
    assert v == ""


def test_extract_verdict_free_form_keeps_label():
    v, _ = ds._extract_verdict("VERDICT: option-a\nREASON: x", None)
    assert v == "option-a"


# ── pure: _tally_votes ───────────────────────────────────────────


def _votes(*verdicts: str) -> list[dict[str, Any]]:
    return [{"verdict": v, "reason": "", "agent_id": "reviewer"} for v in verdicts]


def test_tally_majority():
    t = ds._tally_votes(_votes("yes", "yes", "no"), ["yes", "no"])
    assert t["verdict"] == "yes"
    assert t["confidence"] == pytest.approx(0.667, abs=0.001)
    assert t["tie"] is False
    assert t["unanimous"] is False
    assert t["tally"] == {"yes": 2, "no": 1}


def test_tally_unanimous():
    t = ds._tally_votes(_votes("yes", "yes", "yes"), ["yes", "no"])
    assert t["verdict"] == "yes"
    assert t["unanimous"] is True
    assert t["confidence"] == 1.0


def test_tally_tie_has_no_verdict():
    t = ds._tally_votes(_votes("yes", "no"), ["yes", "no"])
    assert t["verdict"] is None
    assert t["tie"] is True
    assert t["tie_between"] == ["no", "yes"]


def test_tally_all_abstain():
    t = ds._tally_votes(_votes("", ""), ["yes", "no"])
    assert t["verdict"] is None
    assert t["votes_cast"] == 0
    assert t["abstentions"] == 2
    assert t["confidence"] == 0.0


# ── end-to-end (mocked spawn) ────────────────────────────────────


def test_vote_majority_end_to_end(monkeypatch):
    _patch_voters(
        monkeypatch,
        [
            "VERDICT: yes\nREASON: a",
            "VERDICT: yes\nREASON: b",
            "VERDICT: no\nREASON: c",
        ],
    )
    r = ds._call_agent_vote(question="is the bug real?", n=3, choices=["yes", "no"])
    assert r["ok"] is True
    assert r["verdict"] == "yes"
    assert r["tally"] == {"yes": 2, "no": 1}
    assert r["votes_cast"] == 3
    assert len(r["votes"]) == 3
    assert r["note"].startswith("[majority]")


def test_vote_tie_end_to_end(monkeypatch):
    _patch_voters(
        monkeypatch,
        [
            "VERDICT: yes\nREASON: a",
            "VERDICT: no\nREASON: b",
        ],
    )
    r = ds._call_agent_vote(question="A or B?", n=2, choices=["yes", "no"])
    assert r["ok"] is True  # the vote ran...
    assert r["verdict"] is None  # ...but produced no decision
    assert r["tie"] is True
    assert r["note"].startswith("[no-consensus]")


def test_vote_unanimous_end_to_end(monkeypatch):
    _patch_voters(
        monkeypatch,
        [
            "VERDICT: yes\nREASON: a",
            "VERDICT: yes\nREASON: b",
            "VERDICT: yes\nREASON: c",
        ],
    )
    r = ds._call_agent_vote(question="correct?", n=3, choices=["yes", "no"])
    assert r["verdict"] == "yes"
    assert r["unanimous"] is True
    assert r["note"].startswith("[unanimous]")


def test_vote_missing_question_errors():
    r = ds._call_agent_vote(question="")
    assert r["ok"] is False
    assert "required" in (r["error"] or "")
    assert r["verdict"] is None


def test_vote_reads_schema_parsed(monkeypatch):
    """When call_subagent returns a schema-validated ``parsed`` object, the
    vote trusts the structured fields instead of regex-parsing the text — and
    it passes a JSON Schema down to each voter."""
    seen_schema: list[Any] = []
    lock = threading.Lock()
    box = [
        {"verdict": "yes", "reason": "reproduced"},
        {"verdict": "yes", "reason": "confirmed"},
        {"verdict": "no", "reason": "cannot repro"},
    ]

    def _fake(agent_id: str = "", prompt: str = "", **kw: Any) -> dict[str, Any]:
        with lock:
            seen_schema.append(kw.get("output_schema"))
            parsed = box.pop(0) if box else {"verdict": "abstain"}
        return {
            "agent_id": agent_id,
            "output": json.dumps(parsed),
            "parsed": parsed,
            "schema_ok": True,
            "success": True,
            "error": None,
            "codename": "Voter",
        }

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", _fake)
    monkeypatch.setattr("runtime.execution.subagents.bridge.call_subagent", _fake)

    r = ds._call_agent_vote(question="is the bug real?", n=3, choices=["yes", "no"])
    assert r["verdict"] == "yes"
    assert r["tally"] == {"yes": 2, "no": 1}
    # reason came from the structured field, not a free-text slice
    assert "reproduced" in {v["reason"] for v in r["votes"]}
    # every voter received a JSON Schema ballot
    assert seen_schema and all(
        isinstance(s, dict) and s.get("type") == "object" for s in seen_schema
    )


def test_vote_falls_back_when_no_parsed(monkeypatch):
    """If call_subagent could not validate (no ``parsed`` key), the vote still
    parses the raw VERDICT text rather than hard-abstaining."""
    _patch_voters(
        monkeypatch,
        [
            "VERDICT: yes\nREASON: a",
            "VERDICT: yes\nREASON: b",
        ],
    )
    r = ds._call_agent_vote(question="real?", n=2, choices=["yes", "no"])
    assert r["verdict"] == "yes"
    assert r["votes_cast"] == 2
    assert r["unanimous"] is True


def test_vote_n_is_clamped(monkeypatch):
    # n=99 must clamp to the per-turn cap (5) so a single vote can't blow
    # the whole turn budget.
    seen = {"count": 0}
    lock = threading.Lock()

    def _fake(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        with lock:
            seen["count"] += 1
        return {"agent_id": agent_id, "output": "VERDICT: yes", "success": True, "error": None}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", _fake)
    monkeypatch.setattr("runtime.execution.subagents.bridge.call_subagent", _fake)

    ds._call_agent_vote(question="q", n=99, choices=["yes", "no"])
    assert seen["count"] == ds._VOTE_MAX


def test_vote_panel_uses_distinct_roles(monkeypatch):
    # Independence: N voters must not all be the same persona. The first
    # voter is the requested role, the rest rotate through distinct roles.
    seen: list[str] = []
    lock = threading.Lock()

    def _fake(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        with lock:
            seen.append(agent_id)
        return {"agent_id": agent_id, "output": "VERDICT: yes", "success": True, "error": None}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", _fake)
    monkeypatch.setattr("runtime.execution.subagents.bridge.call_subagent", _fake)

    ds._call_agent_vote(question="q", n=5, choices=["yes", "no"])
    assert len(seen) == 5
    assert len(set(seen)) >= 2  # genuinely distinct personas, not one role x5
    assert set(seen) <= {
        "reviewer",
        "architect",
        "security-review",
        "debugger",
        "researcher",
        "explorer",
    }

