"""``_run_verdict_repair`` / ``_run_tournament`` · judge panels.

Extracted from delegation_skills.py. This module holds the three "judge a
panel" handlers: the verdict-gated repair loop, the worktree tournament, and
the team of external CLI agents. They all reuse ``_call_agent_vote`` (and, for
the repair loop, ``_call_agent_parallel``) as the judge; those are resolved
lazily via ``delegation_skills`` so a monkeypatch of
``delegation_skills._call_agent_vote`` / ``_call_agent_parallel`` is observed
at call time — the same pattern used by ``_delegation_skills_agent``.
"""

from __future__ import annotations

from typing import Any

from ._delegation_skills_common import (
    _DEFAULT_SUBAGENT_TIMEOUT_S,
    _VOTE_MAX,
    _VOTE_MIN,
    _coerce_vote_choices,
)
from ._delegation_skills_orchestration import _ORCH_MAX_SPAWNS_CEILING
from .delegation_budget import orchestration_budget_scope as _orchestration_budget_scope

# ── verdict-gated repair loop (produce -> judge -> rewrite -> re-judge) ──
_REPAIR_MAX_REPAIRS_CEILING = 4
_REPAIR_DEFAULT_JUDGE_N = 3


def _run_verdict_repair(
    task: str = "",
    *,
    agent_id: str = "general",
    judge_n: int | str = _REPAIR_DEFAULT_JUDGE_N,
    max_repairs: int | str = 2,
    choices: Any = None,
    timeout_s: int | str = _DEFAULT_SUBAGENT_TIMEOUT_S,
    context: dict[str, Any] | None = None,
    session: Any = None,
    **kw: Any,
) -> dict[str, Any]:
    """Produce → judge → (on FAIL) rewrite-with-critique → re-judge, bounded.

    The verdict-gated repair loop echo lacked: a worker attempts the task, an
    independent ``call_agent_vote`` judges it, and a rejection drives a corrected
    re-attempt that is *fed the critique* — Orca's PLAN→CRITIQUE→REWRITE closed
    loop, built on echo's own sub-agent + vote primitives. The control flow
    (loop, stop-on-pass, bound) is deterministic code in
    :func:`runtime.execution.suckers.verdict_repair.run_verdict_repair`.
    """
    # Resolve the monkeypatch-visible names lazily via the delegation_skills
    # module so tests patching ``delegation_skills._call_agent_parallel`` /
    # ``_call_agent_vote`` observe them here.
    from runtime.execution.suckers.delegation_skills import (
        _call_agent_parallel,
        _call_agent_vote,
    )
    from runtime.execution.suckers.verdict_repair import Verdict, run_verdict_repair

    task = str(task or kw.get("goal") or kw.get("prompt") or kw.get("query") or "").strip()
    if not task:
        return {
            "ok": False,
            "error": "task is required",
            "passed": False,
            "output": "",
            "attempts": 0,
            "rounds": [],
        }

    def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
        try:
            return max(lo, min(hi, int(value)))
        except (TypeError, ValueError):
            return default

    n_judge = _clamp(judge_n, _VOTE_MIN, _VOTE_MAX, _REPAIR_DEFAULT_JUDGE_N)
    repairs = _clamp(max_repairs, 0, _REPAIR_MAX_REPAIRS_CEILING, 2)
    # First ballot choice = "accept"; default to a plain pass/fail gate.
    ballot = _coerce_vote_choices(choices) or ["pass", "fail"]
    pass_choice = ballot[0]

    def _produce(attempt: int, critique: str) -> str:
        if attempt == 0 or not critique:
            prompt = task
        else:
            prompt = (
                f"{task}\n\n"
                "An independent reviewer REJECTED your previous attempt for these "
                f"reasons:\n{critique}\n\n"
                "Produce a corrected result that fixes every cited problem. "
                "Return only the result."
            )
        env = _call_agent_parallel(
            specs=[{"agent_id": str(agent_id or "general"), "prompt": prompt}],
            timeout_s=timeout_s,
            context=context,
            session=session,
        )
        successes = env.get("successes") or []
        return str(successes[0].get("output") or "").strip() if successes else ""

    def _judge(output: str) -> Verdict:
        if not output:
            return Verdict(passed=False, label="fail", critique="the attempt produced no output")
        question = (
            "Does the RESULT fully and correctly accomplish the TASK? Answer "
            "'fail' if anything is missing, wrong, or unverified.\n\n"
            f"TASK:\n{task}\n\nRESULT:\n{output}"
        )
        vote = _call_agent_vote(
            question=question,
            n=n_judge,
            choices=ballot,
            timeout_s=timeout_s,
            context=context,
            session=session,
        )
        label = str(vote.get("verdict") or "")
        passed = bool(label) and label == pass_choice
        critique = ""
        if not passed:
            reasons = [
                str(v.get("reason") or "").strip()
                for v in vote.get("votes", [])
                if str(v.get("verdict") or "") != pass_choice and v.get("reason")
            ]
            critique = " ; ".join(r for r in reasons if r)[:600] or (
                "rejected by the reviewers without a specific reason"
            )
        return Verdict(
            passed=passed,
            label=label or "fail",
            critique=critique,
            confidence=float(vote.get("confidence") or 0.0),
        )

    # Bound the whole loop in a spawn budget so the per-turn delegation cap
    # doesn't refuse the repair re-runs (same mechanism the orchestration loop
    # uses): (1 + repairs) producers, each followed by n_judge voters.
    planned = (1 + repairs) * (1 + n_judge)
    max_spawns = _clamp(planned, 1 + n_judge, _ORCH_MAX_SPAWNS_CEILING, planned)
    with _orchestration_budget_scope(int(max_spawns)):
        result = run_verdict_repair(produce=_produce, judge=_judge, max_repairs=repairs)

    final = result.final_verdict
    return {
        "ok": True,
        "task": task[:240],
        "passed": result.passed,
        "repaired": result.repaired,
        "output": result.output,
        "attempts": result.attempts,
        "verdict": final.label if final else "",
        "confidence": final.confidence if final else 0.0,
        "rounds": [
            {
                "attempt": r.attempt,
                "passed": r.verdict.passed,
                "verdict": r.verdict.label,
                "critique": r.verdict.critique,
            }
            for r in result.rounds
        ],
        "max_spawns": int(max_spawns),
    }


# ── tournament: N isolated candidates -> judge -> pick the best ─────────
_TOURNAMENT_MAX_CANDIDATES = 5


def _run_tournament(
    goal: str = "",
    *,
    n: int | str = 3,
    agent_id: str = "worktree_writer",
    judge_n: int | str = 3,
    repo_root: str | None = None,
    timeout_s: int | str = _DEFAULT_SUBAGENT_TIMEOUT_S,
    max_workers: int | str = 4,
    context: dict[str, Any] | None = None,
    session: Any = None,
    **kw: Any,
) -> dict[str, Any]:
    """Run the SAME goal as N isolated git-worktree candidates, then judge-pick
    the best diff — the missing half on top of echo's existing worktree
    isolation. Reuses ``run_worktree_loop`` (each candidate edits in its own
    worktree, no collisions) + ``call_agent_vote`` (an independent panel picks
    the winner); the selection logic is deterministic in
    ``subagents.tournament.select_winner``. Never auto-applies — the winner's
    diff is returned for review (reconciling parallel edits is a human call).
    """
    import os

    from runtime.execution.subagents.tournament import Candidate, select_winner
    from runtime.execution.subagents.worktree_loop import (
        is_git_repo,
        run_worktree_loop,
        subagent_worktree_worker,
    )

    # Resolve the monkeypatch-visible name lazily via the delegation_skills
    # module so tests patching ``delegation_skills._call_agent_vote`` observe it.
    from runtime.execution.suckers.delegation_skills import _call_agent_vote

    goal = str(goal or kw.get("task") or kw.get("prompt") or "").strip()
    if not goal:
        return {
            "ok": False,
            "error": "goal is required",
            "winner": None,
            "candidates": [],
            "decided_by": "none",
        }

    def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
        try:
            return max(lo, min(hi, int(value)))
        except (TypeError, ValueError):
            return default

    root = str(repo_root or os.getcwd())
    if not is_git_repo(root):
        return {
            "ok": False,
            "error": f"not a git repo: {root}",
            "winner": None,
            "candidates": [],
            "decided_by": "none",
        }

    n_cand = _clamp(n, 2, _TOURNAMENT_MAX_CANDIDATES, 3)
    n_judge = _clamp(judge_n, _VOTE_MIN, _VOTE_MAX, 3)

    worker = subagent_worktree_worker(agent_id=str(agent_id or "worktree_writer"))
    loop = run_worktree_loop(
        root,
        [goal] * n_cand,
        worker,
        max_workers=_clamp(max_workers, 1, n_cand, n_cand),
    )
    results = loop.get("results") or []
    candidates = [
        Candidate(
            id=f"candidate_{r.get('index', i) + 1}",
            output=str(r.get("diff") or ""),
            ok=bool(r.get("ok")),
            meta={
                "files": r.get("files") or [],
                "error": r.get("error"),
            },
        )
        for i, r in enumerate(results)
    ]

    def _judge(viable: list[Candidate]) -> str | None:
        blocks: list[str] = []
        for c in viable:
            files = ", ".join(str(f) for f in (c.meta.get("files") or [])) or "(no files)"
            diff = (c.output or "")[:2000]
            blocks.append(f"### {c.id} — files: {files}\n{diff}")
        question = (
            "Each candidate independently attempted the SAME goal in isolation. "
            "Which ONE best and most correctly accomplishes it? Weigh "
            "correctness, completeness, and simplicity.\n\n"
            f"GOAL:\n{goal}\n\n" + "\n\n".join(blocks)
        )
        vote = _call_agent_vote(
            question=question,
            n=n_judge,
            choices=[c.id for c in viable],
            timeout_s=timeout_s,
            context=context,
            session=session,
        )
        return str(vote.get("verdict") or "") or None

    # The judge's voters go through the delegation path → bound them in a spawn
    # budget so the per-turn cap doesn't refuse them. The worktree candidates run
    # under their own concurrency cap, before the judge.
    with _orchestration_budget_scope(n_judge + 1):
        result = select_winner(candidates, _judge)

    winner = result.winner
    return {
        "ok": winner is not None,
        "goal": goal[:240],
        "decided_by": result.decided_by,
        "candidate_count": len(candidates),
        "viable_count": result.viable_count,
        "winner": (
            {
                "id": winner.id,
                "files": list(winner.meta.get("files") or []),
                "diff": winner.output,
            }
            if winner
            else None
        ),
        "candidates": [
            {
                "id": c.id,
                "ok": c.ok,
                "viable": c.viable,
                "files": list(c.meta.get("files") or []),
                "error": c.meta.get("error"),
            }
            for c in candidates
        ],
        "note": "diffs are NOT auto-applied — review the winner and apply it yourself",
    }
