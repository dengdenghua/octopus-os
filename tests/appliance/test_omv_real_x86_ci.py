from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPOSITORY = Path(__file__).resolve().parents[2]
_SCRIPT = _REPOSITORY / "deploy" / "omv" / "verify-real-omv-x86-ci.sh"
_NFS_PROBE = _REPOSITORY / "deploy" / "omv" / "real_omv_nfs_probe.py"
_ACCOUNT_PROBE = _REPOSITORY / "deploy" / "omv" / "real_omv_account_probe.py"
_DOCKERFILE = _REPOSITORY / "deploy" / "omv" / "real-omv-x86-ci.Dockerfile"
_WORKFLOW = _REPOSITORY / ".github" / "workflows" / "omv-real-x86.yml"
_FULL_SHA_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
_EXPECTED_WORKFLOW_ACTIONS = {
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
}
_ACCOUNT_SPEC = importlib.util.spec_from_file_location(
    "echo_real_omv_account_probe", _ACCOUNT_PROBE
)
assert _ACCOUNT_SPEC is not None and _ACCOUNT_SPEC.loader is not None
account_probe = importlib.util.module_from_spec(_ACCOUNT_SPEC)
sys.modules[_ACCOUNT_SPEC.name] = account_probe
_ACCOUNT_SPEC.loader.exec_module(account_probe)


def test_real_omv_x86_probe_is_ci_only_and_non_destructive_to_storage() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")

    assert os.access(_SCRIPT, os.X_OK)
    assert "ECHO_REAL_OMV_CI" in source
    assert "GITHUB_ACTIONS" in source
    assert '"$(uname -m)" != x86_64' in source
    assert "/run/systemd/container" in source
    assert "packages.openmediavault.org/public/archive.key" in source
    assert (
        "archive_key_sha256=ffa18c6c27dccd41656b6a71ca2ba042c3028077cb099dbca05fd1fd245906a3"
    ) in source
    assert "signed-by=/usr/share/keyrings/openmediavault-archive-keyring.gpg" in source
    assert "synchrony main" in source
    assert "apt-get install --yes openmediavault" in source
    assert "99-echo-ci-negative.yaml" in source
    assert "active Netplan DNS unexpectedly passed" in source
    assert "the read-only preflight changed an OMV upstream file" in source
    assert "activeNetplanBehaviorVerified" in source
    assert "netplanProbeResult" in source
    assert "upstreamFilesUnchangedByPreflight" in source
    assert "apt-get purge --yes openmediavault-echo-os" in source
    assert "purgePreservedNasData" in source
    assert "reinstallHealthy" in source
    assert "real_omv_nfs_probe.py create" in source
    assert "real_omv_nfs_probe.py verify-purged" in source
    assert "real_omv_nfs_probe.py verify-reinstalled" in source
    assert "real_omv_account_probe.py create" in source
    assert "real_omv_account_probe.py verify-purged" in source
    assert "real_omv_account_probe.py verify-reinstalled" in source
    assert '"schemaVersion": 6' in source
    assert '"readOnlyRemountVerified": True' in source
    assert '"shared-folder.create.simple.v1"' in source
    assert '"shared-folder.privilege.simple.v1"' in source
    assert '"privilegePlanId"' in source
    assert '"purgePreservedPrivilege"' in source
    assert '"reinstallPrivilegePlanNoop"' in source
    assert '"purgePreservedShare"' in source
    assert '"reinstallReadbackVerified"' in source
    assert '"passwordNeverReturned"' in source
    assert '"existingUserCreateRejected"' in source
    assert '"smbAuthenticationVerified"' in source
    assert '"smbReadWriteVerified"' in source
    assert '"oldPasswordRejected"' in source
    assert '"replacementPasswordAuthenticationVerified"' in source
    assert '"purgePreservedSmbAuthentication"' in source
    assert '"reinstallSmbPlanNoop"' in source
    assert "ECHO_REAL_OMV_X86_OK" in source
    for forbidden in (
        r"\bmkfs(?:\.|\s)",
        r"\bwipefs\b",
        r"\bparted\b",
        r"\bmdadm\b.*--create",
        r"\bzpool\s+create\b",
        r"\bbtrfs\s+device\s+add\b",
        r"/var/run/docker\.sock",
        r"\brm\s+-rf\b",
    ):
        assert re.search(forbidden, source) is None


def test_real_omv_nfs_probe_uses_only_a_fixed_disposable_loop_fixture() -> None:
    source = _NFS_PROBE.read_text(encoding="utf-8")

    assert os.access(_NFS_PROBE, os.X_OK)
    assert "ECHO_REAL_OMV_CI" in source
    assert "GITHUB_ACTIONS" in source
    assert "/run/systemd/container" in source
    assert 'TEMP_ROOT = Path("/tmp")  # nosec B108' in source
    assert 'IMAGE_PATH = TEMP_ROOT / "echo-omv-ci-volume.img"' in source
    assert '["/usr/sbin/losetup", "--find", "--show", str(IMAGE_PATH)]' in source
    assert 'rpc("FileSystemMgmt", "create"' in source
    assert '"FileSystemMgmt",\n        "setMountPoint"' in source
    assert '"/v1/sharing/folders/plan"' in source
    assert '"/v1/sharing/folders/apply"' in source
    assert '"/v1/sharing/privileges/plan"' in source
    assert '"/v1/sharing/privileges/apply"' in source
    assert '"ShareMgmt",\n        "set"' not in source
    assert '"/v1/sharing/nfs/plan"' in source
    assert '"/v1/sharing/nfs/apply"' in source
    assert '"sync,subtree_check,root_squash"' in source
    assert "users_group_identity" in source
    assert "0o2770" in source
    assert 'REMOTE_PATH = f"/{SHARE_NAME}"' in source
    assert '"-t",\n            "nfs4"' in source
    assert "read-only NFS remount unexpectedly allowed a write" in source
    assert '"purgePreservedShare": True' in source
    assert '"purgePreservedPrivilege": True' in source
    assert '"reinstallReadbackVerified": True' in source
    assert '"reinstallPrivilegePlanNoop": True' in source
    for forbidden in (
        r"\bmkfs(?:\.|\s)",
        r"\bwipefs\b",
        r"\bparted\b",
        r"\bmdadm\b.*--create",
        r"\bzpool\s+create\b",
        r"\bbtrfs\s+device\s+add\b",
        r"/var/run/docker\.sock",
        r"\brm\s+-rf\b",
    ):
        assert re.search(forbidden, source) is None


def test_real_omv_account_probe_is_fixed_create_only_and_password_safe() -> None:
    source = _ACCOUNT_PROBE.read_text(encoding="utf-8")

    assert os.access(_ACCOUNT_PROBE, os.X_OK)
    assert "ECHO_REAL_OMV_CI" in source
    assert "GITHUB_ACTIONS" in source
    assert "/run/systemd/container" in source
    assert 'GROUP_NAME = "familyci"' in source
    assert 'USER_NAME = "motherci"' in source
    assert '"/v1/accounts/groups/plan"' in source
    assert '"/v1/accounts/groups/apply"' in source
    assert '"/v1/accounts/users/plan"' in source
    assert '"/v1/accounts/users/apply"' in source
    assert '"/v1/accounts/users/password/plan"' in source
    assert '"/v1/accounts/users/password/apply"' in source
    assert '"/usr/sbin/nologin"' in source
    assert '"passwordNeverReturned": True' in source
    assert "SECRET_CANARIES" in source
    assert "Never include a secret-bearing remote body" in source
    assert '"/usr/bin/smbclient"' in source
    assert 'environment["PASSWD_FD"] = str(read_descriptor)' in source
    assert "pass_fds=(read_descriptor,)" in source
    assert 'secret[:] = b"\\x00" * len(secret)' in source
    assert '"/v1/sharing/smb/plan"' in source
    assert '"/v1/sharing/smb/apply"' in source
    assert '"smbAuthenticationVerified": True' in source
    assert '"smbReadWriteVerified": True' in source
    assert '"oldPasswordRejected": True' in source
    assert '"replacementPasswordAuthenticationVerified": True' in source
    assert '"existingGroupCreateRejected": True' in source
    assert '"existingUserCreateRejected": True' in source
    assert "--password" not in source
    assert "PASSWD=" not in source
    for forbidden in (
        r"\buseradd\b",
        r"\busermod\b",
        r"\bgroupadd\b",
        r"\bsmbpasswd\b",
        r"\brm\s+-rf\b",
        r"/var/run/docker\.sock",
    ):
        assert re.search(forbidden, source) is None


def test_real_omv_account_probe_passes_smb_password_only_through_one_shot_fd(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        pass_fds = kwargs["pass_fds"]
        assert isinstance(environment, dict)
        assert isinstance(pass_fds, tuple) and len(pass_fds) == 1
        descriptor = int(environment["PASSWD_FD"])
        assert descriptor == pass_fds[0]
        captured["argv"] = argv
        captured["environment"] = environment
        captured["secret"] = os.read(descriptor, 4096)
        return subprocess.CompletedProcess(argv, 0, stdout="listed", stderr="")

    monkeypatch.setattr(account_probe.subprocess, "run", fake_run)

    assert account_probe.run_smbclient("dir") == "listed"
    canary = account_probe.USER_REPLACEMENT_CI_CANARY
    assert captured["secret"] == canary.encode()
    assert all(canary not in argument for argument in captured["argv"])
    assert all(canary not in value for value in captured["environment"].values())


def test_real_omv_account_probe_requires_old_smb_password_rejection(monkeypatch) -> None:
    def rejected(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        descriptor = int(kwargs["env"]["PASSWD_FD"])  # type: ignore[index]
        assert os.read(descriptor, 4096) == account_probe.USER_CI_CANARY.encode()
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="session setup failed: NT_STATUS_LOGON_FAILURE",
        )

    monkeypatch.setattr(account_probe.subprocess, "run", rejected)
    assert (
        account_probe.run_smbclient(
            "ls",
            password=account_probe.USER_CI_CANARY,
            expect_success=False,
        )
        == ""
    )

    def unexpectedly_accepted(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        descriptor = int(kwargs["env"]["PASSWD_FD"])  # type: ignore[index]
        os.read(descriptor, 4096)
        return subprocess.CompletedProcess(argv, 0, stdout="listed", stderr="")

    monkeypatch.setattr(account_probe.subprocess, "run", unexpectedly_accepted)
    with pytest.raises(account_probe.ProbeError, match="unexpectedly succeeded"):
        account_probe.run_smbclient(
            "ls",
            password=account_probe.USER_CI_CANARY,
            expect_success=False,
        )


def test_real_omv_x86_probe_refuses_local_execution_before_mutation(tmp_path: Path) -> None:
    package = tmp_path / "openmediavault-echo-os_0.2.0-1_all.deb"
    package.write_bytes(b"not a package")

    result = subprocess.run(
        [str(_SCRIPT), str(package), "/tmp/evidence.json"],
        check=False,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "restricted to GitHub Actions" in result.stderr


def test_real_omv_nfs_probe_refuses_local_execution_before_mutation() -> None:
    result = subprocess.run(
        [str(_NFS_PROBE), "create"],
        check=False,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "restricted to GitHub Actions" in result.stderr


def test_real_omv_account_probe_refuses_local_execution_before_mutation() -> None:
    result = subprocess.run(
        [str(_ACCOUNT_PROBE), "create"],
        check=False,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "restricted to GitHub Actions" in result.stderr


def test_real_omv_x86_systemd_image_is_pinned_and_minimal() -> None:
    source = _DOCKERFILE.read_text(encoding="utf-8")

    assert source.startswith("FROM python:3.13-slim-trixie@sha256:")
    assert "systemd-sysv" in source
    assert "smbclient" in source
    assert "rm -f /usr/sbin/policy-rc.d" in source
    assert 'CMD ["/sbin/init"]' in source
    assert "COPY " not in source
    assert "ADD " not in source
    assert "EXPOSE " not in source


def test_ci_runs_real_omv_in_disposable_x86_host_and_uploads_evidence() -> None:
    source = _WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)

    assert isinstance(workflow, dict)
    assert "omv-real-x86" in workflow["jobs"]
    job = workflow["jobs"]["omv-real-x86"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 45
    assert 'test "$(uname -m)" = x86_64' in source
    assert "--privileged" in source
    assert '--mount "type=bind,source=$PWD,target=/source,readonly"' in source
    assert "--publish" not in source
    assert "-p 80" not in source
    assert "verify-real-omv-x86-ci.sh" in source
    assert "verify-real-omv-x86-evidence.py" in source
    assert "echo-real-omv-x86-evidence.json" in source
    assert "echo-real-omv-x86-evidence.verification.json" in source
    assert "docker rm --force echo-real-omv-x86-ci" in source
    assert "if-no-files-found: error" in source
    assert "continue-on-error" not in source
    assert "workflow_dispatch" in source
    assert "deploy/omv/**" in source
    assert "tests/appliance/test_omv_*.py" in source


def test_real_omv_workflow_runs_for_the_actual_delivery_branch_and_relevant_paths() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    trigger = workflow.get("on", workflow.get(True))

    assert isinstance(trigger, dict)
    assert trigger["workflow_dispatch"] is None
    for event in ("pull_request", "push"):
        assert set(trigger[event]["branches"]) == {"os-main", "main"}
        assert set(trigger[event]["paths"]) == {
            "appliance/approval.py",
            "appliance/omv_*.py",
            "deploy/omv/**",
            "tests/appliance/test_omv_*.py",
            ".github/workflows/omv-real-x86.yml",
        }


def test_real_omv_workflow_pins_actions_and_attests_only_verified_branch_runs() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["source-contract"]
    job = workflow["jobs"]["omv-real-x86"]
    assert job["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
        "artifact-metadata": "write",
    }
    assert job["needs"] == "source-contract"
    condition = str(job["if"])
    assert "github.event_name != 'pull_request'" in condition
    assert "refs/heads/os-main" in condition
    assert "refs/heads/main" in condition
    steps = job["steps"]
    actions = [str(step["uses"]) for step in steps if "uses" in step]

    assert actions
    assert all(_FULL_SHA_ACTION.fullmatch(action) is not None for action in actions)
    assert set(actions) == _EXPECTED_WORKFLOW_ACTIONS

    attestations = [
        step for step in steps if str(step.get("uses", "")).startswith("actions/attest@")
    ]
    assert len(attestations) == 3
    assert all("success()" in str(step["if"]) for step in attestations)
    assert all("github.event_name != 'pull_request'" in str(step["if"]) for step in attestations)
    assert all("refs/heads/os-main" in str(step["if"]) for step in attestations)


def test_real_omv_artifact_is_self_contained_and_offline_checkable() -> None:
    source = _WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    steps = workflow["jobs"]["omv-real-x86"]["steps"]
    upload = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    paths = str(upload["with"]["path"])

    for required in (
        "dist/openmediavault-echo-os_*_all.deb",
        "dist/openmediavault-echo-os_*_all.deb.sha256",
        "dist/openmediavault-echo-os_*_all.spdx.json",
        "dist/echo-real-omv-x86-evidence.json",
        "dist/echo-real-omv-x86-evidence.verification.json",
        "dist/echo-real-omv-x86-artifact-set.sha256",
        "dist/verify-real-omv-x86-evidence.py",
    ):
        assert required in paths

    assert "cd dist" in source
    assert "sha256sum --check openmediavault-echo-os_*_all.deb.sha256" in source
    assert "python -m json.tool openmediavault-echo-os_*_all.spdx.json" in source
    assert "> echo-real-omv-x86-artifact-set.sha256" in source
    assert "cp deploy/omv/verify-real-omv-x86-evidence.py" in source
