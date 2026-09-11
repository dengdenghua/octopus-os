"""Tests for runtime.memory.learning.soul_holdout + deep_evolve gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.memory import soul_holdout as sh
from runtime.memory.learning.soul_holdout import (
    GatePolicy,
    HoldoutEntry,
    HoldoutResult,
    auto_seed_holdout,
    evaluate_against_holdout,
    gate,
    load_holdout,
)

# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``_project_root`` in soul_holdout to a tmp dir."""
    monkeypatch.setattr(sh, "_project_root", lambda: tmp_path)
    return tmp_path


def _write_score(
    root: Path,
    agent_id: str,
    *,
    score: float,
    thread_id: str,
) -> None:
    p = root / "agents" / agent_id / "agent-core" / ".scores.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": "2026-05-30T00:00:00",
                    "agent_id": agent_id,
                    "score": score,
                    "reason": "success",
                    "soul_hash": "deadbeef",
                    "rounds": 1,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "duration_ms": 0,
                    "thread_id": thread_id,
                    "turn_id": "",
                }
            )
            + "\n"
        )


def _write_session(
    root: Path,
    agent_id: str,
    thread_id: str,
    *,
    prompt: str,
    reply: str,
) -> None:
    p = root / "agents" / agent_id / "sessions" / f"{thread_id}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "op": "upsert",
        "thread_id": thread_id,
        "thread": {
            "values": {
                "messages": [
                    {"type": "human", "content": prompt},
                    {"type": "ai", "content": reply},
                ],
            },
        },
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


# ─────────────────────────────────────────────────────────────────────
# load_holdout / auto_seed
# ─────────────────────────────────────────────────────────────────────


def test_load_holdout_missing_file_no_journal_returns_empty(
    tmp_root: Path,
) -> None:
    assert load_holdout("ghost") == []


def test_load_holdout_missing_file_auto_seeds_from_high_scoring_journal(
    tmp_root: Path,
) -> None:
    aid = "general"
    # Two high-scoring turns + one low-scoring (should be skipped).
    _write_score(tmp_root, aid, score=0.95, thread_id="t1")
    _write_score(tmp_root, aid, score=0.88, thread_id="t2")
    _write_score(tmp_root, aid, score=0.5, thread_id="t3")
    _write_session(
        tmp_root,
        aid,
        "t1",
        prompt="Hello world",
        reply="The answer to that question is forty-two.",
    )
    _write_session(
        tmp_root,
        aid,
        "t2",
        prompt="What is 2+2",
        reply="2+2 equals 4 in standard arithmetic.",
    )
    _write_session(
        tmp_root,
        aid,
        "t3",
        prompt="Bad turn",
        reply="Should never seed",
    )

    entries = load_holdout(aid)
    assert len(entries) == 2
    prompts = {e.prompt for e in entries}
    assert "Hello world" in prompts
    assert "What is 2+2" in prompts
    # Low-scoring turn must not appear.
    assert "Bad turn" not in prompts
    # File now exists on disk.
    assert (tmp_root / "data" / "soul_holdout" / f"{aid}.jsonl").exists()


def test_auto_seed_holdout_writes_file_and_returns_count(
    tmp_root: Path,
) -> None:
    aid = "agent_x"
    _write_score(tmp_root, aid, score=0.9, thread_id="ta")
    _write_session(
        tmp_root,
        aid,
        "ta",
        prompt="Solve X",
        reply="Solution: X equals 7.",
    )
    n = auto_seed_holdout(aid)
    assert n == 1
    rows = (
        (tmp_root / "data" / "soul_holdout" / f"{aid}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(rows) == 1
    obj = json.loads(rows[0])
    assert obj["prompt"] == "Solve X"
    assert "Solution" in obj["expected_signal"]


def test_auto_seed_holdout_empty_when_no_journal(tmp_root: Path) -> None:
    assert auto_seed_holdout("nobody") == 0


def test_load_holdout_reads_existing_file(tmp_root: Path) -> None:
    aid = "preset"
    p = tmp_root / "data" / "soul_holdout" / f"{aid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"prompt": "p1", "expected_signal": "ok", "weight": 2.0}) + "\n",
        encoding="utf-8",
    )
    entries = load_holdout(aid)
    assert len(entries) == 1
    assert entries[0].prompt == "p1"
    assert entries[0].expected_signal == "ok"
    assert entries[0].weight == 2.0


# ─────────────────────────────────────────────────────────────────────
# evaluate_against_holdout
# ─────────────────────────────────────────────────────────────────────


def test_evaluate_scores_one_when_signal_in_reply(tmp_root: Path) -> None:
    entries = [HoldoutEntry(prompt="q1", expected_signal="hello")]
    runner = lambda soul, prompt: "hello there"  # noqa: E731
    res = evaluate_against_holdout("a", "soul", runner, entries)
    assert res.pass_rate == 1.0
    assert res.detail[0]["score"] == 1.0
    assert res.detail[0]["output"] == "hello there"


def test_evaluate_scores_zero_when_signal_missing(tmp_root: Path) -> None:
    entries = [HoldoutEntry(prompt="q1", expected_signal="hello")]
    runner = lambda soul, prompt: "goodbye"  # noqa: E731
    res = evaluate_against_holdout("a", "soul", runner, entries)
    assert res.pass_rate == 0.0
    assert res.detail[0]["score"] == 0.0


def test_evaluate_supports_regex_signal(tmp_root: Path) -> None:
    entries = [HoldoutEntry(prompt="q1", expected_signal=r"\d{3,}")]
    runner = lambda soul, prompt: "answer is 12345 over here"  # noqa: E731
    res = evaluate_against_holdout("a", "soul", runner, entries)
    assert res.pass_rate == 1.0


def test_evaluate_aggregates_weighted_pass_rate(tmp_root: Path) -> None:
    entries = [
        HoldoutEntry(prompt="q1", expected_signal="ok", weight=3.0),
        HoldoutEntry(prompt="q2", expected_signal="never", weight=1.0),
    ]

    def runner(soul: str, prompt: str) -> str:
        return "ok yes" if prompt == "q1" else "no good"

    res = evaluate_against_holdout("a", "soul", runner, entries)
    # weighted: (1*3 + 0*1) / 4 = 0.75
    assert res.pass_rate == pytest.approx(0.75)


# ─────────────────────────────────────────────────────────────────────
# gate
# ─────────────────────────────────────────────────────────────────────


def test_gate_allows_when_new_meets_floor_and_within_tolerance() -> None:
    new = HoldoutResult(pass_rate=0.8, detail=[])
    old = HoldoutResult(pass_rate=0.82, detail=[])
    allowed, reason = gate(new, old, GatePolicy())
    assert allowed is True
    assert "ok" in reason


def test_gate_rejects_when_below_floor() -> None:
    new = HoldoutResult(pass_rate=0.5, detail=[])
    old = HoldoutResult(pass_rate=0.5, detail=[])
    allowed, reason = gate(new, old, GatePolicy(floor=0.6))
    assert allowed is False
    assert "floor" in reason


def test_gate_rejects_when_regression_exceeds_tolerance() -> None:
    new = HoldoutResult(pass_rate=0.7, detail=[])
    old = HoldoutResult(pass_rate=0.9, detail=[])
    allowed, reason = gate(
        new,
        old,
        GatePolicy(floor=0.6, regression_tolerance=0.05),
    )
    assert allowed is False
    assert "regress" in reason


def test_gate_allows_tiny_regression_within_tolerance() -> None:
    new = HoldoutResult(pass_rate=0.78, detail=[])
    old = HoldoutResult(pass_rate=0.80, detail=[])
    allowed, _reason = gate(
        new,
        old,
        GatePolicy(floor=0.6, regression_tolerance=0.05),
    )
    assert allowed is True


# ─────────────────────────────────────────────────────────────────────
# deep_evolve integration · gate refuses on regression
# ─────────────────────────────────────────────────────────────────────


def test_deep_evolve_refuses_on_holdout_regression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regressing runner must cause deep_evolve(dry_run=False) to skip apply."""
    from runtime.memory import deep_evolution as dev

    aid = "general"

    # Redirect both modules' project root to tmp_path so files land there.
    from runtime.memory import turn_scoring as ts

    monkeypatch.setattr(sh, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(ts, "_project_root", lambda: tmp_path)

    # Seed agent SOUL.md and a scores file.
    soul_dir = tmp_path / "agents" / aid / "agent-core"
    soul_dir.mkdir(parents=True, exist_ok=True)
    (soul_dir / "SOUL.md").write_text(
        "# soul\nbe helpful\n",
        encoding="utf-8",
    )
    # Need scored turns so deep_evolve doesn't bail early.
    _write_score(tmp_path, aid, score=1.0, thread_id="t1")

    # Seed a holdout file directly so we don't depend on auto-seed.
    hpath = tmp_path / "data" / "soul_holdout" / f"{aid}.jsonl"
    hpath.parent.mkdir(parents=True, exist_ok=True)
    hpath.write_text(
        json.dumps(
            {
                "prompt": "p1",
                "expected_signal": "MAGIC",
                "weight": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # Force the evolve router to be "wired" so deep_evolve proceeds.
    from runtime.platform.process.service_provider import get_provider

    get_provider().register_instance("evolve_router", object())
    get_provider().register_instance("evolve_default_model", "stub-model")

    # Stub the propose + judge LLM JSON calls to return an apply-add_lesson.
    call_log: list[str] = []

    def fake_call_json(*, system: str, user: str, **kw):
        if "self-improvement proposer" in system:
            call_log.append("propose")
            return (
                {
                    "candidates": [
                        {
                            "id": "c1",
                            "kind": "add_lesson",
                            "lesson": "always cite sources",
                            "tag": "quality",
                            "predicted_impact": "+",
                            "risk": "low",
                        }
                    ]
                },
                {"input_tokens": 1, "output_tokens": 1},
            )
        if "impact judge" in system:
            call_log.append("judge")
            return (
                {
                    "candidate_id": "c1",
                    "predicted_avg_score_delta": 0.2,
                    "predicted_failure_mode_change": "none",
                    "would_help_count": 5,
                    "would_hurt_count": 0,
                    "confidence": "high",
                    "verdict": "apply",
                },
                {"input_tokens": 1, "output_tokens": 1},
            )
        return None, {"error": "unexpected system prompt"}

    monkeypatch.setattr(dev._EVOLVE_CALLER, "call_json", fake_call_json)

    # Inject a fake holdout runner that NEVER produces "MAGIC" for the new
    # SOUL (which contains "always cite sources") · old SOUL hits "MAGIC"
    # so we get a clear regression.
    def fake_runner_factory(*, model=None):
        def runner(soul: str, prompt: str) -> str:
            if "always cite sources" in soul:
                return "no signal here"
            return "the MAGIC word"

        return runner

    monkeypatch.setattr(dev, "_build_holdout_runner", fake_runner_factory)

    # The apply path calls _update_soul; spy to make sure it is NOT called.
    apply_spy: list[tuple] = []

    def stub_update_soul(*, lesson: str, tag: str) -> dict:
        apply_spy.append((lesson, tag))
        return {"ok": True}

    def stub_revert_soul(**kw) -> dict:
        apply_spy.append(("revert", kw))
        return {"ok": True}

    import runtime.execution.suckers.memory_skills as mskills

    monkeypatch.setattr(mskills, "_update_soul", stub_update_soul)
    monkeypatch.setattr(mskills, "_revert_soul", stub_revert_soul)

    out = dev.deep_evolve(agent_id=aid, dry_run=False, max_rounds=1)
    assert out["ok"] is True
    assert "propose" in call_log and "judge" in call_log
    # Apply must have been refused — no actual SOUL mutation.
    assert apply_spy == []
    # The audit record should show holdout_regression on the round.
    rounds = out["audit"]
    assert len(rounds) == 1
    applied = rounds[0].get("applied") or {}
    assert applied.get("error") == "holdout_regression"
    assert applied.get("applied") is False
    assert applied.get("old_pass_rate") == 1.0
    assert applied.get("new_pass_rate") == 0.0
