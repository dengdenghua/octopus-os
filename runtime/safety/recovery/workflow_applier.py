from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from runtime.core.cerebrum.planner import Rule

from .workflow_rewriter import RewriteProposal, Severity

PriorityPolicy = Literal["halve", "subtract"]


@dataclass(frozen=True)
class ApplyOutcome:
    proposal_id: str
    kind: str
    action: Literal[
        "applied",
        "skipped_threshold",
        "skipped_unknown_target",
        "skipped_duplicate",
        "skipped_invalid",
    ]
    detail: str = ""


@dataclass(frozen=True)
class ApplyResult:
    rules: list[Rule]
    outcomes: list[ApplyOutcome] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "applied")

    @property
    def skipped_count(self) -> int:
        return sum(1 for o in self.outcomes if o.action != "applied")


_SEVERITY_ORDER: dict[Severity, int] = {"low": 0, "mid": 1, "high": 2}


def apply_proposals_to_rules(
    rules: list[Rule],
    proposals: list[RewriteProposal],
    *,
    min_confidence: float = 0.7,
    min_severity: Severity = "mid",
    priority_policy: PriorityPolicy = "subtract",
    priority_step: int = 5,
) -> ApplyResult:
    accepted_sev = _SEVERITY_ORDER[min_severity]
    by_name: dict[str, Rule] = {r.name: r for r in rules}
    new_rules: list[Rule] = [r for r in rules]  # Implementation note.
    outcomes: list[ApplyOutcome] = []
    existing_names = set(by_name.keys())

    for p in proposals:
        pid = str(p.proposal_id)
        if p.confidence < min_confidence or _SEVERITY_ORDER[p.severity] < accepted_sev:
            outcomes.append(
                ApplyOutcome(
                    proposal_id=pid,
                    kind=p.kind,
                    action="skipped_threshold",
                    detail=f"conf={p.confidence:.2f} sev={p.severity}",
                )
            )
            continue

        if p.kind == "remove_degraded_step":
            result = _apply_remove_step(new_rules, p)
        elif p.kind == "lower_rule_priority":
            result = _apply_lower_priority(new_rules, p, priority_policy, priority_step)
        elif p.kind == "propose_new_rule":
            result = _apply_new_rule(new_rules, p, existing_names)
        elif p.kind == "merge_redundant_adjacent":
            result = _apply_merge_adjacent(new_rules, p)
        else:
            result = ApplyOutcome(
                proposal_id=pid,
                kind=p.kind,
                action="skipped_invalid",
                detail=f"unknown kind {p.kind!r}",
            )
        outcomes.append(result)

    new_rules.sort(key=lambda r: -r.priority)
    return ApplyResult(rules=new_rules, outcomes=outcomes)


def _apply_remove_step(rules: list[Rule], p: RewriteProposal) -> ApplyOutcome:
    pid = str(p.proposal_id)
    if p.target_rule_name is None or p.target_step_index is None:
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_invalid",
            detail="missing target",
        )
    idx_target = next((i for i, r in enumerate(rules) if r.name == p.target_rule_name), None)
    if idx_target is None:
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_unknown_target",
            detail=p.target_rule_name,
        )
    old = rules[idx_target]
    step_i = p.target_step_index
    if step_i < 0 or step_i >= len(old.skill_sequence):
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_invalid",
            detail=f"step_index {step_i} out of range",
        )
    new_sequence = [s for i, s in enumerate(old.skill_sequence) if i != step_i]
    new_templates = [t for i, t in enumerate(old.node_args_templates) if i != step_i]
    rules[idx_target] = replace(
        old,
        skill_sequence=new_sequence,
        node_args_templates=new_templates,
    )
    return ApplyOutcome(
        proposal_id=pid,
        kind=p.kind,
        action="applied",
        detail=f"{old.name}: removed step {step_i}",
    )


def _apply_lower_priority(
    rules: list[Rule],
    p: RewriteProposal,
    policy: PriorityPolicy,
    step: int,
) -> ApplyOutcome:
    pid = str(p.proposal_id)
    if p.target_rule_name is None:
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_invalid",
            detail="missing target_rule_name",
        )
    idx_target = next((i for i, r in enumerate(rules) if r.name == p.target_rule_name), None)
    if idx_target is None:
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_unknown_target",
            detail=p.target_rule_name,
        )
    old = rules[idx_target]
    new_prio = max(0, old.priority // 2) if policy == "halve" else max(0, old.priority - step)
    rules[idx_target] = replace(old, priority=new_prio)
    return ApplyOutcome(
        proposal_id=pid,
        kind=p.kind,
        action="applied",
        detail=f"{old.name}: priority {old.priority} → {new_prio}",
    )


def _apply_new_rule(rules: list[Rule], p: RewriteProposal, existing: set[str]) -> ApplyOutcome:
    pid = str(p.proposal_id)
    if not p.suggested_skill_sequence:
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_invalid",
            detail="empty skill sequence",
        )
    name = p.target_rule_name or f"learned_{pid[:8]}"
    if name in existing:
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_duplicate",
            detail=name,
        )
    rules.append(
        Rule(
            name=name,
            intent_types=[],
            keywords=[],
            skill_sequence=list(p.suggested_skill_sequence),
            priority=5,
        )
    )
    existing.add(name)
    return ApplyOutcome(
        proposal_id=pid,
        kind=p.kind,
        action="applied",
        detail=f"added rule {name!r} ({len(p.suggested_skill_sequence)} steps)",
    )


def _apply_merge_adjacent(rules: list[Rule], p: RewriteProposal) -> ApplyOutcome:
    pid = str(p.proposal_id)
    if p.target_rule_name is None:
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_invalid",
            detail="missing target_rule_name",
        )
    idx_target = next((i for i, r in enumerate(rules) if r.name == p.target_rule_name), None)
    if idx_target is None:
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_unknown_target",
            detail=p.target_rule_name,
        )
    old = rules[idx_target]
    deduped: list = []
    deduped_templates: list = []
    prev = None
    for i, s in enumerate(old.skill_sequence):
        if s == prev:
            continue
        deduped.append(s)
        deduped_templates.append(
            old.node_args_templates[i] if i < len(old.node_args_templates) else None
        )
        prev = s
    if len(deduped) == len(old.skill_sequence):
        return ApplyOutcome(
            proposal_id=pid,
            kind=p.kind,
            action="skipped_invalid",
            detail="no adjacent duplicates",
        )
    rules[idx_target] = replace(old, skill_sequence=deduped, node_args_templates=deduped_templates)
    return ApplyOutcome(
        proposal_id=pid,
        kind=p.kind,
        action="applied",
        detail=f"{old.name}: {len(old.skill_sequence)} → {len(deduped)} steps",
    )
