"""RecipeForge subsystem for evolution operators."""

from __future__ import annotations

import contextlib
import os
import time
from datetime import UTC
from typing import Any


def _empty_forge_run(
    *,
    n_iter: int,
    eval_tasks: int,
    trigger: str,
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "run_id": f"fallback-{int(time.time())}",
        "created_at": int(time.time()),
        "trigger": trigger,
        "n_iter": n_iter,
        "eval_tasks": eval_tasks,
        "recipe_id": None,
        "candidates": [],
        "best_candidate_id": None,
        "best_prompt": None,
        "best_score": None,
        "baseline_score": None,
        "history": [],
        "applied": False,
        "applied_at": None,
        "error": error,
        "source": "fallback",
    }


def _forge_applied_snapshot() -> dict[str, Any]:
    try:
        from runtime.safety.recovery.gepa_addendum_store import legacy_global_path

        target = legacy_global_path()
        if not target.is_file():
            return {
                "applied": False,
                "path": str(target),
                "size": 0,
                "mtime": None,
                "content_preview": "",
                "source": "gepa",
            }
        content = target.read_text(encoding="utf-8")
        stat = target.stat()
        return {
            "applied": True,
            "path": str(target),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "content_preview": content[:600],
            "source": "gepa",
        }
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "applied": False,
            "error": f"{type(exc).__name__}: {exc}",
            "source": "gepa",
        }


def _forge_runs_snapshot(*, limit: int) -> dict[str, Any]:
    try:
        from runtime.safety.recovery.gepa_runs import (
            enrich_run_records,
            get_default_store,
        )

        runs = enrich_run_records(get_default_store().list_recent(limit=limit))
        return {"runs": runs, "source": "gepa", "limit": limit}
    except (
        OSError,
        ImportError,
        AttributeError,
        TypeError,
        ValueError,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        return {
            "runs": [],
            "source": "gepa",
            "limit": limit,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_addendums_snapshot() -> dict[str, Any]:
    try:
        from runtime.safety.recovery.gepa_addendum_store import list_all

        return {"addendums": list_all(), "source": "gepa"}
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "addendums": [],
            "source": "gepa",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_recipes_snapshot() -> dict[str, Any]:
    try:
        from runtime.safety.recovery.gepa_variants import list_all_manifests

        return {"recipes": list_all_manifests(), "source": "gepa"}
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "recipes": [],
            "source": "gepa",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_auto_tick_status() -> dict[str, Any]:
    try:
        from runtime.safety.recovery import forge_auto_tick

        return {**forge_auto_tick.get_status(), "source": "gepa"}
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "enabled": False,
            "interval_hours": None,
            "next_tick_at": None,
            "last_tick": None,
            "source": "gepa",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_auto_tick_enable(
    *,
    journal: Any,
    interval_hours: float,
    min_uses: int,
    min_lead: float,
    approver: str | None,
) -> dict[str, Any]:
    if journal is None:
        return {
            "ok": False,
            "enabled": False,
            "source": "gepa",
            "error": "journal is required for RecipeForge auto-tick",
        }
    try:
        from runtime.safety.gene_locks import LockViolation, check_monotonic
        from runtime.safety.recovery import forge_auto_tick

        status = forge_auto_tick.get_status()
        warnings: list[str] = []
        for field_path, old_value, new_value in (
            ("auto_tick.interval_hours", status.get("interval_hours", 24.0), interval_hours),
            ("auto_tick.min_uses", status.get("min_uses", 20), min_uses),
            ("auto_tick.min_lead", status.get("min_lead", 0.15), min_lead),
        ):
            try:
                result = check_monotonic(
                    field_path=field_path,
                    old_value=old_value,
                    new_value=new_value,
                    approver=approver,
                )
                warnings.extend(result.get("warnings", []))
            except LockViolation as lv:
                return {**lv.as_dict(), "source": "gepa"}

        stack_ref = type("_ForgeStackRef", (), {"journal": journal})()
        forge_auto_tick.bind_stack(stack_ref)
        result = forge_auto_tick.enable(
            interval_hours=interval_hours,
            min_uses=min_uses,
            min_lead=min_lead,
        )
        if warnings:
            result["gene_lock_warnings"] = warnings
        return {**result, "source": "gepa"}
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "enabled": False,
            "source": "gepa",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_auto_tick_disable() -> dict[str, Any]:
    try:
        from runtime.safety.recovery import forge_auto_tick

        return {**forge_auto_tick.disable(), "source": "gepa"}
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "enabled": False,
            "source": "gepa",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_auto_tick_run_now(
    *,
    journal: Any,
    apply: bool,
    min_uses: int,
    min_lead: float,
) -> dict[str, Any]:
    try:
        from dataclasses import asdict

        from runtime.safety.recovery import forge_auto_tick

        tick = forge_auto_tick.run_tick(
            apply=apply,
            min_uses=min_uses,
            min_lead=min_lead,
            journal=journal,
        )
        payload = asdict(tick)
        payload.update(
            {
                "ok": not any(
                    r.get("error") == "no journal bound" for r in payload.get("results", [])
                ),
                "apply": apply,
                "applied": bool(payload.get("recipes_promoted", 0)),
                "source": "gepa",
            }
        )
        return payload
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "apply": apply,
            "applied": False,
            "source": "gepa",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_router(planner: Any) -> Any:
    return getattr(planner, "router", None) if planner is not None else None


def _forge_seed_prompt(planner: Any) -> str:
    seed = (
        getattr(planner, "_PLANNER_SYSTEM_PROMPT", "") or getattr(planner, "base_prompt", "") or ""
    )
    if seed:
        return str(seed)
    try:
        from runtime.core.cerebrum.llm_planner import _load_planner_prompt

        return str(_load_planner_prompt() or "")
    except (OSError, ImportError, AttributeError, TypeError):
        return "You are a planner. Build a TaskGraph for the user goal."


def _forge_candidate_payload(candidate: Any) -> dict[str, Any]:
    prompt = str(getattr(candidate, "prompt", "") or "")
    return {
        "candidate_id": getattr(candidate, "candidate_id", None),
        "avg_score": float(getattr(candidate, "avg_score", 0.0) or 0.0),
        "task_scores": list(getattr(candidate, "task_scores", []) or []),
        "rationale": str(getattr(candidate, "rationale", "") or ""),
        "prompt_preview": prompt[:400],
        "prompt": prompt,
        "born_at_iter": getattr(candidate, "born_at_iter", None),
        "parent_id": getattr(candidate, "parent_id", None),
    }


def _forge_result_payload(
    result: Any,
    *,
    trigger: str,
    recipe_id: str | None,
    run_ts: float | None,
) -> dict[str, Any]:
    best = getattr(result, "best_avg", None)
    candidates = [
        _forge_candidate_payload(candidate)
        for candidate in (getattr(result, "final_front", []) or [])
    ]
    best_payload = _forge_candidate_payload(best) if best is not None else None
    return {
        "ok": True,
        "run_id": str(run_ts or int(time.time())),
        "created_at": run_ts,
        "ts": run_ts,
        "trigger": trigger,
        "recipe_id": recipe_id,
        "iterations_run": int(getattr(result, "iterations_run", 0) or 0),
        "elapsed_s": float(getattr(result, "elapsed_s", 0.0) or 0.0),
        "front_size": len(getattr(result, "final_front", []) or []),
        "candidates": candidates,
        "best": best_payload,
        "best_candidate_id": best_payload.get("candidate_id") if best_payload else None,
        "best_prompt": best_payload.get("prompt") if best_payload else None,
        "best_score": best_payload.get("avg_score") if best_payload else None,
        "baseline_score": candidates[0].get("avg_score") if candidates else None,
        "winner_proposal": getattr(result, "winner_proposal", None),
        "native_evaluation": getattr(result, "native_evaluation", []),
        "native_replay": getattr(result, "native_replay", {}),
        "native_sandbox_replay": getattr(result, "native_sandbox_replay", {}),
        "native_turn_replay": getattr(result, "native_turn_replay", {}),
        "native_llm_replay": getattr(result, "native_llm_replay", {}),
        "history": list(getattr(result, "history", []) or []),
        "applied": False,
        "applied_at": None,
        "optimizer_backend": getattr(result, "optimizer_backend", None) or "native_gepa",
        "source": "gepa",
    }


def _forge_run_optimizer(
    *,
    journal: Any,
    planner: Any,
    n_iter: int,
    eval_tasks: int,
    recipe_id: str | None,
    judge_model: str,
    mutator_model: str,
    optimizer_backend: str | None = None,
) -> dict[str, Any]:
    router = _forge_router(planner)
    if journal is None:
        return _empty_forge_run(
            n_iter=n_iter,
            eval_tasks=eval_tasks,
            trigger="manual",
            error="RecipeForge optimizer requires a journal.",
        )
    if router is None:
        return _empty_forge_run(
            n_iter=n_iter,
            eval_tasks=eval_tasks,
            trigger="manual",
            error="RecipeForge optimizer is not configured: planner.router missing.",
        )
    try:
        from runtime.safety.recovery.gepa_runs import (
            get_default_store,
            record_from_result,
        )
        from runtime.safety.recovery.optimizer_backends import (
            OptimizerRunConfig,
            optimize_with_backend,
        )

        result = optimize_with_backend(
            seed_prompt=_forge_seed_prompt(planner),
            journal=journal,
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
        rec = record_from_result(result, trigger="manual", recipe_id=recipe_id)
        get_default_store().add(rec)
        return _forge_result_payload(
            result,
            trigger="manual",
            recipe_id=recipe_id,
            run_ts=rec.ts,
        )
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            **_empty_forge_run(
                n_iter=n_iter,
                eval_tasks=eval_tasks,
                trigger="manual",
                error=f"{type(exc).__name__}: {exc}",
            ),
            "source": "gepa",
        }


def _forge_auto_propose(
    *,
    journal: Any,
    planner: Any,
    n_iter: int,
    eval_tasks: int,
    max_recipes: int,
    judge_model: str,
    mutator_model: str,
) -> dict[str, Any]:
    router = _forge_router(planner)
    if journal is None:
        return {
            "ok": False,
            "error": "RecipeForge auto-propose requires a journal.",
            "source": "gepa",
        }
    if router is None:
        return {"ok": False, "error": "planner.router missing", "source": "gepa"}
    try:
        from runtime.safety.recovery.gepa_bridge import propose_for_losing_recipes

        results = propose_for_losing_recipes(
            journal=journal,
            router=router,
            seed_prompt=_forge_seed_prompt(planner),
            judge_model=judge_model,
            mutator_model=mutator_model,
            n_iter=n_iter,
            eval_tasks=eval_tasks,
            max_recipes=max_recipes,
        )
        return {
            "ok": True,
            "proposals_generated": sum(1 for result in results if result.get("ok")),
            "results": results,
            "source": "gepa",
        }
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "source": "gepa"}


def _forge_gate_mutation(
    kind: str,
    target: str,
    *,
    approver: str | None,
    bypass_cooldown: bool = False,
) -> dict[str, Any]:
    try:
        from runtime.safety.gene_locks import LockViolation, gate_mutation

        try:
            return gate_mutation(
                kind=kind,
                target=target,
                autonomous=approver is None,
                approver=approver,
                bypass_cooldown=bypass_cooldown,
            )
        except LockViolation as lv:
            return lv.as_dict()
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {"ok": False, "error": f"gene-lock unavailable: {type(exc).__name__}: {exc}"}


def _forge_apply_candidate(body: dict[str, Any], *, approver: str | None) -> dict[str, Any]:
    text = body.get("prompt")
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "error": "missing prompt", "source": "gepa"}

    target_recipe_id = body.get("target_recipe_id")
    target_key = (
        target_recipe_id
        if isinstance(target_recipe_id, str) and target_recipe_id.strip()
        else "__global__"
    )

    try:
        from runtime.safety.gene_locks import MutationKind, record_mutation
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "error": f"gene-lock unavailable: {type(exc).__name__}: {exc}",
            "source": "gepa",
        }

    gate = _forge_gate_mutation(
        MutationKind.APPLY_ADDENDUM,
        target=target_key,
        approver=approver,
    )
    if not gate.get("ok"):
        return {**gate, "source": "gepa"}

    section = (
        "## GEPA-optimized addendum\n\n"
        f"<!-- candidate {body.get('candidate_id', '?')} · "
        f"avg_score {body.get('avg_score', 0)} · "
        f"recipe {target_key} · "
        f"rationale: {body.get('rationale', '')} -->\n\n" + text
    )

    try:
        variant_id = body.get("variant_id")
        variant_weight = body.get("variant_weight", 1)
        if (
            isinstance(target_recipe_id, str)
            and target_recipe_id.strip()
            and isinstance(variant_id, str)
            and variant_id.strip()
        ):
            from runtime.safety.recovery.gepa_variants import (
                add_variant,
                variant_path,
            )

            add_variant(
                target_recipe_id,
                variant_id,
                content=section,
                weight=int(variant_weight) if isinstance(variant_weight, (int, float)) else 1,
                candidate_id=str(body.get("candidate_id", "")),
                rationale=str(body.get("rationale", "")),
                avg_score=(
                    float(body["avg_score"])
                    if isinstance(body.get("avg_score"), (int, float))
                    else None
                ),
            )
            target = variant_path(target_recipe_id, variant_id)
            scope = "variant"
        elif isinstance(target_recipe_id, str) and target_recipe_id.strip():
            from runtime.safety.recovery.gepa_addendum_store import save_for_recipe

            target = save_for_recipe(target_recipe_id, section)
            scope = "per_recipe"
            variant_id = None
        else:
            from runtime.core.cerebrum.prompt_persistence import dump_section
            from runtime.safety.recovery.gepa_addendum_store import legacy_global_path

            target = legacy_global_path()
            dump_section(target, section, label="forge")
            scope = "global"
            variant_id = None

        run_ts_raw = body.get("run_ts")
        applied_flag = False
        if isinstance(run_ts_raw, (int, float)):
            try:
                from runtime.safety.recovery.gepa_runs import get_default_store

                applied_flag = get_default_store().mark_applied(ts=float(run_ts_raw))
            except (OSError, ImportError, AttributeError, TypeError):  # noqa: BLE001 — applied-flag update best-effort; not on critical path
                pass
        winner_payload = body.get("winner_proposal")
        if not isinstance(winner_payload, dict):
            winner_payload = {}
        winner_applied = {"ok": False, "skipped": True, "reason": "no_winner_payload"}
        with contextlib.suppress(OSError, ImportError, AttributeError, TypeError, ValueError):
            from runtime.safety.recovery.gepa_bridge import mark_winner_proposal_applied

            winner_applied = mark_winner_proposal_applied(
                recipe_id=target_recipe_id
                if isinstance(target_recipe_id, str) and target_recipe_id.strip()
                else None,
                variant_id=variant_id if scope == "variant" else None,
                candidate_id=str(
                    winner_payload.get("candidate_id") or body.get("candidate_id") or ""
                )
                or None,
                proposal_id=str(winner_payload.get("proposal_id") or body.get("proposal_id") or "")
                or None,
                canary_key=str(winner_payload.get("canary_key") or body.get("canary_key") or "")
                or None,
                ledger_path="data/proposal_ledger.jsonl",
            )
        with contextlib.suppress(OSError, ImportError, AttributeError, TypeError):
            record_mutation(MutationKind.APPLY_ADDENDUM, target_key)
        return {
            "ok": True,
            "scope": scope,
            "target_recipe_id": target_recipe_id,
            "variant_id": variant_id if scope == "variant" else None,
            "path": str(target),
            "size": len(section),
            "run_marked_applied": applied_flag,
            "winner_applied": winner_applied,
            "gene_lock": {
                "level": gate.get("level"),
                "warnings": gate.get("warnings", []),
            },
            "source": "gepa",
        }
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "source": "gepa"}


def _forge_delete_addendum(recipe_id: str, *, approver: str | None) -> dict[str, Any]:
    try:
        from runtime.safety.gene_locks import MutationKind, record_mutation
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "error": f"gene-lock unavailable: {type(exc).__name__}: {exc}",
            "source": "gepa",
        }

    gate = _forge_gate_mutation(
        MutationKind.DELETE_ADDENDUM,
        target=recipe_id,
        approver=approver,
    )
    if not gate.get("ok"):
        return {**gate, "source": "gepa"}
    try:
        from runtime.safety.recovery.gepa_addendum_store import (
            delete_for_recipe,
            legacy_global_path,
        )

        if recipe_id == "__global__":
            target = legacy_global_path()
            deleted = target.is_file()
            if deleted:
                target.unlink()
            scope = "global"
        else:
            deleted = delete_for_recipe(recipe_id)
            scope = "per_recipe"
        with contextlib.suppress(OSError, ImportError, AttributeError, TypeError):
            record_mutation(MutationKind.DELETE_ADDENDUM, recipe_id)
        return {
            "ok": True,
            "deleted": deleted,
            "scope": scope,
            "recipe_id": recipe_id if scope == "per_recipe" else None,
            "gene_lock": {"warnings": gate.get("warnings", [])},
            "source": "gepa",
        }
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "source": "gepa"}


def _forge_variant_stats(*, journal: Any, recipe_id: str) -> dict[str, Any]:
    try:
        from dataclasses import asdict

        from runtime.safety.recovery.variant_evaluator import collect_variant_stats

        comps = collect_variant_stats(journal, base_recipe_id=recipe_id)
        if not comps:
            return {
                "recipe_id": recipe_id,
                "variants": [],
                "baseline": None,
                "total_calls": 0,
                "total_uses": 0,
                "source": "gepa",
            }
        comparison = comps[0]
        variants = [
            {
                **asdict(variant),
                "success_rate": variant.success_rate,
                "wilson_lower": variant.wilson_lower,
            }
            for variant in comparison.variants
        ]
        return {
            "recipe_id": comparison.base_recipe_id,
            "variants": variants,
            "baseline": next(
                (v for v in variants if v.get("variant_id") in ("", "__default__")), None
            ),
            "total_calls": comparison.total_uses,
            "total_uses": comparison.total_uses,
            "source": "gepa",
        }
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "recipe_id": recipe_id,
            "variants": [],
            "baseline": None,
            "total_calls": 0,
            "total_uses": 0,
            "source": "gepa",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_auto_promote(
    *,
    journal: Any,
    recipe_id: str,
    min_uses: int,
    min_lead: float,
    apply: bool,
) -> dict[str, Any]:
    try:
        from runtime.safety.recovery.gepa_variants import list_variants, set_weights
        from runtime.safety.recovery.variant_evaluator import (
            collect_variant_stats,
            propose_weights,
        )

        comps = collect_variant_stats(journal, base_recipe_id=recipe_id)
        if not comps:
            return {
                "ok": False,
                "skipped": True,
                "recipe_id": recipe_id,
                "reason": f"no trajectories tagged with recipe {recipe_id} yet · accumulate traffic first",
                "current_stats": [],
                "source": "gepa",
            }
        proposal = propose_weights(comps[0], min_uses=min_uses, min_lead=min_lead)
        if proposal is None:
            return {
                "ok": False,
                "skipped": True,
                "recipe_id": recipe_id,
                "reason": (
                    f"no winner yet (need >= {min_uses} uses per variant "
                    f"and >= {min_lead * 100:.0f}pp Wilson-lower lead)"
                ),
                "current_stats": [
                    {
                        "variant_id": variant.variant_id,
                        "uses": variant.uses,
                        "success_rate": variant.success_rate,
                        "wilson_lower": variant.wilson_lower,
                    }
                    for variant in comps[0].variants
                ],
                "source": "gepa",
            }
        result: dict[str, Any] = {
            "ok": True,
            "recipe_id": recipe_id,
            "proposal": {
                "base_recipe_id": proposal.base_recipe_id,
                "winner_variant_id": proposal.winner_variant_id,
                "winner_lower_bound": proposal.winner_lower_bound,
                "runner_up_lower_bound": proposal.runner_up_lower_bound,
                "weights": proposal.weights,
                "rationale": proposal.rationale,
            },
            "applied": False,
            "source": "gepa",
        }
        if apply:
            manifest = set_weights(recipe_id, weights=proposal.weights)
            if manifest is None:
                result["apply_error"] = f"no manifest for {recipe_id} · cannot apply"
            else:
                result["applied"] = True
                result["new_manifest"] = list_variants(recipe_id)
        return result
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "recipe_id": recipe_id,
            "error": f"{type(exc).__name__}: {exc}",
            "source": "gepa",
        }


def _forge_variants_snapshot(recipe_id: str) -> dict[str, Any]:
    try:
        from runtime.safety.recovery.gepa_variants import list_variants

        return {**list_variants(recipe_id), "source": "gepa"}
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "recipe_id": recipe_id,
            "variants": [],
            "source": "gepa",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _forge_variant_weights(
    recipe_id: str,
    body: dict[str, Any],
    *,
    approver: str | None,
) -> dict[str, Any]:
    try:
        from runtime.safety.gene_locks import MutationKind, record_mutation
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "error": f"gene-lock unavailable: {type(exc).__name__}: {exc}",
            "source": "gepa",
        }

    gate = _forge_gate_mutation(
        MutationKind.SET_VARIANT_WEIGHTS,
        target=recipe_id,
        approver=approver,
    )
    if not gate.get("ok"):
        return {**gate, "source": "gepa"}
    try:
        from runtime.safety.recovery.gepa_variants import list_variants, set_weights

        weights = body.get("weights") or {}
        if not isinstance(weights, dict):
            return {
                "ok": False,
                "recipe_id": recipe_id,
                "error": "weights must be a dict",
                "source": "gepa",
            }
        normalized = {
            str(key): max(0, int(value))
            for key, value in weights.items()
            if isinstance(value, (int, float))
        }
        default_raw = body.get("default_weight")
        default_weight = max(0, int(default_raw)) if isinstance(default_raw, (int, float)) else None
        manifest = set_weights(
            recipe_id,
            weights=normalized,
            default_weight=default_weight,
        )
        if manifest is None:
            return {
                "ok": False,
                "recipe_id": recipe_id,
                "error": f"no manifest for recipe {recipe_id}",
                "source": "gepa",
            }
        with contextlib.suppress(OSError, ImportError, AttributeError, TypeError):
            record_mutation(MutationKind.SET_VARIANT_WEIGHTS, recipe_id)
        return {
            "ok": True,
            **list_variants(recipe_id),
            "gene_lock": {"level": gate.get("level"), "warnings": gate.get("warnings", [])},
            "source": "gepa",
        }
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "recipe_id": recipe_id,
            "error": f"{type(exc).__name__}: {exc}",
            "source": "gepa",
        }


def _forge_delete_variant(
    recipe_id: str, variant_id: str, *, approver: str | None
) -> dict[str, Any]:
    try:
        from runtime.safety.gene_locks import MutationKind, record_mutation
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "error": f"gene-lock unavailable: {type(exc).__name__}: {exc}",
            "source": "gepa",
        }

    gate = _forge_gate_mutation(
        MutationKind.DELETE_ADDENDUM,
        target=recipe_id,
        approver=approver,
    )
    if not gate.get("ok"):
        return {**gate, "source": "gepa"}
    try:
        from runtime.safety.recovery.gepa_variants import remove_variant

        removed = remove_variant(recipe_id, variant_id)
        with contextlib.suppress(OSError, ImportError, AttributeError, TypeError):
            record_mutation(MutationKind.DELETE_ADDENDUM, recipe_id)
        return {
            "ok": True,
            "removed": removed,
            "deleted": removed,
            "recipe_id": recipe_id,
            "variant_id": variant_id,
            "gene_lock": {"warnings": gate.get("warnings", [])},
            "source": "gepa",
        }
    except (OSError, ImportError, AttributeError, TypeError) as exc:
        return {
            "ok": False,
            "recipe_id": recipe_id,
            "variant_id": variant_id,
            "error": f"{type(exc).__name__}: {exc}",
            "source": "gepa",
        }


def _forge_runs_csv_rows() -> list[list[Any]]:
    try:
        from datetime import datetime as _datetime

        from runtime.safety.recovery.gepa_runs import enrich_run_records, get_default_store

        rows: list[list[Any]] = []
        for run in enrich_run_records(get_default_store().list_recent(limit=200)):
            rows.append(
                [
                    f"{run['ts']:.3f}",
                    _datetime.fromtimestamp(run["ts"], tz=UTC).isoformat(),
                    run["trigger"],
                    run["recipe_id"] or "",
                    run["iterations_run"],
                    f"{run['elapsed_s']:.3f}",
                    run["front_size"],
                    run["best_candidate_id"] or "",
                    f"{run['best_avg_score']:.4f}" if run["best_avg_score"] is not None else "",
                    "1" if run["applied"] else "0",
                    f"{run['applied_at']:.3f}" if run["applied_at"] else "",
                    run["winner_lifecycle_state"] or "",
                    run["winner_proposal_id"] or "",
                    run["winner_canary_phase"] or "",
                    run["winner_rollback_reason"] or "",
                    run["best_rationale"] or "",
                ]
            )
        return rows
    except (OSError, ImportError, AttributeError, TypeError):
        return []


def _forge_addendums_csv_rows() -> list[list[Any]]:
    try:
        from datetime import datetime as _datetime

        from runtime.safety.recovery.gepa_addendum_store import list_all

        rows: list[list[Any]] = []
        for addendum in list_all():
            mtime = float(addendum.get("mtime") or 0)
            rows.append(
                [
                    addendum.get("scope", ""),
                    addendum.get("recipe_id") or "",
                    addendum.get("path", ""),
                    addendum.get("size", 0),
                    f"{mtime:.3f}" if mtime else "",
                    _datetime.fromtimestamp(mtime, tz=UTC).isoformat() if mtime else "",
                    addendum.get("preview", ""),
                ]
            )
        return rows
    except (OSError, ImportError, AttributeError, TypeError):
        return []
