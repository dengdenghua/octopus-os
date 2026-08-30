from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks import hardened_verifier_smoke as smoke
from benchmarks.trusted_verifier_controller import (
    UnsafeLocalWorkerLauncher,
    evaluate_concurrent_cache,
    evaluate_path_boundary,
)
from benchmarks.verifier_sandbox import FixtureInfrastructureError, VerifierProcessResult


def _repository(root: Path) -> Path:
    verifier_root = root / "benchmarks" / "verifiers"
    verifier_root.mkdir(parents=True)
    (verifier_root / "verify_path_boundary.py").write_text(
        "# evaluator path wrapper\n", encoding="utf-8"
    )
    (verifier_root / "verify_concurrent_cache.py").write_text(
        "# evaluator cache wrapper\n", encoding="utf-8"
    )
    return root


def _passing_result(case: Any) -> VerifierProcessResult:
    return VerifierProcessResult(
        returncode=0,
        stdout=json.dumps(
            {
                "checks": list(case.checks),
                "passed": True,
                "reason": case.reason,
                "score": 1.0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        stderr="",
        timed_out=False,
    )


def test_full_chain_smoke_uses_two_private_known_good_workspaces_and_cleans_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repository(tmp_path / "repo")
    by_verifier = {case.verifier_name: case for case in smoke._CASES}
    observed: list[dict[str, Any]] = []

    def fake_run_hidden_verifier(**kwargs: Any) -> VerifierProcessResult:
        verifier = Path(kwargs["verifier_source"])
        workspace = Path(kwargs["workspace"])
        case = by_verifier[verifier.name]
        observed.append(
            {
                "workspace": workspace,
                "module": (workspace / case.module_name).read_text(encoding="utf-8"),
                "test": (workspace / case.test_name).read_text(encoding="utf-8"),
                "pyproject": (workspace / "pyproject.toml").read_text(encoding="utf-8"),
                "arguments": kwargs["argument_templates"],
                "timeout": kwargs["timeout_seconds"],
                "expected_sha256": kwargs["expected_source_sha256"],
                "verifier_sha256": hashlib.sha256(verifier.read_bytes()).hexdigest(),
            }
        )
        return _passing_result(case)

    monkeypatch.setattr(smoke, "run_hidden_verifier", fake_run_hidden_verifier)

    result = smoke.run_hardened_verifier_full_chain_smoke(root)

    assert result["schema"] == smoke.FULL_CHAIN_SMOKE_SCHEMA
    assert result["passed"] is True
    assert [row["case_id"] for row in result["cases"]] == [
        "coding.path-boundary",
        "coding.concurrent-cache",
    ]
    assert len({row["workspace"] for row in observed}) == 2
    for call, case, evidence in zip(observed, smoke._CASES, result["cases"], strict=True):
        assert call["module"] == case.candidate_source
        assert call["test"] == smoke._TEST_SOURCE
        assert call["pyproject"] == smoke._PYPROJECT_SOURCE
        assert call["arguments"] == ("{workspace}",)
        assert call["timeout"] == smoke._TIMEOUT_SECONDS
        assert call["expected_sha256"] == call["verifier_sha256"]
        assert (
            evidence["candidate_sha256"]
            == hashlib.sha256(case.candidate_source.encode("utf-8")).hexdigest()
        )
        assert evidence["verifier_sha256"] == call["verifier_sha256"]
        assert evidence["checks"] == list(case.checks)
        assert not call["workspace"].exists()


def test_evaluator_owned_candidates_pass_the_controller_with_local_worker(tmp_path: Path) -> None:
    path_case, cache_case = smoke._CASES
    path_workspace = tmp_path / path_case.case_id
    cache_workspace = tmp_path / cache_case.case_id
    smoke._write_workspace(path_workspace, path_case)
    smoke._write_workspace(cache_workspace, cache_case)

    path_result = evaluate_path_boundary(
        path_workspace,
        launcher=UnsafeLocalWorkerLauncher(),
    )
    cache_result = evaluate_concurrent_cache(
        cache_workspace,
        launcher=UnsafeLocalWorkerLauncher(),
    )

    assert path_result == {
        "checks": list(path_case.checks),
        "passed": True,
        "reason": path_case.reason,
        "score": 1.0,
    }
    assert cache_result == {
        "checks": list(cache_case.checks),
        "passed": True,
        "reason": cache_case.reason,
        "score": 1.0,
    }


def test_known_good_rejection_is_infrastructure_and_workspace_is_cleaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repository(tmp_path / "repo")
    workspaces: list[Path] = []

    def reject(**kwargs: Any) -> VerifierProcessResult:
        workspaces.append(Path(kwargs["workspace"]))
        return VerifierProcessResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "checks": [],
                    "passed": False,
                    "reason": "candidate observation failed",
                    "score": 0.0,
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr(smoke, "run_hidden_verifier", reject)

    with pytest.raises(smoke.HardenedVerifierFullChainSmokeError) as exc_info:
        smoke.run_hardened_verifier_full_chain_smoke(root)

    assert isinstance(exc_info.value, FixtureInfrastructureError)
    assert exc_info.value.category == "known_good_rejected"
    assert exc_info.value.case_id == "coding.path-boundary"
    assert len(workspaces) == 1
    assert not workspaces[0].exists()


def test_runner_failure_remains_distinct_infrastructure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repository(tmp_path / "repo")
    workspaces: list[Path] = []

    def fail(**kwargs: Any) -> VerifierProcessResult:
        workspaces.append(Path(kwargs["workspace"]))
        raise FixtureInfrastructureError("attested controller failed")

    monkeypatch.setattr(smoke, "run_hidden_verifier", fail)

    with pytest.raises(smoke.HardenedVerifierFullChainSmokeError) as exc_info:
        smoke.run_hardened_verifier_full_chain_smoke(root)

    assert exc_info.value.category == "runner_infrastructure"
    assert exc_info.value.case_id == "coding.path-boundary"
    assert "attested controller failed" in str(exc_info.value)
    assert len(workspaces) == 1
    assert not workspaces[0].exists()


def test_unexpected_runner_exception_is_promoted_to_infrastructure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repository(tmp_path / "repo")

    def fail(**_kwargs: Any) -> VerifierProcessResult:
        raise RuntimeError("runner protocol crashed")

    monkeypatch.setattr(smoke, "run_hidden_verifier", fail)

    with pytest.raises(smoke.HardenedVerifierFullChainSmokeError) as exc_info:
        smoke.run_hardened_verifier_full_chain_smoke(root)

    assert isinstance(exc_info.value, FixtureInfrastructureError)
    assert exc_info.value.category == "runner_infrastructure"
    assert exc_info.value.case_id == "coding.path-boundary"
    assert "RuntimeError: runner protocol crashed" in str(exc_info.value)


@pytest.mark.parametrize(
    "variant",
    ["extra-line", "duplicate-key", "missing-field", "nonfinite", "partial-score", "stderr"],
)
def test_controller_output_is_strictly_single_json(
    variant: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repository(tmp_path / "repo")
    case = smoke._CASES[0]
    payload = {
        "checks": list(case.checks),
        "passed": True,
        "reason": case.reason,
        "score": 1.0,
    }
    stdout = json.dumps(payload, allow_nan=True, sort_keys=True) + "\n"
    stderr = ""
    if variant == "extra-line":
        stdout += "{}\n"
    elif variant == "duplicate-key":
        stdout = '{"checks":[],"passed":true,"passed":true,"reason":"duplicate","score":1.0}\n'
    elif variant == "missing-field":
        payload.pop("reason")
        stdout = json.dumps(payload, sort_keys=True) + "\n"
    elif variant == "nonfinite":
        payload["score"] = float("nan")
        stdout = json.dumps(payload, allow_nan=True, sort_keys=True) + "\n"
    elif variant == "partial-score":
        payload["score"] = 0.5
        stdout = json.dumps(payload, sort_keys=True) + "\n"
    elif variant == "stderr":
        stderr = "unexpected warning"

    monkeypatch.setattr(
        smoke,
        "run_hidden_verifier",
        lambda **_kwargs: VerifierProcessResult(
            returncode=0,
            stdout=stdout,
            stderr=stderr,
        ),
    )

    with pytest.raises(smoke.HardenedVerifierFullChainSmokeError) as exc_info:
        smoke.run_hardened_verifier_full_chain_smoke(root)

    assert exc_info.value.category == "invalid_controller_output"
    assert exc_info.value.case_id == "coding.path-boundary"


def test_missing_wrapper_is_reported_as_infrastructure(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    (root / "benchmarks/verifiers/verify_path_boundary.py").unlink()

    with pytest.raises(smoke.HardenedVerifierFullChainSmokeError) as exc_info:
        smoke.run_hardened_verifier_full_chain_smoke(root)

    assert exc_info.value.category == "runner_infrastructure"
    assert exc_info.value.case_id == "coding.path-boundary"

