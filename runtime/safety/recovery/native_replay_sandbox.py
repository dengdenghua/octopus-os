from __future__ import annotations

"""Sandbox-backed replay probes for native evolution.

This is the first deliberately small step beyond heuristic replay. It does
not rerun a full LLM/tool loop yet; instead it materializes each replay case
inside an isolated workspace and executes a deterministic probe through
``SandboxRunner``. The output gives the evolution layer a concrete signal:
the case was replayable in an isolated workspace, the candidate prompt was
inspectable, and the replay score can be tied to persisted artifacts.
"""

import json  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import asdict, dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from runtime.safety.recovery.evolution_dataset import EvolutionDataset  # noqa: E402
from runtime.safety.recovery.native_replay import (  # noqa: E402
    ReplayCase,
    build_replay_cases,
    replay_candidate,
)
from runtime.safety.sandboxing.sandbox import (  # noqa: E402
    SandboxPolicy,
    SandboxRunner,
    SandboxViolation,
    inference_domains,
)


@dataclass(frozen=True, slots=True)
class SandboxReplayCaseResult:
    case_id: str
    kind: str
    score: float
    heuristic_score: float
    sandbox_passed: bool
    sandbox_dir: str | None = None
    duration_ms: int = 0
    reason: str = ""
    artifacts: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SandboxReplayCandidateReport:
    candidate_id: str
    total: float
    passed: bool
    case_results: list[SandboxReplayCaseResult] = field(default_factory=list)
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
class SandboxReplayReport:
    candidates: list[SandboxReplayCandidateReport] = field(default_factory=list)
    case_count: int = 0
    sandbox_root: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "case_count": self.case_count,
            "sandbox_root": self.sandbox_root,
        }


def run_sandbox_replay(
    candidates: list[Any],
    *,
    failures: list[dict[str, Any]] | None = None,
    positive_dataset: EvolutionDataset | None = None,
    cases: list[ReplayCase] | None = None,
    baseline_prompt: str | None = None,
    workspace_root: str | Path | None = None,
    keep_workspaces: bool = False,
    min_pass_score: float = 0.55,
) -> SandboxReplayReport:
    replay_cases = cases or build_replay_cases(
        failures=failures,
        positive_dataset=positive_dataset,
    )
    root_context: Any
    if workspace_root is None:
        root_context = tempfile.TemporaryDirectory(prefix="echo-native-replay-")
        root = Path(root_context.name)
    else:
        root_context = None
        root = Path(workspace_root)
        root.mkdir(parents=True, exist_ok=True)
    try:
        reports = [
            _run_candidate(
                candidate,
                replay_cases,
                root=root,
                baseline_prompt=baseline_prompt,
                failures=failures,
                positive_dataset=positive_dataset,
                keep_workspaces=keep_workspaces,
                min_pass_score=min_pass_score,
            )
            for candidate in candidates
        ]
        reports.sort(key=lambda report: (-report.total, report.candidate_id))
        return SandboxReplayReport(
            candidates=reports,
            case_count=len(replay_cases),
            sandbox_root=str(root) if keep_workspaces else None,
        )
    finally:
        if root_context is not None and not keep_workspaces:
            root_context.cleanup()


def _run_candidate(
    candidate: Any,
    cases: list[ReplayCase],
    *,
    root: Path,
    baseline_prompt: str | None,
    failures: list[dict[str, Any]] | None,
    positive_dataset: EvolutionDataset | None,
    keep_workspaces: bool,
    min_pass_score: float,
) -> SandboxReplayCandidateReport:
    heuristic = replay_candidate(
        candidate,
        cases,
        baseline_prompt=baseline_prompt,
        failures=failures,
        positive_dataset=positive_dataset,
    )
    heuristic_by_case = {result.case_id: result for result in heuristic.case_results}
    case_results = [
        _run_case_probe(
            candidate,
            case,
            heuristic_result=heuristic_by_case.get(case.case_id),
            root=root,
            keep_workspace=keep_workspaces,
        )
        for case in cases
    ]
    total = _weighted_average(case_results)
    weak = [result.case_id for result in case_results if result.score < min_pass_score]
    return SandboxReplayCandidateReport(
        candidate_id=str(getattr(candidate, "candidate_id", "") or ""),
        total=total,
        passed=not weak,
        case_results=case_results,
        reasons=(
            [f"sandbox replay weak cases: {', '.join(weak[:3])}"]
            if weak
            else ["sandbox replay passed"]
        ),
    )


def _run_case_probe(
    candidate: Any,
    case: ReplayCase,
    *,
    heuristic_result: Any,
    root: Path,
    keep_workspace: bool,
) -> SandboxReplayCaseResult:
    candidate_id = str(getattr(candidate, "candidate_id", "") or "candidate")
    safe_case = _safe_name(case.case_id)
    case_dir = root / _safe_name(candidate_id) / safe_case
    case_dir.mkdir(parents=True, exist_ok=True)
    prompt = str(getattr(candidate, "prompt", "") or "")
    raw_score = getattr(heuristic_result, "score", None)
    try:
        heuristic_score = 0.5 if raw_score is None else float(raw_score)
    except (TypeError, ValueError):
        heuristic_score = 0.5
    missing_signals = list(getattr(heuristic_result, "missing_signals", []) or [])
    _write_json(case_dir / "case.json", case.to_dict())
    (case_dir / "candidate_prompt.txt").write_text(prompt, encoding="utf-8")
    probe = _probe_script()
    try:
        result = SandboxRunner(
            SandboxPolicy(
                workspace=case_dir,
                allow_network=False,
                timeout_s=5.0,
                max_output_bytes=16 * 1024,
                # Model inference endpoints stay reachable in a
                # network-denied sandbox (Claude Desktop parity).
                inference_domains=inference_domains(),
            )
        ).run([_python_executable(), "-c", probe], cwd=case_dir)
    except SandboxViolation as exc:
        return SandboxReplayCaseResult(
            case_id=case.case_id,
            kind=case.kind,
            score=0.0,
            heuristic_score=round(heuristic_score, 3),
            sandbox_passed=False,
            sandbox_dir=str(case_dir) if keep_workspace else None,
            reason=f"sandbox violation: {exc}",
            missing_signals=missing_signals,
        )
    passed = result.exit_code == 0 and not result.timed_out
    sandbox_bonus = 1.0 if passed else 0.0
    score = round(heuristic_score * 0.85 + sandbox_bonus * 0.15, 3)
    artifacts = ["case.json", "candidate_prompt.txt", "probe_result.json"]
    reason = "sandbox probe passed" if passed else (result.stderr or "sandbox probe failed")
    return SandboxReplayCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        score=score,
        heuristic_score=round(heuristic_score, 3),
        sandbox_passed=passed,
        sandbox_dir=str(case_dir) if keep_workspace else None,
        duration_ms=result.duration_ms,
        reason=reason[:300],
        artifacts=artifacts if keep_workspace else [],
        missing_signals=missing_signals,
    )


def _probe_script() -> str:
    return (
        "import json, pathlib\n"
        "root = pathlib.Path.cwd().resolve()\n"
        "case = json.loads((root / 'case.json').read_text(encoding='utf-8'))\n"
        "prompt = (root / 'candidate_prompt.txt').read_text(encoding='utf-8')\n"
        "out = {\n"
        "  'case_id': case.get('case_id'),\n"
        "  'kind': case.get('kind'),\n"
        "  'prompt_chars': len(prompt),\n"
        "  'goal_chars': len(case.get('task_input') or ''),\n"
        "}\n"
        "(root / 'probe_result.json').write_text(json.dumps(out), encoding='utf-8')\n"
        "print(json.dumps(out))\n"
    )


def _weighted_average(results: list[SandboxReplayCaseResult]) -> float:
    if not results:
        return 0.5
    return round(sum(result.score for result in results) / len(results), 3)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in value)
    return safe[:80] or "case"


def _python_executable() -> str:
    import sys

    return sys.executable


__all__ = [
    "SandboxReplayCandidateReport",
    "SandboxReplayCaseResult",
    "SandboxReplayReport",
    "run_sandbox_replay",
]
