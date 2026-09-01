from __future__ import annotations

"""Turn-level replay oracles for native evolution.

This layer sits above heuristic prompt replay. It translates recurring
Echo turn failures into deterministic scenario checks, so a prompt
mutation must explicitly preserve the behaviors that keep real sessions
healthy: continuing truncated reports, using tools when agent mode allows
them, and closing progress state after the final answer.
"""

import re  # noqa: E402
from dataclasses import asdict, dataclass, field  # noqa: E402
from typing import Any  # noqa: E402


@dataclass(frozen=True, slots=True)
class TurnReplayCase:
    case_id: str
    kind: str
    task_input: str
    expected_behavior: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TurnReplayCaseResult:
    case_id: str
    kind: str
    score: float
    passed: bool
    matched_signals: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TurnReplayCandidateReport:
    candidate_id: str
    total: float
    passed: bool
    case_results: list[TurnReplayCaseResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "total": self.total,
            "passed": self.passed,
            "case_results": [result.to_dict() for result in self.case_results],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class TurnReplayReport:
    candidates: list[TurnReplayCandidateReport] = field(default_factory=list)
    cases: list[TurnReplayCase] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "cases": [case.to_dict() for case in self.cases],
        }


def build_turn_replay_cases(
    *,
    failures: list[dict[str, Any]] | None = None,
    limit: int = 20,
) -> list[TurnReplayCase]:
    cases: list[TurnReplayCase] = []
    seen: set[str] = set()
    for idx, failure in enumerate((failures or [])[: max(0, int(limit))]):
        kind = _classify_failure(failure)
        if kind is None:
            continue
        case_id = str(
            failure.get("turn_id")
            or failure.get("proposal_id")
            or failure.get("failure_cluster")
            or f"turn-{idx + 1}"
        )
        dedupe_key = f"{kind}:{case_id}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cases.append(
            TurnReplayCase(
                case_id=case_id,
                kind=kind,
                task_input=str(failure.get("goal") or "").strip(),
                expected_behavior=_expected_behavior(kind),
                weight=_case_weight(kind, failure),
                metadata=dict(failure),
            )
        )
    return cases


def replay_turn_candidates(
    candidates: list[Any],
    *,
    failures: list[dict[str, Any]] | None = None,
    cases: list[TurnReplayCase] | None = None,
    min_case_score: float = 0.62,
) -> TurnReplayReport:
    replay_cases = (
        cases
        if cases is not None
        else build_turn_replay_cases(
            failures=failures,
        )
    )
    reports = [
        _replay_turn_candidate(
            candidate,
            replay_cases,
            min_case_score=max(0.0, float(min_case_score)),
        )
        for candidate in candidates
    ]
    reports.sort(key=lambda report: (-report.total, report.candidate_id))
    return TurnReplayReport(candidates=reports, cases=replay_cases)


def _replay_turn_candidate(
    candidate: Any,
    cases: list[TurnReplayCase],
    *,
    min_case_score: float,
) -> TurnReplayCandidateReport:
    prompt = str(getattr(candidate, "prompt", "") or "")
    case_results = [_score_turn_case(prompt, case, min_case_score) for case in cases]
    total = _weighted_average(case_results, cases)
    weak = [result for result in case_results if not result.passed]
    return TurnReplayCandidateReport(
        candidate_id=str(getattr(candidate, "candidate_id", "") or ""),
        total=total,
        passed=not weak,
        case_results=case_results,
        reasons=(
            [f"turn replay weak cases: {', '.join(r.case_id for r in weak[:3])}"]
            if weak
            else ["turn replay passed"]
        ),
    )


def _score_turn_case(
    prompt: str,
    case: TurnReplayCase,
    min_case_score: float,
) -> TurnReplayCaseResult:
    text = _normalize(prompt)
    signals = _signals_for_kind(case.kind)
    matched = [signal for signal, pattern in signals if _has(text, pattern)]
    missing = [signal for signal, _pattern in signals if signal not in matched]
    penalty = _penalty_for_kind(text, case.kind)
    base = 0.25 + (0.65 * (len(matched) / len(signals)) if signals else 0.25)
    score = round(max(0.0, min(1.0, base - penalty)), 3)
    passed = score >= min_case_score and not penalty >= 0.55
    return TurnReplayCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        score=score,
        passed=passed,
        matched_signals=matched,
        missing_signals=missing,
        reason=(
            "turn behavior covered" if passed else _failure_reason(case.kind, missing, penalty)
        ),
    )


def _classify_failure(failure: dict[str, Any]) -> str | None:
    text = _normalize(
        " ".join(
            str(failure.get(key) or "")
            for key in (
                "failure_cluster",
                "failure_source",
                "last_error",
                "goal",
                "summary",
                "reason",
            )
        )
    )
    if _has(text, r"length|truncate|截断|max[_ -]?tokens|finish_reason"):
        return "report_truncation"
    if _has(text, r"tool|skill|permission|权限|无法调用|不能调用|no tools"):
        return "tool_permission_confusion"
    if _has(text, r"spinner|stuck|in_progress|progress|转圈|卡住|最后一步|final step"):
        return "final_step_stuck"
    return None


def _signals_for_kind(kind: str) -> list[tuple[str, str]]:
    if kind == "report_truncation":
        return [
            ("detect-length-limit", r"length|truncate|截断|max[_ -]?tokens|finish_reason"),
            ("continue-from-checkpoint", r"continue|resume|续写|接着|checkpoint|from where"),
            ("do-not-summarize-away", r"complete|完整|final answer|交付|report"),
        ]
    if kind == "tool_permission_confusion":
        return [
            ("agent-default-tools", r"agent|tool|skill|工具|技能"),
            ("allowed-unless-discussion", r"unless|except|只聊|灵感|discussion|inspiration"),
            ("do-not-claim-no-tools", r"do not claim|不要声称|never say|available|可调用"),
        ]
    if kind == "final_step_stuck":
        return [
            ("close-after-final", r"final answer|complete|完成|交付|done"),
            ("mark-progress-complete", r"todo|progress|in_progress|步骤|进度|mark.*complete"),
            ("no-action-after-final", r"after final|stop|停止|close|收尾"),
        ]
    return [("generic-correction", r"correct|修复|avoid|避免")]


def _penalty_for_kind(text: str, kind: str) -> float:
    if kind == "tool_permission_confusion":  # noqa: SIM102
        if _has(text, r"never use tools|do not use tools|无法调用工具|不能调用工具|no tool"):
            return 0.65
    if kind == "report_truncation" and _has(text, r"summarize instead|简短总结|only summarize"):
        return 0.35
    if kind == "final_step_stuck" and _has(text, r"keep running|继续转圈|remain in_progress"):
        return 0.65
    return 0.0


def _expected_behavior(kind: str) -> str:
    return {
        "report_truncation": (
            "Detect length-limit truncation and continue from the last complete "
            "checkpoint until the report is fully delivered."
        ),
        "tool_permission_confusion": (
            "Default agent mode may call tools/skills; only inspiration or "
            "discussion mode should prefer talk-first behavior."
        ),
        "final_step_stuck": (
            "When final answer and file changes are done, mark progress complete "
            "and close the active step instead of leaving spinners running."
        ),
    }.get(kind, "Address the observed turn failure without regressing success paths.")


def _case_weight(kind: str, failure: dict[str, Any]) -> float:
    cluster_count = failure.get("failure_cluster_count")
    try:
        base = max(1.0, float(cluster_count))
    except (TypeError, ValueError):
        base = 1.0
    if kind in {"report_truncation", "tool_permission_confusion", "final_step_stuck"}:
        return base * 1.25
    return base


def _weighted_average(
    results: list[TurnReplayCaseResult],
    cases: list[TurnReplayCase],
) -> float:
    if not results:
        return 0.5
    by_id = {case.case_id: case for case in cases}
    total_weight = 0.0
    total = 0.0
    for result in results:
        weight = max(
            0.0,
            float(
                by_id.get(
                    result.case_id,
                    TurnReplayCase(
                        case_id=result.case_id,
                        kind=result.kind,
                        task_input="",
                        expected_behavior="",
                    ),
                ).weight
            ),
        )
        total += result.score * weight
        total_weight += weight
    if total_weight <= 0:
        return 0.5
    return round(total / total_weight, 3)


def _failure_reason(kind: str, missing: list[str], penalty: float) -> str:
    if penalty >= 0.55:
        return f"{kind} contains an explicit regression"
    if missing:
        return f"{kind} missing: {', '.join(missing[:3])}"
    return f"{kind} below pass threshold"


def _normalize(text: str) -> str:
    return str(text or "").lower()


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None
