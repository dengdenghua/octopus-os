from __future__ import annotations

"""Echo-native multi-objective evolution scoring.

This is intentionally independent of DSPy/GEPA. It scores prompt candidates
against the signals Echo owns: task scores, repeated failure clusters,
positive examples, prompt size/growth, and hard constraint outcomes.
"""

from dataclasses import asdict, dataclass, field  # noqa: E402
from typing import Any  # noqa: E402

from runtime.safety.recovery.evolution_constraints import (  # noqa: E402
    EvolutionConstraintResult,
    EvolutionConstraintValidator,
)
from runtime.safety.recovery.evolution_dataset import EvolutionDataset  # noqa: E402


@dataclass(slots=True)
class NativeEvolutionWeights:
    task_score: float = 0.42
    constraint: float = 0.22
    failure_coverage: float = 0.16
    positive_preservation: float = 0.12
    efficiency: float = 0.08


@dataclass(slots=True)
class NativeEvolutionScore:
    candidate_id: str
    total: float
    verdict: str
    task_score: float
    constraint_score: float
    failure_coverage: float
    positive_preservation: float
    efficiency: float
    reasons: list[str] = field(default_factory=list)
    constraint_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_candidate_native(
    candidate: Any,
    *,
    baseline_prompt: str | None = None,
    failures: list[dict[str, Any]] | None = None,
    positive_dataset: EvolutionDataset | None = None,
    weights: NativeEvolutionWeights | None = None,
    validator: EvolutionConstraintValidator | None = None,
) -> NativeEvolutionScore:
    weights = weights or NativeEvolutionWeights()
    prompt = str(getattr(candidate, "prompt", "") or "")
    candidate_id = str(getattr(candidate, "candidate_id", "") or "")
    task_score = _avg_score(getattr(candidate, "task_scores", None))

    validator = validator or EvolutionConstraintValidator()
    constraint_results = validator.validate_prompt(
        prompt,
        baseline_prompt=baseline_prompt,
    )
    constraint_score = _constraint_score(constraint_results)
    failure_coverage = _failure_coverage_score(prompt, failures or [])
    positive_preservation = _positive_preservation_score(prompt, positive_dataset)
    efficiency = _efficiency_score(prompt, baseline_prompt)

    total = round(
        task_score * weights.task_score
        + constraint_score * weights.constraint
        + failure_coverage * weights.failure_coverage
        + positive_preservation * weights.positive_preservation
        + efficiency * weights.efficiency,
        3,
    )
    reasons = _score_reasons(
        task_score=task_score,
        constraint_score=constraint_score,
        failure_coverage=failure_coverage,
        positive_preservation=positive_preservation,
        efficiency=efficiency,
    )
    return NativeEvolutionScore(
        candidate_id=candidate_id,
        total=total,
        verdict=_verdict(total, constraint_score=constraint_score),
        task_score=round(task_score, 3),
        constraint_score=round(constraint_score, 3),
        failure_coverage=round(failure_coverage, 3),
        positive_preservation=round(positive_preservation, 3),
        efficiency=round(efficiency, 3),
        reasons=reasons,
        constraint_results=[
            {
                "passed": result.passed,
                "name": result.name,
                "message": result.message,
                "details": result.details,
            }
            for result in constraint_results
        ],
    )


def evaluate_front_native(
    candidates: list[Any],
    *,
    baseline_prompt: str | None = None,
    failures: list[dict[str, Any]] | None = None,
    positive_dataset: EvolutionDataset | None = None,
) -> list[NativeEvolutionScore]:
    scored = [
        score_candidate_native(
            candidate,
            baseline_prompt=baseline_prompt,
            failures=failures,
            positive_dataset=positive_dataset,
        )
        for candidate in candidates
    ]
    return sorted(scored, key=lambda score: (-score.total, score.candidate_id))


def _avg_score(values: Any) -> float:
    if not isinstance(values, list) or not values:
        return 0.5
    nums: list[float] = []
    for value in values:
        try:
            nums.append(max(0.0, min(1.0, float(value))))
        except (TypeError, ValueError):
            continue
    if not nums:
        return 0.5
    return sum(nums) / len(nums)


def _constraint_score(results: list[EvolutionConstraintResult]) -> float:
    if not results:
        return 0.0
    return sum(1.0 for result in results if result.passed) / len(results)


def _failure_coverage_score(prompt: str, failures: list[dict[str, Any]]) -> float:
    if not failures:
        return 0.5
    text = prompt.lower()
    clusters = {
        str(failure.get("failure_cluster") or failure.get("failure_source") or "").lower()
        for failure in failures
        if str(failure.get("failure_cluster") or failure.get("failure_source") or "").strip()
    }
    if not clusters:
        return 0.5
    hits = 0
    for cluster in clusters:
        words = [
            part
            for part in cluster.replace(":", " ").replace("_", " ").split()
            if len(part) >= 4 and part not in {"error", "failed", "failure"}
        ]
        if any(word in text for word in words):
            hits += 1
    return hits / len(clusters)


def _positive_preservation_score(
    prompt: str,
    positive_dataset: EvolutionDataset | None,
) -> float:
    examples = positive_dataset.all_examples if positive_dataset is not None else []
    if not examples:
        return 0.5
    text = prompt.lower()
    risky_phrases = (
        "never use tools",
        "avoid tools",
        "do not call tools",
        "skip verification",
        "do not verify",
        "always answer from memory",
    )
    if any(phrase in text for phrase in risky_phrases):
        return 0.0
    metadata_hits = 0
    total_with_actions = 0
    for example in examples:
        actions = example.metadata.get("action_chain")
        if not isinstance(actions, list) or not actions:
            continue
        total_with_actions += 1
        if any(str(action).lower() in text for action in actions):
            metadata_hits += 1
    if total_with_actions:
        return max(0.5, metadata_hits / total_with_actions)
    return 0.75


def _efficiency_score(prompt: str, baseline_prompt: str | None) -> float:
    if not prompt.strip():
        return 0.0
    if not baseline_prompt:
        return 1.0 if len(prompt) <= 2_000 else 0.4
    baseline_len = max(1, len(baseline_prompt))
    ratio = len(prompt) / baseline_len
    if ratio <= 1.0:
        return 1.0
    if ratio <= 1.25:
        return 0.85
    if ratio <= 1.5:
        return 0.65
    return 0.35


def _score_reasons(
    *,
    task_score: float,
    constraint_score: float,
    failure_coverage: float,
    positive_preservation: float,
    efficiency: float,
) -> list[str]:
    reasons: list[str] = []
    if task_score < 0.55:
        reasons.append("weak task score")
    if constraint_score < 1.0:
        reasons.append("constraint violations")
    if failure_coverage < 0.5:
        reasons.append("low failure-cluster coverage")
    if positive_preservation < 0.5:
        reasons.append("risks regressing successful paths")
    if efficiency < 0.65:
        reasons.append("prompt growth is high")
    if not reasons:
        reasons.append("balanced candidate")
    return reasons


def _verdict(total: float, *, constraint_score: float) -> str:
    if constraint_score < 1.0:
        return "reject"
    if total >= 0.78:
        return "promote"
    if total >= 0.62:
        return "canary"
    if total >= 0.48:
        return "hold"
    return "reject"


__all__ = [
    "NativeEvolutionScore",
    "NativeEvolutionWeights",
    "evaluate_front_native",
    "score_candidate_native",
]
