"""Verdict-gated repair loop — pure control flow + the wired skill handler."""

from __future__ import annotations

from runtime.execution.suckers import delegation_skills as ds
from runtime.execution.suckers.verdict_repair import (
    Verdict,
    run_verdict_repair,
)

# ── pure primitive: produce -> judge -> rewrite -> re-judge ──────────


def _producer(outputs):
    """produce() returning successive outputs; records (attempt, critique)."""
    seen: list[tuple[int, str]] = []

    def produce(attempt, critique):
        seen.append((attempt, critique))
        return outputs[min(attempt, len(outputs) - 1)]

    return produce, seen


def _judge_seq(verdicts):
    """judge() returning successive Verdicts; records the judged outputs."""
    seen: list[str] = []

    def judge(output):
        v = verdicts[min(len(seen), len(verdicts) - 1)]
        seen.append(output)
        return v

    return judge, seen


def test_passes_on_first_attempt_no_repair() -> None:
    produce, pseen = _producer(["answer"])
    judge, _ = _judge_seq([Verdict(passed=True, label="pass")])
    res = run_verdict_repair(produce=produce, judge=judge, max_repairs=2)
    assert res.passed is True
    assert res.attempts == 1
    assert res.repaired is False
    assert res.output == "answer"
    assert pseen == [(0, "")]  # produced once, no critique


def test_repairs_then_passes_and_feeds_critique_forward() -> None:
    produce, pseen = _producer(["v0", "v1"])
    judge, jseen = _judge_seq(
        [Verdict(passed=False, label="fail", critique="missing edge case"), Verdict(passed=True)]
    )
    res = run_verdict_repair(produce=produce, judge=judge, max_repairs=2)
    assert res.passed is True
    assert res.repaired is True  # only passed AFTER a repair
    assert res.attempts == 2
    assert res.output == "v1"
    # the critique from round 0 was fed into produce for round 1
    assert pseen == [(0, ""), (1, "missing edge case")]
    assert jseen == ["v0", "v1"]


def test_bounded_when_never_passes() -> None:
    produce, pseen = _producer(["x"])
    judge, _ = _judge_seq([Verdict(passed=False, label="fail", critique="nope")])
    res = run_verdict_repair(produce=produce, judge=judge, max_repairs=2)
    assert res.passed is False
    assert res.attempts == 3  # initial + 2 repairs, then stop
    assert len(pseen) == 3


def test_max_repairs_zero_is_one_shot() -> None:
    produce, pseen = _producer(["x"])
    judge, _ = _judge_seq([Verdict(passed=False, label="fail")])
    res = run_verdict_repair(produce=produce, judge=judge, max_repairs=0)
    assert res.attempts == 1
    assert res.passed is False
    assert len(pseen) == 1  # no repair attempted


# ── wired skill handler over fake sub-agent + vote ───────────────────


def _fake_parallel(outputs):
    calls: list[str] = []

    def f(*, specs, **_kw):
        calls.append(specs[0]["prompt"])
        out = outputs[min(len(calls) - 1, len(outputs) - 1)]
        return {"ok": True, "successes": [{"output": out}], "failures": [], "status_summary": ""}

    return f, calls


def _fake_vote(verdicts):
    calls: list[str] = []

    def f(*, question, n, choices, **_kw):
        label, reason = verdicts[min(len(calls), len(verdicts) - 1)]
        calls.append(question)
        votes = [{"verdict": label, "reason": reason}]
        return {"ok": True, "verdict": label, "confidence": 0.9, "votes": votes}

    return f, calls


def test_handler_missing_task_errors() -> None:
    r = ds._run_verdict_repair(task="")
    assert r["ok"] is False
    assert "required" in r["error"]


def test_handler_passes_first_try(monkeypatch) -> None:
    par, pcalls = _fake_parallel(["the answer"])
    vote, _ = _fake_vote([("pass", "")])
    monkeypatch.setattr(ds, "_call_agent_parallel", par)
    monkeypatch.setattr(ds, "_call_agent_vote", vote)
    r = ds._run_verdict_repair(task="do X", judge_n=3, max_repairs=2)
    assert r["ok"] is True
    assert r["passed"] is True
    assert r["repaired"] is False
    assert r["attempts"] == 1
    assert r["output"] == "the answer"
    assert len(pcalls) == 1  # one produce, no repair


def test_handler_repairs_then_passes(monkeypatch) -> None:
    par, pcalls = _fake_parallel(["bad answer", "good answer"])
    vote, _ = _fake_vote([("fail", "missing edge case"), ("pass", "")])
    monkeypatch.setattr(ds, "_call_agent_parallel", par)
    monkeypatch.setattr(ds, "_call_agent_vote", vote)
    r = ds._run_verdict_repair(task="do X", max_repairs=2)
    assert r["passed"] is True
    assert r["repaired"] is True
    assert r["attempts"] == 2
    assert r["output"] == "good answer"
    # the corrective re-attempt was handed the reviewer's critique
    assert "missing edge case" in pcalls[1]
    assert len(pcalls) == 2


def test_handler_bounded_when_never_passes(monkeypatch) -> None:
    par, pcalls = _fake_parallel(["x"])
    vote, _ = _fake_vote([("fail", "still wrong")])
    monkeypatch.setattr(ds, "_call_agent_parallel", par)
    monkeypatch.setattr(ds, "_call_agent_vote", vote)
    r = ds._run_verdict_repair(task="do X", max_repairs=2)
    assert r["passed"] is False
    assert r["attempts"] == 3  # initial + 2 repairs
    assert len(pcalls) == 3
    assert r["rounds"][-1]["critique"]  # carries the rejection reason

