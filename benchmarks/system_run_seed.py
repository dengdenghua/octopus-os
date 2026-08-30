"""Safely reuse verified behavioral system-run artifacts as suite checkpoints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.eval_harness import CaseResult, EvalCase, SuiteReport, Trajectory, Verdict
from runtime.safety.evolution.behavioral_surpass_evidence import (
    behavioral_system_provenance_digest,
    validate_behavioral_system_provenance,
)

SYSTEM_RUN_SCHEMA = "echo.behavioral_system_run.v2"
TRAJECTORY_SCHEMA = "echo.behavioral_trajectory.v2"


def load_system_run_seed(
    path: Path | str,
    *,
    root: Path | str,
    expected_system: str,
    expected_version: str,
    expected_suite_id: str,
    expected_k: int,
    cases: Sequence[EvalCase],
    expected_provenance: dict[str, Any] | None = None,
) -> SuiteReport:
    """Load digest-addressed completed cases from a prior system run.

    A seed is accepted only when its identity, fixed-case metadata, artifact
    digest, trajectory identity, and verdict counts still match the current
    suite.  This lets a selected k-run become a trusted starting point for a
    full run without weakening the final evidence checks.
    """

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"seed run is unreadable: {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"seed run must be an object: {source}")
    expected_identity = {"suite_id": expected_suite_id, "system_id": expected_system}
    for field, value in expected_identity.items():
        if payload.get(field) != value:
            raise ValueError(f"seed run {field} does not match this run: {source}")

    system = payload.get("system")
    if not isinstance(system, dict):
        raise ValueError(f"seed run system payload is missing: {source}")
    if system.get("version") != expected_version:
        raise ValueError(f"seed run system version does not match this run: {source}")
    expected_provenance_digest: str | None = None
    if expected_provenance is not None:
        if payload.get("schema") != SYSTEM_RUN_SCHEMA:
            raise ValueError(f"seed run schema does not carry release provenance: {source}")
        normalized_expected = validate_behavioral_system_provenance(
            expected_provenance,
            system_id=expected_system,
        )
        normalized_seed = validate_behavioral_system_provenance(
            system.get("provenance"),
            system_id=expected_system,
        )
        expected_provenance_digest = behavioral_system_provenance_digest(normalized_expected)
        if (
            normalized_seed != normalized_expected
            or system.get("provenance_sha256") != expected_provenance_digest
        ):
            raise ValueError(f"seed run system provenance does not match this run: {source}")
    elif payload.get("schema") not in {
        "echo.behavioral_system_run.v1",
        SYSTEM_RUN_SCHEMA,
    }:
        raise ValueError(f"seed run schema does not match this run: {source}")
    rows = system.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"seed run contains no completed cases: {source}")

    case_by_id = {case.id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("current evaluation case IDs must be unique")
    base = Path(root).resolve()
    seen: set[str] = set()
    results: list[CaseResult] = []
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            raise ValueError(f"seed run case must be an object: {source}")
        case_id = str(raw_row.get("id") or "")
        if case_id in seen:
            raise ValueError(f"seed run contains duplicate case: {case_id}")
        seen.add(case_id)
        case = case_by_id.get(case_id)
        if case is None:
            raise ValueError(f"seed run case is outside the requested suite: {case_id}")
        _validate_case_metadata(raw_row, case=case, expected_k=expected_k)
        artifacts = raw_row.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) != expected_k:
            raise ValueError(f"seed run case {case_id} does not contain exactly k artifacts")

        loaded = [
            _load_artifact(
                raw_artifact,
                root=base,
                expected_system=expected_system,
                expected_version=expected_version,
                expected_case=case,
                expected_provenance_sha256=expected_provenance_digest,
            )
            for raw_artifact in artifacts
        ]
        loaded.sort(key=lambda item: item[0])
        indices = [index for index, _trajectory, _verdict in loaded]
        if indices != list(range(expected_k)):
            raise ValueError(f"seed run case {case_id} trial indices are not exactly 0..k-1")
        trajectories = [trajectory for _index, trajectory, _verdict in loaded]
        verdicts = [verdict for _index, _trajectory, verdict in loaded]
        passes = sum(verdict.passed for verdict in verdicts)
        if raw_row.get("passes") != passes:
            raise ValueError(f"seed run case {case_id} pass count does not match artifacts")
        results.append(
            CaseResult(
                case_id=case_id,
                k=expected_k,
                passes=passes,
                trajectories=trajectories,
                verdicts=verdicts,
            )
        )

    started_at = min(
        trajectory.started_at for result in results for trajectory in result.trajectories
    )
    ended_at = max(
        trajectory.ended_at or trajectory.started_at
        for result in results
        for trajectory in result.trajectories
    )
    return SuiteReport(cases=results, started_at=started_at, ended_at=ended_at)


def merge_seed_reports(*reports: SuiteReport | None) -> SuiteReport | None:
    """Merge distinct completed/partial case reports without hiding conflicts."""

    present = [report for report in reports if report is not None]
    if not present:
        return None
    by_id: dict[str, CaseResult] = {}
    for report in present:
        for result in report.cases:
            if result.case_id in by_id:
                raise ValueError(f"duplicate seeded/checkpoint case: {result.case_id}")
            by_id[result.case_id] = result
    started_at = min(report.started_at for report in present)
    return SuiteReport(cases=list(by_id.values()), started_at=started_at)


def _validate_case_metadata(
    raw: dict[str, Any],
    *,
    case: EvalCase,
    expected_k: int,
) -> None:
    metadata = case.metadata
    expected = {
        "domain": metadata.get("domain"),
        "execution_mode": metadata.get("execution_mode"),
        "outcome_grader": True,
        "isolated_state": True,
        "prompt_digest": metadata.get("prompt_digest"),
        "rubric_digest": metadata.get("rubric_digest"),
        "k": expected_k,
        "trajectory_count": expected_k,
    }
    for field, value in expected.items():
        if raw.get(field) != value:
            raise ValueError(f"seed run case {case.id} {field} does not match current suite")


def _load_artifact(
    raw: Any,
    *,
    root: Path,
    expected_system: str,
    expected_version: str,
    expected_case: EvalCase,
    expected_provenance_sha256: str | None,
) -> tuple[int, Trajectory, Verdict]:
    if not isinstance(raw, dict):
        raise ValueError(f"seed artifact for {expected_case.id} must be an object")
    relative_path = raw.get("path")
    digest = str(raw.get("sha256") or "").lower()
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError(f"seed artifact path is missing for {expected_case.id}")
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"seed artifact escapes repository root: {relative_path}") from exc
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"seed artifact is unreadable: {relative_path}") from exc
    if len(digest) != 64 or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError(f"seed artifact digest mismatch: {relative_path}")
    try:
        artifact = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"seed artifact is invalid JSON: {relative_path}") from exc
    if not isinstance(artifact, dict):
        raise ValueError(f"seed artifact must be an object: {relative_path}")
    allowed_schemas = (
        {TRAJECTORY_SCHEMA}
        if expected_provenance_sha256 is not None
        else {"echo.behavioral_trajectory.v1", TRAJECTORY_SCHEMA}
    )
    if artifact.get("schema") not in allowed_schemas:
        raise ValueError(f"seed artifact schema mismatch: {relative_path}")
    expected_identity = {
        "system_id": expected_system,
        "system_version": expected_version,
        "case_id": expected_case.id,
        "prompt_sha256": expected_case.metadata.get("prompt_digest"),
    }
    for field, value in expected_identity.items():
        if artifact.get(field) != value:
            raise ValueError(f"seed artifact {field} mismatch: {relative_path}")
    if (
        expected_provenance_sha256 is not None
        and artifact.get("system_provenance_sha256") != expected_provenance_sha256
    ):
        raise ValueError(f"seed artifact provenance mismatch: {relative_path}")
    trial_index = artifact.get("trial_index")
    if not isinstance(trial_index, int) or isinstance(trial_index, bool):
        raise ValueError(f"seed artifact trial_index is invalid: {relative_path}")
    trajectory_raw = artifact.get("trajectory")
    verdict_raw = artifact.get("verdict")
    if not isinstance(trajectory_raw, dict) or not isinstance(verdict_raw, dict):
        raise ValueError(f"seed artifact trajectory/verdict is missing: {relative_path}")
    trajectory = Trajectory.from_dict(trajectory_raw)
    if trajectory.case_id != expected_case.id or trajectory.failure_category is not None:
        raise ValueError(f"seed artifact trajectory identity/status is invalid: {relative_path}")
    passed = verdict_raw.get("passed")
    score = verdict_raw.get("score")
    rubric = verdict_raw.get("rubric")
    if not isinstance(passed, bool) or not isinstance(score, (int, float)):
        raise ValueError(f"seed artifact verdict is invalid: {relative_path}")
    if not 0.0 <= float(score) <= 1.0 or not isinstance(rubric, dict):
        raise ValueError(f"seed artifact verdict score/rubric is invalid: {relative_path}")
    verdict = Verdict(
        passed=passed,
        score=float(score),
        reason=str(verdict_raw.get("reason") or ""),
        rubric=rubric,
    )
    return trial_index, trajectory, verdict


__all__ = ["load_system_run_seed", "merge_seed_reports"]


