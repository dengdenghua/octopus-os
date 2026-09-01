from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
ACTIONLINT_CONFIG = ROOT / ".github" / "actionlint.yaml"
DELIVERY_WORKFLOWS = (
    "ci.yml",
    "os-image.yml",
    "ab-update-smoke.yml",
    "desktop-session-smoke.yml",
    "omv-real-x86.yml",
    "appliance-release.yml",
    "delivery-release-candidate.yml",
)
BRANCH_WORKFLOWS = DELIVERY_WORKFLOWS[:5]
DELIVERY_BRANCHES = {"os-main", "main"}
FULL_SHA_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
APPROVED_ACTIONS = {
    "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
    "astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86",
    "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
    "docker/login-action@dbcb813823bdd20940b903addbd779551569679f",
    "docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302",
    "docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e",
    "docker/setup-qemu-action@96fe6ef7f33517b61c61be40b68a1882f3264fb8",
    "pnpm/action-setup@ff378ebe6b225b0680b81c1ad4498ae0d1d3a5e3",
}
ATTESTATION_JOB_PERMISSIONS = {
    "contents": "read",
    "id-token": "write",
    "attestations": "write",
    "artifact-metadata": "write",
}


def _workflow(name: str) -> dict[str, object]:
    value = yaml.safe_load((WORKFLOW_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _trigger(workflow: dict[str, object]) -> dict[str, object]:
    value = workflow.get("on", workflow.get(True))
    assert isinstance(value, dict)
    return value


def _steps(workflow: dict[str, object]) -> list[dict[str, object]]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    steps: list[dict[str, object]] = []
    for job in jobs.values():
        assert isinstance(job, dict)
        raw_steps = job.get("steps", [])
        assert isinstance(raw_steps, list)
        assert all(isinstance(step, dict) for step in raw_steps)
        steps.extend(raw_steps)
    return steps


def test_workflow_linter_is_checksum_pinned_and_knows_the_dedicated_runner() -> None:
    config = yaml.safe_load(ACTIONLINT_CONFIG.read_text(encoding="utf-8"))
    assert config == {"self-hosted-runner": {"labels": ["echo-os-image"]}}

    workflow = _workflow("ci.yml")
    job = workflow["jobs"]["workflow-contract"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 10
    assert job["env"] == {
        "ACTIONLINT_VERSION": "1.7.12",
        "ACTIONLINT_LINUX_AMD64_SHA256": (
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
        ),
    }
    source = str(job["steps"][1]["run"])
    assert "https://github.com/rhysd/actionlint/releases/download/" in source
    assert "sha256sum --check --strict" in source
    assert '"$actionlint_directory/actionlint" -shellcheck= -pyflakes=' in source


def test_main_ci_runs_the_complete_frontend_quality_gate_on_pull_requests() -> None:
    workflow = _workflow("ci.yml")
    job = workflow["jobs"]["frontend"]

    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 20
    assert "if" not in job
    steps = job["steps"]
    install = next(
        step for step in steps if step.get("name") == "Install dependencies"
    )
    quality = next(step for step in steps if step.get("name") == "Verify frontend source quality")
    tests = next(
        step for step in steps if step.get("name") == "Run frontend and Electron contracts"
    )
    build = next(step for step in steps if step.get("name") == "Build production frontend")

    assert install["working-directory"] == "frontend"
    assert install["run"] == "pnpm install --frozen-lockfile"
    assert str(quality["run"]).split() == ["pnpm", "format", "pnpm", "lint", "pnpm", "typecheck"]
    assert tests["run"] == "pnpm test"
    assert build["run"] == "pnpm build"


def test_privileged_container_jobs_use_the_validated_container_scratch_path() -> None:
    for workflow_name, job_name in (
        ("os-image.yml", "build-and-boot"),
        ("ab-update-smoke.yml", "signed-update-rollback"),
    ):
        workflow = _workflow(workflow_name)
        job = workflow["jobs"][job_name]
        assert job["env"]["TMPDIR"] == "/__w/_temp"
        assert "runner.temp" not in str(job["env"])


def test_installer_smoke_jobs_install_the_production_gpt_runtime() -> None:
    for workflow_name, job_name in (
        ("os-image.yml", "build-and-boot"),
        ("ab-update-smoke.yml", "signed-update-rollback"),
    ):
        workflow = _workflow(workflow_name)
        job = workflow["jobs"][job_name]
        dependency_step = next(
            step for step in job["steps"] if str(step.get("name", "")).startswith("Install image")
        )
        packages = str(dependency_step["run"]).split()
        assert "gdisk" in packages, f"{workflow_name} cannot provide installer sgdisk"


def test_delivery_workflows_target_the_actual_and_compatible_delivery_branches() -> None:
    for name in BRANCH_WORKFLOWS:
        trigger = _trigger(_workflow(name))
        for event in ("push", "pull_request"):
            event_trigger = trigger[event]
            assert isinstance(event_trigger, dict), f"{name} {event} trigger is not bounded"
            assert set(event_trigger["branches"]) == DELIVERY_BRANCHES, (
                f"{name} {event} does not cover the actual os-main delivery branch"
            )


def test_every_delivery_workflow_action_is_allowlisted_and_pinned_to_a_full_sha() -> None:
    for name in DELIVERY_WORKFLOWS:
        actions = [str(step["uses"]) for step in _steps(_workflow(name)) if "uses" in step]
        assert actions, f"{name} has no third-party actions to validate"
        for action in actions:
            assert FULL_SHA_ACTION.fullmatch(action) is not None, (
                f"{name} contains a mutable or malformed action reference: {action}"
            )
            assert action in APPROVED_ACTIONS, f"{name} contains an unreviewed action: {action}"


def test_branch_attestations_cannot_run_for_pull_requests_or_unrelated_branches() -> None:
    for name in ("ci.yml", "os-image.yml", "ab-update-smoke.yml", "omv-real-x86.yml"):
        attestations = [
            step
            for step in _steps(_workflow(name))
            if str(step.get("uses", "")).startswith("actions/attest@")
        ]
        assert attestations
        for step in attestations:
            condition = str(step.get("if", ""))
            assert "github.event_name" in condition
            assert "pull_request" in condition or "== 'push'" in condition
            assert "refs/heads/os-main" in condition
            assert "refs/heads/main" in condition


def test_privileged_delivery_jobs_are_branch_bounded_and_hold_the_only_oidc_permissions() -> None:
    jobs_by_workflow = {
        "os-image.yml": "build-and-boot",
        "ab-update-smoke.yml": "signed-update-rollback",
        "omv-real-x86.yml": "omv-real-x86",
    }
    for name, delivery_job_name in jobs_by_workflow.items():
        workflow = _workflow(name)
        assert workflow["permissions"] == {"contents": "read"}
        jobs = workflow["jobs"]
        source_job = jobs["source-contract"]
        delivery_job = jobs[delivery_job_name]
        assert "permissions" not in source_job
        assert delivery_job["permissions"] == ATTESTATION_JOB_PERMISSIONS
        condition = str(delivery_job["if"])
        assert "github.event_name != 'pull_request'" in condition
        assert "refs/heads/os-main" in condition
        assert "refs/heads/main" in condition
        assert delivery_job["needs"] == "source-contract"


def test_ab_source_contract_uses_debian_13_native_systemd_parser() -> None:
    workflow = _workflow("ab-update-smoke.yml")
    source_job = workflow["jobs"]["source-contract"]
    assert source_job["container"] == "debian:trixie-slim"
    steps = source_job["steps"]
    dependency_step = next(
        step for step in steps if step.get("name") == "Install source-contract dependencies"
    )
    contract_step = next(
        step for step in steps if step.get("name") == "Verify private-source and image contracts"
    )
    assert "systemd" in str(dependency_step["run"]).split()
    source = str(contract_step["run"])
    assert "verify_operations_systemd_units.py" in source
    assert "--require-os-id debian --require-version-id 13" in source
    assert '--source-revision "$GITHUB_SHA"' in source
    assert "operations_bundle.py extract" in source
    assert "--require-root-owner" in source
    assert "> /tmp/echo-operations-native-systemd-verification.json" in source
    assert "| tee" not in source

    signed_job = workflow["jobs"]["signed-update-rollback"]
    signed_steps = signed_job["steps"]
    native_step = next(
        step
        for step in signed_steps
        if step.get("name") == "Verify operations units with Debian 13 native systemd"
    )
    bind_step = next(
        step
        for step in signed_steps
        if step.get("name") == "Bind and sign the complete A/B lifecycle evidence"
    )
    native_source = str(native_step["run"])
    assert "--require-os-id debian --require-version-id 13" in native_source
    assert '--source-revision "$GITHUB_SHA"' in native_source
    assert "ECHO_OPERATIONS_SYSTEMD_VERIFICATION" in native_source
    assert "--operations-systemd-verification" in str(bind_step["run"])


def test_release_candidate_workflow_has_bounded_manual_inputs_and_permissions() -> None:
    workflow = _workflow("delivery-release-candidate.yml")
    trigger = _trigger(workflow)
    assert set(trigger) == {"workflow_dispatch"}
    dispatch = trigger["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    assert set(dispatch["inputs"]) == {
        "source_revision",
        "release_tag",
        "os_image_run_id",
        "ab_update_run_id",
        "real_omv_x86_run_id",
        "appliance_run_id",
    }
    assert all(value["required"] is True for value in dispatch["inputs"].values())
    assert workflow["permissions"] == {
        "contents": "read",
        "actions": "read",
        "id-token": "write",
        "attestations": "write",
        "artifact-metadata": "write",
    }
    assert workflow["jobs"]["bind-candidate"]["if"] == "github.ref == 'refs/heads/os-main'"


def test_raw_and_ab_evidence_have_oidc_identity_and_release_coordination_retention() -> None:
    expected = {
        "os-image.yml": (
            "echo-os-x86-64-release-evidence",
            (
                "${{ runner.temp }}/echo-os-image-evidence.json",
                "${{ runner.temp }}/echo-os-image-evidence.json.gpg",
                "${{ runner.temp }}/echo-install-keyring.gpg",
            ),
        ),
        "ab-update-smoke.yml": (
            "echo-os-ab-update-release-evidence",
            (
                "${{ runner.temp }}/echo-ab-update-evidence.json",
                "${{ runner.temp }}/echo-ab-update-evidence.json.gpg",
                "${{ runner.temp }}/echo-update-keyring.gpg",
            ),
        ),
    }
    for name, (artifact_name, artifacts) in expected.items():
        workflow = _workflow(name)
        assert workflow["permissions"] == {"contents": "read"}
        uploads = [
            step
            for step in _steps(workflow)
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
            and step["with"]["name"] == artifact_name
        ]
        assert len(uploads) == 1
        upload = uploads[0]["with"]
        assert tuple(str(upload["path"]).splitlines()) == artifacts
        assert upload["if-no-files-found"] == "error"
        assert upload["retention-days"] == 30


def test_release_candidate_workflow_never_injects_inputs_into_shell_scripts() -> None:
    workflow = _workflow("delivery-release-candidate.yml")
    for step in _steps(workflow):
        if "run" in step:
            assert "${{ inputs." not in str(step["run"])


def test_release_candidate_workflow_downloads_and_binds_all_exact_upstream_runs() -> None:
    workflow = _workflow("delivery-release-candidate.yml")
    source = (WORKFLOW_ROOT / "delivery-release-candidate.yml").read_text(encoding="utf-8")
    assert source.count("actions/download-artifact@") == 4
    for artifact in (
        "echo-os-x86-64-release-evidence",
        "echo-os-ab-update-release-evidence",
        "echo-real-omv-x86-evidence",
        "echo-appliance-${{ inputs.release_tag }}",
    ):
        assert f"name: {artifact}" in source
    for command in (
        "delivery_source_preflight.py",
        "hub_lifecycle_lab.py",
        "lan_discovery_functional_lab.py",
        "paperless_functional_lab.py",
        "physical_acceptance.py",
        "physical_acceptance_capture.py",
        "product_delivery_bundle.py",
        "release_candidate_preflight.py",
        "release_evidence_index.py",
        "verify_public_keyring.py",
        "verify-release-candidate-bundle.sh",
        "inputs/openmediavault-echo-os.deb",
        "--candidate-preflight",
        "--ab-keyring evidence/ab-update/echo-update-keyring.gpg",
        "Attest the complete release-candidate decision",
        "physical_acceptance_capture.py plan",
        "physical_acceptance_capture.py verify-plan",
        "echo-physical-acceptance-lab-plan.json",
    ):
        assert command in source
    upload = [
        step
        for step in _steps(workflow)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(upload) == 1
    assert upload[0]["with"]["if-no-files-found"] == "error"
    assert upload[0]["with"]["retention-days"] == 90


def test_release_candidate_packages_and_replays_one_complete_offline_audit_bundle() -> None:
    workflow = _workflow("delivery-release-candidate.yml")
    steps = _steps(workflow)
    names = [str(step.get("name", "")) for step in steps]
    package = next(
        step for step in steps if step.get("name") == "Package the offline candidate audit contract"
    )
    replay = next(
        step
        for step in steps
        if step.get("name") == "Replay the packaged candidate without repository source"
    )
    attest = next(step for step in steps if str(step.get("uses", "")).startswith("actions/attest@"))
    upload = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )

    assert names.index(str(package["name"])) < names.index(str(replay["name"]))
    assert names.index(str(replay["name"])) < names.index(str(attest["name"]))
    package_source = str(package["run"])
    for required in (
        "delivery_source_preflight.py",
        "lan_discovery_functional_lab.py",
        "release_candidate_preflight.py",
        "release_evidence_index.py",
        "verify_public_keyring.py",
        "verify-os-image-evidence-release.sh",
        "verify-release-candidate-bundle.sh",
        "inputs/os-image-evidence.json",
        "inputs/os-image-evidence.json.gpg",
        "inputs/os-image-keyring.gpg",
        "inputs/ab-update-evidence.json",
        "inputs/ab-update-evidence.json.gpg",
        "inputs/ab-update-keyring.gpg",
        "inputs/omv-evidence.json",
        "inputs/omv-verification.json",
        "inputs/openmediavault-echo-os.deb",
        "inputs/appliance-release.json",
    ):
        assert required in package_source
    assert replay["run"] == "./dist/release-candidate/verify-release-candidate-bundle.sh"
    assert "dist/release-candidate/inputs/*" in str(attest["with"]["subject-path"])
    assert upload["with"]["path"] == "dist/release-candidate/**"


def test_release_candidate_artifact_contract_matches_every_upstream_upload_root() -> None:
    contracts = (
        (
            "os-image.yml",
            "echo-os-x86-64-release-evidence",
            "echo-os-x86-64-release-evidence",
            "evidence/os-image",
            (
                "echo-os-image-evidence.json",
                "echo-os-image-evidence.json.gpg",
                "echo-install-keyring.gpg",
            ),
        ),
        (
            "ab-update-smoke.yml",
            "echo-os-ab-update-release-evidence",
            "echo-os-ab-update-release-evidence",
            "evidence/ab-update",
            (
                "echo-ab-update-evidence.json",
                "echo-ab-update-evidence.json.gpg",
                "echo-update-keyring.gpg",
            ),
        ),
        (
            "omv-real-x86.yml",
            "echo-real-omv-x86-evidence",
            "echo-real-omv-x86-evidence",
            "evidence/omv",
            (
                "echo-real-omv-x86-evidence.json",
                "echo-real-omv-x86-evidence.verification.json",
                "openmediavault-echo-os_*_all.deb",
            ),
        ),
        (
            "appliance-release.yml",
            "echo-appliance-${{ github.ref_name }}",
            "echo-appliance-${{ inputs.release_tag }}",
            "evidence/appliance",
            ("echo-appliance-release.json",),
        ),
    )
    candidate = _workflow("delivery-release-candidate.yml")
    candidate_source = (WORKFLOW_ROOT / "delivery-release-candidate.yml").read_text(
        encoding="utf-8"
    )
    downloads = [
        step
        for step in _steps(candidate)
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    ]

    for producer_name, upload_name, download_name, download_root, filenames in contracts:
        producer = _workflow(producer_name)
        uploads = [
            step
            for step in _steps(producer)
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
            and step["with"]["name"] == upload_name
        ]
        matching_downloads = [step for step in downloads if step["with"]["name"] == download_name]
        assert len(uploads) == 1
        assert len(matching_downloads) == 1
        assert matching_downloads[0]["with"]["path"] == download_root

        upload_paths = str(uploads[0]["with"]["path"]).splitlines()
        matched_paths = [path for path in upload_paths if path.rsplit("/", 1)[-1] in set(filenames)]
        assert len(matched_paths) == len(filenames)
        assert len({path.rsplit("/", 1)[0] for path in matched_paths}) == 1
        for filename in filenames:
            assert f"{download_root}/{filename}" in candidate_source


def test_release_candidate_bundle_contains_every_offline_replay_input() -> None:
    workflow = _workflow("delivery-release-candidate.yml")
    source = (WORKFLOW_ROOT / "delivery-release-candidate.yml").read_text(encoding="utf-8")
    packaged_inputs = (
        "inputs/os-image-evidence.json",
        "inputs/os-image-evidence.json.gpg",
        "inputs/os-image-keyring.gpg",
        "inputs/ab-update-evidence.json",
        "inputs/ab-update-evidence.json.gpg",
        "inputs/ab-update-keyring.gpg",
        "inputs/omv-evidence.json",
        "inputs/omv-verification.json",
        "inputs/openmediavault-echo-os.deb",
        "inputs/appliance-release.json",
    )
    for relative_name in packaged_inputs:
        assert source.count(relative_name) >= 2
    for packaged_tool in (
        "delivery_source_preflight.py",
        "release_candidate_preflight.py",
        "release_evidence_index.py",
        "verify-os-image-evidence-release.sh",
        "verify-release-candidate-bundle.sh",
    ):
        assert source.count(packaged_tool) >= 2

    attestation = next(
        step for step in _steps(workflow) if str(step.get("uses", "")).startswith("actions/attest@")
    )
    assert "dist/release-candidate/inputs/*" in str(attestation["with"]["subject-path"])
    upload = next(
        step
        for step in _steps(workflow)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["with"]["path"] == "dist/release-candidate/**"
