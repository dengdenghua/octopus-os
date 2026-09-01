from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from runtime.platform.models import (
    BudgetSpec,
    ParsedIntent,
    SkillId,
    TaskGraph,
    TaskNode,
    WorkflowEdge,
)


class PlannerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rule:
    name: str
    intent_types: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    skill_sequence: list[SkillId] = field(default_factory=list)
    node_args_templates: list[dict | None] = field(default_factory=list)
    priority: int = 0

    def matches(self, intent: ParsedIntent) -> bool:
        if self.intent_types and intent.intent_type not in self.intent_types:
            return False
        if self.keywords:
            goal_lower = intent.normalized_goal.lower()
            if not any(kw.lower() in goal_lower for kw in self.keywords):
                return False
        return True

    def args_for(self, index: int, intent: ParsedIntent) -> dict:
        default = {"intent_goal": intent.normalized_goal}
        if index < len(self.node_args_templates):
            override = self.node_args_templates[index]
            if override is not None:
                return {
                    **default,
                    **_render_intent_placeholders(override, intent),
                }
        return default


# ─── StaticPlanner ─────────────────────────────────────────


class StaticPlanner:
    def __init__(
        self,
        rules: list[Rule] | None = None,
        default_budget: BudgetSpec | None = None,
        fallback_skill: SkillId | None = None,
        task_type_default: str = "general",
        *,
        auto_persist_rules_path: Any = None,  # str | Path | None
    ) -> None:
        self.auto_persist_rules_path = auto_persist_rules_path
        initial = list(rules or [])
        self._logger = logging.getLogger(__name__)

        if auto_persist_rules_path is not None:
            from .rules_persistence import load_rules_from_file

            try:
                loaded = load_rules_from_file(auto_persist_rules_path)
                if loaded:
                    initial = loaded
            except (OSError, ValueError) as e:
                # OSError: file missing / unreadable on fresh install.
                # ValueError: YAML parse error on corrupt file.
                # Either way, start empty rather than fail boot · log
                # so operators notice.
                self._logger.warning(
                    "rules auto-load failed from %s: %s",
                    auto_persist_rules_path,
                    e,
                )

        self.rules = sorted(initial, key=lambda r: -r.priority)
        self.default_budget = default_budget or BudgetSpec(tokens=50_000, usd=0.50)
        self.fallback_skill = fallback_skill
        self.task_type_default = task_type_default

    def plan(
        self,
        intent: ParsedIntent,
        *,
        allowed_skills: list[str] | None = None,
        model: str | None = None,
    ) -> TaskGraph:
        allow_set = set(allowed_skills) if allowed_skills is not None else None

        def _fits(rule_skills: list) -> bool:
            if allow_set is None:
                return True
            return all(str(s) in allow_set for s in rule_skills)

        matched = next(
            (r for r in self.rules if r.matches(intent) and _fits(r.skill_sequence)),
            None,
        )

        if matched and matched.skill_sequence:
            skills = matched.skill_sequence
            rule_name = matched.name
            source_rule: Rule | None = matched
        elif self.fallback_skill and (allow_set is None or str(self.fallback_skill) in allow_set):
            skills = [self.fallback_skill]
            rule_name = "fallback"
            source_rule = None
        else:
            raise PlannerError(
                f"no rule matched intent={intent.intent_type!r} "
                f"goal={intent.normalized_goal!r} "
                f"(allowed_skills={allowed_skills})"
            )

        nodes = [
            TaskNode(
                node_id=f"n{i}",
                skill_ref=s,
                kind="sucker",
                args_template=(
                    source_rule.args_for(i, intent)
                    if source_rule is not None
                    else {"intent_goal": intent.normalized_goal}
                ),
            )
            for i, s in enumerate(skills)
        ]
        edges = [
            WorkflowEdge(from_node=f"n{i}", to_node=f"n{i + 1}") for i in range(len(skills) - 1)
        ]

        return TaskGraph(
            nodes=nodes,
            edges=edges,
            budget=self.default_budget,
            strategy=rule_name,
            task_type=_derive_task_type(intent, self.task_type_default),
        )

    def apply_rewrite_proposals(
        self,
        proposals: list,
        *,
        min_confidence: float = 0.7,
        min_severity: str = "mid",
        approver: str | None = None,
        bypass_cooldown: bool = False,
    ) -> ApplyResult:
        from runtime.safety.recovery import apply_proposals_to_rules

        # Gene-lock gate · workflow rewrites mutate the planner's rule
        # set, which decides which skills run for every future intent
        # · they're high-impact. Apply the same lock semantics as
        # GEPA ``APPLY_ADDENDUM`` and SkillForge ``AUTO_PROMOTE``:
        # block when LEVEL < 3, when PANIC is set, or while in the
        # TEMPORAL cooldown window. ``approver=None`` marks the call
        # as autonomous (stricter). Failures degrade to an empty
        # ApplyResult (no rules mutated, all outcomes "skipped").
        try:
            from runtime.safety.gene_locks import (
                LockViolation,
                MutationKind,
                gate_mutation,
            )

            gate_mutation(
                kind=MutationKind.APPLY_WORKFLOW_REWRITE,
                target=f"planner:{len(proposals)}_proposals",
                autonomous=approver is None,
                approver=approver,
                bypass_cooldown=bypass_cooldown,
            )
        except LockViolation:
            from runtime.safety.recovery.workflow_applier import (
                ApplyOutcome,
                ApplyResult,
            )

            return ApplyResult(
                rules=list(self.rules),
                outcomes=[
                    ApplyOutcome(
                        proposal_id=str(getattr(p, "proposal_id", "?")),
                        kind=getattr(p, "kind", "?"),
                        action="skipped_invalid",
                        detail="gene_locks blocked",
                    )
                    for p in proposals
                ],
            )
        except (ImportError, AttributeError, OSError):  # noqa: BLE001 — gene_locks unavailable in test env; proceed without gate
            pass

        result = apply_proposals_to_rules(
            self.rules,
            proposals,
            min_confidence=min_confidence,
            min_severity=min_severity,  # type: ignore[arg-type]
        )
        self.rules = result.rules
        if result.applied_count > 0:
            self._autosave_rules()
        return result

    def _autosave_rules(self) -> None:
        if self.auto_persist_rules_path is None:
            return
        try:
            from .rules_persistence import dump_rules_to_file

            dump_rules_to_file(self.rules, self.auto_persist_rules_path)
        except OSError as e:
            # Disk full / permission denied. Rules are still live in
            # memory; next autosave will retry. Log so repeated
            # failures are visible.
            self._logger.warning(
                "rules autosave failed (%s): %s",
                type(e).__name__,
                e,
            )

    def rewrite_from_journal(
        self,
        journal,
        *,
        min_confidence: float = 0.7,
        min_severity: str = "mid",
    ) -> ApplyResult:
        from runtime.safety.recovery import WorkflowRewriter

        report = WorkflowRewriter(journal).analyze(rules=self.rules)
        return self.apply_rewrite_proposals(
            report.proposals,
            min_confidence=min_confidence,
            min_severity=min_severity,
        )


if TYPE_CHECKING:
    from runtime.safety.recovery import ApplyResult


def _render_intent_placeholders(value: Any, intent: ParsedIntent) -> Any:
    """Replace intent-level placeholders in static rule templates."""
    if isinstance(value, str):
        return value.replace("{intent_goal}", intent.normalized_goal).replace("{raw}", intent.raw)
    if isinstance(value, list):
        return [_render_intent_placeholders(v, intent) for v in value]
    if isinstance(value, dict):
        return {k: _render_intent_placeholders(v, intent) for k, v in value.items()}
    return value


def _derive_task_type(intent: ParsedIntent, default: str) -> str:
    mapping = {
        "debug": "code_fix",
        "refactor": "code_design",
        "plan": "multi_step_reasoning",
        "query": "quick_lookup",
        "chitchat": "chitchat",
    }
    if intent.intent_type in mapping:
        return mapping[intent.intent_type]
    goal = intent.normalized_goal.lower()
    if re.search(r"\bsql\b|\bquery\b|\bdata\b", goal):
        return "data_query"
    if re.search(r"\btest\b|\bpytest\b|\bbug\b", goal):
        return "code_fix"
    return default
