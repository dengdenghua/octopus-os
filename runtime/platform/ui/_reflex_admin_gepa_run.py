"""GEPA "generate proposal" endpoints.

Groups the two endpoints that trigger prompt-evolution runs
(manual ``/run`` and the "look for losing recipes" sweep
``/auto-propose``). Neither auto-applies a winner · the operator
reviews results and POSTs ``/apply`` to persist them.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.ui._reflex_admin_gepa_aliases import register_aliases


def register_gepa_run(_reflex_admin: Any, *, stack: Any) -> None:
    """Register the GEPA run / auto-propose endpoints + aliases."""

    @_reflex_admin.post("/api/evolution/gepa/run")
    def _gepa_run(
        n_iter: int = 8,
        eval_tasks: int = 4,
        recipe_id: str | None = None,
        judge_model: str = "claude-sonnet-4-6",
        mutator_model: str = "claude-sonnet-4-6",
        optimizer_backend: str | None = None,
    ) -> dict:
        """Trigger one GEPA optimization run · pulls failed
        trajectories from the journal, mutates the planner's
        current system prompt, scores candidates with
        LLM-as-judge, returns the Pareto front + best.

        Does NOT auto-apply · operator inspects the result and
        POSTs /api/evolution/gepa/apply to persist the winner
        as a planner prompt addendum. Default budget (8 iter
        × 4 task judges × ~2 LLM calls) is ~64 LLM calls per
        run · tune ``n_iter``/``eval_tasks`` to taste.
        """
        import os

        try:
            from runtime.safety.recovery.optimizer_backends import (
                OptimizerRunConfig,
                optimize_with_backend,
            )

            planner = stack.planner
            # Seed = the planner's current base prompt. We'd
            # ideally include learned_rules + memories sections
            # too, but keeping the seed scope to the base lets
            # GEPA produce a clean delta we can review.
            seed = (
                getattr(planner, "_PLANNER_SYSTEM_PROMPT", "")
                or getattr(planner, "base_prompt", "")
                or ""
            )
            if not seed:
                # Fall back to module-level constant · loaded once
                # at import. Re-trigger the loader in case the
                # prompts file changed since boot.
                try:
                    from runtime.core.cerebrum.llm_planner import (
                        _load_planner_prompt,
                    )

                    seed = _load_planner_prompt()
                except (ImportError, OSError, TypeError, AttributeError):  # noqa: BLE001
                    seed = "You are a planner. Build a TaskGraph for the user goal."
            router = getattr(planner, "router", None)
            if router is None:
                return {"ok": False, "error": "planner.router missing"}
            result = optimize_with_backend(
                seed_prompt=seed,
                journal=stack.journal,
                router=router,
                config=OptimizerRunConfig(
                    backend=optimizer_backend
                    or os.environ.get("ECHO_OPTIMIZER_BACKEND")
                    or "native_gepa",
                    recipe_id=recipe_id,
                    judge_model=judge_model,
                    mutator_model=mutator_model,
                    n_iter=n_iter,
                    eval_tasks=eval_tasks,
                    trigger="manual",
                ),
            )
            # Persist to the run store so /api/evolution/gepa/runs
            # can show "last N runs". Always store · even
            # zero-iter (no-data) runs are useful for the
            # operator to see "I tried it, here's why it didn't
            # produce anything".
            try:
                from runtime.safety.recovery.gepa_runs import (
                    get_default_store,
                    record_from_result,
                )

                store = get_default_store()
                rec = record_from_result(
                    result,
                    trigger="manual",
                    recipe_id=recipe_id,
                )
                store.add(rec)
                _run_ts = rec.ts
            except (OSError, ImportError, TypeError, ValueError) as _exc:  # noqa: BLE001
                _run_ts = None
            return {
                "ok": True,
                "optimizer_backend": getattr(result, "optimizer_backend", None) or "native_gepa",
                "iterations_run": result.iterations_run,
                "elapsed_s": result.elapsed_s,
                "front_size": len(result.final_front),
                "ts": _run_ts,
                # Echo the recipe_id back so the panel can offer
                # "Apply to <recipe>" when the run was scoped.
                # None / missing means the run wasn't scoped to
                # a specific recipe · panel offers "Apply
                # globally" only.
                "recipe_id": recipe_id,
                "best": (
                    {
                        "candidate_id": result.best_avg.candidate_id,
                        "avg_score": result.best_avg.avg_score,
                        "task_scores": result.best_avg.task_scores,
                        "rationale": result.best_avg.rationale,
                        "prompt_preview": result.best_avg.prompt[:400],
                    }
                    if result.best_avg
                    else None
                ),
                "winner_proposal": getattr(result, "winner_proposal", None),
                "native_evaluation": getattr(result, "native_evaluation", []),
                "native_replay": getattr(result, "native_replay", {}),
                "native_sandbox_replay": getattr(result, "native_sandbox_replay", {}),
                "native_turn_replay": getattr(result, "native_turn_replay", {}),
                "native_llm_replay": getattr(result, "native_llm_replay", {}),
                "history": result.history,
                "source": "gepa",
            }
        except (
            OSError,
            ImportError,
            ValueError,
            TypeError,
            RuntimeError,
            NotImplementedError,
        ) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "source": "gepa"}

    @_reflex_admin.post("/api/evolution/gepa/auto-propose")
    def _gepa_auto_propose(
        n_iter: int = 6,
        eval_tasks: int = 4,
        max_recipes: int = 3,
        judge_model: str = "claude-sonnet-4-6",
        mutator_model: str = "claude-sonnet-4-6",
    ) -> dict:
        """One-click "look for losing recipes, propose GEPA fixes
        for each". Result records land in the run store · the
        operator opens /workspace/reflex and reviews the
        suggestions in the GEPA panel's history section.

        Doesn't auto-apply any winner · same conservative
        policy as the manual run endpoint.
        """
        try:
            from runtime.core.cerebrum.llm_planner import (
                _load_planner_prompt,
            )
            from runtime.safety.recovery.gepa_bridge import (
                propose_for_losing_recipes,
            )

            seed = _load_planner_prompt()
            router = getattr(stack.planner, "router", None)
            if router is None:
                return {"ok": False, "error": "planner.router missing"}
            results = propose_for_losing_recipes(
                journal=stack.journal,
                router=router,
                seed_prompt=seed,
                judge_model=judge_model,
                mutator_model=mutator_model,
                n_iter=n_iter,
                eval_tasks=eval_tasks,
                max_recipes=max_recipes,
            )
            ok_count = sum(1 for r in results if r.get("ok"))
            return {
                "ok": True,
                "proposals_generated": ok_count,
                "results": results,
                "source": "gepa",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "source": "gepa"}

    register_aliases(
        _reflex_admin,
        [
            ("POST", "/api/evolution/gepa/run", "/api/evolution/forge/run", _gepa_run),
            (
                "POST",
                "/api/evolution/gepa/auto-propose",
                "/api/evolution/forge/auto-propose",
                _gepa_auto_propose,
            ),
        ],
    )
