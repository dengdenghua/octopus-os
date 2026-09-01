"""Agent eval harness — pass@k / pass^k driver.

Implements Anthropic's recommended methodology from
"Demystifying evals for AI agents" (Mar 2026):
  * Run each task ``k`` times with isolated state.
  * Report both ``pass@k`` (any-success probability) and ``pass^k``
    (all-success probability).
  * Grade outcomes, not paths — verdicts come from a pluggable grader.
  * Capture full trajectories so failures are debuggable.

Usage::

    from benchmarks.eval_harness import EvalCase, run_suite

    cases = [
        EvalCase(
            id="echo-hello",
            prompt="Reply with exactly: hello",
            grader=lambda traj: "hello" in traj.last_text(),
        ),
    ]
    report = run_suite(cases, k=3, runner=my_runner)
    print(report.summary())
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from runtime.safety.evolution.behavioral_surpass_evidence import (
    BUNDLE_SCHEMA,
    behavioral_system_provenance_digest,
    validate_behavioral_system_provenance,
)

TRAJECTORY_SCHEMA = "echo.behavioral_trajectory.v2"

# ── Trajectory ───────────────────────────────────────────────


@dataclass
class TrajectoryStep:
    """One observable event in a trial. Mirrors the ReAct event shape."""

    kind: str  # "text_delta" / "tool_start" / "tool_end" / ...
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TrajectoryStep:
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("trajectory step payload must be an object")
        return cls(
            kind=str(raw.get("kind") or "event"),
            payload=payload,
            ts=float(raw.get("ts") or time.time()),
        )


@dataclass
class Trajectory:
    """Complete record of one trial run.

    The grader inspects this; the runner builds it event-by-event.
    """

    trial_id: str
    case_id: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    error: str | None = None
    failure_category: str | None = None

    def append(self, kind: str, **payload: Any) -> None:
        self.steps.append(TrajectoryStep(kind=kind, payload=payload))

    def last_text(self) -> str:
        """All ``text_delta`` payloads concatenated. Convenient for graders
        that only care about the final answer.
        """
        out: list[str] = []
        for s in self.steps:
            if s.kind == "text_delta":
                out.append(str(s.payload.get("delta", "")))
        return "".join(out)

    def tool_names(self) -> list[str]:
        """Every ``tool_start`` name in order. Useful for "did agent call X?" graders."""
        names: list[str] = []
        for step in self.steps:
            if step.kind != "tool_start":
                continue
            flat = str(step.payload.get("tool_name") or "")
            item = step.payload.get("item")
            item = item if isinstance(item, dict) else {}
            # Echo realtime wraps skills as a camelCase commandExecution
            # item and preserves the real skill name in ``item.command``.
            # Codex uses snake_case command_execution for arbitrary shell
            # commands, where exposing the command text as a tool name would
            # make cross-system counts incomparable.
            item_type = str(item.get("type") or "")
            if item_type == "commandExecution" and item.get("command"):
                names.append(str(item["command"]))
            else:
                names.append(flat or str(item.get("tool_name") or item_type or ""))
        return names

    def runtime_ms(self) -> float:
        end = self.ended_at or time.time()
        return (end - self.started_at) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "case_id": self.case_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "runtime_ms": self.runtime_ms(),
            "error": self.error,
            "failure_category": self.failure_category,
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Trajectory:
        steps = raw.get("steps")
        if not isinstance(steps, list):
            raise ValueError("trajectory steps must be a list")
        ended_at = raw.get("ended_at")
        error = raw.get("error")
        failure_category = raw.get("failure_category")
        return cls(
            trial_id=str(raw.get("trial_id") or ""),
            case_id=str(raw.get("case_id") or ""),
            steps=[TrajectoryStep.from_dict(step) for step in steps if isinstance(step, dict)],
            started_at=float(raw.get("started_at") or time.time()),
            ended_at=float(ended_at) if ended_at is not None else None,
            error=str(error) if error is not None else None,
            failure_category=(str(failure_category) if failure_category is not None else None),
        )


# ── Grader ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    passed: bool
    score: float = 0.0  # 0..1 partial-credit score
    reason: str = ""
    rubric: dict[str, Any] = field(default_factory=dict)


# A grader is a callable taking the trajectory and returning a verdict.
# Keep it dead simple: no class hierarchy required — just a function.
Grader = Callable[[Trajectory], Verdict | bool]


def _coerce_verdict(raw: Verdict | bool) -> Verdict:
    if isinstance(raw, Verdict):
        return raw
    return Verdict(passed=bool(raw), score=1.0 if raw else 0.0)


# ── Case + Runner ────────────────────────────────────────────


@dataclass
class EvalCase:
    """One concrete evaluation task."""

    id: str
    prompt: str
    grader: Grader
    setup: Callable[[], None] | None = None
    teardown: Callable[[], None] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TrialRunner(Protocol):
    """A function that runs one trial and yields ReAct-shaped events."""

    def __call__(self, prompt: str) -> Any: ...


CaseRunnerFactory = Callable[[EvalCase], TrialRunner]


# ── Suite report ─────────────────────────────────────────────


@dataclass
class CaseResult:
    case_id: str
    k: int
    passes: int
    trajectories: list[Trajectory] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def pass_at_k(self) -> float:
        """Probability of *at least one* success across the k trials."""
        return 1.0 if self.passes >= 1 else 0.0

    @property
    def pass_pow_k(self) -> float:
        """Probability of *all k* trials succeeding."""
        return 1.0 if self.passes == self.k else 0.0

    @property
    def avg_score(self) -> float:
        if not self.verdicts:
            return 0.0
        return sum(v.score for v in self.verdicts) / len(self.verdicts)

    @property
    def avg_runtime_ms(self) -> float:
        if not self.trajectories:
            return 0.0
        return sum(t.runtime_ms() for t in self.trajectories) / len(self.trajectories)

    @property
    def has_infrastructure_failure(self) -> bool:
        return any(
            trajectory.failure_category == "infrastructure" for trajectory in self.trajectories
        )


@dataclass
class SuiteReport:
    cases: list[CaseResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    def add(self, result: CaseResult) -> None:
        self.cases.append(result)

    @property
    def aggregate_pass_at_k(self) -> float:
        if not self.cases:
            return 0.0
        return sum(c.pass_at_k for c in self.cases) / len(self.cases)

    @property
    def aggregate_pass_pow_k(self) -> float:
        if not self.cases:
            return 0.0
        return sum(c.pass_pow_k for c in self.cases) / len(self.cases)

    @property
    def infrastructure_failures(self) -> list[CaseResult]:
        return [case for case in self.cases if case.has_infrastructure_failure]

    def summary(self) -> str:
        lines = [
            f"Eval suite · {len(self.cases)} cases · k={self.cases[0].k if self.cases else 0}",
            f"  pass@k  = {self.aggregate_pass_at_k:.2%}  (any-success)",
            f"  pass^k  = {self.aggregate_pass_pow_k:.2%}  (all-success)",
            "",
        ]
        for c in self.cases:
            mark = "✓" if c.pass_pow_k == 1.0 else ("~" if c.pass_at_k == 1.0 else "✗")
            lines.append(
                f"  {mark} {c.case_id:30s} "
                f"{c.passes}/{c.k} · score={c.avg_score:.2f} · "
                f"avg={c.avg_runtime_ms:.0f}ms",
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "aggregate_pass_at_k": self.aggregate_pass_at_k,
            "aggregate_pass_pow_k": self.aggregate_pass_pow_k,
            "cases": [
                {
                    "case_id": c.case_id,
                    "k": c.k,
                    "passes": c.passes,
                    "pass_at_k": c.pass_at_k,
                    "pass_pow_k": c.pass_pow_k,
                    "avg_score": c.avg_score,
                    "avg_runtime_ms": c.avg_runtime_ms,
                    "verdicts": [
                        {
                            "passed": v.passed,
                            "score": v.score,
                            "reason": v.reason,
                            "rubric": v.rubric,
                        }
                        for v in c.verdicts
                    ],
                    "trajectories": [trajectory.to_dict() for trajectory in c.trajectories],
                }
                for c in self.cases
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SuiteReport:
        case_rows = raw.get("cases")
        if not isinstance(case_rows, list):
            raise ValueError("suite checkpoint cases must be a list")
        report = cls(started_at=float(raw.get("started_at") or time.time()))
        ended_at = raw.get("ended_at")
        report.ended_at = float(ended_at) if ended_at is not None else None
        for case_row in case_rows:
            if not isinstance(case_row, dict):
                raise ValueError("suite checkpoint case must be an object")
            trajectories_raw = case_row.get("trajectories")
            verdicts_raw = case_row.get("verdicts")
            if not isinstance(trajectories_raw, list) or not isinstance(verdicts_raw, list):
                raise ValueError("checkpoint case must include trajectories and verdicts")
            verdicts: list[Verdict] = []
            for verdict in verdicts_raw:
                if not isinstance(verdict, dict):
                    raise ValueError("checkpoint verdict must be an object")
                rubric = verdict.get("rubric")
                verdicts.append(
                    Verdict(
                        passed=verdict.get("passed") is True,
                        score=float(verdict.get("score") or 0.0),
                        reason=str(verdict.get("reason") or ""),
                        rubric=rubric if isinstance(rubric, dict) else {},
                    )
                )
            report.add(
                CaseResult(
                    case_id=str(case_row.get("case_id") or ""),
                    k=int(case_row.get("k") or 0),
                    passes=int(case_row.get("passes") or 0),
                    trajectories=[
                        Trajectory.from_dict(trajectory)
                        for trajectory in trajectories_raw
                        if isinstance(trajectory, dict)
                    ],
                    verdicts=verdicts,
                )
            )
        return report

    def write_json(self, path: Path | str) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2),
            encoding="utf-8",
        )


def resumable_report(report: SuiteReport) -> SuiteReport:
    """Return checkpoint-safe completed trials, dropping only infra trials.

    A transport failure on trial N must not erase healthy trials 0..N-1 from
    the same case.  The previous case-level filter did exactly that, defeating
    trial-atomic checkpoints during the outages they are meant to survive.
    """

    cases: list[CaseResult] = []
    for case in report.cases:
        healthy = [
            (trajectory, verdict)
            for trajectory, verdict in zip(case.trajectories, case.verdicts, strict=True)
            if trajectory.failure_category != "infrastructure"
        ]
        if not healthy:
            continue
        trajectories = [trajectory for trajectory, _verdict in healthy]
        verdicts = [verdict for _trajectory, verdict in healthy]
        cases.append(
            CaseResult(
                case_id=case.case_id,
                k=case.k,
                passes=sum(verdict.passed for verdict in verdicts),
                trajectories=trajectories,
                verdicts=verdicts,
            )
        )
    return SuiteReport(cases=cases, started_at=report.started_at)


def write_behavioral_system_evidence(
    report: SuiteReport,
    cases: Sequence[EvalCase],
    *,
    root: Path | str,
    system_id: str,
    version: str,
    provenance: dict[str, Any] | None = None,
    artifact_dir: Path | str = "benchmarks/results/behavioral-artifacts",
) -> dict[str, Any]:
    """Write digest-addressed trajectories for one side of a head-to-head run.

    Each ``EvalCase.metadata`` must contain ``domain``, ``execution_mode``,
    ``outcome_grader``, ``isolated_state``, and a 64-character
    ``rubric_digest``. The returned object is ready to place under
    ``systems.<system_id>`` in a behavioral surpass bundle.
    """

    base = Path(root).resolve()
    normalized_provenance = (
        validate_behavioral_system_provenance(provenance, system_id=system_id)
        if provenance is not None
        else {}
    )
    provenance_digest = (
        behavioral_system_provenance_digest(normalized_provenance) if normalized_provenance else ""
    )
    if report.infrastructure_failures:
        failed = ", ".join(case.case_id for case in report.infrastructure_failures)
        raise ValueError("behavioral evidence cannot score infrastructure failures: " + failed)
    output_dir = (base / Path(artifact_dir)).resolve()
    try:
        output_dir.relative_to(base)
    except ValueError as exc:
        raise ValueError("artifact_dir must stay inside root") from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    case_by_id = {case.id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("evaluation case IDs must be unique")
    result_rows: list[dict[str, Any]] = []
    for result in report.cases:
        case = case_by_id.get(result.case_id)
        if case is None:
            raise ValueError(f"missing EvalCase metadata for {result.case_id}")
        metadata = case.metadata
        rubric_digest = str(metadata.get("rubric_digest") or "").lower()
        prompt_digest = (
            str(case.metadata.get("prompt_digest") or "")
            or hashlib.sha256(case.prompt.encode("utf-8")).hexdigest()
        )
        if len(rubric_digest) != 64 or any(
            character not in "0123456789abcdef" for character in rubric_digest
        ):
            raise ValueError(f"invalid rubric_digest for {case.id}")
        if len(result.trajectories) != result.k or len(result.verdicts) != result.k:
            raise ValueError(f"case {case.id} does not contain exactly k trials")
        artifacts: list[dict[str, str]] = []
        for trial_index, (trajectory, verdict) in enumerate(
            zip(result.trajectories, result.verdicts, strict=True)
        ):
            artifact = {
                "schema": TRAJECTORY_SCHEMA,
                "system_id": system_id,
                "system_version": version,
                "system_provenance_sha256": provenance_digest,
                "case_id": case.id,
                "trial_index": trial_index,
                "prompt_sha256": prompt_digest,
                "trajectory": trajectory.to_dict(),
                "verdict": {
                    "passed": verdict.passed,
                    "score": verdict.score,
                    "reason": verdict.reason,
                    "rubric": verdict.rubric,
                },
            }
            serialized = json.dumps(
                artifact,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            artifact_digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            filename = (
                f"{_safe_artifact_name(system_id)}-{_safe_artifact_name(case.id)}-"
                f"{trial_index}-{artifact_digest[:12]}.json"
            )
            path = output_dir / filename
            path.write_text(serialized, encoding="utf-8")
            artifacts.append(
                {
                    "path": str(path.relative_to(base)),
                    "sha256": artifact_digest,
                }
            )
        result_rows.append(
            {
                "id": case.id,
                "domain": str(metadata.get("domain") or ""),
                "k": result.k,
                "passes": result.passes,
                "trajectory_count": len(result.trajectories),
                "outcome_grader": metadata.get("outcome_grader") is True,
                "isolated_state": metadata.get("isolated_state") is True,
                "execution_mode": str(metadata.get("execution_mode") or ""),
                "rubric_digest": rubric_digest,
                "prompt_digest": prompt_digest,
                "artifacts": artifacts,
            }
        )
    return {
        "version": version,
        "provenance": normalized_provenance,
        "provenance_sha256": provenance_digest,
        "cases": result_rows,
    }


def write_behavioral_bundle(
    *,
    path: Path | str,
    suite_manifest_path: Path | str,
    suite_id: str,
    runner_version: str,
    source_revision: str,
    generated_at: str,
    systems: dict[str, dict[str, Any]],
) -> None:
    """Write an atomic-shaped head-to-head bundle without fabricating results."""

    manifest_path = Path(suite_manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("suite_id") != suite_id:
        raise ValueError("suite manifest ID does not match bundle suite_id")
    payload = {
        "schema": BUNDLE_SCHEMA,
        "suite_id": suite_id,
        "runner_version": runner_version,
        "source_revision": source_revision,
        "generated_at": generated_at,
        "suite_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "systems": systems,
    }
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_artifact_name(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_." else "-" for character in value
    )
    cleaned = cleaned.strip("-.") or "unnamed"
    if len(cleaned) <= 80:
        return cleaned
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{cleaned[:64]}-{suffix}"


# ── Runner core ──────────────────────────────────────────────


def run_case(
    case: EvalCase,
    *,
    runner: TrialRunner,
    k: int = 3,
    initial_result: CaseResult | None = None,
    trial_complete: Callable[[CaseResult], None] | None = None,
) -> CaseResult:
    """Execute one case ``k`` times, optionally resuming completed trials."""
    result = initial_result or CaseResult(case_id=case.id, k=k, passes=0)
    if (
        result.case_id != case.id
        or result.k != k
        or len(result.trajectories) != len(result.verdicts)
        or len(result.trajectories) > k
        or result.passes != sum(verdict.passed for verdict in result.verdicts)
    ):
        raise ValueError(f"invalid partial case result: {case.id}")
    for trial_idx in range(len(result.trajectories), k):
        trial_id = f"{case.id}.{trial_idx}.{uuid.uuid4().hex[:6]}"
        traj = Trajectory(trial_id=trial_id, case_id=case.id)

        if case.setup:
            try:
                case.setup()
            except Exception as exc:
                traj.error = f"setup failed: {exc}"
                traj.failure_category = "infrastructure"
                traj.ended_at = time.time()
                if case.teardown:
                    try:
                        case.teardown()
                    except Exception as teardown_exc:
                        traj.append("teardown_error", message=str(teardown_exc))
                result.trajectories.append(traj)
                result.verdicts.append(Verdict(passed=False, reason=traj.error))
                if trial_complete is not None:
                    trial_complete(result)
                continue

        try:
            for raw in runner(case.prompt):
                if isinstance(raw, dict):
                    kind = raw.get("kind") or raw.get("type") or "event"
                    payload = {k_: v for k_, v in raw.items() if k_ not in ("kind", "type")}
                    traj.steps.append(TrajectoryStep(kind=kind, payload=payload))
                    if kind in {"error", "infrastructure_error"}:
                        detail = payload.get("error") or payload
                        rendered = (
                            json.dumps(detail, ensure_ascii=False, sort_keys=True)
                            if isinstance(detail, (dict, list))
                            else str(detail)
                        )
                        traj.error = f"runner error: {rendered}"
                        if kind == "infrastructure_error" or (
                            isinstance(detail, dict) and detail.get("type") == "infrastructure"
                        ):
                            traj.failure_category = "infrastructure"
        except Exception as exc:
            traj.error = f"runner raised: {exc}"
            traj.failure_category = "infrastructure"
        traj.ended_at = time.time()
        try:
            verdict = _coerce_verdict(case.grader(traj))
        except Exception as exc:
            grader_error = f"grader raised: {exc}"
            traj.error = f"{traj.error}; {grader_error}" if traj.error else grader_error
            traj.failure_category = "infrastructure"
            verdict = Verdict(passed=False, reason=grader_error)
        if traj.error:
            verdict = Verdict(
                passed=False,
                score=0.0,
                reason=traj.error,
                rubric=verdict.rubric,
            )
        if case.teardown:
            try:
                case.teardown()
            except Exception as exc:
                teardown_error = f"teardown failed: {exc}"
                traj.append("teardown_error", message=str(exc))
                traj.error = f"{traj.error}; {teardown_error}" if traj.error else teardown_error
                traj.failure_category = "infrastructure"
                verdict = Verdict(
                    passed=False,
                    score=0.0,
                    reason=traj.error,
                    rubric=verdict.rubric,
                )
        if verdict.passed:
            result.passes += 1
        result.trajectories.append(traj)
        result.verdicts.append(verdict)
        if trial_complete is not None:
            trial_complete(result)

    return result


def run_suite(
    cases: Sequence[EvalCase],
    *,
    runner: TrialRunner,
    k: int = 3,
) -> SuiteReport:
    """Run every case ``k`` times and return an aggregated report."""
    report = SuiteReport()
    for case in cases:
        report.add(run_case(case, runner=runner, k=k))
    report.ended_at = time.time()
    return report


def run_suite_by_case(
    cases: Sequence[EvalCase],
    *,
    runner_factory: CaseRunnerFactory,
    k: int = 3,
    initial_report: SuiteReport | None = None,
    case_complete: Callable[[SuiteReport], None] | None = None,
) -> SuiteReport:
    """Run a suite with case-specific system adapters.

    This is required for fixed suites where browser, multi-agent, and coding
    cases need different topology or sandbox parameters while preserving the
    exact same prompts and graders across systems.
    """

    initial_by_id: dict[str, CaseResult] = {}
    if initial_report is not None:
        for checkpoint_result in initial_report.cases:
            if checkpoint_result.case_id in initial_by_id:
                raise ValueError(f"duplicate checkpoint case: {checkpoint_result.case_id}")
            if (
                checkpoint_result.k != k
                or not 0 < len(checkpoint_result.trajectories) <= k
                or len(checkpoint_result.trajectories) != len(checkpoint_result.verdicts)
                or not 0 <= checkpoint_result.passes <= k
                or checkpoint_result.passes
                != sum(verdict.passed for verdict in checkpoint_result.verdicts)
                or checkpoint_result.has_infrastructure_failure
            ):
                raise ValueError(f"non-resumable checkpoint case: {checkpoint_result.case_id}")
            initial_by_id[checkpoint_result.case_id] = checkpoint_result
    report = SuiteReport(
        started_at=initial_report.started_at if initial_report is not None else time.time()
    )
    for case in cases:
        result: CaseResult | None = initial_by_id.pop(case.id, None)
        if result is None or len(result.trajectories) < k:
            result = result or CaseResult(case_id=case.id, k=k, passes=0)
            report.add(result)
            run_case(
                case,
                runner=runner_factory(case),
                k=k,
                initial_result=result,
                trial_complete=(
                    (lambda _result: case_complete(report)) if case_complete is not None else None
                ),
            )
        else:
            report.add(result)
    if initial_by_id:
        raise ValueError(f"checkpoint contains unknown cases: {sorted(initial_by_id)}")
    report.ended_at = time.time()
    return report


__all__ = [
    "CaseResult",
    "CaseRunnerFactory",
    "EvalCase",
    "Grader",
    "SuiteReport",
    "Trajectory",
    "TrajectoryStep",
    "TrialRunner",
    "Verdict",
    "run_case",
    "resumable_report",
    "run_suite",
    "run_suite_by_case",
    "write_behavioral_bundle",
    "write_behavioral_system_evidence",
]


