"""Guard false-positive judge — semantic second-pass review.

When ``evaluate_guards`` blocks a Final Answer, the result is recorded
to ``GuardTelemetry``. Some of those firings are real catches; others
are false positives where the guard's regex/heuristic was too eager.
Without distinguishing them, the digest's "magic-number guard fired
50 times this week" tells the prompt evolver nothing useful — was that
50 real bugs or 50 noise hits?

This module wires an LLM judge that reviews each (label, guard_message,
trajectory_excerpt) and returns ``true_positive`` / ``false_positive``
/ ``uncertain``. The judge runs OFFLINE (batched, scheduled) so the
ReAct hot path stays unblocked.

Mirrors the patterns in ``runtime/safety/constitution/llm_judge``:
fail-open, TTL cache, defensive parsing. Different semantics — this
judges guard accuracy, not message safety — but the wiring shape is
deliberately the same so future maintainers see one judge pattern.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

_log = logging.getLogger("runtime.safety.evolution.guard_judge")


if TYPE_CHECKING:  # pragma: no cover
    from runtime.platform.models.llm import ModelRouter


GuardJudgeAction = Literal["true_positive", "false_positive", "uncertain"]


@dataclass(frozen=True)
class GuardJudgeVerdict:
    """Verdict for one guard hit.

    * ``true_positive``  — the guard caught a real problem; agent was
      genuinely about to ship a bad final answer.
    * ``false_positive`` — the guard fired but the agent's answer was
      actually fine; the guard's heuristic is too aggressive.
    * ``uncertain``      — judge couldn't decide (ambiguous trajectory,
      missing context). Treated as "unjudged" by the digest aggregator.
    """

    action: GuardJudgeAction
    reason: str = ""
    confidence: float = 0.0


GuardJudge = Callable[[str, str, str], GuardJudgeVerdict]


def null_guard_judge(
    label: str,
    guard_message: str,
    trajectory_excerpt: str,
) -> GuardJudgeVerdict:
    """Default — always uncertain. Used when no LLM is wired so the
    digest aggregator silently treats every hit as unjudged."""
    return GuardJudgeVerdict(action="uncertain", reason="no_judge_configured")


# ═══════════════════════════════════════════════════════════
# Prompt template · kept here so phrasing stays next to the
# verdict semantics · not buried in app bootstrap.
# ═══════════════════════════════════════════════════════════

GUARD_JUDGE_PROMPT = """\
You are the Guard Accuracy Judge. A safety guard just blocked an
agent's Final Answer. Your job: was the block CORRECT (the agent was
about to do something wrong) or a FALSE POSITIVE (the agent was fine,
the guard was too aggressive)?

Guard label: {label}

Guard message (what the guard told the agent):
<guard_message>
{guard_message}
</guard_message>

Recent trajectory excerpt (what the agent actually did):
<trajectory>
{trajectory_excerpt}
</trajectory>

Reply with EXACTLY one line in this format:
VERDICT: <true_positive|false_positive|uncertain>
REASON: <one short sentence, < 80 chars>
CONFIDENCE: <0.0 to 1.0>

Rules:
* true_positive  = guard caught a real bug / leak / anti-pattern.
* false_positive = agent was clearly fine; guard misfired.
* uncertain      = trajectory too short, context missing, judge cannot tell.
"""


# ═══════════════════════════════════════════════════════════
# Parser — robust against minor LLM formatting drift
# ═══════════════════════════════════════════════════════════


def _parse_verdict(text: str) -> GuardJudgeVerdict:
    """Parse the judge LLM reply. Defensive — returns ``uncertain`` on
    any parse failure rather than raising, matching fail-open policy."""
    if not text or not text.strip():
        return GuardJudgeVerdict(action="uncertain", reason="empty_reply")
    action: GuardJudgeAction = "uncertain"
    reason = ""
    confidence = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("VERDICT:"):
            value = line.split(":", 1)[1].strip().lower()
            if value in ("true_positive", "false_positive", "uncertain"):
                action = value  # type: ignore[assignment]
        elif upper.startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()[:80]
        elif upper.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip())
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, TypeError):
                confidence = 0.0
    return GuardJudgeVerdict(action=action, reason=reason, confidence=confidence)


# ═══════════════════════════════════════════════════════════
# Public builder — wires a router into a ready-to-use judge
# ═══════════════════════════════════════════════════════════


def build_guard_judge_from_router(
    router: ModelRouter,
    *,
    model: str = "claude-haiku-4-5-20251001",
    system_provider: str = "anthropic",
    max_tokens: int = 200,
    temperature: float = 0.0,
    max_excerpt_chars: int = 4000,
    prompt_template: str = GUARD_JUDGE_PROMPT,
) -> GuardJudge:
    """Build a guard-accuracy judge backed by ``router``.

    Designed for batch / offline use, not the ReAct hot path. Caller
    feeds (label, guard_message, trajectory_excerpt) and receives a
    GuardJudgeVerdict. Errors degrade to ``uncertain`` rather than
    raising.
    """
    from runtime.platform.models.llm import Message, ModelRequest

    def _judge(
        label: str,
        guard_message: str,
        trajectory_excerpt: str,
    ) -> GuardJudgeVerdict:
        if not label.strip() or not guard_message.strip():
            return GuardJudgeVerdict(action="uncertain", reason="empty_input")
        excerpt = trajectory_excerpt or ""
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[-max_excerpt_chars:]  # keep tail
        prompt = prompt_template.format(
            label=label,
            guard_message=guard_message,
            trajectory_excerpt=excerpt,
        )
        try:
            req = ModelRequest(
                model=model,
                messages=[Message(role="user", content=prompt)],
                max_tokens=max_tokens,
                temperature=temperature,
                system_provider=system_provider,
            )
            resp = router.call(req)
        except Exception as exc:  # noqa: BLE001 — fail open
            _log.warning(
                "guard_judge router error %s: %s — defaulting uncertain",
                type(exc).__name__,
                exc,
            )
            return GuardJudgeVerdict(action="uncertain", reason="router_error")
        return _parse_verdict(resp.text or "")

    return _judge


__all__ = [
    "GUARD_JUDGE_PROMPT",
    "GuardJudge",
    "GuardJudgeAction",
    "GuardJudgeVerdict",
    "build_guard_judge_from_router",
    "null_guard_judge",
]
