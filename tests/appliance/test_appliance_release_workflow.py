from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/appliance-release.yml"
FULL_SHA_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
EXPECTED_ACTIONS = {
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
    "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
    "pnpm/action-setup@ff378ebe6b225b0680b81c1ad4498ae0d1d3a5e3",
    "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
    "docker/setup-qemu-action@96fe6ef7f33517b61c61be40b68a1882f3264fb8",
    "docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e",
    "docker/login-action@dbcb813823bdd20940b903addbd779551569679f",
    "docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302",
    "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
    "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
}


def _workflow() -> dict[str, object]:
    value = yaml.safe_load(WORKFLOW_PATH.read_text())
    assert isinstance(value, dict)
    return value


def _steps() -> list[dict[str, object]]:
    workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    publish = jobs["publish"]
    assert isinstance(publish, dict)
    steps = publish["steps"]
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def test_release_workflow_is_tag_only_and_has_publish_permissions() -> None:
    workflow = _workflow()
    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {"push": {"tags": ["echo-appliance-v*"]}}
    assert workflow["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
        "artifact-metadata": "write",
    }
    concurrency = workflow["concurrency"]
    assert isinstance(concurrency, dict)
    assert concurrency["cancel-in-progress"] is False


def test_release_workflow_pins_every_third_party_action_to_a_full_sha() -> None:
    uses = [str(step["uses"]) for step in _steps() if "uses" in step]

    assert uses
    assert all(FULL_SHA_ACTION.fullmatch(action) is not None for action in uses)
    assert set(uses) == EXPECTED_ACTIONS


def test_release_workflow_builds_exact_two_platform_index_with_attestations() -> None:
    steps = _steps()
    setup_uv = next(step for step in steps if step.get("name") == "Setup uv")
    assert setup_uv["with"] == {"version": "0.11.25"}
    build = next(step for step in steps if step.get("id") == "build")
    preflight_position = next(
        index
        for index, step in enumerate(steps)
        if step.get("name")
        == "Reject malformed release and verify the unified Echo source"
    )
    hub_storage_position = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Verify Hub OCI storage attestations"
    )
    build_position = steps.index(build)
    assert preflight_position < build_position
    assert preflight_position < hub_storage_position < build_position
    assert "preflight_release_source" in str(steps[preflight_position]["run"])
    assert str(steps[hub_storage_position]["run"]) == ("python deploy/appliance/hub_oci_storage.py")
    options = build["with"]
    assert isinstance(options, dict)
    assert options["platforms"] == "linux/amd64,linux/arm64"
    assert options["push"] is True
    assert options["pull"] is True
    assert options["provenance"] == "mode=max"
    assert options["sbom"] is True
    metadata = next(step for step in steps if step.get("id") == "metadata")
    metadata_options = metadata["with"]
    assert isinstance(metadata_options, dict)
    assert "latest" not in str(metadata_options["tags"]).lower()

    attestations = [
        step for step in steps if str(step.get("uses", "")).startswith("actions/attest@")
    ]
    assert len(attestations) == 2
    image_attestation = attestations[0]["with"]
    assert isinstance(image_attestation, dict)
    assert image_attestation["push-to-registry"] is True
    assert image_attestation["subject-digest"] == "${{ steps.build.outputs.digest }}"


def test_release_workflow_extracts_and_binds_both_platform_sboms() -> None:
    source = WORKFLOW_PATH.read_text()

    assert '(index .SBOM "linux/amd64").SPDX' in source
    assert '(index .SBOM "linux/arm64").SPDX' in source
    assert "--sbom-amd64 dist/echo-appliance-linux-amd64.spdx.json" in source
    assert "--sbom-arm64 dist/echo-appliance-linux-arm64.spdx.json" in source
    assert "--build-lock dist/build-requirements.lock" in source
    assert "--runtime-lock dist/runtime-requirements.lock" in source
    assert "--dependency-lock-metadata dist/python-dependency-lock.json" in source
    assert "python deploy/appliance/operations_bundle.py build" in source
    assert '--image-reference "$ECHO_IMAGE@$ECHO_INDEX_DIGEST"' in source
    assert "--operations-bundle dist/echo-appliance-operations.tar.gz" in source
    assert "--operations-checksum dist/echo-appliance-operations.tar.gz.sha256" in source
    assert "--operations-sbom dist/echo-appliance-operations.spdx.json" in source
    assert "--operations-verifier dist/operations_bundle.py" in source
    assert "python deploy/appliance/image_release.py" in source
    assert '--source-sha "$GITHUB_SHA"' in source

    upload = next(
        step
        for step in _steps()
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    paths = str(upload["with"]["path"])
    assert "echo-appliance-index.json" in paths
    assert "echo-appliance-linux-amd64.spdx.json" in paths
    assert "echo-appliance-linux-arm64.spdx.json" in paths
    assert "echo-appliance-release.json.sha256" in paths
    assert "echo-release.env" in paths
    assert "build-requirements.lock" in paths
    assert "runtime-requirements.lock" in paths
    assert "python-dependency-lock.json" in paths
    assert "echo-appliance-operations.tar.gz" in paths
    assert "echo-appliance-operations.tar.gz.sha256" in paths
    assert "echo-appliance-operations.spdx.json" in paths
    assert "operations_bundle.py" in paths
