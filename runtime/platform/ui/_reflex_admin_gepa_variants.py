"""GEPA recipe-variant endpoints.

A/B-experiment management for RecipeForge: listing the recipes
with variant manifests, per-recipe per-variant stats, Wilson-
lower-bound auto-promote proposals, weight updates, and variant
deletion. Weight mutations run through the gene-lock gate.
"""

from __future__ import annotations

from typing import Any

from fastapi import Header as _Header

from runtime.platform.ui._reflex_admin_gepa_aliases import register_aliases
from runtime.platform.ui._reflex_admin_helpers import gate_forge_mutation


def register_gepa_variants(_reflex_admin: Any, *, stack: Any) -> None:
    """Register the GEPA recipe-variant endpoints + aliases."""

    @_reflex_admin.get("/api/evolution/gepa/recipes")
    def _gepa_recipes_with_manifests() -> dict:
        """List every recipe that has an active variant manifest ·
        powers the "all A/B experiments" view. One row per
        recipe; the operator drills into a specific recipe to
        see per-variant stats via /variants/<id>/stats.
        """
        from runtime.safety.recovery.gepa_variants import (
            list_all_manifests,
        )

        return {"recipes": list_all_manifests(), "source": "gepa"}

    @_reflex_admin.get("/api/evolution/gepa/variants/{recipe_id:path}/stats")
    def _gepa_variants_stats(recipe_id: str) -> dict:
        from dataclasses import asdict

        from runtime.safety.recovery.variant_evaluator import (
            collect_variant_stats,
        )

        comps = collect_variant_stats(
            stack.journal,
            base_recipe_id=recipe_id,
        )
        if not comps:
            return {"recipe_id": recipe_id, "variants": [], "total_uses": 0, "source": "gepa"}
        cmp_ = comps[0]
        return {
            "recipe_id": cmp_.base_recipe_id,
            "total_uses": cmp_.total_uses,
            "variants": [
                {
                    **asdict(v),
                    "success_rate": v.success_rate,
                    "wilson_lower": v.wilson_lower,
                }
                for v in cmp_.variants
            ],
            "source": "gepa",
        }

    @_reflex_admin.post("/api/evolution/gepa/variants/{recipe_id:path}/auto-promote")
    def _gepa_variants_auto_promote(
        recipe_id: str,
        min_uses: int = 10,
        min_lead: float = 0.10,
        apply: bool = False,
    ) -> dict:
        """Compute a promote proposal · winner gets 10× weight,
        losers stay at 1 (kept alive at low traffic for
        continued evidence). With ``apply=true`` the proposal
        is auto-committed via ``set_weights`` · with
        ``apply=false`` (default) it's returned for the
        operator to review and apply manually."""
        from runtime.safety.recovery.gepa_variants import (
            list_variants,
            set_weights,
        )
        from runtime.safety.recovery.variant_evaluator import (
            collect_variant_stats,
            propose_weights,
        )

        comps = collect_variant_stats(
            stack.journal,
            base_recipe_id=recipe_id,
        )
        if not comps:
            # Treat "no data" as skipped (not an error) so the
            # panel renders it in the gentle gray-info style
            # rather than the red-error style.
            return {
                "ok": False,
                "skipped": True,
                "reason": (
                    f"no trajectories tagged with recipe {recipe_id} yet · accumulate traffic first"
                ),
                "current_stats": [],
                "source": "gepa",
            }
        proposal = propose_weights(
            comps[0],
            min_uses=min_uses,
            min_lead=min_lead,
        )
        if proposal is None:
            return {
                "ok": False,
                "skipped": True,
                "reason": (
                    f"no winner yet (need ≥{min_uses} uses per variant "
                    f"and ≥{min_lead * 100:.0f}pp Wilson-lower lead)"
                ),
                "current_stats": [
                    {
                        "variant_id": v.variant_id,
                        "uses": v.uses,
                        "success_rate": v.success_rate,
                        "wilson_lower": v.wilson_lower,
                    }
                    for v in comps[0].variants
                ],
                "source": "gepa",
            }
        result: dict = {
            "ok": True,
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
            m = set_weights(recipe_id, weights=proposal.weights)
            if m is not None:
                result["applied"] = True
                result["new_manifest"] = list_variants(recipe_id)
            else:
                result["apply_error"] = f"no manifest for {recipe_id} · cannot apply"
        return result

    @_reflex_admin.get("/api/evolution/gepa/variants/{recipe_id:path}")
    def _gepa_variants_list(recipe_id: str) -> dict:
        """List all variants for a recipe + their weights +
        content previews. Returns ``manifest_present: false``
        when the recipe is in single-file mode (no manifest)."""
        from runtime.safety.recovery.gepa_variants import list_variants

        return {**list_variants(recipe_id), "source": "gepa"}

    @_reflex_admin.post("/api/evolution/gepa/variants/{recipe_id:path}/weights")
    def _gepa_variants_weights(
        recipe_id: str,
        body: dict,
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Bulk-update variant weights · operator's "shift more
        traffic to the winner" knob. Body shape::

            {
              "weights": {"vA": 10, "vB": 1},
              "default_weight": 0
            }

        ``weights`` may include only a subset of variants ·
        unlisted ones keep their current weight. Pass
        ``default_weight`` to also tune the control-group share.
        Returns the updated manifest summary."""
        from runtime.safety.gene_locks import MutationKind, record_mutation
        from runtime.safety.recovery.gepa_variants import (
            list_variants,
            set_weights,
        )

        # Gene-lock gate · weight changes are high-risk (live
        # traffic impact) so they go through the QUORUM soft-
        # advisory path + TEMPORAL (6h per recipe).
        _gate = gate_forge_mutation(
            MutationKind.SET_VARIANT_WEIGHTS,
            target=recipe_id,
            approver=x_human_approver,
        )
        if not _gate.get("ok"):
            return _gate
        try:
            weights = body.get("weights") or {}
            if not isinstance(weights, dict):
                return {"ok": False, "error": "weights must be a dict", "source": "gepa"}
            # Normalise · drop non-int values defensively.
            norm = {
                str(k): max(0, int(v)) for k, v in weights.items() if isinstance(v, (int, float))
            }
            dw_raw = body.get("default_weight")
            dw = max(0, int(dw_raw)) if isinstance(dw_raw, (int, float)) else None
            m = set_weights(
                recipe_id,
                weights=norm,
                default_weight=dw,
            )
            if m is None:
                return {
                    "ok": False,
                    "error": f"no manifest for recipe {recipe_id}",
                    "source": "gepa",
                }
            record_mutation(
                MutationKind.SET_VARIANT_WEIGHTS,
                recipe_id,
            )
            return {
                "ok": True,
                **list_variants(recipe_id),
                "gene_lock": {
                    "level": _gate.get("level"),
                    "warnings": _gate.get("warnings", []),
                },
                "source": "gepa",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "source": "gepa"}

    @_reflex_admin.delete(
        "/api/evolution/gepa/variants/{recipe_id:path}/{variant_id}",
    )
    def _gepa_variants_delete(
        recipe_id: str,
        variant_id: str,
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Drop a variant · removes its file + manifest entry.
        When the last variant of a recipe is removed AND
        default_weight is 0, the entire manifest file is
        dropped too · planner falls back to single-file mode."""
        from runtime.safety.gene_locks import MutationKind, record_mutation
        from runtime.safety.recovery.gepa_variants import remove_variant

        _gate = gate_forge_mutation(
            MutationKind.DELETE_ADDENDUM,
            target=recipe_id,
            approver=x_human_approver,
        )
        if not _gate.get("ok"):
            return _gate
        removed = remove_variant(recipe_id, variant_id)
        record_mutation(MutationKind.DELETE_ADDENDUM, recipe_id)
        return {
            "ok": True,
            "removed": removed,
            "recipe_id": recipe_id,
            "variant_id": variant_id,
            "gene_lock": {"warnings": _gate.get("warnings", [])},
            "source": "gepa",
        }

    register_aliases(
        _reflex_admin,
        [
            (
                "GET",
                "/api/evolution/gepa/recipes",
                "/api/evolution/forge/recipes",
                _gepa_recipes_with_manifests,
            ),
            (
                "GET",
                "/api/evolution/gepa/variants/{recipe_id:path}/stats",
                "/api/evolution/forge/variants/{recipe_id:path}/stats",
                _gepa_variants_stats,
            ),
            (
                "POST",
                "/api/evolution/gepa/variants/{recipe_id:path}/auto-promote",
                "/api/evolution/forge/variants/{recipe_id:path}/auto-promote",
                _gepa_variants_auto_promote,
            ),
            (
                "GET",
                "/api/evolution/gepa/variants/{recipe_id:path}",
                "/api/evolution/forge/variants/{recipe_id:path}",
                _gepa_variants_list,
            ),
            (
                "POST",
                "/api/evolution/gepa/variants/{recipe_id:path}/weights",
                "/api/evolution/forge/variants/{recipe_id:path}/weights",
                _gepa_variants_weights,
            ),
            (
                "DELETE",
                "/api/evolution/gepa/variants/{recipe_id:path}/{variant_id}",
                "/api/evolution/forge/variants/{recipe_id:path}/{variant_id}",
                _gepa_variants_delete,
            ),
        ],
    )
