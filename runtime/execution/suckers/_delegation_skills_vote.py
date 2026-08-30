"""``_call_agent_vote`` · the consensus / vote gate.

Extracted from delegation_skills.py. This module holds the ``_call_agent_vote``
handler: spawn N independent voters on the same question, parse each VERDICT,
and tally a majority. The pure aggregation helpers (``_extract_verdict`` /
``_tally_votes`` / ``_coerce_vote_choices`` / ``_build_ballot_prompt`` /
``_normalize_verdict`` / ``_vote_note``) live in ``_delegation_skills_common``.
The spawn reuses ``_call_agent_parallel``, which is resolved lazily via
``delegation_skills`` so a monkeypatch of ``delegation_skills._call_agent_parallel``
is observed at call time.
"""

from __future__ import annotations

from typing import Any

from ._delegation_skills_common import (
    _DEFAULT_SUBAGENT_TIMEOUT_S,
    _VOTE_MAX,
    _VOTE_MIN,
    _build_ballot_prompt,
    _coerce_vote_choices,
    _extract_verdict,
    _isolated_judge_context,
    _normalize_verdict,
    _tally_votes,
    _vote_note,
)

# Distinct reviewer-adjacent personas to cycle through so the panel is made of
# genuinely independent judgments rather than N samples of the same role.
_VOTER_INDEPENDENT_ROLES = (
    "reviewer",
    "architect",
    "security-review",
    "debugger",
    "researcher",
    "explorer",
)


def _call_agent_vote(
    question: str = "",
    *,
    n: int | str = 3,
    agent_id: str = "reviewer",
    choices: Any = None,
    timeout_s: int | str = _DEFAULT_SUBAGENT_TIMEOUT_S,
    context: dict[str, Any] | None = None,
    session: Any = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Verification gate: spawn N independent voters on the SAME question,
    parse each VERDICT, return a majority decision + confidence + dissent.

    Use it to CONFIRM or REFUTE a claim with independent judgment rather
    than trusting a single agent (e.g. "is this bug real?", "does this
    patch fix it?", "which design is safer: A or B?").
    """
    q = str(
        question
        or _kw.get("prompt")
        or _kw.get("task")
        or _kw.get("claim")
        or _kw.get("query")
        or "",
    ).strip()
    if not q:
        return {
            "ok": False,
            "error": "question is required",
            "verdict": None,
            "confidence": 0.0,
            "tally": {},
            "votes": [],
            "voter_count": 0,
            "votes_cast": 0,
            "abstentions": 0,
            "note": "[no-verdict] nothing to vote on",
        }
    try:
        n_int = int(n)
    except (TypeError, ValueError):
        n_int = 3
    n_int = max(_VOTE_MIN, min(_VOTE_MAX, n_int))
    ballot_choices = _coerce_vote_choices(choices)
    voter = str(agent_id or "reviewer").strip() or "reviewer"
    ballot = _build_ballot_prompt(q, ballot_choices)
    # Judge isolation, applied HERE rather than at each caller: every verifier
    # lane funnels through this function (run_orchestration's verify,
    # verdict_repair's judge, tournament's panel), so one switch covers them all
    # and a future caller inherits it by construction. See
    # ``_isolated_judge_context`` for what is withheld and why.
    #
    # This matters more since the panel began rotating personas: the rotation
    # seats ``debugger`` (whose role allowlist carries ``exec_shell``) and
    # ``researcher`` (``bb_write``), so without the read-only intersection a
    # voter could run shell or publish to the board its fellow voters read.
    vote_context = _isolated_judge_context(context)
    # Ask each voter for a JSON object; call_subagent validates it and re-asks
    # once on a mismatch. ``verdict`` is left a free string (not an enum) so
    # _normalize_verdict keeps its lenient casefold/substring mapping to the
    # ballot — avoids rejecting a valid "Keep" against choice "keep".
    vote_schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["verdict"],
    }
    # Independent panel: put the requested voter role first, then rotate through
    # distinct personas so each voter samples a different judgment posture.
    #
    # The BALLOT is identical for every voter — a majority only means something
    # if all of them answered the same question under the same framing. What
    # varies is the judging persona, not the question.
    voter_pool = tuple(dict.fromkeys((voter,) + _VOTER_INDEPENDENT_ROLES))
    specs = [
        {
            "agent_id": voter_pool[i % len(voter_pool)],
            "prompt": ballot,
            "output_schema": vote_schema,
        }
        for i in range(n_int)
    ]

    # Resolve the monkeypatch-visible name lazily via the delegation_skills
    # module so tests patching ``delegation_skills._call_agent_parallel``
    # observe it here.
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    env = _call_agent_parallel(
        specs=specs,
        timeout_s=timeout_s,
        context=vote_context,
        session=session,
    )

    votes: list[dict[str, Any]] = []
    for s in env.get("successes", []):
        parsed = s.get("parsed")
        if isinstance(parsed, dict) and "verdict" in parsed:
            # Schema-validated reply — trust the structured fields.
            verdict = _normalize_verdict(str(parsed.get("verdict") or ""), ballot_choices)
            reason = str(parsed.get("reason") or "").strip()[:200]
            if not reason:
                reason = " ".join(str(s.get("output") or "").split())[:200]
        else:
            # No parsed object (schema failed after retry, or none requested):
            # fall back to the legacy free-text parse so a malformed reply is
            # no worse than before rather than a hard abstention.
            verdict, reason = _extract_verdict(str(s.get("output") or ""), ballot_choices)
        votes.append(
            {
                "verdict": verdict,
                "reason": reason,
                "agent_id": s.get("agent_id"),
                "codename": s.get("codename"),
                "abstained": not verdict,
            }
        )

    tally = _tally_votes(votes, ballot_choices)
    return {
        "ok": bool(env.get("ok")) and tally["votes_cast"] > 0,
        "question": q[:240],
        "choices": ballot_choices,
        **tally,
        "votes": votes,
        "note": _vote_note(tally),
        "subagent_status": env.get("status_summary"),
        "honesty_warning": env.get("honesty_warning") or "",
    }
