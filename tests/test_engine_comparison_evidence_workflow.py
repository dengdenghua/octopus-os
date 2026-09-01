from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/engine-comparison-evidence.yml"


def _job() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["engine-comparison-evidence"]


def _step(name: str) -> dict:
    return next(step for step in _job()["steps"] if step.get("name") == name)


def test_all_engine_evidence_shell_steps_parse() -> None:
    for step in _job()["steps"]:
        script = step.get("run")
        if script is None:
            continue
        parsed = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert parsed.returncode == 0, f"{step.get('name')}: {parsed.stderr}"


def test_engine_evidence_requires_protected_preprovisioned_linux_runner() -> None:
    job = _job()

    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "hardened-verifier"]
    assert job["environment"] == "engine-comparison-evidence"
    assert job["env"]["ECHO_HARDENED_VERIFIER_RUNNER"] == (
        "${{ vars.ECHO_HARDENED_ATTESTATION_PATH }}"
    )
    preflight = _step("Fail closed unless protected runner identities are complete")["run"]
    assert "ECHO_ENGINE_EVIDENCE_GUARD" in preflight
    assert "root-owned and non-writable" in preflight
    assert '"${ECHO_HARDENED_LAUNCHER_PYTHON}" -I' in preflight
    assert '"${ECHO_HARDENED_LAUNCHER_MODULE}" validate' in preflight
    assert "ECHO_HARDENED_LAUNCHER_PYTHON_SHA256" in preflight
    assert "ECHO_HARDENED_LAUNCHER_MODULE_SHA256" in preflight
    assert ".sources.launcher_executable_sha256 == $launcher_python" in preflight
    assert ".sources.launcher_module_sha256 == $launcher_module" in preflight
    assert '.candidate_api_isolation_schema == "echo.candidate_api_process.v1"' in preflight
    assert ".git_sha == $target" in preflight
    assert ".sources.contract_sha256" in preflight


def test_launcher_validate_cli_runs_as_an_isolated_absolute_script(tmp_path: Path) -> None:
    launcher = REPO_ROOT / "benchmarks/linux_hardened_verifier.py"
    environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    help_result = subprocess.run(
        [sys.executable, "-I", str(launcher), "validate", "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert help_result.returncode == 0
    assert "--attestation" in help_result.stdout
    assert "ModuleNotFoundError" not in help_result.stderr

    invalid_result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(launcher),
            "validate",
            "--attestation",
            str(tmp_path / "missing-attestation.json"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert invalid_result.returncode == 78
    assert "infrastructure invalid" in invalid_result.stderr
    assert invalid_result.stdout == ""


def test_engine_evidence_checks_out_and_records_one_exact_target_sha() -> None:
    checkout = _step("Check out the exact approved revision")
    assert checkout["with"] == {
        "ref": "${{ inputs.target_sha }}",
        "fetch-depth": 1,
        "persist-credentials": False,
    }
    provenance = _step("Build exact-SHA provenance envelope")["run"]
    assert 'revision != {"git_commit": target, "worktree_dirty": False}' in provenance
    assert '"target_sha": target' in provenance
    assert '"controller_source_manifest_sha256"' in provenance
    assert '"contract_sha256": "benchmarks/trusted_verifier_contract.py"' in provenance
    assert 'source_manifest["sha256"]' in provenance
    assert '"candidate_api_isolation_schema"' in provenance


def test_attested_sources_are_bound_before_attack_or_model_execution() -> None:
    steps = _job()["steps"]
    names = [step.get("name") for step in steps]
    preflight_name = "Fail closed unless protected runner identities are complete"
    attack_name = "Run real Linux hardened-verifier attacks before model access"
    start_name = "Start one isolated Echo control plane"
    assert names.index(preflight_name) < names.index(attack_name) < names.index(start_name)

    preflight = _step(preflight_name)["run"]
    for relative_path in (
        "benchmarks/linux_hardened_verifier.py",
        "benchmarks/trusted_verifier_contract.py",
        "benchmarks/trusted_verifier_controller.py",
        "benchmarks/trusted_verifier_worker.py",
    ):
        assert relative_path in preflight
    assert ".sources.launcher_module_sha256 == $checkout_launcher" in preflight
    assert ".sources.contract_sha256 == $checkout_contract" in preflight
    assert ".sources.controller_sha256 == $checkout_controller" in preflight
    assert ".sources.worker_sha256 == $checkout_worker" in preflight


def test_engine_evidence_uses_same_service_and_seeded_ab_ba_schedule() -> None:
    start = _step("Start one isolated Echo control plane")["run"]
    comparison = _step("Run seeded native and Codex AB/BA trials on the same service")["run"]

    assert start.count("python -m runtime serve") == 1
    assert "--port 18080" in start
    assert "--backend native" in comparison
    assert "--backend codex" in comparison
    assert "--case coding.concurrent-cache" in comparison
    assert "--case coding.path-boundary" in comparison
    assert '--schedule-seed "engine-comparison:${TARGET_SHA}"' in comparison
    assert "ws://127.0.0.1:18080/api/realtime" in comparison


def test_real_linux_attack_suite_precedes_service_and_is_provenance_bound() -> None:
    steps = _job()["steps"]
    attack_name = "Run real Linux hardened-verifier attacks before model access"
    start_name = "Start one isolated Echo control plane"
    comparison_name = "Run seeded native and Codex AB/BA trials on the same service"
    names = [step.get("name") for step in steps]
    assert names.index(attack_name) < names.index(start_name) < names.index(comparison_name)

    attack = _step(attack_name)
    assert attack["env"] == {"ECHO_RUN_HARDENED_VERIFIER_ATTACKS": "1"}
    assert "tests/test_linux_hardened_verifier_attacks.py" in attack["run"]
    assert "hardened-verifier-attacks.junit.xml" in attack["run"]
    assert 'counts["skipped"] != 0' in attack["run"]
    assert "real Linux hardened attack suite did not fully pass" in attack["run"]
    assert "ECHO_API_TOKEN" not in str(attack)

    provenance = _step("Build exact-SHA provenance envelope")["run"]
    upload = _step("Upload signed exact-SHA comparison evidence")["with"]["path"]
    assert '"hardened_attack_suite"' in provenance
    assert 'attack_counts["skipped"] != 0' in provenance
    assert "digest(attack_source_path)" in provenance
    assert "hardened-verifier-attacks.junit.xml" in upload
    assert "hardened-verifier-attacks.log" in upload


def test_protected_runtime_inputs_are_rechecked_without_publishing_auth_identity() -> None:
    job = _job()
    baseline = job["env"]["ECHO_PROTECTED_IDENTITY_BASELINE"]
    assert baseline.startswith("${{ runner.temp }}/echo-protected-identities-")
    preflight = _step("Fail closed unless protected runner identities are complete")["run"]
    for value in (
        "ECHO_EVAL_CONFIG",
        "ECHO_CODEX_EXECUTABLE",
        "ECHO_CODEX_SOURCE_HOME",
        "auth_path",
    ):
        assert value in preflight
    assert "stat -c '%d'" in preflight
    assert "stat -c '%i'" in preflight
    assert "emit_file_identity codex_auth" in preflight

    revalidate = _step("Revalidate protected identities before provenance")
    assert revalidate["if"] == "always()"
    assert "cmp --silent" in revalidate["run"]
    assert "Protected input identity changed" in revalidate["run"]
    assert "git status --porcelain --untracked-files=all" in revalidate["run"]
    assert "ECHO_EVAL_EXPECTED_CONFIG_SHA256" in revalidate["run"]
    assert "ECHO_EVAL_EXPECTED_CODEX_SHA256" in revalidate["run"]
    names = [step.get("name") for step in job["steps"]]
    assert (
        names.index("Run seeded native and Codex AB/BA trials on the same service")
        < names.index("Revalidate protected identities before provenance")
        < names.index("Build exact-SHA provenance envelope")
    )

    provenance = _step("Build exact-SHA provenance envelope")["run"]
    upload = _step("Upload signed exact-SHA comparison evidence")["with"]["path"]
    assert "codex_auth" not in provenance
    assert "auth.json" not in provenance
    assert "protected-identities" not in upload
    assert (
        "ECHO_PROTECTED_IDENTITY_BASELINE"
        in _step("Stop the isolated Echo control plane")["run"]
    )


def test_engine_evidence_is_oidc_signed_verified_and_uploaded() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    sign = _step("Sign and verify the provenance-bound evidence")["run"]
    upload = _step("Upload signed exact-SHA comparison evidence")["with"]

    assert "cosign sign-blob --yes" in sign
    assert "cosign verify-blob" in sign
    assert "https://token.actions.githubusercontent.com" in sign
    assert "evidence-provenance.json" in upload["path"]
    assert "evidence-provenance.sig" in upload["path"]
    assert "evidence-provenance.pem" in upload["path"]
    assert upload["if-no-files-found"] == "error"

