from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402
from uuid import UUID  # noqa: E402

from runtime.core.cerebrum import LLMPlanner  # noqa: E402
from runtime.platform.models import ParsedIntent, TaskGraph, TaskId  # noqa: E402
from runtime.safety.auth.scope import TenantScope  # noqa: E402

from .variant import ABSplitter, Variant, VariantStats  # noqa: E402


@dataclass(frozen=True)
class PromptVariant:
    name: str
    system_prompt_suffix: str = ""  # Implementation note.
    weight: float = 1.0
    description: str = ""  # Implementation note.

    parents: tuple[str, ...] = ()
    generation: int = 0
    origin: str = "seed"  # seed | mutation | crossover
    reason: str = ""


@dataclass
class VariantReport:
    name: str
    assignments: int
    successes: int
    failures: int
    success_rate: float
    recipe_score: Any = None  # Implementation note.
    recipe_hash: str = ""

    @property
    def verdict(self) -> str:
        if self.recipe_score is not None:
            return self.recipe_score.verdict
        return "insufficient_data"


class PromptOptimizer:
    def __init__(
        self,
        stack: Any,
        variants: list[PromptVariant],
        *,
        sticky: bool = True,
        auto_persist_path: Any = None,  # str | Path | None
    ) -> None:
        if not variants:
            raise ValueError("PromptOptimizer requires at least one variant")
        if not isinstance(stack.planner, LLMPlanner):
            raise TypeError("PromptOptimizer only supports LLMPlanner · StaticPlanner 配方固定")
        self.stack = stack
        self.sticky = sticky
        self.auto_persist_path = auto_persist_path

        self._planner_by_variant: dict[str, LLMPlanner] = {}
        for v in variants:
            cloned = _clone_llm_planner(stack.planner)
            cloned.learned_rules_section = v.system_prompt_suffix
            self._planner_by_variant[v.name] = cloned

        self._splitter = ABSplitter(
            [
                Variant(
                    name=v.name,
                    payload=(v, self._planner_by_variant[v.name]),
                    weight=v.weight,
                )
                for v in variants
            ],
        )
        self._variants = {v.name: v for v in variants}
        self._task_variant_map: dict[str, str] = {}  # Implementation note.
        self._autosave()

    def plan(self, intent: ParsedIntent, task_id: UUID | TaskId | str) -> TaskGraph:
        key = str(task_id)
        v = self._splitter.assign_for(key) if self.sticky else self._splitter.next_variant()
        _variant_obj, planner = v.payload
        self._task_variant_map[key] = v.name

        graph = planner.plan(intent)
        from uuid import UUID as _UUID

        tid_uuid = _UUID(key) if isinstance(key, str) and len(key) == 36 else None
        if tid_uuid is not None:
            graph = graph.model_copy(update={"task_id": TaskId(tid_uuid)})
        return graph

    def record_outcome(self, task_id: UUID | TaskId | str, *, success: bool) -> None:
        key = str(task_id)
        name = self._task_variant_map.get(key)
        if name is None:
            return
        self._splitter.record_outcome(name, success=success)

    def report(
        self,
        journal: Any = None,
        *,
        scope: TenantScope | None = None,
    ) -> dict[str, VariantReport]:
        journal = journal or self.stack.journal

        from runtime.safety.recovery import RecipeEvaluator

        eval_report = RecipeEvaluator(journal, scope=scope).evaluate()
        score_by_hash = {s.recipe_id: s for s in eval_report.scores}

        out: dict[str, VariantReport] = {}
        for name, planner in self._planner_by_variant.items():
            stats: VariantStats = self._splitter.stats[name]
            recipe_hash = planner.recipe_hash()
            recipe_score = score_by_hash.get(recipe_hash)
            out[name] = VariantReport(
                name=name,
                assignments=stats.assignments,
                successes=stats.successes,
                failures=stats.failures,
                success_rate=stats.success_rate,
                recipe_score=recipe_score,
                recipe_hash=recipe_hash,
            )
        return out

    def retire_variant(self, name: str) -> bool:
        if name not in self._variants:
            return False
        if len(self._variants) <= 1:
            return False  # Implementation note.
        retired_obj = self._variants.pop(name)
        if not hasattr(self, "_retired_variants"):
            self._retired_variants: dict[str, PromptVariant] = {}
        self._retired_variants[name] = retired_obj
        self._planner_by_variant.pop(name, None)
        self._rebuild_splitter(preserve_stats=True, retired=name)
        self._autosave()
        return True

    def adjust_weight(self, name: str, new_weight: float) -> bool:
        if new_weight <= 0:
            raise ValueError(f"weight must be > 0 (got {new_weight})")
        if name not in self._variants:
            return False
        old = self._variants[name]
        self._variants[name] = PromptVariant(
            name=old.name,
            system_prompt_suffix=old.system_prompt_suffix,
            weight=new_weight,
            description=old.description,
            parents=old.parents,
            generation=old.generation,
            origin=old.origin,
            reason=old.reason,
        )
        self._rebuild_splitter(preserve_stats=True)
        self._autosave()
        return True

    def add_variant(self, variant: PromptVariant) -> None:
        if variant.name in self._variants:
            raise ValueError(f"duplicate variant name: {variant.name!r}")

        cloned = _clone_llm_planner(self.stack.planner)
        cloned.learned_rules_section = variant.system_prompt_suffix
        self._planner_by_variant[variant.name] = cloned
        self._variants[variant.name] = variant

        self._rebuild_splitter(preserve_stats=True)
        self._autosave()

    def _rebuild_splitter(
        self,
        *,
        preserve_stats: bool = True,
        retired: str | None = None,
    ) -> None:
        new_ab_variants = [
            Variant(
                name=n,
                payload=(self._variants[n], self._planner_by_variant[n]),
                weight=self._variants[n].weight,
            )
            for n in self._variants
        ]
        old_stats = self._splitter.stats if preserve_stats else {}
        if retired is not None and retired in old_stats:
            self._retired_stats = getattr(self, "_retired_stats", {})
            self._retired_stats[retired] = old_stats[retired]
        self._splitter = ABSplitter(new_ab_variants)
        for name, st in old_stats.items():
            if name not in self._splitter.stats:
                continue
            s = self._splitter.stats[name]
            s.assignments = st.assignments
            s.successes = st.successes
            s.failures = st.failures

    @property
    def variant_names(self) -> list[str]:
        return list(self._variants.keys())

    def planner_for(self, variant_name: str) -> LLMPlanner:
        return self._planner_by_variant[variant_name]

    def variant_for_task(self, task_id: UUID | TaskId | str) -> str | None:
        return self._task_variant_map.get(str(task_id))

    def _autosave(self) -> None:
        if self.auto_persist_path is None:
            return
        try:
            from pathlib import Path

            path = Path(self.auto_persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                dump_variants_to_yaml(list(self._variants.values())),
                encoding="utf-8",
            )
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            _logger.debug("PromptOptimizer autosave skipped: %s", exc)

    def lineage(self, name: str, *, max_depth: int = 20) -> list[PromptVariant]:
        chain: list[PromptVariant] = []
        known: dict[str, PromptVariant] = dict(self._variants)
        known.update(getattr(self, "_retired_variants", {}))

        current_name = name
        for _ in range(max_depth):
            v = known.get(current_name)
            if v is None:
                break
            chain.append(v)
            if not v.parents:
                break
            current_name = v.parents[0]
        return chain

    def ancestors_tree(self, name: str) -> dict[str, Any]:
        known: dict[str, PromptVariant] = dict(self._variants)
        known.update(getattr(self, "_retired_variants", {}))
        return _build_ancestors_dict(name, known, visited=set())


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _clone_llm_planner(src: LLMPlanner) -> LLMPlanner:
    cloned = LLMPlanner(
        router=src.router,
        registry=src.registry,
        composer=src.composer,
        planner_model=src.planner_model,
        default_budget=src.default_budget,
        max_nodes=src.max_nodes,
        learned_rules_section=src.learned_rules_section,
        learned_memories_section=src.learned_memories_section,
    )
    cloned.kg = src.kg
    cloned.kg_max_triples = src.kg_max_triples
    cloned.current_recipe_verdict = None  # Implementation note.
    return cloned


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def load_variants_from_yaml(path: Any) -> list[PromptVariant]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError("PyYAML required for load_variants_from_yaml") from e

    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    raw = data.get("variants") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected 'variants' list")

    out: list[PromptVariant] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: variants[{i}] must be a mapping")
        parents_raw = item.get("parents", [])
        parents = tuple(str(p) for p in parents_raw) if parents_raw else ()
        out.append(
            PromptVariant(
                name=item["name"],
                system_prompt_suffix=item.get("system_prompt_suffix", ""),
                weight=float(item.get("weight", 1.0)),
                description=item.get("description", ""),
                parents=parents,
                generation=int(item.get("generation", 0)),
                origin=str(item.get("origin", "seed")),
                reason=str(item.get("reason", "")),
            )
        )
    return out


def dump_variants_to_yaml(variants: list[PromptVariant]) -> str:
    lines = ["variants:"]
    for v in variants:
        suffix_esc = (v.system_prompt_suffix or "").replace("'", "''")
        desc_esc = (v.description or "").replace("'", "''")
        reason_esc = (v.reason or "").replace("'", "''")
        lines.append(f"  - name: {v.name}")
        lines.append(f"    system_prompt_suffix: '{suffix_esc}'")
        lines.append(f"    weight: {v.weight}")
        if desc_esc:
            lines.append(f"    description: '{desc_esc}'")
        if v.parents:
            parents_str = ", ".join(str(p) for p in v.parents)
            lines.append(f"    parents: [{parents_str}]")
        if v.generation > 0:
            lines.append(f"    generation: {v.generation}")
        if v.origin != "seed":
            lines.append(f"    origin: {v.origin}")
        if reason_esc:
            lines.append(f"    reason: '{reason_esc}'")
    return "\n".join(lines) + "\n"


def _build_ancestors_dict(
    name: str,
    known: dict[str, PromptVariant],
    visited: set[str],
) -> dict[str, Any]:
    if name in visited:
        return {"name": name, "cycle": True}
    visited = visited | {name}
    v = known.get(name)
    if v is None:
        return {"name": name, "unknown": True}
    node: dict[str, Any] = {
        "name": v.name,
        "origin": v.origin,
        "generation": v.generation,
    }
    if v.parents:
        node["parents"] = [_build_ancestors_dict(p, known, visited) for p in v.parents]
    return node
