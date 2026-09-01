# ruff: noqa: E402 — module-level imports below intentionally appear
# after the local logger is built so that runtime modules log through
# the configured handler from their first import-time side effects.
from __future__ import annotations

import logging
import threading
from typing import Any

_logger = logging.getLogger(__name__)

from runtime.adapters.instrumentation import trace_stage
from runtime.execution.suckers import SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import Journal
from runtime.platform.models import (
    BudgetSpec,
    ParsedIntent,
    SkillId,
    TaskGraph,
    TaskNode,
)
from runtime.platform.models.llm import (
    Message,
    ModelRequest,
    ModelRouter,
)
from runtime.platform.prompts import get_prompt

# Pure helpers and plan parsing/validation were split into the ``_planner_*``
# submodules to keep this module under 1000 lines. They are re-imported here
# so ``from runtime.core.cerebrum.llm_planner import X`` keeps working for
# callers that reach into the old module-level names.
from ._planner_helpers import (
    _derive_task_type,
    _extract_edges,
    _has_cycle,
    _normalize_node_ref,
    _render_conversation_history,
    _render_team_roster_section,
    _scan_balanced_object,
)
from ._planner_parse import extract_plan_json, validate_plan_nodes
from .planner import PlannerError

# Re-exported helper names that callers reach into from the old module-level
# surface. Ruff keeps them because they are listed in ``__all__``.
__all__ = [
    "_derive_task_type",
    "_extract_edges",
    "_has_cycle",
    "_normalize_node_ref",
    "_render_conversation_history",
    "_render_team_roster_section",
    "_scan_balanced_object",
    "extract_plan_json",
    "validate_plan_nodes",
    "PlannerError",
]

# Output ceiling for the single plan-generation call. Reasoning models
# (e.g. agnes-2.0-flash) spend most of the budget on hidden reasoning
# tokens *before* emitting the plan JSON — at 1024 the reasoning alone
# (~980–1024 tokens) consumed the whole budget and finish_reason=length
# truncated the JSON to empty, so every plan came back with zero nodes.
# This is a ceiling, not a target: non-reasoning models emit the JSON and
# stop well under it, so raising it has no cost for them.
_PLAN_MAX_TOKENS = 4096

_PLANNER_SYSTEM_PROMPT: str = ""


def _load_planner_prompt() -> str:
    global _PLANNER_SYSTEM_PROMPT
    _PLANNER_SYSTEM_PROMPT = get_prompt("planner_base")
    return _PLANNER_SYSTEM_PROMPT


_load_planner_prompt()


class LLMPlanner:
    """LLM-driven task planner: takes a parsed intent, emits a TaskGraph.

    ╔════════════════════════════════════════════════════════════════════╗
    ║ llm_planner.py · navigation map.                                   ║
    ║                                                                    ║
    ║ Free helpers live in submodules:                                   ║
    ║   _planner_helpers.py · prompt-section rendering (team roster /     ║
    ║       conversation history), edge extraction + cycle detection,     ║
    ║       balanced-brace scan, _derive_task_type.                      ║
    ║   _planner_parse.py  · plan-JSON extraction + node validation.     ║
    ║                                                                    ║
    ║ This module keeps the LLMPlanner class (the core entrypoint).      ║
    ╚════════════════════════════════════════════════════════════════════╝
    """

    def __init__(
        self,
        router: ModelRouter,
        registry: SkillRegistry,
        composer: ContextComposer,
        planner_model: str = "mock/planner",
        default_budget: BudgetSpec | None = None,
        max_nodes: int = 10,
        learned_rules_section: str = "",
        learned_memories_section: str = "",
        *,
        auto_persist_rules_path: Any = None,  # str | Path | None
        auto_persist_memories_path: Any = None,  # str | Path | None
    ) -> None:
        self.router = router
        self.registry = registry
        self.composer = composer
        self.planner_model = planner_model
        self.default_budget = default_budget or BudgetSpec(tokens=50_000, usd=0.50)
        self.max_nodes = max_nodes
        self.auto_persist_rules_path = auto_persist_rules_path
        self.auto_persist_memories_path = auto_persist_memories_path

        if auto_persist_rules_path is not None:
            from .prompt_persistence import load_section

            loaded = load_section(auto_persist_rules_path)
            if loaded:
                learned_rules_section = loaded
        if auto_persist_memories_path is not None:
            from .prompt_persistence import load_section

            loaded = load_section(auto_persist_memories_path)
            if loaded:
                learned_memories_section = loaded

        self.learned_rules_section = learned_rules_section
        self.learned_memories_section = learned_memories_section
        self.kg: Any = None
        self.kg_max_triples: int = 15
        # When True, ``learn_kg_from_journal`` accumulates into the attached
        # durable KG instead of rebuilding a throwaway in-memory graph — so
        # learned facts survive restarts (see ``enable_persistent_kg``).
        self._kg_persistent: bool = False
        self.current_recipe_verdict: Any = None  # RecipeScore | None
        self._rules_updated_count = 0  # Implementation note.
        self._memories_updated_count = 0
        self._kg_attached_count = 0
        self._recipe_assessed_count = 0
        self._plan_usage_local = threading.local()
        self._last_chosen_variant: str | None = None

    @property
    def last_plan_usage(self) -> dict[str, int]:
        usage_local = getattr(self, "_plan_usage_local", None)
        if usage_local is None:
            return {}
        usage = getattr(usage_local, "last_plan_usage", None)
        return dict(usage) if isinstance(usage, dict) else {}

    @last_plan_usage.setter
    def last_plan_usage(self, value: dict[str, int] | None) -> None:
        if not hasattr(self, "_plan_usage_local"):
            self._plan_usage_local = threading.local()
        self._plan_usage_local.last_plan_usage = dict(value or {})

    def update_learned_rules(self, rules: list, *, max_total_chars: int = 2000) -> None:
        from runtime.safety.recovery import format_rules_for_prompt

        self.learned_rules_section = format_rules_for_prompt(rules, max_total_chars=max_total_chars)
        self._rules_updated_count += 1
        with trace_stage("cerebrum.rules_updated") as span:
            span.set_attribute("echo.rules.count", len(rules))
            span.set_attribute(
                "echo.rules.prompt_section_chars",
                len(self.learned_rules_section),
            )
        self._autosave_rules()

    def learn_from_journal(
        self,
        journal: Journal,
        *,
        min_hits: int = 3,
        max_rules: int = 30,
    ) -> int:
        from runtime.safety.recovery import ExtractorConfig, RuleExtractor

        extractor = RuleExtractor(
            journal=journal,
            config=ExtractorConfig(min_hits=min_hits, max_rules_per_run=max_rules),
        )
        report = extractor.extract()
        self.update_learned_rules(report.rules_produced)
        return len(report.rules_produced)

    def update_learned_memories(self, memories: list, *, max_total_chars: int = 2000) -> None:
        from runtime.safety.recovery import format_memories_for_prompt

        self.learned_memories_section = format_memories_for_prompt(
            memories, max_total_chars=max_total_chars
        )
        self._memories_updated_count += 1
        with trace_stage("cerebrum.memories_updated") as span:
            span.set_attribute("echo.memories.count", len(memories))
            span.set_attribute(
                "echo.memories.prompt_section_chars",
                len(self.learned_memories_section),
            )
        self._autosave_memories()

    def learn_memories_from_journal(self, journal: Journal) -> int:
        from runtime.safety.recovery import MemoryConsolidator

        report = MemoryConsolidator(journal).consolidate()
        self.update_learned_memories(report.memories_produced)
        return len(report.memories_produced)

    def attach_kg(self, kg: Any, *, max_triples: int = 15) -> None:
        self.kg = kg
        self.kg_max_triples = max_triples
        self._kg_attached_count += 1
        with trace_stage("cerebrum.kg_attached") as span:
            size = kg.count() if hasattr(kg, "count") else 0
            span.set_attribute("echo.kg.triples", size)
            span.set_attribute("echo.kg.max_triples", max_triples)

    def enable_persistent_kg(self, db_path: Any, *, max_triples: int | None = None) -> int:
        """Back the planner's KG with a durable on-disk store.

        Once enabled, :meth:`learn_kg_from_journal` ACCUMULATES distilled
        triples into this store instead of rebuilding a throwaway in-memory
        graph each call, so knowledge survives process restarts and compounds
        across sessions — the durable half of the self-evolution loop. Triples
        already on disk are loaded immediately, so recall sees them on the very
        first turn. Returns the triple count loaded from disk.
        """
        from runtime.memory.knowledge_graph.sqlite_kg import SqliteKnowledgeGraph

        kg = SqliteKnowledgeGraph(db_path)
        self.attach_kg(
            kg,
            max_triples=max_triples if max_triples is not None else self.kg_max_triples,
        )
        self._kg_persistent = True
        return kg.count()

    def learn_kg_from_journal(self, journal: Journal, *, max_triples: int | None = None) -> int:
        from runtime.safety.recovery import KGUpdater

        if max_triples is not None:
            self.kg_max_triples = max_triples

        # Durable path: accumulate into the attached persistent store (de-duped
        # + persisted) rather than discarding a fresh graph each call. This is
        # not a re-attach, so ``_kg_attached_count`` is left untouched.
        if self._kg_persistent and self.kg is not None:
            KGUpdater(journal, self.kg).update()
            return self.kg.count()

        # Legacy ephemeral path (unchanged): rebuild an in-memory graph.
        from runtime.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        KGUpdater(journal, kg).update()
        self.attach_kg(kg, max_triples=self.kg_max_triples)
        return kg.count()

    def _render_kg_section(self) -> str:
        if self.kg is None:
            return ""
        from runtime.memory.knowledge_graph import format_triples_for_prompt

        triples = self.kg.query()  # Implementation note.
        return format_triples_for_prompt(triples, max_triples=self.kg_max_triples)

    def _render_codebase_section(self, intent: Any) -> str:
        """Auto-retrieve codebase grounding the context composer never provides
        (wiki pages + source chunks for the goal). Shared with the react chat
        loop via ``render_codebase_context``. Disable with
        ECHO_CODEBASE_CONTEXT=0."""
        from runtime.memory.hemolymph.repo_context import render_codebase_context

        try:
            return render_codebase_context(
                str(getattr(intent, "normalized_goal", "") or ""),
            )
        except Exception:  # noqa: BLE001 — grounding must never break planning
            return ""

    def assess_recipe_from_journal(self, journal: Journal) -> Any:
        from runtime.safety.recovery import RecipeEvaluator

        report = RecipeEvaluator(journal).evaluate()
        my_hash = self.recipe_hash()
        match = next((s for s in report.scores if s.recipe_id == my_hash), None)
        self.current_recipe_verdict = match
        self._recipe_assessed_count += 1
        with trace_stage("cerebrum.recipe_assessed") as span:
            span.set_attribute("echo.recipe.id", my_hash)
            span.set_attribute(
                "echo.recipe.verdict",
                match.verdict if match else "not_found",
            )
            if match is not None:
                span.set_attribute("echo.recipe.score", match.score)
        return match

    def _render_recipe_self_assessment(self) -> str:
        v = self.current_recipe_verdict
        if v is None or v.verdict != "losing":
            return ""
        return (
            "RECIPE SELF-ASSESSMENT (warning):\n"
            f"  Your current prompt recipe ({v.recipe_id}) has been scored "
            f"as LOSING from {v.uses} past runs (success rate "
            f"{v.success_rate * 100:.0f}%, avg ${v.avg_cost_usd:.4f}).\n"
            "  Consider being more conservative: prefer fewer, safer steps; "
            "avoid repeating patterns that previously failed."
        )

    def plan(
        self,
        intent: ParsedIntent,
        *,
        allowed_skills: list[str] | None = None,
        soul: str | None = None,
        model: str | None = None,
    ) -> TaskGraph:
        base_prompt = _PLANNER_SYSTEM_PROMPT
        from datetime import datetime as _dt

        base_prompt += (
            f"\n\n当前日期: {_dt.now().strftime('%Y-%m-%d %A')}。"
            " 搜索时请注意信息时效性,优先引用最新来源。"
        )
        if soul:
            base_prompt = f"# Agent Soul\n\n{soul}\n\n---\n\n" + base_prompt
        # Team-mode awareness · when the turn runs inside a group-chat
        # thread, tell the LLM which teammates exist so it stops
        # Source · intent.user_context["agent_roster"] · populated by the
        # turn-builder (``build_turn_session`` / realtime gateway) when
        # the thread values / metadata supply a roster. Absent roster
        # → no section, prompt length unchanged for solo turns.
        team_section = _render_team_roster_section(intent.user_context)
        if team_section:
            base_prompt = base_prompt + "\n\n" + team_section
        if self.learned_rules_section:
            base_prompt = base_prompt + "\n\n" + self.learned_rules_section
        if self.learned_memories_section:
            base_prompt = base_prompt + "\n\n" + self.learned_memories_section
        # GEPA-optimized addendum · written by
        # ``/api/evolution/gepa/apply``. Re-read on every plan() call
        # so a hot apply is picked up without restarting the planner
        # instance · cheap since the file is small + OS-cached.
        #
        # Two sources, concatenated in this order so per-recipe
        # instructions take precedence by recency:
        #
        #   1. Legacy global ``data/gepa_planner_addendum.md`` ·
        #      affects all turns regardless of recipe. Kept for
        #      backward compat with first-cut deployments.
        #   2. Per-recipe ``data/gepa_addendums/<base_recipe_id>.md``
        #      · only fires when this planner's BASE recipe_hash
        #      matches. Computed BEFORE the addendum gets folded in
        #      (recipe_hash() doesn't include the addendum), so
        #      there's no chicken-and-egg cycle.
        try:
            from runtime.safety.recovery.gepa_addendum_store import (
                load_for_recipe,
                load_global,
            )

            _base_recipe_id = self.recipe_hash()
            _conv_id = (
                intent.user_context.get("conversation_id")
                if isinstance(intent.user_context, dict)
                else None
            )
            # The governed candidate registry is authoritative. Legacy GEPA
            # addenda remain a compatibility fallback until their records are
            # migrated into the typed lifecycle.
            _candidate_global_id = None
            _candidate_global_content = ""
            _candidate_recipe_id = None
            _candidate_recipe_content = ""
            try:
                from runtime.safety.evolution.runtime_deployment import (
                    default_runtime_selector,
                )

                _candidate_selector = default_runtime_selector()
                _candidate_global_id, _candidate_global_content = (
                    _candidate_selector.prompt_addendum(
                        "planner.prompt:__global__",
                        routing_key=_conv_id,
                    )
                )
                _candidate_recipe_id, _candidate_recipe_content = (
                    _candidate_selector.prompt_addendum(
                        f"planner.prompt:{_base_recipe_id}",
                        routing_key=_conv_id,
                    )
                )
            except (OSError, ImportError, ValueError):
                pass

            _global_section = _candidate_global_content or load_global()
            if _global_section:
                base_prompt = base_prompt + "\n\n" + _global_section

            if _candidate_recipe_id is not None:
                if _candidate_recipe_content:
                    base_prompt = base_prompt + "\n\n" + _candidate_recipe_content
                self._last_chosen_variant = f"candidate:{_candidate_recipe_id}"
            else:
                # Multi-variant lookup FIRST · when a manifest exists for
                # this recipe, A/B-split traffic with a sticky conversation
                # bucket. Otherwise fall back to the single-file addendum.
                try:
                    from runtime.safety.recovery.gepa_variants import (
                        select_variant,
                    )

                    _variant_id, _variant_content = select_variant(
                        _base_recipe_id,
                        _conv_id,
                    )
                except (OSError, ImportError, ValueError):
                    _variant_id, _variant_content = None, ""
                if _variant_id is not None:
                    if _variant_content:
                        base_prompt = base_prompt + "\n\n" + _variant_content
                    self._last_chosen_variant = _variant_id
                else:
                    _recipe_section = load_for_recipe(_base_recipe_id)
                    if _recipe_section:
                        base_prompt = base_prompt + "\n\n" + _recipe_section
                    self._last_chosen_variant = None
        except (OSError, ImportError, ValueError) as exc:
            _logger.debug("recipe/variant load skipped: %s", exc)
        kg_section = self._render_kg_section()
        if kg_section:
            base_prompt = base_prompt + "\n\n" + kg_section
        codebase_section = self._render_codebase_section(intent)
        if codebase_section:
            base_prompt = base_prompt + "\n\n" + codebase_section
        recipe_warning = self._render_recipe_self_assessment()
        if recipe_warning:
            base_prompt = base_prompt + "\n\n" + recipe_warning

        from runtime.safety.recovery.tenant_scope import trusted_scope_from_user_context

        memory_scope = trusted_scope_from_user_context(intent.user_context)
        packet = self.composer.compose(
            task_info=intent,
            system_prompt=base_prompt,
            budget_tokens=8_000,  # Implementation note.
            relevant_skills=allowed_skills,
            scope=memory_scope,
        )

        system_parts = [s.content for s in packet.segments if s.bucket == "system"]
        sucker_parts = [s.content for s in packet.segments if s.bucket == "suckers"]
        user_parts = [f"USER GOAL: {intent.normalized_goal}"]
        from runtime.memory.users.profile import render_profile_memories

        profile_section = render_profile_memories(
            intent.user_context.get("profile_memories", []),
        )
        if profile_section:
            user_parts.insert(0, profile_section)
        conversation_history = _render_conversation_history(intent)
        if conversation_history:
            user_parts.insert(
                0,
                f"CONVERSATION HISTORY (oldest to newest):\n{conversation_history}",
            )

        messages = [
            Message(role="system", content="\n\n".join(system_parts)),
            Message(role="user", content="\n\n".join(sucker_parts + user_parts)),
        ]

        prefer = "default"
        if (
            self.current_recipe_verdict is not None
            and getattr(self.current_recipe_verdict, "verdict", None) == "losing"
        ):
            prefer = "strong"
        response = self.router.call(
            ModelRequest(
                model=model or self.planner_model,
                messages=messages,
                max_tokens=_PLAN_MAX_TOKENS,
                temperature=0.0,
                system_provider="anthropic",
                prefer_strength=prefer,  # type: ignore[arg-type]
            )
        )

        # Carry planner-call usage on the immutable TaskGraph so the
        # accounting data follows the turn that produced it. The legacy
        # instance attribute is kept as a best-effort compatibility mirror,
        # but callers must not treat it as authoritative in concurrent runs.
        planner_usage = {
            "input_tokens": int(getattr(response, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(response, "output_tokens", 0) or 0),
        }
        self.last_plan_usage = dict(planner_usage)

        plan_dict = self._extract_json(response.text)
        nodes = self._validate_nodes(plan_dict.get("nodes", []))

        # (explicit depends_on > template refs > linear fallback).
        # Pre-2026-04 edges were unconditionally linear, which made
        # swarm split_strategy="topo_layers" degenerate to "single" —
        # killing all parallelism the LLM could have expressed.
        edges = _extract_edges(nodes, len(nodes))
        return TaskGraph(
            nodes=[
                TaskNode(
                    node_id=f"n{i}",
                    skill_ref=SkillId(nd["skill"]),
                    kind="sucker",
                    args_template=nd.get("args") or {},
                )
                for i, nd in enumerate(nodes)
            ],
            edges=edges,
            budget=self.default_budget,
            strategy="llm_planner",
            task_type=_derive_task_type(intent),
            planner_usage=planner_usage,
            # When a GEPA variant was picked for this turn, stamp it
            # onto the recipe_hash so RecipeEvaluator naturally groups
            # success/failure by (recipe + variant). Format:
            #   ``llm@bad42``        · base recipe, no variant
            #   ``llm@bad42#vA``     · variant vA picked
            #   ``llm@bad42#__default__`` · control branch (no addendum)
            # The base hash is unchanged; the suffix is purely for
            # downstream attribution. ``_last_chosen_variant`` is set
            # in the variant-selection block above.
            recipe_hash=self._compose_trajectory_recipe_id(),
        )

    def _compose_trajectory_recipe_id(self) -> str:
        """Build the recipe_id stamped on this turn's TaskGraph ·
        base ``recipe_hash()`` plus the GEPA-variant suffix when a
        variant was picked. Set on the planner instance by the
        variant-selection block in plan().

        Suffix conventions:
          * ``""`` (empty)       → control branch picked (no addendum
                                   content was injected). We stamp
                                   the suffix anyway so the evaluator
                                   can group "no-addendum baseline"
                                   separately from "no-manifest base".
          * ``"vA"`` etc          → that named variant was picked
          * ``None``              → no manifest at all (legacy single
                                   file or no addendum) · no suffix
        """
        base = self.recipe_hash()
        v = getattr(self, "_last_chosen_variant", None)
        if v is None:
            return base
        if v == "":
            return f"{base}#__default__"
        return f"{base}#{v}"

    def recipe_hash(self) -> str:
        import hashlib

        kg_fingerprint = ""
        if self.kg is not None and hasattr(self.kg, "count"):
            kg_fingerprint = f"kg@{self.kg.count()}@{self.kg_max_triples}"
        payload = "|".join(
            [
                self.planner_model,
                _PLANNER_SYSTEM_PROMPT,
                self.learned_rules_section,
                self.learned_memories_section,
                kg_fingerprint,
            ]
        )
        h = hashlib.blake2b(payload.encode("utf-8"), digest_size=4).hexdigest()
        return f"llm@{h}"

    def _extract_json(self, text: str) -> dict:
        """Extract the LLM's JSON plan from free-form text.

        Delegates to :func:`_planner_parse.extract_plan_json` — the
        fenced-block / balanced-brace extraction logic lives there.
        """
        return extract_plan_json(text)

    def _validate_nodes(
        self,
        nodes: list,
    ) -> list[dict]:
        """Validate the LLM's raw plan nodes against the skill registry.

        Delegates to :func:`_planner_parse.validate_plan_nodes`.
        """
        return validate_plan_nodes(nodes, self.registry, self.max_nodes)

    def _autosave_rules(self) -> None:
        if self.auto_persist_rules_path is None:
            return
        try:
            from .prompt_persistence import dump_section

            dump_section(
                self.auto_persist_rules_path,
                self.learned_rules_section,
                label="learned_rules",
            )
        except OSError as e:
            # Disk full / permission denied / path invalid. Don't
            # fail the turn · the rules live in memory and can re-
            # persist next turn. Logger rather than silent drop so
            # operators see recurring write failures.
            _logger.warning(
                "learned_rules autosave failed (%s): %s",
                type(e).__name__,
                e,
            )

    def _autosave_memories(self) -> None:
        if self.auto_persist_memories_path is None:
            return
        try:
            from .prompt_persistence import dump_section

            dump_section(
                self.auto_persist_memories_path,
                self.learned_memories_section,
                label="learned_memories",
            )
        except OSError as e:
            _logger.warning(
                "learned_memories autosave failed (%s): %s",
                type(e).__name__,
                e,
            )
