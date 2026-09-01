"""GEPA "apply" endpoints.

Persistence side of the prompt-evolution loop: persisting a
candidate prompt as an addendum (``/apply``), reading back the
currently-live addendum (``/applied``), and rolling one back
(``DELETE /addendums/{recipe_id}``). All three run through the
gene-lock gate so mutations are gated by LEVEL / TEMPORAL /
PANIC rules.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import Header as _Header

from runtime.platform.ui._reflex_admin_gepa_aliases import register_aliases
from runtime.platform.ui._reflex_admin_helpers import gate_forge_mutation


def register_gepa_apply(_reflex_admin: Any, *, stack: Any) -> None:
    """Register the GEPA apply / applied / addendum-delete
    endpoints + aliases."""

    @_reflex_admin.post("/api/evolution/gepa/apply")
    def _gepa_apply(
        body: dict,
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Persist a candidate's prompt as a GEPA addendum.

        Body shape::

            {
              "prompt": <text>,            # required
              "candidate_id": ...,         # for the metadata header
              "avg_score": ...,            # for the metadata header
              "rationale": ...,            # for the metadata header
              "run_ts": <float>,           # mark history applied
              "target_recipe_id": <str>    # NEW · routes to per-recipe
                                           # file at
                                           # ``data/gepa_addendums/<id>.md``
                                           # · when omitted, falls back
                                           # to the legacy global file
                                           # for back-compat
            }

        The next planner instance loads the matching addendum on
        its first plan() call · no restart needed. Per-recipe
        scope means the prompt only affects turns that match the
        target recipe_id, leaving winning recipes untouched.
        """
        from runtime.core.cerebrum.prompt_persistence import dump_section

        text = body.get("prompt")
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "missing prompt"}
        target_recipe_id = body.get("target_recipe_id")

        # Gene-lock gate · blocks per LEVEL / TEMPORAL / PANIC.
        # Target key for TEMPORAL cooldown = the recipe_id when
        # per-recipe, "global" for legacy path. Variant apply
        # shares the same cooldown bucket as non-variant apply
        # for the same recipe (all 3 paths are "changing this
        # recipe's prompt").
        from runtime.safety.gene_locks import MutationKind, record_mutation

        _gate = gate_forge_mutation(
            MutationKind.APPLY_ADDENDUM,
            target=target_recipe_id or "__global__",
            approver=x_human_approver,
        )
        if not _gate.get("ok"):
            return _gate
        section = (
            "## GEPA-optimized addendum\n\n"
            f"<!-- candidate {body.get('candidate_id', '?')} · "
            f"avg_score {body.get('avg_score', 0)} · "
            f"recipe {target_recipe_id or 'global'} · "
            f"rationale: {body.get('rationale', '')} -->\n\n" + text
        )
        try:
            # NEW · variant routing. When ``variant_id`` is set
            # alongside ``target_recipe_id``, route into the
            # per-recipe variant manifest instead of the single
            # per-recipe file. Lets the operator A/B-split
            # multiple GEPA candidates against the same recipe.
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
                # Let the variants module compute the canonical
                # on-disk path so the rebrand doesn't leak
                # through hardcoded directory names.
                from runtime.safety.recovery.gepa_variants import (
                    variant_path as _variant_path,
                )

                target = _variant_path(target_recipe_id, variant_id)
                scope = "variant"
            elif isinstance(target_recipe_id, str) and target_recipe_id.strip():
                # Per-recipe path · isolates the addendum to
                # turns whose planner recipe_hash matches.
                from runtime.safety.recovery.gepa_addendum_store import (
                    save_for_recipe,
                )

                target = save_for_recipe(target_recipe_id, section)
                scope = "per_recipe"
            else:
                # Global scope · route through the addendum
                # store helper so the path name tracks the
                # current branding (and auto-migrates from
                # any pre-rebrand filename).
                from runtime.safety.recovery.gepa_addendum_store import (
                    legacy_global_path,
                )

                target = legacy_global_path()
                dump_section(target, section, label="forge")
                scope = "global"
            # Mark the originating run as applied · best-effort.
            run_ts_raw = body.get("run_ts")
            applied_flag = False
            if isinstance(run_ts_raw, (int, float)):
                try:
                    from runtime.safety.recovery.gepa_runs import (
                        get_default_store,
                    )

                    applied_flag = get_default_store().mark_applied(
                        ts=float(run_ts_raw),
                    )
                except (OSError, ImportError, TypeError, ValueError) as _exc:  # noqa: BLE001
                    pass
            # Gene-lock bookkeeping · stamp the cooldown AFTER the
            # write succeeds so a failed write doesn't start a
            # cooldown for nothing.
            winner_payload = body.get("winner_proposal")
            if not isinstance(winner_payload, dict):
                winner_payload = {}
            winner_applied = {"ok": False, "skipped": True, "reason": "no_winner_payload"}
            with contextlib.suppress(ImportError, OSError, TypeError, ValueError):
                from runtime.safety.recovery.gepa_bridge import (
                    mark_winner_proposal_applied,
                )

                winner_applied = mark_winner_proposal_applied(
                    recipe_id=target_recipe_id
                    if isinstance(target_recipe_id, str) and target_recipe_id.strip()
                    else None,
                    variant_id=variant_id if scope == "variant" else None,
                    candidate_id=str(
                        winner_payload.get("candidate_id") or body.get("candidate_id") or ""
                    )
                    or None,
                    proposal_id=str(
                        winner_payload.get("proposal_id") or body.get("proposal_id") or ""
                    )
                    or None,
                    canary_key=str(winner_payload.get("canary_key") or body.get("canary_key") or "")
                    or None,
                    ledger_path="data/proposal_ledger.jsonl",
                )
            with contextlib.suppress(ImportError, OSError, TypeError, ValueError):
                record_mutation(
                    MutationKind.APPLY_ADDENDUM,
                    target_recipe_id or "__global__",
                )
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
                    "level": _gate.get("level"),
                    "warnings": _gate.get("warnings", []),
                },
                "source": "gepa",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "source": "gepa"}

    @_reflex_admin.get("/api/evolution/gepa/applied")
    def _gepa_applied() -> dict:
        """Read back the currently-applied GEPA addendum (if any)
        so the operator can see what's live without grepping
        the data dir."""
        from runtime.safety.recovery.gepa_addendum_store import (
            legacy_global_path,
        )

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
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            return {"applied": False, "error": str(exc), "source": "gepa"}
        return {
            "applied": True,
            "path": str(target),
            "size": len(content),
            "mtime": target.stat().st_mtime,
            "content_preview": content[:600],
            "source": "gepa",
        }

    @_reflex_admin.delete("/api/evolution/gepa/addendums/{recipe_id}")
    def _gepa_addendum_delete(
        recipe_id: str,
        x_human_approver: str | None = _Header(None, alias="X-Human-Approver"),
    ) -> dict:
        """Drop a per-recipe addendum · operator's "rollback" knob.

        ``recipe_id="__global__"`` removes the legacy global file
        instead. Returns ok=True even when the file didn't exist
        so the panel's delete button is idempotent.
        """
        from runtime.safety.gene_locks import MutationKind, record_mutation

        _gate = gate_forge_mutation(
            MutationKind.DELETE_ADDENDUM,
            target=recipe_id,
            approver=x_human_approver,
        )
        if not _gate.get("ok"):
            return _gate
        try:
            from runtime.safety.recovery.gepa_addendum_store import (
                delete_for_recipe,
                legacy_global_path,
            )

            if recipe_id == "__global__":
                p = legacy_global_path()
                if p.is_file():
                    p.unlink()
                    record_mutation(MutationKind.DELETE_ADDENDUM, recipe_id)
                    return {"ok": True, "deleted": True, "scope": "global", "source": "gepa"}
                return {"ok": True, "deleted": False, "scope": "global", "source": "gepa"}
            deleted = delete_for_recipe(recipe_id)
            record_mutation(MutationKind.DELETE_ADDENDUM, recipe_id)
            return {
                "ok": True,
                "deleted": deleted,
                "scope": "per_recipe",
                "recipe_id": recipe_id,
                "gene_lock": {"warnings": _gate.get("warnings", [])},
                "source": "gepa",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "source": "gepa"}

    register_aliases(
        _reflex_admin,
        [
            ("POST", "/api/evolution/gepa/apply", "/api/evolution/forge/apply", _gepa_apply),
            ("GET", "/api/evolution/gepa/applied", "/api/evolution/forge/applied", _gepa_applied),
            (
                "DELETE",
                "/api/evolution/gepa/addendums/{recipe_id}",
                "/api/evolution/forge/addendums/{recipe_id}",
                _gepa_addendum_delete,
            ),
        ],
    )
