"""Run selected coding tasks through both engines on one Echo control plane.

Examples::

    python -m benchmarks.run_engine_comparison \
      --backend native --backend codex \
      --case coding.concurrent-cache \
      --case coding.path-boundary \
      --output benchmarks/results/native-codex.json

No task is selected implicitly: live agent trials consume time and may incur
provider charges.  Each backend/case/trial receives a fresh fixture workspace,
while prompts and outcome graders remain byte-for-byte equivalent.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
import re
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from benchmarks.eval_harness import EvalCase, SuiteReport, run_case, run_suite_by_case
from benchmarks.execution_metrics import (
    BackendId,
    ExecutionMeasurement,
    aggregate_measurements,
    measurement_from_trial,
)
from benchmarks.fixed_suite_fixtures import (
    FIXTURE_SPECS,
    PreparedFixtureSuite,
    prepare_fixture_suite,
)
from benchmarks.fixture_grading import PythonTestFixture, python_test_runner_provenance
from benchmarks.hardened_verifier_smoke import run_hardened_verifier_full_chain_smoke
from benchmarks.realtime_runner import RealtimeTrialRunner, probe_realtime_endpoint
from benchmarks.source_provenance import SourceManifest, build_source_manifest
from benchmarks.verifier_sandbox import (
    FixtureInfrastructureError,
    verifier_sandbox_provenance,
)
from runtime.platform.process.paths import app_paths
from runtime.safety.evolution.experiment_protocol import (
    ExperimentStore,
    ExperimentTrial,
    TaskSpec,
    TrialStatus,
)

COMPARISON_SCHEMA = "echo.engine_comparison.v2"
COMPARISON_VERSION = 2
REPO_ROOT = Path(__file__).resolve().parents[1]
CODING_CASES = frozenset({"coding.concurrent-cache", "coding.path-boundary"})
DEFAULT_AGENTS: dict[BackendId, str] = {
    "native": "general",
    "codex": "coder",
}
_NATIVE_PATH_WRITE_TOOLS = frozenset(
    {
        "write_text_file",
        "append_text_file",
        "edit_text_file",
        "edit_file",
        "multi_edit_file",
        "edit_code",
        "add_import",
        "propose_patch",
    }
)
_LEADING_PATH_RE = re.compile(
    r"^\{\s*'path'\s*:\s*(?P<value>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")\s*(?:,|\})"
)

_BACKEND_ORDER: tuple[BackendId, ...] = ("native", "codex")
_SOURCE_DRIFT_EXIT = 3
_HARDENED_RUNNER_SCHEMA = "echo.hardened_verifier_runner.v2"
_CANDIDATE_API_ISOLATION_SCHEMA = "echo.candidate_api_process.v1"
_TRUSTED_RUNNER_SOURCE_BINDINGS = {
    "launcher_module_sha256": "benchmarks/linux_hardened_verifier.py",
    "contract_sha256": "benchmarks/trusted_verifier_contract.py",
    "controller_sha256": "benchmarks/trusted_verifier_controller.py",
    "worker_sha256": "benchmarks/trusted_verifier_worker.py",
}
_UNATTESTED_GAPS = (
    "realtime_server_build_and_loaded_resource_state",
    "active_prompt_and_skill_registry",
    "effective_server_config_and_feature_flags",
    "resolved_native_provider_model_and_weights",
    "codex_binary_protocol_account_and_remote_model",
)


@dataclass(frozen=True, slots=True)
class TrialSpec:
    ordinal: int
    case_id: str
    trial_index: int
    backend: BackendId

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "case_id": self.case_id,
            "trial_index": self.trial_index,
            "backend": self.backend,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be at least 1")
    backends = _unique_backends(args.backend)
    cases = _unique_cases(args.case)
    token = os.environ.get(args.echo_token_env) or None
    agents: dict[BackendId, str] = {
        "native": str(args.native_agent).strip(),
        "codex": str(args.codex_agent).strip(),
    }
    if any(not agents[backend] for backend in backends):
        parser.error("selected backend agent IDs must not be empty")

    # Benchmark infrastructure is checked before a provider-backed turn can
    # start, so a missing interpreter/pytest installation is never scored as
    # an engine failure.
    try:
        test_runner = python_test_runner_provenance()
    except RuntimeError as exc:
        parser.error(str(exc))
    try:
        verifier_sandbox = verifier_sandbox_provenance()
    except FixtureInfrastructureError as exc:
        parser.error(str(exc))

    try:
        source_before = build_source_manifest(
            REPO_ROOT,
            selected_case_ids=cases,
            selected_agent_ids=sorted({agents[backend] for backend in backends}),
        )
        _assert_hardened_runner_matches_controller_source(
            verifier_sandbox,
            source_before,
            repo_root=REPO_ROOT,
        )
        hardened_runner_identity_before = _hardened_runner_identity_sha256(verifier_sandbox)
    except (OSError, ValueError) as exc:
        parser.error(f"controller source provenance failed: {exc}")

    try:
        verifier_full_chain_smoke = run_hardened_verifier_full_chain_smoke(REPO_ROOT)
    except FixtureInfrastructureError as exc:
        parser.error(f"hardened verifier full-chain preflight failed: {exc}")

    try:
        asyncio.run(
            probe_realtime_endpoint(
                args.echo_url,
                token=token,
                timeout_seconds=min(args.timeout, 10.0),
            )
        )
    except Exception as exc:  # argparse gives the cleanest CLI failure surface
        parser.error(f"Echo realtime preflight failed: {exc}")

    run_id = f"engine-comparison-{uuid.uuid4().hex}"
    measurements: list[ExecutionMeasurement] = []
    prepared_by_backend: dict[BackendId, PreparedFixtureSuite] = {}
    reference_cases: list[EvalCase] | None = None
    case_contracts: list[dict[str, Any]] = []
    isolation_roots: dict[str, str] = {}
    for backend in backends:
        runs_root = _backend_runs_root(args.runs_root, run_id=run_id, backend=backend)
        isolation_roots[backend] = str(runs_root)
        prepared = prepare_fixture_suite(
            repo_root=REPO_ROOT,
            runs_root=runs_root,
            preserve_runs=args.preserve_runs,
            case_ids=set(cases),
        )
        prepared_by_backend[backend] = prepared
        if reference_cases is None:
            reference_cases = prepared.cases
            case_contracts = [
                _case_contract(case, source_manifest=source_before) for case in prepared.cases
            ]
        else:
            _assert_same_case_contracts(
                reference_cases,
                prepared.cases,
                source_manifest=source_before,
            )

    schedule = _build_schedule(
        backends=backends,
        case_ids=cases,
        k=args.k,
        seed=args.schedule_seed,
    )
    actual_schedule: list[dict[str, Any]] = []
    for trial in schedule:
        prepared = prepared_by_backend[trial.backend]
        case_by_id = {case.id: case for case in prepared.cases}
        case = case_by_id[trial.case_id]
        actual_schedule.append(trial.to_dict())
        result = run_case(
            case,
            runner=_runner_for_case(
                backend=trial.backend,
                agent_id=agents[trial.backend],
                prepared=prepared,
                case=case,
                url=args.echo_url,
                token=token,
                model=args.model,
                timeout=args.timeout,
            ),
            k=1,
        )
        if len(result.trajectories) != 1 or len(result.verdicts) != 1:
            raise RuntimeError("scheduled trial did not produce exactly one result")
        measurements.append(
            measurement_from_trial(
                result.trajectories[0],
                result.verdicts[0],
                backend=trial.backend,
                agent_id=agents[trial.backend],
                model=args.model,
                schedule_ordinal=trial.ordinal,
                trial_index=trial.trial_index,
            )
        )

    source_after: SourceManifest | None = None
    source_drift_error: str | None = None
    try:
        source_after = build_source_manifest(
            REPO_ROOT,
            selected_case_ids=cases,
            selected_agent_ids=sorted({agents[backend] for backend in backends}),
        )
    except (OSError, ValueError) as exc:
        source_drift_error = str(exc)
    source_stable = source_after is not None and source_after.sha256 == source_before.sha256

    verifier_sandbox_after: dict[str, Any] | None = None
    hardened_runner_postflight_error: str | None = None
    hardened_runner_identity_after: str | None = None
    if source_after is not None:
        try:
            verifier_sandbox_after = verifier_sandbox_provenance()
            _assert_hardened_runner_matches_controller_source(
                verifier_sandbox_after,
                source_after,
                repo_root=REPO_ROOT,
            )
            hardened_runner_identity_after = _hardened_runner_identity_sha256(
                verifier_sandbox_after
            )
            if hardened_runner_identity_after != hardened_runner_identity_before:
                raise ValueError("hardened verifier provenance changed during the run")
        except (FixtureInfrastructureError, OSError, ValueError) as exc:
            hardened_runner_postflight_error = str(exc)
    else:
        hardened_runner_postflight_error = "controller source manifest is unavailable"
    hardened_runner_stable = hardened_runner_postflight_error is None

    payload = build_comparison_payload(
        measurements,
        source_revision=_source_revision(REPO_ROOT),
        controller_source_manifest=_controller_source_manifest_payload(
            source_before,
            source_after=source_after,
            drift_error=source_drift_error,
        ),
        configuration={
            "control_plane": "echo_realtime",
            "mode": "head_to_head" if set(backends) == {"native", "codex"} else "measurement",
            "realtime_url": _safe_url(args.echo_url),
            "token_env": args.echo_token_env,
            "k": args.k,
            "requested_model": args.model,
            "backends": [{"backend": backend, "agent_id": agents[backend]} for backend in backends],
            "cases": case_contracts,
            "execution_schedule": {
                "algorithm": "sha256-seeded-alternating-ab-ba-v1",
                "seed": args.schedule_seed,
                "planned": [trial.to_dict() for trial in schedule],
                "actual": actual_schedule,
            },
            "isolation": {
                "strategy": "backend_case_trial_workspace",
                "preserve_runs": bool(args.preserve_runs),
                "backend_roots": isolation_roots,
            },
            "approval": {
                "policy": "on-request",
                "default_response": "decline",
                "codex": "decline_all",
                "native": "accept_only_exact_fixture_paths_and_test_runner",
            },
            "timeout_seconds": args.timeout,
            "fixture_test_runner": test_runner,
            "hidden_verifier_full_chain_smoke": verifier_full_chain_smoke,
            "hidden_verifier_sandbox": verifier_sandbox,
            "hidden_verifier_postflight": {
                "stable_during_run": hardened_runner_stable,
                "pre_identity_sha256": hardened_runner_identity_before,
                "post_identity_sha256": hardened_runner_identity_after,
                "error": hardened_runner_postflight_error,
            },
        },
        run_id=run_id,
        requested_k=args.k,
        source_stable=source_stable,
        hardened_runner_stable=hardened_runner_stable,
    )
    payload["experiment_protocol"] = ingest_comparison_measurements(
        measurements,
        payload=payload,
        store=ExperimentStore(args.experiment_store),
        artifact_path=args.output,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _print_summary(measurements, path=args.output)
    return 0 if payload["run_validity"]["valid"] is True else _SOURCE_DRIFT_EXIT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure explicitly selected coding tasks through native ReAct and/or the "
            "integrated Codex backend on the same Echo realtime control plane."
        )
    )
    parser.add_argument(
        "--backend",
        action="append",
        choices=("native", "codex"),
        required=True,
        help="Backend to run; repeat native+codex for a real head-to-head comparison.",
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=tuple(sorted(CODING_CASES)),
        required=True,
        help="Fixed task to run; repeat to select both coding cases.",
    )
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument("--schedule-seed", default="engine-comparison-v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=REPO_ROOT / "benchmarks/results/engine-comparison-runs",
    )
    parser.add_argument("--echo-url", default="ws://127.0.0.1:8000/api/realtime")
    parser.add_argument("--echo-token-env", default="ECHO_API_TOKEN")
    parser.add_argument("--native-agent", default=DEFAULT_AGENTS["native"])
    parser.add_argument("--codex-agent", default=DEFAULT_AGENTS["codex"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--preserve-runs", action="store_true")
    parser.add_argument(
        "--experiment-store",
        type=Path,
        default=app_paths().evolution_experiments_path,
        help="Append typed same-task trial evidence to this JSONL store.",
    )
    return parser


def _run_backend(
    *,
    backend: BackendId,
    agent_id: str,
    prepared: PreparedFixtureSuite,
    k: int,
    url: str,
    token: str | None,
    model: str | None,
    timeout: float,
) -> SuiteReport:
    def runner_factory(case: EvalCase) -> RealtimeTrialRunner:
        return _runner_for_case(
            backend=backend,
            agent_id=agent_id,
            prepared=prepared,
            case=case,
            url=url,
            token=token,
            model=model,
            timeout=timeout,
        )

    return run_suite_by_case(prepared.cases, runner_factory=runner_factory, k=k)


def _runner_for_case(
    *,
    backend: BackendId,
    agent_id: str,
    prepared: PreparedFixtureSuite,
    case: EvalCase,
    url: str,
    token: str | None,
    model: str | None,
    timeout: float,
) -> RealtimeTrialRunner:
    allowed_write_paths = case.metadata.get("allowed_write_paths")
    normalized_write_paths = (
        tuple(str(path) for path in allowed_write_paths)
        if isinstance(allowed_write_paths, list)
        else ()
    )
    fixture = prepared.fixtures[case.id]

    def context(_workspace: Path) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if isinstance(allowed_write_paths, list):
            values["allowed_write_paths"] = list(allowed_write_paths)
        return values

    return RealtimeTrialRunner(
        url=url,
        token=token,
        approval_policy="on-request",
        approval_action="decline",
        approval_responder=(
            _strict_native_approval_responder(
                workspace=lambda: prepared.workspace(case.id),
                allowed_write_paths=normalized_write_paths,
                fixture=fixture,
            )
            if backend == "native"
            else None
        ),
        agent_id=agent_id,
        model=model,
        workspace=lambda: prepared.workspace(case.id),
        context_overrides=context,
        sandbox_policy={"type": "workspaceWrite", "networkAccess": False},
        timeout_seconds=timeout,
    )


def _strict_native_approval_responder(
    *,
    workspace: Callable[[], Path],
    allowed_write_paths: Sequence[str],
    fixture: object | None = None,
):
    """Approve only an exact structured write target or the owned test runner."""

    allowed = frozenset(Path(path).as_posix() for path in allowed_write_paths)

    def respond(method: str, params: dict[str, Any]) -> dict[str, str]:
        if "requestApproval" not in method:
            return {"action": "decline"}
        try:
            root = workspace().resolve(strict=True)
        except (OSError, RuntimeError):
            return {"action": "decline"}
        tool = str(params.get("tool") or "").strip()
        preview = str(params.get("argsPreview") or "")
        if tool in _NATIVE_PATH_WRITE_TOOLS:
            raw_path = _leading_preview_path(preview)
            if raw_path is not None and _is_exact_allowed_path(root, raw_path, allowed):
                return {"action": "accept"}
            return {"action": "decline"}
        if tool != "exec_shell":
            return {"action": "decline"}
        parsed = _literal_preview_dict(preview)
        if parsed is None or not set(parsed).issubset({"command", "cwd"}):
            return {"action": "decline"}
        expected_command = (
            ".echo-eval\\run-tests.cmd" if os.name == "nt" else "./.echo-eval/run-tests"
        )
        if parsed.get("command") != expected_command:
            return {"action": "decline"}
        cwd = parsed.get("cwd")
        if cwd not in (None, ".", str(root)):
            return {"action": "decline"}
        request_cwd = params.get("cwd")
        if request_cwd not in (None, ".", str(root)):
            return {"action": "decline"}
        if isinstance(fixture, PythonTestFixture):
            try:
                fixture.assert_runner_integrity()
            except RuntimeError:
                return {"action": "decline"}
        return {"action": "accept"}

    return respond


def _leading_preview_path(preview: str) -> str | None:
    match = _LEADING_PATH_RE.match(preview)
    if match is None:
        return None
    try:
        value = ast.literal_eval(match.group("value"))
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    return value


def _literal_preview_dict(preview: str) -> dict[str, Any] | None:
    try:
        value = ast.literal_eval(preview)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        return None
    return value


def _is_exact_allowed_path(root: Path, raw_path: str, allowed: frozenset[str]) -> bool:
    supplied = Path(raw_path)
    try:
        relative = supplied.relative_to(root) if supplied.is_absolute() else supplied
    except ValueError:
        return False
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return False
    if relative.as_posix() not in allowed:
        return False
    candidate = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return False
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return False
    return resolved == candidate.absolute() and resolved.is_relative_to(root)


def _measurements_from_report(
    report: SuiteReport,
    *,
    backend: BackendId,
    agent_id: str,
    model: str | None,
) -> list[ExecutionMeasurement]:
    measurements: list[ExecutionMeasurement] = []
    for case in report.cases:
        if len(case.trajectories) != len(case.verdicts):
            raise ValueError(f"case {case.case_id} has mismatched trajectories and verdicts")
        for trajectory, verdict in zip(case.trajectories, case.verdicts, strict=True):
            measurements.append(
                measurement_from_trial(
                    trajectory,
                    verdict,
                    backend=backend,
                    agent_id=agent_id,
                    model=model,
                )
            )
    return measurements


def build_comparison_payload(
    measurements: Sequence[ExecutionMeasurement],
    *,
    source_revision: dict[str, Any],
    controller_source_manifest: dict[str, Any],
    configuration: dict[str, Any],
    run_id: str,
    requested_k: int,
    source_stable: bool,
    hardened_runner_stable: bool = True,
) -> dict[str, Any]:
    """Return an auditable artifact that makes unattested state explicit."""

    aggregates = aggregate_measurements(measurements, requested_k=requested_k)
    evidence_complete = bool(aggregates) and all(row["complete"] is True for row in aggregates)
    run_valid = source_stable and hardened_runner_stable and evidence_complete
    if not source_stable:
        invalid_reason = "controller_source_changed_during_run"
    elif not hardened_runner_stable:
        invalid_reason = "hardened_verifier_provenance_changed_during_run"
    elif not evidence_complete:
        invalid_reason = "insufficient_valid_trials"
    else:
        invalid_reason = None

    return {
        "schema": COMPARISON_SCHEMA,
        "version": COMPARISON_VERSION,
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_revision": source_revision,
        "controller_source_manifest": controller_source_manifest,
        "run_validity": {
            "valid": run_valid,
            "reason": invalid_reason,
        },
        "reproducibility_status": {
            "server_state": "server_state_unattested",
            "external_model": "external_model_unattested",
            "claim": "controller_inputs_attested_outputs_unattested",
            "unattested_gaps": list(_UNATTESTED_GAPS),
        },
        "configuration": configuration,
        "measurement_summary": _measurement_summary(measurements),
        "aggregates": aggregates,
        "measurements": [measurement.to_dict() for measurement in measurements],
    }


def ingest_comparison_measurements(
    measurements: Sequence[ExecutionMeasurement],
    *,
    payload: dict[str, Any],
    store: ExperimentStore,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    """Persist the real head-to-head run using the unified experiment protocol."""

    experiment_id = str(payload.get("run_id") or "").strip()
    if not experiment_id:
        raise ValueError("comparison payload run_id is required")
    existing = {trial.run_id for trial in store.list_trials(experiment_id=experiment_id)}
    configuration = payload.get("configuration")
    configuration = configuration if isinstance(configuration, dict) else {}
    contracts = {
        str(row.get("case_id") or ""): row
        for row in configuration.get("cases") or []
        if isinstance(row, dict)
    }
    source = payload.get("controller_source_manifest")
    source = source if isinstance(source, dict) else {}
    validity = payload.get("run_validity")
    validity = validity if isinstance(validity, dict) else {}
    environment_digest = _canonical_sha256(
        {
            "controller_source": source.get("pre_run_sha256"),
            "control_plane": configuration.get("control_plane"),
            "requested_model": configuration.get("requested_model"),
            "approval": configuration.get("approval"),
            "sandbox": configuration.get("hidden_verifier_sandbox"),
            "hardened_verifier": configuration.get("hidden_verifier_postflight"),
        }
    )
    appended = skipped = 0
    trial_ids: list[str] = []
    for measurement in measurements:
        run_id = ":".join(
            (
                experiment_id,
                measurement.backend,
                measurement.case_id,
                str(measurement.trial_index),
            )
        )
        trial_ids.append(run_id)
        if run_id in existing:
            skipped += 1
            continue
        contract = contracts.get(measurement.case_id, {})
        grader = contract.get("grader") if isinstance(contract.get("grader"), dict) else {}
        verifier = contract.get("verifier") if isinstance(contract.get("verifier"), dict) else {}
        fixture = contract.get("fixture") if isinstance(contract.get("fixture"), dict) else {}
        task_spec = TaskSpec(
            case_id=measurement.case_id,
            goal=str(contract.get("prompt") or measurement.case_id),
            domain=measurement.case_id.split(".", 1)[0],
            environment_digest=environment_digest,
            workspace_fixture_digest=str(fixture.get("manifest_sha256") or "unattested"),
            role_id="engine_comparison",
            gene_scope="baseline",
            budget_policy={"timeout_s": configuration.get("timeout_seconds")},
            grader_version=_canonical_sha256({"grader": grader, "verifier": verifier}),
            metadata={
                "prompt_sha256": contract.get("prompt_sha256"),
                "suite_prompt_contract_sha256": contract.get("suite_prompt_contract_sha256"),
            },
        )
        globally_valid = validity.get("valid") is True
        infrastructure_error = measurement.infrastructure_reason
        if not globally_valid:
            infrastructure_error = str(validity.get("reason") or "comparison run invalid")
        status = (
            TrialStatus.COMPLETED
            if globally_valid and measurement.valid_for_engine_rate
            else TrialStatus.INFRASTRUCTURE_FAILED
        )
        postflight = configuration.get("hidden_verifier_postflight")
        postflight = postflight if isinstance(postflight, dict) else {}
        hard_gates = {
            "controller_source_stable": source.get("stable_during_run") is True,
            "hardened_verifier_stable": postflight.get("stable_during_run") is True,
            "execution_completed": measurement.execution_success is True,
            "outcome_grader": measurement.grader_passed is True,
        }
        metrics = {
            "quality": 1.0 if measurement.grader_passed is True else 0.0,
            "duration_ms": float(measurement.duration_ms),
        }
        if measurement.usage.total_tokens is not None:
            metrics["total_tokens"] = float(measurement.usage.total_tokens)
        if measurement.usage.cost_usd is not None:
            metrics["cost_usd"] = float(measurement.usage.cost_usd)
        seed = int(
            hashlib.sha256(
                f"{configuration.get('execution_schedule')}:{measurement.trial_index}".encode()
            ).hexdigest()[:8],
            16,
        )
        store.append(
            ExperimentTrial(
                experiment_id=experiment_id,
                run_id=run_id,
                task_spec=task_spec,
                engine="echo" if measurement.backend == "native" else "codex",
                trial_index=measurement.trial_index,
                seed=seed,
                status=status,
                outcome_passed=(
                    measurement.grader_passed if status == TrialStatus.COMPLETED else None
                ),
                hard_gates=hard_gates,
                metrics=metrics,
                artifacts={
                    "comparison_artifact": str(artifact_path) if artifact_path else None,
                    "trajectory_sha256": measurement.trajectory_sha256,
                    "measurement_trial_id": measurement.trial_id,
                    "agent_id": measurement.agent_id,
                },
                error=measurement.grader_reason if measurement.grader_passed is False else None,
                infrastructure_error=infrastructure_error,
            )
        )
        appended += 1
    return {
        "schema": "echo.evolution.experiment_ingest.v1",
        "experiment_id": experiment_id,
        "store_path": str(store.path),
        "appended": appended,
        "skipped_existing": skipped,
        "trial_run_ids": trial_ids,
    }


def _canonical_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _measurement_summary(
    measurements: Sequence[ExecutionMeasurement],
) -> dict[str, Any]:
    categories: dict[str, int] = {}
    for measurement in measurements:
        if measurement.failure_category is not None:
            categories[measurement.failure_category] = (
                categories.get(measurement.failure_category, 0) + 1
            )
    valid = sum(measurement.valid_for_engine_rate for measurement in measurements)
    return {
        "total": len(measurements),
        "infrastructure_valid": valid,
        "infrastructure_invalid": len(measurements) - valid,
        "failure_categories": dict(sorted(categories.items())),
    }


def _case_contract(
    case: EvalCase,
    *,
    source_manifest: SourceManifest | None = None,
) -> dict[str, Any]:
    verifier_path = str(case.metadata.get("hidden_verifier_path") or "")
    verifier_record = source_manifest.file(verifier_path) if source_manifest else None
    if verifier_record is not None and verifier_record.sha256 != str(
        case.metadata.get("hidden_verifier_sha256") or ""
    ):
        raise ValueError(f"case {case.id} verifier bytes changed during preparation")
    fixture_name = str(case.metadata.get("fixture_name") or FIXTURE_SPECS[case.id].fixture_name)
    fixture_path = f"benchmarks/fixtures/{fixture_name}"
    return {
        "case_id": case.id,
        "prompt": case.prompt,
        "prompt_sha256": hashlib.sha256(case.prompt.encode("utf-8")).hexdigest(),
        "suite_prompt_contract_sha256": str(case.metadata.get("prompt_digest") or ""),
        "grader": {
            "id": str(case.metadata.get("grader_id") or ""),
            "rubric": case.metadata.get("rubric"),
            "rubric_sha256": str(case.metadata.get("rubric_digest") or ""),
        },
        "verifier": {
            "path": verifier_path,
            "size_bytes": (
                verifier_record.size_bytes
                if verifier_record is not None
                else int(case.metadata.get("hidden_verifier_size_bytes") or 0)
            ),
            "sha256": (
                verifier_record.sha256
                if verifier_record is not None
                else str(case.metadata.get("hidden_verifier_sha256") or "")
            ),
        },
        "fixture": {
            "path": fixture_path,
            "manifest_sha256": (
                source_manifest.subtree_sha256(fixture_path) if source_manifest else ""
            ),
        },
    }


def _assert_same_case_contracts(
    reference: Sequence[EvalCase],
    candidate: Sequence[EvalCase],
    *,
    source_manifest: SourceManifest | None = None,
) -> None:
    reference_rows = [_case_contract(case, source_manifest=source_manifest) for case in reference]
    candidate_rows = [_case_contract(case, source_manifest=source_manifest) for case in candidate]
    if reference_rows != candidate_rows:
        raise ValueError("backend suites do not use identical prompts and grader contracts")


def _controller_source_manifest_payload(
    before: SourceManifest,
    *,
    source_after: SourceManifest | None,
    drift_error: str | None,
) -> dict[str, Any]:
    payload = before.to_dict()
    payload.update(
        {
            "scope": "controller_checkout_only",
            "pre_run_sha256": before.sha256,
            "post_run_sha256": source_after.sha256 if source_after is not None else None,
            "stable_during_run": (
                source_after is not None and source_after.sha256 == before.sha256
            ),
            "post_run_error": drift_error,
        }
    )
    return payload


def _assert_hardened_runner_matches_controller_source(
    provenance: dict[str, Any],
    source_manifest: SourceManifest,
    *,
    repo_root: Path,
) -> None:
    """Bind the root-attested verifier to this exact controller checkout.

    The hardened runner validates root ownership and runtime isolation.  This
    separate comparison prevents a valid attestation for an older checkout (or
    a different worker protocol) from authorizing paid trials from the current
    source tree.
    """

    if provenance.get("schema") != _HARDENED_RUNNER_SCHEMA:
        raise ValueError("hardened verifier provenance uses an unsupported schema")
    if provenance.get("authorization") is not True:
        raise ValueError("hardened verifier provenance is not authorized")
    if provenance.get("candidate_api_isolation_schema") != _CANDIDATE_API_ISOLATION_SCHEMA:
        raise ValueError("hardened verifier does not attest the isolated candidate API")

    git_sha = provenance.get("git_sha")
    if not isinstance(git_sha, str) or re.fullmatch(r"[0-9a-f]{40,64}", git_sha) is None:
        raise ValueError("hardened verifier git identity is invalid")
    current_commit = _exact_git_commit(repo_root)
    if git_sha != current_commit:
        raise ValueError("hardened verifier was provisioned from a different git commit")

    sources = provenance.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("hardened verifier source bindings are missing")
    for binding, relative_path in _TRUSTED_RUNNER_SOURCE_BINDINGS.items():
        expected = sources.get(binding)
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError(f"hardened verifier source binding is invalid: {binding}")
        try:
            observed = source_manifest.file(relative_path).sha256
        except KeyError as exc:
            raise ValueError(f"controller source manifest does not bind {relative_path}") from exc
        if observed != expected:
            raise ValueError(
                f"hardened verifier source differs from controller checkout: {relative_path}"
            )


def _hardened_runner_identity_sha256(provenance: dict[str, Any]) -> str:
    """Hash stable runner identity while excluding live-probe counters/nonces."""

    try:
        stable = json.loads(
            json.dumps(
                provenance,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("hardened verifier provenance is not canonical JSON") from exc
    if not isinstance(stable, dict):
        raise ValueError("hardened verifier provenance is not an object")
    cgroup = stable.get("cgroup_v2")
    if isinstance(cgroup, dict):
        cgroup.pop("live_probe", None)
    scratch = stable.get("scratch")
    if isinstance(scratch, dict):
        scratch.pop("live_probe", None)
    serialized = json.dumps(
        stable,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _exact_git_commit(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("controller git identity is unavailable") from exc
    commit = completed.stdout.strip()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", commit) is None:
        raise ValueError("controller git identity is unavailable")
    return commit


def _backend_runs_root(root: Path, *, run_id: str, backend: BackendId) -> Path:
    return root.resolve() / run_id / backend


def _unique_backends(raw: Sequence[str]) -> list[BackendId]:
    selected = {"native" if value == "native" else "codex" for value in raw}
    return [backend for backend in _BACKEND_ORDER if backend in selected]


def _unique_cases(raw: Sequence[str]) -> list[str]:
    output: list[str] = []
    for value in raw:
        if value not in output:
            output.append(value)
    return output


def _build_schedule(
    *,
    backends: Sequence[BackendId],
    case_ids: Sequence[str],
    k: int,
    seed: str,
) -> list[TrialSpec]:
    if k < 1:
        raise ValueError("k must be at least 1")
    canonical_backends = [backend for backend in _BACKEND_ORDER if backend in set(backends)]
    if not canonical_backends:
        raise ValueError("at least one backend is required")
    canonical_cases = sorted(dict.fromkeys(case_ids))
    if not canonical_cases:
        raise ValueError("at least one case is required")
    normalized_seed = str(seed)
    schedule: list[TrialSpec] = []
    for trial_index in range(k):
        for case_id in canonical_cases:
            order = list(canonical_backends)
            if len(order) == 2:
                initial_swap = (
                    hashlib.sha256(f"{normalized_seed}\0{case_id}".encode()).digest()[0] & 1
                )
                if initial_swap ^ (trial_index & 1):
                    order.reverse()
            for backend in order:
                schedule.append(
                    TrialSpec(
                        ordinal=len(schedule),
                        case_id=case_id,
                        trial_index=trial_index,
                        backend=backend,
                    )
                )
    return schedule


def _source_revision(root: Path) -> dict[str, Any]:
    commit: str | None = None
    dirty: bool | None = None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        value = completed.stdout.strip()
        if completed.returncode == 0 and value:
            commit = value
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if status.returncode == 0:
            dirty = bool(status.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"git_commit": commit, "worktree_dirty": dirty}


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _print_summary(measurements: Sequence[ExecutionMeasurement], *, path: Path) -> None:
    for row in measurements:
        cost = "n/a" if row.usage.cost_usd is None else f"${row.usage.cost_usd:.6f}"
        terminal = row.terminal_status or "missing"
        infrastructure = (
            "valid"
            if row.infrastructure_valid
            else f"invalid({row.failure_category or 'infrastructure'})"
        )
        grader = "n/a" if row.grader_passed is None else ("pass" if row.grader_passed else "fail")
        print(
            f"{row.backend:6s} {row.case_id:28s} "
            f"infrastructure={infrastructure} terminal={terminal} "
            f"verification={row.verification} grader={grader} "
            f"duration={row.duration_ms:.0f}ms cost={cost}"
        )
    print(f"comparison artifact: {path}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CODING_CASES",
    "COMPARISON_SCHEMA",
    "COMPARISON_VERSION",
    "DEFAULT_AGENTS",
    "build_comparison_payload",
    "main",
]


