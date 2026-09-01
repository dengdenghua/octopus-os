from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchmarks import run_engine_comparison as comparison
from benchmarks.eval_harness import EvalCase, Trajectory, Verdict
from benchmarks.execution_metrics import measurement_from_trial
from benchmarks.fixed_suite_fixtures import PreparedFixtureSuite, prepare_fixture_suite
from benchmarks.source_provenance import FileDigest, SourceManifest, build_file_manifest
from runtime.safety.evolution.experiment_protocol import ExperimentStore, build_pair_evidence


def _trusted_source_manifest(*, worker_sha256: str = "d" * 64) -> SourceManifest:
    rows = (
        FileDigest("benchmarks/linux_hardened_verifier.py", 1, "a" * 64),
        FileDigest("benchmarks/trusted_verifier_contract.py", 1, "b" * 64),
        FileDigest("benchmarks/trusted_verifier_controller.py", 1, "c" * 64),
        FileDigest("benchmarks/trusted_verifier_worker.py", 1, worker_sha256),
    )
    return SourceManifest(
        rule_id="test-rule",
        selected_case_ids=("coding.path-boundary",),
        files=rows,
        sha256="e" * 64,
    )


def _trusted_runner_provenance(*, git_sha: str = "f" * 40) -> dict[str, Any]:
    return {
        "schema": "echo.hardened_verifier_runner.v2",
        "authorization": True,
        "git_sha": git_sha,
        "candidate_api_isolation_schema": "echo.candidate_api_process.v1",
        "sources": {
            "launcher_module_sha256": "a" * 64,
            "contract_sha256": "b" * 64,
            "controller_sha256": "c" * 64,
            "worker_sha256": "d" * 64,
        },
    }


def _trusted_full_chain_smoke() -> dict[str, Any]:
    return {
        "schema": "echo.hardened_verifier_full_chain_smoke.v1",
        "passed": True,
        "cases": [
            {"case_id": "coding.path-boundary", "passed": True},
            {"case_id": "coding.concurrent-cache", "passed": True},
        ],
    }


def test_cli_requires_explicit_backend_and_case() -> None:
    parser = comparison._parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--output", "result.json"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--backend", "native", "--output", "result.json"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--case", "coding.path-boundary", "--output", "result.json"])

    parsed = parser.parse_args(
        [
            "--backend",
            "native",
            "--backend",
            "codex",
            "--case",
            "coding.path-boundary",
            "--output",
            "result.json",
        ]
    )
    assert parsed.backend == ["native", "codex"]
    assert parsed.case == ["coding.path-boundary"]
    assert parsed.native_agent == "general"
    assert parsed.codex_agent == "coder"


def test_test_runner_preflight_fails_before_realtime_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realtime_probed = False

    async def fake_probe(*_args, **_kwargs) -> None:
        nonlocal realtime_probed
        realtime_probed = True

    def unavailable_runner() -> dict[str, str]:
        raise RuntimeError("coding fixture interpreter is unavailable")

    monkeypatch.setattr(comparison, "probe_realtime_endpoint", fake_probe)
    monkeypatch.setattr(comparison, "python_test_runner_provenance", unavailable_runner)

    with pytest.raises(SystemExit) as exc_info:
        comparison.main(
            [
                "--backend",
                "native",
                "--case",
                "coding.path-boundary",
                "--output",
                str(tmp_path / "result.json"),
            ]
        )

    assert exc_info.value.code == 2
    assert realtime_probed is False


def test_hardened_runner_must_match_exact_controller_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = _trusted_runner_provenance()
    manifest = _trusted_source_manifest()
    monkeypatch.setattr(comparison, "_exact_git_commit", lambda _root: "f" * 40)

    comparison._assert_hardened_runner_matches_controller_source(
        provenance,
        manifest,
        repo_root=Path("/unused"),
    )

    mismatched = _trusted_runner_provenance()
    mismatched["sources"] = {**mismatched["sources"], "worker_sha256": "0" * 64}
    with pytest.raises(ValueError, match="trusted_verifier_worker.py"):
        comparison._assert_hardened_runner_matches_controller_source(
            mismatched,
            manifest,
            repo_root=Path("/unused"),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "echo.hardened_verifier_runner.v1", "unsupported schema"),
        ("authorization", False, "not authorized"),
        ("candidate_api_isolation_schema", "legacy", "isolated candidate API"),
        ("git_sha", "0" * 40, "different git commit"),
    ],
)
def test_hardened_runner_rejects_stale_or_legacy_attestation(
    field: str,
    value: Any,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = _trusted_runner_provenance()
    provenance[field] = value
    monkeypatch.setattr(comparison, "_exact_git_commit", lambda _root: "f" * 40)

    with pytest.raises(ValueError, match=message):
        comparison._assert_hardened_runner_matches_controller_source(
            provenance,
            _trusted_source_manifest(),
            repo_root=Path("/unused"),
        )


def test_attested_source_mismatch_fails_before_realtime_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realtime_probed = False

    async def fake_probe(*_args, **_kwargs) -> None:
        nonlocal realtime_probed
        realtime_probed = True

    provenance = _trusted_runner_provenance()
    provenance["sources"] = {**provenance["sources"], "worker_sha256": "0" * 64}
    monkeypatch.setattr(comparison, "python_test_runner_provenance", lambda: {})
    monkeypatch.setattr(comparison, "verifier_sandbox_provenance", lambda: provenance)
    monkeypatch.setattr(
        comparison,
        "build_source_manifest",
        lambda *_args, **_kwargs: _trusted_source_manifest(),
    )
    monkeypatch.setattr(comparison, "_exact_git_commit", lambda _root: "f" * 40)
    monkeypatch.setattr(comparison, "probe_realtime_endpoint", fake_probe)

    with pytest.raises(SystemExit) as exc_info:
        comparison.main(
            [
                "--backend",
                "native",
                "--case",
                "coding.path-boundary",
                "--output",
                str(tmp_path / "result.json"),
            ]
        )

    assert exc_info.value.code == 2
    assert realtime_probed is False


def test_comparison_artifact_records_fixture_runner_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provenance = {
        "schema": "echo.fixture_test_runner.v1",
        "ownership": "evaluator",
        "interpreter_path": "/controlled/python",
        "python_version": "3.12.0",
        "pytest_version": "9.0.0",
        "runner_path": ".echo-eval/run-tests",
        "runner_sha256": "a" * 64,
        "test_root": "tests",
    }
    verifier_provenance = {
        "schema": "echo.hardened_verifier_runner.v1",
        "backend": "external-test-double",
        "authorization": True,
    }

    async def fake_probe(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(comparison, "probe_realtime_endpoint", fake_probe)
    monkeypatch.setattr(comparison, "python_test_runner_provenance", lambda: provenance)
    smoke = _trusted_full_chain_smoke()
    monkeypatch.setattr(
        comparison,
        "run_hardened_verifier_full_chain_smoke",
        lambda _root: smoke,
    )
    monkeypatch.setattr(
        comparison,
        "verifier_sandbox_provenance",
        lambda: verifier_provenance,
    )
    monkeypatch.setattr(
        comparison,
        "_assert_hardened_runner_matches_controller_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        comparison,
        "build_source_manifest",
        lambda *_args, **_kwargs: SourceManifest(
            rule_id="test-rule",
            selected_case_ids=("coding.path-boundary",),
            files=(),
            sha256="b" * 64,
        ),
    )
    case = EvalCase(
        id="coding.path-boundary",
        prompt="fixed prompt",
        grader=lambda _trajectory: True,
    )
    monkeypatch.setattr(
        comparison,
        "prepare_fixture_suite",
        lambda **_kwargs: PreparedFixtureSuite(cases=[case], fixtures={}),
    )
    monkeypatch.setattr(
        comparison, "_case_contract", lambda *_args, **_kwargs: {"case_id": case.id}
    )
    monkeypatch.setattr(comparison, "_runner_for_case", lambda **_kwargs: object())

    def fake_run_case(*_args, **_kwargs):
        trajectory = Trajectory(trial_id="trial", case_id=case.id)
        trajectory.append("turn_result", turn={"status": "completed", "items": []})
        return SimpleNamespace(
            trajectories=[trajectory],
            verdicts=[Verdict(passed=True, reason="passed")],
        )

    monkeypatch.setattr(comparison, "run_case", fake_run_case)
    monkeypatch.setattr(
        comparison,
        "_source_revision",
        lambda _root: {"git_commit": None, "worktree_dirty": True},
    )
    output = tmp_path / "result.json"

    assert (
        comparison.main(
            [
                "--backend",
                "native",
                "--case",
                "coding.path-boundary",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["configuration"]["fixture_test_runner"] == provenance
    assert payload["configuration"]["hidden_verifier_full_chain_smoke"] == smoke
    assert payload["configuration"]["hidden_verifier_sandbox"] == verifier_provenance
    assert payload["configuration"]["approval"] == {
        "policy": "on-request",
        "default_response": "decline",
        "codex": "decline_all",
        "native": "accept_only_exact_fixture_paths_and_test_runner",
    }
    assert payload["controller_source_manifest"]["stable_during_run"] is True
    assert payload["reproducibility_status"]["server_state"] == "server_state_unattested"
    assert payload["reproducibility_status"]["external_model"] == "external_model_unattested"
    assert "reproducible" not in payload["reproducibility_status"]["claim"]


def test_runner_identity_ignores_only_live_probe_values() -> None:
    before = _trusted_runner_provenance()
    before["cgroup_v2"] = {"parent": "/cg", "live_probe": {"child_name": "one"}}
    before["scratch"] = {"mount": "/scratch", "live_probe": {"used_bytes": 1}}
    after = json.loads(json.dumps(before))
    after["cgroup_v2"]["live_probe"]["child_name"] = "two"
    after["scratch"]["live_probe"]["used_bytes"] = 99

    assert comparison._hardened_runner_identity_sha256(
        before
    ) == comparison._hardened_runner_identity_sha256(after)

    after["sources"]["worker_sha256"] = "0" * 64
    assert comparison._hardened_runner_identity_sha256(
        before
    ) != comparison._hardened_runner_identity_sha256(after)


def test_postflight_runner_provenance_drift_invalidates_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _trusted_runner_provenance()
    after = json.loads(json.dumps(before))
    after["sources"]["worker_sha256"] = "0" * 64
    provenances = iter((before, after))
    manifest = SourceManifest(
        rule_id="test-rule",
        selected_case_ids=("coding.path-boundary",),
        files=(),
        sha256="b" * 64,
    )

    async def fake_probe(*_args, **_kwargs) -> None:
        return None

    case = EvalCase(
        id="coding.path-boundary",
        prompt="fixed prompt",
        grader=lambda _trajectory: True,
    )

    def fake_run_case(*_args, **_kwargs):
        trajectory = Trajectory(trial_id="trial", case_id=case.id)
        trajectory.append("turn_result", turn={"status": "completed", "items": []})
        return SimpleNamespace(
            trajectories=[trajectory],
            verdicts=[Verdict(passed=True, reason="passed")],
        )

    monkeypatch.setattr(comparison, "probe_realtime_endpoint", fake_probe)
    monkeypatch.setattr(comparison, "python_test_runner_provenance", lambda: {})
    monkeypatch.setattr(
        comparison,
        "run_hardened_verifier_full_chain_smoke",
        lambda _root: _trusted_full_chain_smoke(),
    )
    monkeypatch.setattr(
        comparison,
        "verifier_sandbox_provenance",
        lambda: next(provenances),
    )
    monkeypatch.setattr(
        comparison,
        "_assert_hardened_runner_matches_controller_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        comparison,
        "build_source_manifest",
        lambda *_args, **_kwargs: manifest,
    )
    monkeypatch.setattr(
        comparison,
        "prepare_fixture_suite",
        lambda **_kwargs: PreparedFixtureSuite(cases=[case], fixtures={}),
    )
    monkeypatch.setattr(
        comparison,
        "_case_contract",
        lambda *_args, **_kwargs: {"case_id": case.id},
    )
    monkeypatch.setattr(comparison, "_runner_for_case", lambda **_kwargs: object())
    monkeypatch.setattr(comparison, "run_case", fake_run_case)
    monkeypatch.setattr(
        comparison,
        "_source_revision",
        lambda _root: {"git_commit": "f" * 40, "worktree_dirty": False},
    )
    output = tmp_path / "provenance-drift.json"

    assert (
        comparison.main(
            [
                "--backend",
                "native",
                "--case",
                "coding.path-boundary",
                "--output",
                str(output),
            ]
        )
        == comparison._SOURCE_DRIFT_EXIT
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["run_validity"] == {
        "valid": False,
        "reason": "hardened_verifier_provenance_changed_during_run",
    }
    assert payload["configuration"]["hidden_verifier_postflight"] == {
        "stable_during_run": False,
        "pre_identity_sha256": comparison._hardened_runner_identity_sha256(before),
        "post_identity_sha256": comparison._hardened_runner_identity_sha256(after),
        "error": "hardened verifier provenance changed during the run",
    }


def test_verifier_sandbox_preflight_fails_before_realtime_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realtime_probed = False

    async def fake_probe(*_args, **_kwargs) -> None:
        nonlocal realtime_probed
        realtime_probed = True

    monkeypatch.setattr(comparison, "probe_realtime_endpoint", fake_probe)
    monkeypatch.setattr(comparison, "python_test_runner_provenance", lambda: {})
    monkeypatch.setattr(
        comparison,
        "verifier_sandbox_provenance",
        lambda: (_ for _ in ()).throw(
            comparison.FixtureInfrastructureError("hardened verifier runner unavailable")
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        comparison.main(
            [
                "--backend",
                "native",
                "--case",
                "coding.path-boundary",
                "--output",
                str(tmp_path / "result.json"),
            ]
        )

    assert exc_info.value.code == 2
    assert realtime_probed is False


def test_full_chain_smoke_fails_before_realtime_fixture_or_provider_trial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = {"realtime": False, "fixture": False, "trial": False}

    async def fake_probe(*_args, **_kwargs) -> None:
        invoked["realtime"] = True

    def fake_prepare(**_kwargs):
        invoked["fixture"] = True
        raise AssertionError("fixture preparation must remain unreachable")

    def fake_run_case(*_args, **_kwargs):
        invoked["trial"] = True
        raise AssertionError("provider-backed trial must remain unreachable")

    monkeypatch.setattr(comparison, "python_test_runner_provenance", lambda: {})
    monkeypatch.setattr(
        comparison,
        "verifier_sandbox_provenance",
        lambda: _trusted_runner_provenance(),
    )
    monkeypatch.setattr(
        comparison,
        "build_source_manifest",
        lambda *_args, **_kwargs: _trusted_source_manifest(),
    )
    monkeypatch.setattr(comparison, "_exact_git_commit", lambda _root: "f" * 40)
    monkeypatch.setattr(
        comparison,
        "run_hardened_verifier_full_chain_smoke",
        lambda _root: (_ for _ in ()).throw(
            comparison.FixtureInfrastructureError("known-good cache candidate was rejected")
        ),
    )
    monkeypatch.setattr(comparison, "probe_realtime_endpoint", fake_probe)
    monkeypatch.setattr(comparison, "prepare_fixture_suite", fake_prepare)
    monkeypatch.setattr(comparison, "run_case", fake_run_case)
    output = tmp_path / "result.json"
    runs_root = tmp_path / "runs"

    with pytest.raises(SystemExit) as exc_info:
        comparison.main(
            [
                "--backend",
                "native",
                "--case",
                "coding.path-boundary",
                "--runs-root",
                str(runs_root),
                "--output",
                str(output),
            ]
        )

    assert exc_info.value.code == 2
    assert invoked == {"realtime": False, "fixture": False, "trial": False}
    assert not output.exists()
    assert not runs_root.exists()


def test_backend_suite_contract_requires_same_prompt_and_grader(tmp_path: Path) -> None:
    native = prepare_fixture_suite(
        repo_root=comparison.REPO_ROOT,
        runs_root=tmp_path / "native",
        case_ids={"coding.path-boundary"},
    )
    codex = prepare_fixture_suite(
        repo_root=comparison.REPO_ROOT,
        runs_root=tmp_path / "codex",
        case_ids={"coding.path-boundary"},
    )

    comparison._assert_same_case_contracts(native.cases, codex.cases)
    codex.cases[0].prompt += " changed"
    with pytest.raises(ValueError, match="identical prompts and grader"):
        comparison._assert_same_case_contracts(native.cases, codex.cases)


def test_backend_case_trials_use_independent_fixture_workspaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, Any]] = []

    class FakeRealtimeRunner:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def __call__(self, prompt: str):
            workspace = Path(self.kwargs["workspace"]()).resolve()
            context = self.kwargs["context_overrides"](workspace)
            observed.append(
                {
                    "agent_id": self.kwargs["agent_id"],
                    "prompt": prompt,
                    "workspace": workspace,
                    "context": context,
                    "runner_exists": (workspace / ".echo-eval/run-tests").is_file()
                    or (workspace / ".echo-eval/run-tests.cmd").is_file(),
                    "approval_action": self.kwargs["approval_action"],
                    "approval_responder": self.kwargs["approval_responder"],
                }
            )
            return iter(
                [
                    {
                        "kind": "turn_result",
                        "turn": {"status": "completed", "items": []},
                    }
                ]
            )

    monkeypatch.setattr(comparison, "RealtimeTrialRunner", FakeRealtimeRunner)
    native_root = tmp_path / "native"
    codex_root = tmp_path / "codex"
    native = prepare_fixture_suite(
        repo_root=comparison.REPO_ROOT,
        runs_root=native_root,
        preserve_runs=True,
        case_ids={"coding.path-boundary"},
    )
    codex = prepare_fixture_suite(
        repo_root=comparison.REPO_ROOT,
        runs_root=codex_root,
        preserve_runs=True,
        case_ids={"coding.path-boundary"},
    )

    comparison._run_backend(
        backend="native",
        agent_id="general",
        prepared=native,
        k=2,
        url="ws://echo.invalid/api/realtime",
        token=None,
        model="same-model",
        timeout=5,
    )
    comparison._run_backend(
        backend="codex",
        agent_id="coder",
        prepared=codex,
        k=2,
        url="ws://echo.invalid/api/realtime",
        token=None,
        model="same-model",
        timeout=5,
    )

    assert len(observed) == 4
    workspaces = [row["workspace"] for row in observed]
    assert len(set(workspaces)) == 4
    assert all(Path(path).is_relative_to(native_root) for path in workspaces[:2])
    assert all(Path(path).is_relative_to(codex_root) for path in workspaces[2:])
    assert observed[0]["prompt"] == observed[2]["prompt"]
    assert "./.echo-eval/run-tests" in str(observed[0]["prompt"])
    assert all(row["runner_exists"] is True for row in observed)
    assert [row["agent_id"] for row in observed] == [
        "general",
        "general",
        "coder",
        "coder",
    ]
    assert "partner_model" not in observed[0]["context"]
    assert "partner_model" not in observed[2]["context"]
    assert all(row["approval_action"] == "decline" for row in observed)
    assert all(callable(row["approval_responder"]) for row in observed[:2])
    assert all(row["approval_responder"] is None for row in observed[2:])


def test_native_approval_is_exact_path_and_command_allowlist(tmp_path: Path) -> None:
    prepared = prepare_fixture_suite(
        repo_root=comparison.REPO_ROOT,
        runs_root=tmp_path / "runs",
        case_ids={"coding.path-boundary"},
    )
    fixture = prepared.fixtures["coding.path-boundary"]
    fixture.setup()
    try:
        workspace = fixture.workspace()
        responder = comparison._strict_native_approval_responder(
            workspace=fixture.workspace,
            allowed_write_paths=("file_service.py", "tests/test_file_service.py"),
            fixture=fixture,
        )
        assert responder(
            "item/commandExecution/requestApproval",
            {
                "tool": "write_text_file",
                "argsPreview": "{'path': 'tests/test_file_service.py', 'content': '...truncated",
            },
        ) == {"action": "accept"}
        assert responder(
            "item/commandExecution/requestApproval",
            {"tool": "edit_file", "argsPreview": "{'path': 'file_service.py', 'old_string': 'x'"},
        ) == {"action": "accept"}
        assert responder(
            "item/commandExecution/requestApproval",
            {"tool": "exec_shell", "argsPreview": "{'command': './.echo-eval/run-tests'}"},
        ) == {"action": "accept"}

        denied = [
            {"tool": "write_text_file", "argsPreview": "{'path': '../escape.py', 'content': 'x'}"},
            {
                "tool": "write_text_file",
                "argsPreview": "{'content': 'x', 'path': 'file_service.py'}",
            },
            {"tool": "write_text_file", "argsPreview": f"{{'path': '{tmp_path / 'outside.py'}'}}"},
            {"tool": "exec_shell", "argsPreview": "{'command': 'python -m pytest'}"},
            {
                "tool": "exec_shell",
                "argsPreview": "{'command': './.echo-eval/run-tests', 'cwd': '/tmp'}",
            },
            {"tool": "read_text_file", "argsPreview": "{'path': 'file_service.py'}"},
        ]
        for params in denied:
            assert responder("item/commandExecution/requestApproval", params) == {
                "action": "decline"
            }

        (workspace / "tests" / "escape-link.py").symlink_to(tmp_path / "outside.py")
        symlink_responder = comparison._strict_native_approval_responder(
            workspace=fixture.workspace,
            allowed_write_paths=("tests/escape-link.py",),
            fixture=fixture,
        )
        assert symlink_responder(
            "item/commandExecution/requestApproval",
            {"tool": "write_text_file", "argsPreview": "{'path': 'tests/escape-link.py'}"},
        ) == {"action": "decline"}
    finally:
        fixture.teardown()


def test_comparison_payload_keeps_raw_measurements_without_synthetic_score() -> None:
    trajectory = Trajectory(
        trial_id="coding.path-boundary.0.abc",
        case_id="coding.path-boundary",
        started_at=10.0,
        ended_at=11.0,
    )
    trajectory.append(
        "turn_result",
        turn={"status": "completed", "items": []},
    )
    measurement = measurement_from_trial(
        trajectory,
        Verdict(passed=True, reason="hidden verifier passed"),
        backend="native",
        agent_id="coder",
        model=None,
    )

    payload = comparison.build_comparison_payload(
        [measurement],
        source_revision={"git_commit": "a" * 40, "worktree_dirty": True},
        controller_source_manifest={
            "schema": "echo.source_manifest.v1",
            "pre_run_sha256": "b" * 64,
            "post_run_sha256": "b" * 64,
            "stable_during_run": True,
        },
        configuration={"requested_model": None, "cases": ["coding.path-boundary"]},
        run_id="comparison-1",
        requested_k=1,
        source_stable=True,
    )

    assert payload["schema"] == "echo.engine_comparison.v2"
    assert payload["version"] == 2
    assert payload["source_revision"]["git_commit"] == "a" * 40
    assert payload["measurements"][0]["backend"] == "native"
    assert payload["measurements"][0]["trajectory"]["steps"]
    assert payload["measurements"][0]["usage"]["cost_usd"] is None
    assert "score" not in payload["measurements"][0]
    assert payload["measurement_summary"] == {
        "total": 1,
        "infrastructure_valid": 1,
        "infrastructure_invalid": 0,
        "failure_categories": {},
    }
    assert payload["aggregates"] == [
        {
            "backend": "native",
            "case_id": "coding.path-boundary",
            "requested_k": 1,
            "scheduled": 1,
            "valid": 1,
            "invalid": 0,
            "passes": 1,
            "pass_rate": 1.0,
            "complete": True,
            "pass_at_k": 1.0,
        }
    ]


def test_real_comparison_measurements_feed_strict_experiment_pairs(tmp_path: Path) -> None:
    measurements = []
    for backend, agent_id in (("native", "general"), ("codex", "coder")):
        trajectory = Trajectory(
            trial_id=f"coding.path-boundary.0.{backend}",
            case_id="coding.path-boundary",
        )
        trajectory.append("turn_result", turn={"status": "completed", "items": []})
        measurements.append(
            measurement_from_trial(
                trajectory,
                Verdict(passed=True, reason="hidden verifier passed"),
                backend=backend,
                agent_id=agent_id,
                model=None,
                trial_index=0,
            )
        )
    payload = comparison.build_comparison_payload(
        measurements,
        source_revision={"git_commit": "a" * 40, "worktree_dirty": False},
        controller_source_manifest={
            "schema": "echo.source_manifest.v1",
            "pre_run_sha256": "b" * 64,
            "post_run_sha256": "b" * 64,
            "stable_during_run": True,
        },
        configuration={
            "control_plane": "echo_realtime",
            "requested_model": None,
            "timeout_seconds": 900,
            "hidden_verifier_postflight": {"stable_during_run": True},
            "cases": [
                {
                    "case_id": "coding.path-boundary",
                    "prompt": "repair the bounded path fixture",
                    "prompt_sha256": "c" * 64,
                    "suite_prompt_contract_sha256": "d" * 64,
                    "grader": {"id": "fixture", "rubric_sha256": "e" * 64},
                    "verifier": {"sha256": "f" * 64},
                    "fixture": {"manifest_sha256": "1" * 64},
                }
            ],
        },
        run_id="comparison-pair-1",
        requested_k=1,
        source_stable=True,
        hardened_runner_stable=True,
    )
    store = ExperimentStore(tmp_path / "experiments.jsonl")

    first = comparison.ingest_comparison_measurements(
        measurements,
        payload=payload,
        store=store,
        artifact_path=tmp_path / "comparison.json",
    )
    second = comparison.ingest_comparison_measurements(
        measurements,
        payload=payload,
        store=store,
    )
    evidence = build_pair_evidence(store.list_trials())

    assert first["appended"] == 2
    assert second["skipped_existing"] == 2
    assert evidence["paired_count"] == 1
    assert (
        evidence["pairs"][0]["echo"]["task_spec_hash"]
        == evidence["pairs"][0]["codex"]["task_spec_hash"]
    )


def test_comparison_payload_is_invalid_when_valid_trials_do_not_reach_k() -> None:
    trajectory = Trajectory(
        trial_id="coding.path-boundary.0.infrastructure",
        case_id="coding.path-boundary",
    )
    trajectory.failure_category = "infrastructure"
    trajectory.error = "hidden verifier sandbox unavailable"
    trajectory.append("turn_result", turn={"status": "failed", "items": []})
    measurement = measurement_from_trial(
        trajectory,
        Verdict(passed=False, reason="grader did not run"),
        backend="native",
        agent_id="coder",
        model=None,
    )

    payload = comparison.build_comparison_payload(
        [measurement],
        source_revision={"git_commit": "a" * 40, "worktree_dirty": True},
        controller_source_manifest={
            "schema": "echo.source_manifest.v1",
            "pre_run_sha256": "b" * 64,
            "post_run_sha256": "b" * 64,
            "stable_during_run": True,
        },
        configuration={"requested_model": None, "cases": ["coding.path-boundary"]},
        run_id="comparison-infrastructure-invalid",
        requested_k=1,
        source_stable=True,
    )

    assert payload["run_validity"] == {
        "valid": False,
        "reason": "insufficient_valid_trials",
    }
    assert payload["aggregates"][0]["complete"] is False


@pytest.mark.parametrize("k", [3, 4])
def test_seeded_schedule_is_balanced_complete_and_backend_order_independent(k: int) -> None:
    cases = ["coding.path-boundary", "coding.concurrent-cache"]
    schedule = comparison._build_schedule(
        backends=["codex", "native"],
        case_ids=cases,
        k=k,
        seed="fixed-seed",
    )
    reversed_inputs = comparison._build_schedule(
        backends=["native", "codex"],
        case_ids=list(reversed(cases)),
        k=k,
        seed="fixed-seed",
    )

    assert schedule == reversed_inputs
    assert [trial.ordinal for trial in schedule] == list(range(len(schedule)))
    assert len(schedule) == len(cases) * k * 2
    assert len({(row.case_id, row.trial_index, row.backend) for row in schedule}) == len(schedule)
    for case_id in cases:
        first_counts = {"native": 0, "codex": 0}
        for trial_index in range(k):
            pair = [
                row.backend
                for row in schedule
                if row.case_id == case_id and row.trial_index == trial_index
            ]
            assert pair in (["native", "codex"], ["codex", "native"])
            first_counts[pair[0]] += 1
        assert abs(first_counts["native"] - first_counts["codex"]) <= 1
        if k % 2 == 0:
            assert first_counts["native"] == first_counts["codex"]


def test_case_contract_binds_exact_prompt_rubric_verifier_and_fixture_bytes(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "benchmarks/fixtures/coding.path-boundary/input.txt"
    verifier = tmp_path / "benchmarks/verifiers/verify_path_boundary.py"
    fixture.parent.mkdir(parents=True)
    verifier.parent.mkdir(parents=True)
    fixture.write_text("fixture-v1", encoding="utf-8")
    verifier.write_text("verifier-v1", encoding="utf-8")
    manifest = build_file_manifest(tmp_path, [fixture, verifier])
    verifier_record = manifest.file("benchmarks/verifiers/verify_path_boundary.py")
    case = EvalCase(
        id="coding.path-boundary",
        prompt="exact prompt\n",
        grader=lambda _trajectory: True,
        metadata={
            "grader_id": "hidden_fixture_verifier",
            "rubric": {"correctness": 1.0},
            "rubric_digest": "rubric-digest",
            "prompt_digest": "suite-contract-digest",
            "fixture_name": "coding.path-boundary",
            "hidden_verifier_path": verifier_record.path,
            "hidden_verifier_size_bytes": verifier_record.size_bytes,
            "hidden_verifier_sha256": verifier_record.sha256,
        },
    )

    first = comparison._case_contract(case, source_manifest=manifest)
    assert first["prompt"] == "exact prompt\n"
    assert first["grader"]["rubric"] == {"correctness": 1.0}
    assert first["verifier"]["sha256"] == verifier_record.sha256
    assert first["fixture"]["manifest_sha256"]

    fixture.write_text("fixture-v2", encoding="utf-8")
    second_manifest = build_file_manifest(tmp_path, [fixture, verifier])
    second = comparison._case_contract(case, source_manifest=second_manifest)
    assert second["fixture"]["manifest_sha256"] != first["fixture"]["manifest_sha256"]


def test_controller_source_drift_writes_invalid_artifact_and_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_probe(*_args, **_kwargs) -> None:
        return None

    before = SourceManifest(
        rule_id="test-rule",
        selected_case_ids=("coding.path-boundary",),
        files=(),
        sha256="a" * 64,
    )
    after = SourceManifest(
        rule_id="test-rule",
        selected_case_ids=("coding.path-boundary",),
        files=(),
        sha256="b" * 64,
    )
    manifests = iter([before, after])
    case = EvalCase(
        id="coding.path-boundary",
        prompt="fixed prompt",
        grader=lambda _trajectory: True,
    )
    monkeypatch.setattr(comparison, "probe_realtime_endpoint", fake_probe)
    monkeypatch.setattr(comparison, "python_test_runner_provenance", lambda: {})
    monkeypatch.setattr(
        comparison,
        "run_hardened_verifier_full_chain_smoke",
        lambda _root: _trusted_full_chain_smoke(),
    )
    monkeypatch.setattr(comparison, "verifier_sandbox_provenance", lambda: {})
    monkeypatch.setattr(
        comparison,
        "_assert_hardened_runner_matches_controller_source",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        comparison,
        "build_source_manifest",
        lambda *_args, **_kwargs: next(manifests),
    )
    monkeypatch.setattr(
        comparison,
        "prepare_fixture_suite",
        lambda **_kwargs: PreparedFixtureSuite(cases=[case], fixtures={}),
    )
    monkeypatch.setattr(
        comparison, "_case_contract", lambda *_args, **_kwargs: {"case_id": case.id}
    )
    invoked_backends: list[str] = []

    def fake_runner_for_case(**kwargs):
        invoked_backends.append(kwargs["backend"])
        return object()

    def fake_run_case(*_args, **_kwargs):
        trajectory = Trajectory(trial_id="trial", case_id=case.id)
        trajectory.append("turn_result", turn={"status": "completed", "items": []})
        return SimpleNamespace(
            trajectories=[trajectory],
            verdicts=[Verdict(passed=True, reason="passed")],
        )

    monkeypatch.setattr(comparison, "_runner_for_case", fake_runner_for_case)
    monkeypatch.setattr(comparison, "run_case", fake_run_case)
    monkeypatch.setattr(comparison, "_source_revision", lambda _root: {})
    output = tmp_path / "drift.json"

    exit_code = comparison.main(
        [
            "--backend",
            "codex",
            "--backend",
            "native",
            "--case",
            "coding.path-boundary",
            "--k",
            "3",
            "--schedule-seed",
            "fixed-seed",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == comparison._SOURCE_DRIFT_EXIT
    assert payload["run_validity"] == {
        "valid": False,
        "reason": "controller_source_changed_during_run",
    }
    assert payload["controller_source_manifest"]["pre_run_sha256"] == "a" * 64
    assert payload["controller_source_manifest"]["post_run_sha256"] == "b" * 64
    assert payload["controller_source_manifest"]["stable_during_run"] is False
    planned_backends = [
        row["backend"] for row in payload["configuration"]["execution_schedule"]["planned"]
    ]
    actual_backends = [
        row["backend"] for row in payload["configuration"]["execution_schedule"]["actual"]
    ]
    assert invoked_backends == actual_backends == planned_backends

