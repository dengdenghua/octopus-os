from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

_REPOSITORY = Path(__file__).resolve().parents[2]
_LIFECYCLE_SCRIPT = _REPOSITORY / "deploy/omv/verify-plugin-package-lifecycle.sh"
_CI_WORKFLOW = _REPOSITORY / ".github/workflows/ci.yml"
_PINNED_DEBIAN_13_IMAGE = (
    "python:3.13-slim-trixie@"
    "sha256:7e3a6aca9d74f93cca21a91d86a8dad8c34749afd5b4a98ee481c9c47b9f5ed4"
)


def test_omv_plugin_lifecycle_verifier_is_shell_valid_and_covers_delivery_gates() -> None:
    syntax = subprocess.run(
        ["/bin/sh", "-n", str(_LIFECYCLE_SCRIPT)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    script = _LIFECYCLE_SCRIPT.read_text()
    for required in (
        'distribution_version" 13',
        "dpkg --force-depends --install",
        "install ok half-configured|0.2.0-4",
        "failed upgrade state",
        "manual installer conflict unexpectedly succeeded",
        "OMV 9 upgrade unexpectedly passed",
        "dpkg --remove openmediavault-echo-os",
        "dpkg --purge openmediavault-echo-os",
        'groupPreserved":true',
        'nasDataPreserved":true',
    ):
        assert required in script
    for forbidden in ("curl ", "wget ", "apt-get ", "docker.sock", "ECHO_NAS_ROOT"):
        assert forbidden not in script


def test_ci_runs_lifecycle_verifier_offline_in_pinned_debian_13_container() -> None:
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text())
    steps = workflow["jobs"]["omv-host-bundle"]["steps"]
    lifecycle_step = next(
        step
        for step in steps
        if step.get("name") == "Verify native plugin Debian 13 package lifecycle"
    )
    command = lifecycle_step["run"]

    assert _PINNED_DEBIAN_13_IMAGE in command
    assert "docker run --rm --network none" in command
    assert '--mount "type=bind,source=$PWD,target=/source,readonly"' in command
    assert "/source/deploy/omv/verify-plugin-package-lifecycle.sh" in command
    assert 'test "${#plugin_package[@]}" -eq 1' in command
