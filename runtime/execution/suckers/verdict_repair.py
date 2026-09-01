"""Verdict-gated repair loop — the closed-loop orchestration echo lacked.

echo already had the *pieces* of quality control (react guards, the
``call_agent_vote`` consensus gate, offline repair recipes) but no loop that
**acts on a verdict**: produce → judge → if rejected, *rewrite with the
critique* → judge again, bounded. That's the Orca "PLAN → CRITIQUE → REWRITE"
shape; a FAIL doesn't just block, it drives another attempt.

This module is the pure control flow, parametrised over two callables so it is
unit-testable without any LLM:

  * ``produce(attempt, critique)`` → the result text for this attempt. On the
    first attempt ``critique`` is empty; on a repair it carries the judge's
    reason so the producer can actually fix the cited problems.
  * ``judge(output)`` → a :class:`Verdict`. ``passed`` stops the loop;
    ``critique`` feeds the next ``produce``.

The skill layer (``delegation_skills``) supplies real producers/judges backed
by sub-agents and ``call_agent_vote``; everything quality-relevant lives here
where it can be tested deterministically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# produce(attempt_index, critique) -> output text. attempt 0 is the first try.
Produce = Callable[[int, str], str]


@dataclass(frozen=True)
class Verdict:
    """A judge's ruling on one attempt. ``passed`` ends the loop; ``critique``
    is the reason a failed attempt was rejected — fed into the next produce."""

    passed: bool
    label: str = ""  # free-form, e.g. "pass" / "warn" / "fail"
    critique: str = ""
    confidence: float = 0.0


# judge(output) -> Verdict
Judge = Callable[[str], Verdict]


@dataclass(frozen=True)
class RepairRound:
    attempt: int
    output: str
    verdict: Verdict


@dataclass(frozen=True)
class RepairResult:
    passed: bool
    output: str
    rounds: list[RepairRound] = field(default_factory=list)

    @property
    def attempts(self) -> int:
        return len(self.rounds)

    @property
    def final_verdict(self) -> Verdict | None:
        return self.rounds[-1].verdict if self.rounds else None

    @property
    def repaired(self) -> bool:
        """True when the loop only reached PASS *after* a repair — i.e. the
        verdict gate actually salvaged a first attempt that would have shipped
        broken."""
        return self.passed and self.attempts > 1


def run_verdict_repair(
    *,
    produce: Produce,
    judge: Judge,
    max_repairs: int = 2,
) -> RepairResult:
    """Run ``produce`` then ``judge``; while the verdict is a failure and
    repairs remain, re-``produce`` with the critique and re-``judge``. Stops at
    the first PASS (returning it) or when ``max_repairs`` is exhausted
    (returning the last, still-failing attempt). ``max_repairs == 0`` is a plain
    produce-then-judge with no repair.

    Bounded by construction: at most ``1 + max_repairs`` produce calls and the
    same number of judge calls — a runaway model can't spin the loop forever.
    """
    max_repairs = max(0, int(max_repairs))
    rounds: list[RepairRound] = []

    attempt = 0
    output = produce(attempt, "")
    verdict = judge(output)
    rounds.append(RepairRound(attempt, output, verdict))

    while not verdict.passed and attempt < max_repairs:
        attempt += 1
        output = produce(attempt, verdict.critique)
        verdict = judge(output)
        rounds.append(RepairRound(attempt, output, verdict))

    # The loop stops on the first PASS, so the final attempt IS the accepted one
    # when ``passed``; otherwise it's the best we got.
    return RepairResult(passed=verdict.passed, output=output, rounds=rounds)
