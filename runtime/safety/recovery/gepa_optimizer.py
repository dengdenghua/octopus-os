"""
GEPA-style prompt optimizer · 7th reflection path.

Why we built this in-house instead of pulling ``dspy.GEPA``
-----------------------------------------------------------

DSPy + GEPA is the "production-grade" implementation (paper:
arxiv 2507.19457, ICLR 2026 Oral) and we should use it when
heavyweight optimization is justified. But:

* DSPy is a 50 MB dependency tree · adds 30 s to cold-start
  imports on the RK3588-class boxes Echo is designed for
* The full GEPA pipeline assumes a DSPy ``Program`` shape we
  don't have · adapting our planner to that shape is more
  invasive than implementing the core algorithm directly
* The algorithm itself is small (~150 LOC) and worth knowing
  by heart so the operator can debug it

So we ship a minimal Pareto-front prompt optimizer here and
leave a clean swap-in seam for ``dspy.GEPA`` when an operator
wants the full library. The two share the same input/output
shape (``optimize(seed_prompt, eval_fn) -> ranked candidates``)
so the swap is a one-line import change.

Algorithm
---------

GEPA-style loop · per iteration::

  1. Pick a parent from the Pareto front, weighted by
     "tasks where this candidate is best" (per-task coverage)
  2. Reflect: feed the parent prompt + a sample of recent
     trajectories where it under-performed to a stronger LLM,
     ask for a mutation that addresses the failures
  3. Evaluate the mutant on the same eval set, get its scores
  4. Add to candidate pool · prune dominated ones (Pareto)

Repeat until budget is exhausted or no candidate improves
the front for K iterations.

Difference from naive RL/random search:

* Pareto front (not single best) → keeps diverse strategies,
  avoids the local-optimum trap of "best on average"
* Reflection step uses LLM as the mutation operator, not
  random token edits → much higher quality per rollout

Trade-offs vs the full DSPy GEPA:

* No automatic program decomposition · we optimize one
  prompt string, not a structured DSPy chain
* No automatic metric inference · caller supplies eval_fn
* No persistence beyond what RecipeEvaluator already does

When that's not enough, swap to ``dspy.GEPA(auto="medium")``.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_LOG = logging.getLogger("echo.gepa")


@dataclass
class PromptCandidate:
    """One member of the optimization population."""

    prompt: str
    # Per-eval-task scores (parallel lists). Higher = better.
    task_scores: list[float] = field(default_factory=list)
    # Composite scalar for tie-breaking · derived from task_scores.
    avg_score: float = 0.0
    # Iteration this candidate was generated · helps debugging.
    born_at_iter: int = 0
    # Free-text rationale from the mutation step · "what did the
    parent_id: str | None = None
    rationale: str = ""

    @property
    def candidate_id(self) -> str:
        # Short stable id from the prompt's first 40 chars hashed,
        # so logs reading "v3a8c2 dominated v9d40f" stay short.
        import hashlib

        return hashlib.sha1(self.prompt.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]


def dominates(a: PromptCandidate, b: PromptCandidate) -> bool:
    """``a`` Pareto-dominates ``b`` iff a is no-worse on every task
    AND strictly better on at least one. Standard Pareto definition."""
    if len(a.task_scores) != len(b.task_scores) or not a.task_scores:
        return False
    strict_better_anywhere = False
    for sa, sb in zip(a.task_scores, b.task_scores, strict=False):
        if sa < sb:
            return False
        if sa > sb:
            strict_better_anywhere = True
    return strict_better_anywhere


def pareto_front(pop: list[PromptCandidate]) -> list[PromptCandidate]:
    """Return non-dominated members. O(N²) is fine since populations
    are small (typically <30 candidates)."""
    out: list[PromptCandidate] = []
    for c in pop:
        if not any(dominates(other, c) for other in pop if other is not c):
            out.append(c)
    return out


def _per_task_winners(front: list[PromptCandidate]) -> dict[int, list[str]]:
    """For each task index, find which front-members tie for best.
    Used for "weighted by coverage" parent selection · candidates
    that win on rarer tasks get higher selection probability."""
    if not front or not front[0].task_scores:
        return {}
    n_tasks = len(front[0].task_scores)
    out: dict[int, list[str]] = {}
    for ti in range(n_tasks):
        best = max(c.task_scores[ti] for c in front)
        winners = [c.candidate_id for c in front if c.task_scores[ti] == best]
        out[ti] = winners
    return out


def _pick_parent(front: list[PromptCandidate], rng: random.Random) -> PromptCandidate:
    """Stochastic parent selection · weight by coverage as in the
    GEPA paper. A candidate that's the unique winner on 3 tasks
    gets weight 3+ε; one tied with 4 others on 1 task gets 0.2.

    Falls back to uniform when the front is degenerate (single
    member or no scored tasks)."""
    if len(front) == 1:
        return front[0]
    winners = _per_task_winners(front)
    if not winners:
        return rng.choice(front)
    weight: dict[str, float] = {c.candidate_id: 0.001 for c in front}
    for _ti, ids in winners.items():
        share = 1.0 / len(ids)
        for cid in ids:
            weight[cid] = weight.get(cid, 0.0) + share
    ids = [c.candidate_id for c in front]
    weights = [weight[i] for i in ids]
    chosen_id = rng.choices(ids, weights=weights, k=1)[0]
    return next(c for c in front if c.candidate_id == chosen_id)


# ═══════════════════════════════════════════════════════════
# Mutation · LLM-driven prompt rewriting
# ═══════════════════════════════════════════════════════════

_MUTATION_SYSTEM = """\
You are improving a system prompt used by an AI agent's planner.
Below are: the current prompt, sample tasks the planner handles,
and the planner's failures on those tasks. Propose a REVISED
version of the prompt that would have avoided those failures.

Output strict JSON:
  {
    "rationale": "one sentence: what change you're making and why",
    "revised_prompt": "the full new prompt text · keep what works,
                       only change what the failures suggest is wrong"
  }

Constraints:
  * Don't add long examples · the prompt is read every turn.
  * Don't reference task IDs · they aren't visible at runtime.
  * Keep the prompt under 1500 chars (current was ~%d).
"""


@dataclass
class MutationContext:
    """Inputs to the LLM mutator · keeps the call site terse."""

    sample_failures: list[dict[str, Any]]  # [{task, expected, actual, error}]
    meta_notes: str = ""  # e.g. "winning on speed but losing on accuracy"


def llm_mutate(
    parent: PromptCandidate,
    ctx: MutationContext,
    *,
    router: Any,
    model: str,
    iter_idx: int,
) -> PromptCandidate | None:
    """One mutation call · returns a new candidate or None on LLM
    failure. Defensive against malformed JSON · the GEPA loop
    treats None as "skip this iteration", doesn't crash."""
    from runtime.platform.models.llm import Message, ModelRequest

    failures_json = json.dumps(
        ctx.sample_failures[:5],
        ensure_ascii=False,
        indent=2,
    )
    user_msg = f"# CURRENT PROMPT\n{parent.prompt}\n\n# RECENT FAILURES\n{failures_json}\n\n"
    if ctx.meta_notes:
        user_msg += f"# NOTES\n{ctx.meta_notes}\n\n"
    user_msg += "Propose the revised prompt as JSON described above."

    req = ModelRequest(
        model=model,
        messages=[
            Message(role="system", content=_MUTATION_SYSTEM % len(parent.prompt)),
            Message(role="user", content=user_msg),
        ],
        max_tokens=2048,
        temperature=0.7,  # deliberately higher · we WANT diversity
    )
    try:
        resp = router.call(req)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("gepa: llm_mutate router error · %s", exc)
        return None

    text = (getattr(resp, "text", "") or "").strip()
    # Tolerate markdown fences.
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Last-resort · grab the substring between first { and last }.
        s, e = text.find("{"), text.rfind("}")
        if s < 0 or e <= s:
            return None
        try:
            obj = json.loads(text[s : e + 1])
        except json.JSONDecodeError:
            return None
    revised = str(obj.get("revised_prompt") or "").strip()
    rationale = str(obj.get("rationale") or "").strip()[:200]
    if not revised or revised == parent.prompt:
        return None
    return PromptCandidate(
        prompt=revised,
        born_at_iter=iter_idx,
        parent_id=parent.candidate_id,
        rationale=rationale,
    )


# ═══════════════════════════════════════════════════════════
# Main optimization loop
# ═══════════════════════════════════════════════════════════


@dataclass
class GepaConfig:
    n_iter: int = 20
    eval_tasks: int = 5  # how many tasks per eval batch
    early_stop_no_improve: int = 5  # stop after K iters w/ no front change
    seed: int = 0


@dataclass
class GepaResult:
    iterations_run: int
    final_front: list[PromptCandidate]
    best_avg: PromptCandidate | None
    history: list[dict[str, Any]] = field(default_factory=list)
    elapsed_s: float = 0.0


# Type aliases for the eval contract.
EvalFn = Callable[[str, int], list[float]]
"""``eval_fn(prompt, n_tasks) -> [score per task]`` · scores are 0..1
where 1 is perfect. Caller decides what "task" means · for our
RecipeEvaluator wiring it'll be "rerun planner with this prompt
on a recent goal, score by trajectory.outcome.success".
"""

FailureSamplerFn = Callable[[str, int], list[dict[str, Any]]]
"""``failure_sampler(prompt, n) -> [task descriptors]`` for the
mutation step. Each descriptor should be a small dict the LLM
can read · e.g. ``{"goal": "...", "actual": "...", "expected": "..."}``.
"""


def gepa_optimize(
    *,
    seed_prompt: str,
    eval_fn: EvalFn,
    failure_sampler: FailureSamplerFn,
    router: Any,
    model: str,
    config: GepaConfig | None = None,
) -> GepaResult:
    """Run the loop. Caller-supplied ``eval_fn`` makes this generic
    enough to optimize any prompt as long as you can score it.

    Returns the final Pareto front + a per-iter history dict so
    the operator can plot "front size over time" or replay a run.
    """
    cfg = config or GepaConfig()
    rng = random.Random(cfg.seed)
    t0 = time.time()

    # Seed candidate · scored before the loop so iter 0's selection
    # has something to weight against.
    seed = PromptCandidate(prompt=seed_prompt, born_at_iter=0, rationale="seed")
    seed.task_scores = eval_fn(seed_prompt, cfg.eval_tasks)
    seed.avg_score = sum(seed.task_scores) / len(seed.task_scores) if seed.task_scores else 0.0

    pop: list[PromptCandidate] = [seed]
    front = [seed]
    history: list[dict[str, Any]] = [
        {
            "iter": 0,
            "front_size": 1,
            "best_avg": seed.avg_score,
            "candidate_id": seed.candidate_id,
        }
    ]
    no_improve_streak = 0
    last_front_signature = _front_signature(front)

    for i in range(1, cfg.n_iter + 1):
        parent = _pick_parent(front, rng)
        ctx = MutationContext(
            sample_failures=failure_sampler(parent.prompt, 5),
            meta_notes=_summarize_front(front),
        )
        child = llm_mutate(parent, ctx, router=router, model=model, iter_idx=i)
        if child is None:
            history.append(
                {
                    "iter": i,
                    "skipped": True,
                    "reason": "llm_mutate failed",
                }
            )
            no_improve_streak += 1
            continue

        # Score the child on the SAME eval set as the parent (use
        # eval_fn with the same n_tasks · in real impl this would
        # use a fixed seed for comparability).
        child.task_scores = eval_fn(child.prompt, cfg.eval_tasks)
        child.avg_score = (
            sum(child.task_scores) / len(child.task_scores) if child.task_scores else 0.0
        )
        pop.append(child)

        new_front = pareto_front(pop)
        new_sig = _front_signature(new_front)
        improved = new_sig != last_front_signature
        history.append(
            {
                "iter": i,
                "parent_id": parent.candidate_id,
                "child_id": child.candidate_id,
                "child_avg": child.avg_score,
                "front_size": len(new_front),
                "improved": improved,
                "rationale": child.rationale,
            }
        )
        front = new_front
        last_front_signature = new_sig
        if improved:
            no_improve_streak = 0
        else:
            no_improve_streak += 1
            if no_improve_streak >= cfg.early_stop_no_improve:
                history.append({"iter": i, "early_stop": True})
                break

    best = max(pop, key=lambda c: c.avg_score) if pop else None
    return GepaResult(
        iterations_run=len(history),
        final_front=front,
        best_avg=best,
        history=history,
        elapsed_s=time.time() - t0,
    )


def _front_signature(front: list[PromptCandidate]) -> str:
    """Stable id of a Pareto front · the multi-set of candidate
    ids. Used to detect "front didn't change" for early-stop."""
    return ",".join(sorted(c.candidate_id for c in front))


def _summarize_front(front: list[PromptCandidate]) -> str:
    """Short text passed to the mutation LLM as situational
    awareness · "you're up against these alternatives, here are
    their score profiles". Keeps mutation diverse."""
    if len(front) <= 1:
        return ""
    lines = ["Current Pareto front (id · scores):"]
    for c in front[:6]:
        scores = ", ".join(f"{s:.2f}" for s in c.task_scores)
        lines.append(f"  {c.candidate_id}: [{scores}]")
    return "\n".join(lines)


__all__ = [
    "PromptCandidate",
    "GepaConfig",
    "GepaResult",
    "gepa_optimize",
    "pareto_front",
    "dominates",
    "llm_mutate",
]
