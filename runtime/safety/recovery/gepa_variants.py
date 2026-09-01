"""
Multi-variant per-recipe addendums · turns GEPA from "one optimized
prompt per recipe" into "N candidate prompts per recipe, traffic-
split by weight, sticky per conversation".

Why
---

A single per-recipe addendum is a one-bet-at-a-time strategy:
operator picks the best GEPA candidate, applies it, waits for the
RecipeEvaluator verdict to confirm it's actually better. Slow.

With multi-variant, the operator can apply 2-3 promising
candidates at once, each getting a slice of traffic. Real usage
data picks the winner naturally · the runtime keeps the variants
sticky per conversation so a single user doesn't see the prompt
flip mid-thread. Single-string optimizers can't replicate this
traffic-splitting behaviour.

Storage layout
--------------

::

    data/gepa_addendums/
    ├── llm_abc123.md                    # legacy single-file (still works)
    ├── llm_xyz789__variantA.md          # variant A of recipe llm@xyz789
    ├── llm_xyz789__variantB.md          # variant B
    └── llm_xyz789_manifest.json         # weights + metadata

The manifest is the source of truth · variant files without a
manifest entry are ignored (so an orphan file from a manual
``rm`` won't accidentally serve traffic).

Selection · sticky per conversation
-----------------------------------

* Total weight = sum of all variants' weights + ``default_weight``
  (the "no addendum" branch · 0 by default = always pick a
  variant, but operators can set it > 0 to keep a control group
  on the bare prompt for A/B baseline)
* ``select_variant(recipe_id, conversation_id)`` returns either
  a variant_id string or ``None`` (default branch / no manifest)
* Sticky · ``hash(conversation_id) % total_weight`` deterministic
  by conversation, so the same user gets consistent prompts
  within one thread

Backward compat
---------------

When no manifest exists for a recipe, the planner still falls
back to ``load_for_recipe()`` from the original addendum_store.
Existing single-file deployments keep working unchanged.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.gepa.variants")


def _root() -> Path:
    """Reuses the same addendum dir as ``gepa_addendum_store`` ·
    everything addendum-related lives in one place. Delegates so
    the legacy-path migration only needs to live in one file."""
    from runtime.safety.recovery.gepa_addendum_store import _root as _shared_root

    return _shared_root()


def _safe_recipe(recipe_id: str) -> str:
    """Filename-safe form · same sanitiser as ``addendum_store`` so
    we don't fragment two files for the same recipe."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", recipe_id.strip()) or "unknown"


def _safe_variant(variant_id: str) -> str:
    """Filename-safe variant id · alphanumeric + dash/underscore."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", variant_id.strip())
    return s[:32].strip("_") or "v0"


def variant_path(recipe_id: str, variant_id: str) -> Path:
    """``data/gepa_addendums/<recipe>__<variant>.md``"""
    return _root() / f"{_safe_recipe(recipe_id)}__{_safe_variant(variant_id)}.md"


def manifest_path(recipe_id: str) -> Path:
    return _root() / f"{_safe_recipe(recipe_id)}_manifest.json"


# ═══════════════════════════════════════════════════════════
# Manifest · the source of truth for which variants are live
# ═══════════════════════════════════════════════════════════


@dataclass
class VariantEntry:
    """One row in a recipe's variant manifest."""

    variant_id: str
    weight: int  # 0..N · 0 = retired (never selected)
    added_at: float  # unix seconds · ts of first save
    candidate_id: str = ""  # GEPA run's candidate id (provenance)
    rationale: str = ""  # the LLM mutator's "why" string
    avg_score: float | None = None  # GEPA-time avg score


@dataclass
class VariantManifest:
    """Per-recipe manifest · what variants exist, their weights,
    plus ``default_weight`` (the "no addendum" control group)."""

    recipe_id: str
    variants: list[VariantEntry] = field(default_factory=list)
    default_weight: int = 0  # weight for the "no addendum" branch
    updated_at: float = 0.0

    def total_weight(self) -> int:
        return self.default_weight + sum(max(0, v.weight) for v in self.variants)

    def find(self, variant_id: str) -> VariantEntry | None:
        return next(
            (v for v in self.variants if v.variant_id == variant_id),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "variants": [asdict(v) for v in self.variants],
            "default_weight": self.default_weight,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VariantManifest:
        raw_vars = data.get("variants") or []
        variants: list[VariantEntry] = []
        for v in raw_vars:
            if not isinstance(v, dict):
                continue
            vid = str(v.get("variant_id") or "").strip()
            if not vid:
                continue
            try:
                variants.append(
                    VariantEntry(
                        variant_id=vid,
                        weight=int(v.get("weight") or 0),
                        added_at=float(v.get("added_at") or 0.0),
                        candidate_id=str(v.get("candidate_id") or ""),
                        rationale=str(v.get("rationale") or "")[:300],
                        avg_score=(
                            float(v["avg_score"]) if v.get("avg_score") is not None else None
                        ),
                    )
                )
            except (TypeError, ValueError):
                continue
        return cls(
            recipe_id=str(data.get("recipe_id") or ""),
            variants=variants,
            default_weight=max(0, int(data.get("default_weight") or 0)),
            updated_at=float(data.get("updated_at") or 0.0),
        )


def load_manifest(recipe_id: str) -> VariantManifest | None:
    path = manifest_path(recipe_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return VariantManifest.from_dict(data)
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("variant manifest read failed for %s · %s", recipe_id, exc)
        return None


def save_manifest(m: VariantManifest) -> Path:
    """Atomic write · tmp + rename. Caller bumps ``updated_at``
    before calling so a reader can detect 'manifest changed since
    last poll' if it caches."""
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    target = manifest_path(m.recipe_id)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(m.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(target)
        return target
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


# ═══════════════════════════════════════════════════════════
# Public · variant CRUD called from /api/evolution/gepa/...
# ═══════════════════════════════════════════════════════════


def add_variant(
    recipe_id: str,
    variant_id: str,
    *,
    content: str,
    weight: int = 1,
    candidate_id: str = "",
    rationale: str = "",
    avg_score: float | None = None,
) -> VariantManifest:
    """Save a variant file + register in manifest. If the
    variant_id already exists, REPLACES the content + updates
    metadata (existing weight is preserved unless caller sets
    a new one)."""
    if not recipe_id or not variant_id:
        raise ValueError("recipe_id and variant_id are required")
    if not content or not content.strip():
        raise ValueError("content cannot be empty")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    # Write the variant content first · if this fails the manifest
    # stays unchanged (no orphaned manifest pointing to a missing
    # file). Atomic rename.
    target = variant_path(recipe_id, variant_id)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    # Update manifest.
    m = load_manifest(recipe_id) or VariantManifest(recipe_id=recipe_id)
    existing = m.find(variant_id)
    if existing is None:
        m.variants.append(
            VariantEntry(
                variant_id=variant_id,
                weight=max(0, int(weight)),
                added_at=time.time(),
                candidate_id=candidate_id,
                rationale=rationale,
                avg_score=avg_score,
            )
        )
    else:
        existing.weight = max(0, int(weight))
        existing.candidate_id = candidate_id or existing.candidate_id
        existing.rationale = rationale or existing.rationale
        if avg_score is not None:
            existing.avg_score = avg_score
    m.updated_at = time.time()
    save_manifest(m)
    return m


def remove_variant(recipe_id: str, variant_id: str) -> bool:
    """Drop a variant · removes the file AND the manifest entry.
    Returns True when something was actually removed."""
    m = load_manifest(recipe_id)
    if m is None:
        return False
    before = len(m.variants)
    m.variants = [v for v in m.variants if v.variant_id != variant_id]
    if len(m.variants) == before:
        return False
    # Remove file (best-effort · manifest update is the source
    # of truth so a missing file is fine).
    with contextlib.suppress(OSError):
        variant_path(recipe_id, variant_id).unlink(missing_ok=True)
    # If the manifest is now empty AND default_weight=0, drop
    # the whole manifest so the planner falls back to legacy
    # single-file lookup cleanly.
    if not m.variants and m.default_weight == 0:
        with contextlib.suppress(OSError):
            manifest_path(recipe_id).unlink(missing_ok=True)
        return True
    m.updated_at = time.time()
    save_manifest(m)
    return True


def set_weights(
    recipe_id: str,
    *,
    weights: dict[str, int] | None = None,
    default_weight: int | None = None,
) -> VariantManifest | None:
    """Bulk-update weights · operator's "shift more traffic to vB"
    knob. ``weights`` is ``{variant_id: new_weight}`` for each one
    you want to change; unlisted variants keep their current
    weight. Pass ``default_weight`` to also tune the control-group
    share. Returns the updated manifest or None when there's no
    manifest to update."""
    m = load_manifest(recipe_id)
    if m is None:
        return None
    if weights:
        for vid, w in weights.items():
            entry = m.find(vid)
            if entry is not None:
                entry.weight = max(0, int(w))
    if default_weight is not None:
        m.default_weight = max(0, int(default_weight))
    m.updated_at = time.time()
    save_manifest(m)
    return m


def list_variants(recipe_id: str) -> dict[str, Any]:
    """Return manifest + per-variant content preview for the UI."""
    m = load_manifest(recipe_id)
    if m is None:
        return {
            "recipe_id": recipe_id,
            "variants": [],
            "default_weight": 0,
            "manifest_present": False,
        }
    out: list[dict[str, Any]] = []
    for v in m.variants:
        path = variant_path(recipe_id, v.variant_id)
        preview = ""
        size = 0
        mtime = 0.0
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
                preview = content[:300]
                size = path.stat().st_size
                mtime = path.stat().st_mtime
            except OSError:  # noqa: BLE001 — file stat best-effort
                pass
        out.append(
            {
                **asdict(v),
                "preview": preview,
                "size": size,
                "mtime": mtime,
                "path": str(path),
            }
        )
    return {
        "recipe_id": recipe_id,
        "manifest_present": True,
        "default_weight": m.default_weight,
        "total_weight": m.total_weight(),
        "updated_at": m.updated_at,
        "variants": out,
    }


# ═══════════════════════════════════════════════════════════
# Planner-side · sticky variant selection
# ═══════════════════════════════════════════════════════════


def _bucket(key: str) -> int:
    """Stable cross-process hash · same as gating._bucket."""
    return int.from_bytes(
        hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).digest()[:4],
        "big",
    )


def select_variant(
    recipe_id: str | None,
    conversation_id: str | None,
) -> tuple[str | None, str]:
    """Pick a variant for this turn · returns (variant_id, content).

    * ``variant_id is None`` → no variants for this recipe (caller
      should fall back to single-file ``load_for_recipe``)
    * ``variant_id == ""`` → control-group branch picked (no
      addendum content)
    * non-empty variant_id → that variant's file content

    Sticky per conversation_id · ``hash(conv) % total_weight``.
    When conversation_id is missing (CLI / one-shot calls), uses
    a per-second bucket so the choice changes each call (good
    enough · the CLI doesn't have multi-turn state to disrupt).
    """
    if not recipe_id:
        return None, ""
    m = load_manifest(recipe_id)
    if m is None or m.total_weight() <= 0:
        return None, ""
    # Build the cumulative bucket map.
    cumulative: list[tuple[int, str | None]] = []  # (upper_bound, variant_id_or_None)
    running = 0
    for v in m.variants:
        if v.weight <= 0:
            continue
        running += v.weight
        cumulative.append((running, v.variant_id))
    if m.default_weight > 0:
        running += m.default_weight
        cumulative.append((running, None))  # control branch
    if not cumulative:
        return None, ""
    # Sticky pick.
    seed = conversation_id or f"_oneshot_{int(time.time())}"
    pick = _bucket(f"{recipe_id}|{seed}") % running
    chosen: str | None = None
    for upper, vid in cumulative:
        if pick < upper:
            chosen = vid
            break
    if chosen is None:
        # Picked the control-group branch · empty addendum.
        return "", ""
    # Load content. If the file is missing (manifest out of sync
    # with disk), treat as empty so the turn still goes through.
    path = variant_path(recipe_id, chosen)
    if not path.is_file():
        _LOG.warning(
            "variant %s for recipe %s manifest-listed but file missing · treating as empty",
            chosen,
            recipe_id,
        )
        return chosen, ""
    try:
        return chosen, path.read_text(encoding="utf-8")
    except OSError as exc:
        _LOG.warning("variant read failed · %s", exc)
        return chosen, ""


def list_all_manifests() -> list[dict[str, Any]]:
    """Scan the addendum dir for every ``*_manifest.json`` · return
    one summary per recipe with a live manifest. Powers the "all
    recipes with A/B running" view in the admin panel · operator
    doesn't have to remember which recipes they've onboarded.

    Summary fields are intentionally minimal (recipe_id, counts,
    updated_at) · the per-recipe detail view hits ``list_variants``
    when the operator clicks through.
    """
    root = _root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for m_path in sorted(root.glob("*_manifest.json")):
        try:
            data = json.loads(m_path.read_text(encoding="utf-8"))
            m = VariantManifest.from_dict(data)
            out.append(
                {
                    "recipe_id": m.recipe_id,
                    "variant_count": len(m.variants),
                    "total_weight": m.total_weight(),
                    "default_weight": m.default_weight,
                    "updated_at": m.updated_at,
                    "manifest_path": str(m_path),
                }
            )
        except (OSError, json.JSONDecodeError) as exc:
            _LOG.warning("manifest scan: %s skipped · %s", m_path, exc)
            continue
    return out


__all__ = [
    "VariantEntry",
    "VariantManifest",
    "variant_path",
    "manifest_path",
    "load_manifest",
    "save_manifest",
    "add_variant",
    "remove_variant",
    "set_weights",
    "list_variants",
    "list_all_manifests",
    "select_variant",
]
