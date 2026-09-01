from __future__ import annotations

"""Cheap, deterministic replay for native prompt evolution.

The replay layer does not execute tools or call a judge model. It turns
Echo-owned examples into explainable checks so evolution can reject
prompt mutations that look good on average but fail known failure modes or
damage previously successful flows.
"""

import re  # noqa: E402
from dataclasses import asdict, dataclass, field  # noqa: E402
from typing import Any  # noqa: E402

from runtime.safety.recovery.evolution_dataset import (  # noqa: E402
    EvolutionDataset,
    EvolutionExample,
)
from runtime.safety.recovery.native_evolution_eval import (  # noqa: E402
    NativeEvolutionScore,
    score_candidate_native,
)


@dataclass(frozen=True, slots=True)
class ReplayCase:
    case_id: str
    kind: str
    task_input: str
    expected_behavior: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReplayCaseResult:
    case_id: str
    kind: str
    score: float
    weight: float
    matched_signals: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReplayCandidateReport:
    candidate_id: str
    total: float
    native_score: dict[str, Any]
    case_results: list[ReplayCaseResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["case_results"] = [result.to_dict() for result in self.case_results]
        return data


@dataclass(frozen=True, slots=True)
class ReplayReport:
    candidates: list[ReplayCandidateReport] = field(default_factory=list)
    cases: list[ReplayCase] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "cases": [case.to_dict() for case in self.cases],
        }


def build_replay_cases(
    *,
    failures: list[dict[str, Any]] | None = None,
    positive_dataset: EvolutionDataset | None = None,
    failure_limit: int = 20,
    positive_limit: int = 20,
) -> list[ReplayCase]:
    cases: list[ReplayCase] = []
    for idx, failure in enumerate((failures or [])[: max(0, int(failure_limit))]):
        goal = str(failure.get("goal") or "").strip()
        if not goal:
            continue
        cluster_count = _as_positive_float(
            failure.get("failure_cluster_count"),
            default=1.0,
        )
        case_id = str(
            failure.get("turn_id")
            or failure.get("proposal_id")
            or failure.get("failure_cluster")
            or f"failure-{idx + 1}"
        )
        cases.append(
            ReplayCase(
                case_id=case_id,
                kind="failure",
                task_input=goal,
                expected_behavior=str(
                    failure.get("last_error")
                    or failure.get("failure_source")
                    or "address the observed failure",
                ),
                weight=max(1.0, cluster_count),
                metadata=dict(failure),
            )
        )

    positive_examples = positive_dataset.all_examples if positive_dataset is not None else []
    for idx, example in enumerate(positive_examples[: max(0, int(positive_limit))]):
        cases.append(_positive_case(example, idx))
    return cases


def replay_candidate(
    candidate: Any,
    cases: list[ReplayCase],
    *,
    baseline_prompt: str | None = None,
    failures: list[dict[str, Any]] | None = None,
    positive_dataset: EvolutionDataset | None = None,
) -> ReplayCandidateReport:
    native = score_candidate_native(
        candidate,
        baseline_prompt=baseline_prompt,
        failures=failures,
        positive_dataset=positive_dataset,
    )
    prompt = str(getattr(candidate, "prompt", "") or "")
    case_results = [_score_case(prompt, case) for case in cases]
    total = _weighted_average(case_results)
    blended = round(total * 0.7 + native.total * 0.3, 3)
    return ReplayCandidateReport(
        candidate_id=str(getattr(candidate, "candidate_id", "") or ""),
        total=blended,
        native_score=native.to_dict(),
        case_results=case_results,
        reasons=_candidate_reasons(blended, native, case_results),
    )


def replay_candidates(
    candidates: list[Any],
    *,
    baseline_prompt: str | None = None,
    failures: list[dict[str, Any]] | None = None,
    positive_dataset: EvolutionDataset | None = None,
    failure_limit: int = 20,
    positive_limit: int = 20,
) -> ReplayReport:
    cases = build_replay_cases(
        failures=failures,
        positive_dataset=positive_dataset,
        failure_limit=failure_limit,
        positive_limit=positive_limit,
    )
    reports = [
        replay_candidate(
            candidate,
            cases,
            baseline_prompt=baseline_prompt,
            failures=failures,
            positive_dataset=positive_dataset,
        )
        for candidate in candidates
    ]
    reports.sort(key=lambda report: (-report.total, report.candidate_id))
    return ReplayReport(candidates=reports, cases=cases)


def _positive_case(example: EvolutionExample, idx: int) -> ReplayCase:
    case_id = str(
        example.metadata.get("proposal_id")
        or example.metadata.get("trajectory_id")
        or f"positive-{idx + 1}"
    )
    return ReplayCase(
        case_id=case_id,
        kind="positive",
        task_input=example.task_input,
        expected_behavior=example.expected_behavior,
        weight=0.7,
        metadata={
            **example.metadata,
            "source": example.source,
            "category": example.category,
        },
    )


def _score_case(prompt: str, case: ReplayCase) -> ReplayCaseResult:
    if case.kind == "positive":
        return _score_positive_case(prompt, case)
    return _score_failure_case(prompt, case)


def _score_failure_case(prompt: str, case: ReplayCase) -> ReplayCaseResult:
    text = prompt.lower()
    signals = _failure_signals(case)
    matched = [signal for signal in signals if _signal_present(text, signal)]
    missing = [signal for signal in signals if signal not in matched]
    score = 0.55 if not signals else 0.25 + 0.65 * (len(matched) / len(signals))
    if _has_corrective_language(text, case):
        score += 0.25
        matched.append("corrective-action")
    score = round(min(1.0, score), 3)
    reason = "covers failure signals" if score >= 0.7 else "missing failure-specific guidance"
    return ReplayCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        score=score,
        weight=case.weight,
        matched_signals=_dedupe(matched),
        missing_signals=_dedupe(missing),
        reason=reason,
    )


def _score_positive_case(prompt: str, case: ReplayCase) -> ReplayCaseResult:
    text = prompt.lower()
    risky = [phrase for phrase in _RISKY_SUCCESS_REGRESSIONS if phrase in text]
    if risky:
        return ReplayCaseResult(
            case_id=case.case_id,
            kind=case.kind,
            score=0.0,
            weight=case.weight,
            matched_signals=[],
            missing_signals=risky,
            reason="candidate would regress successful paths",
        )
    actions = [
        str(action).strip().lower()
        for action in case.metadata.get("action_chain", [])
        if str(action).strip()
    ]
    matched = [action for action in actions if action in text]
    if actions:
        score = max(0.55, len(matched) / len(actions))
        missing = [action for action in actions if action not in matched]
    else:
        score = 0.75
        missing = []
    return ReplayCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        score=round(min(1.0, score), 3),
        weight=case.weight,
        matched_signals=_dedupe(matched),
        missing_signals=_dedupe(missing),
        reason="preserves successful flow",
    )


def _failure_signals(case: ReplayCase) -> list[str]:
    metadata = case.metadata
    raw = [
        metadata.get("failure_cluster"),
        metadata.get("failure_source"),
        metadata.get("last_error"),
        metadata.get("source"),
    ]
    signals: list[str] = []
    for value in raw:
        signals.extend(_extract_signals(str(value or "")))
    if not signals:
        signals.extend(_extract_signals(case.expected_behavior))
    return _dedupe(signals)[:8]


def _extract_signals(text: str) -> list[str]:
    normalized = text.lower().replace("_", " ").replace(":", " ")
    parts = re.findall(r"[a-z][a-z0-9-]{3,}|[\u4e00-\u9fff]{2,}", normalized)
    ignored = {
        "error",
        "failed",
        "failure",
        "source",
        "none",
        "unknown",
        "proposal",
        "ledger",
        "unclassified",
    }
    return [part for part in parts if part not in ignored]


def _signal_present(text: str, signal: str) -> bool:
    signal = signal.lower().strip()
    return bool(signal) and signal in text


def _has_corrective_language(text: str, case: ReplayCase) -> bool:
    hints = ("verify", "retry", "resume", "continue", "limit", "checkpoint")
    if any(hint in text for hint in hints):
        return True
    cluster = str(case.metadata.get("failure_cluster") or "").lower()
    if "length" in cluster or "truncate" in cluster:
        return any(hint in text for hint in ("continue", "resume", "max_tokens"))
    if "verification" in cluster:
        return any(hint in text for hint in ("verify", "test", "check"))
    return False


def _weighted_average(results: list[ReplayCaseResult]) -> float:
    if not results:
        return 0.5
    total_weight = sum(max(0.0, result.weight) for result in results)
    if total_weight <= 0:
        return 0.5
    score = sum(result.score * max(0.0, result.weight) for result in results) / total_weight
    return round(score, 3)


def _candidate_reasons(
    total: float,
    native: NativeEvolutionScore,
    results: list[ReplayCaseResult],
) -> list[str]:
    reasons: list[str] = []
    low_failure = [r.case_id for r in results if r.kind == "failure" and r.score < 0.55]
    low_positive = [r.case_id for r in results if r.kind == "positive" and r.score < 0.55]
    if low_failure:
        reasons.append(f"weak on failure cases: {', '.join(low_failure[:3])}")
    if low_positive:
        reasons.append(f"success regression risk: {', '.join(low_positive[:3])}")
    if native.verdict == "reject":
        reasons.append("native constraints rejected candidate")
    if total >= 0.75 and not reasons:
        reasons.append("replay coverage is strong")
    if not reasons:
        reasons.append("mixed replay coverage")
    return reasons


def _as_positive_float(value: Any, *, default: float) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = str(value).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


_RISKY_SUCCESS_REGRESSIONS = (
    "never use tools",
    "avoid tools",
    "do not call tools",
    "skip verification",
    "do not verify",
    "always answer from memory",
)


__all__ = [
    "ReplayCandidateReport",
    "ReplayCase",
    "ReplayCaseResult",
    "ReplayReport",
    "build_replay_cases",
    "replay_candidate",
    "replay_candidates",
]
