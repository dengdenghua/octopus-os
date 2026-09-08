from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy" / "appliance" / "verify-running-appliance.py"
)
_SPEC = importlib.util.spec_from_file_location("echo_zfs_runtime_verifier", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verifier)


def _completed(command: list[str], returncode: int = 0, stdout: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout, "")


def _paths(tmp_path: Path) -> dict[str, Path]:
    module = tmp_path / "sys" / "module" / "zfs"
    module.mkdir(parents=True)
    return {
        "module_path": module,
        "uname_path": tmp_path / "uname",
        "modinfo_path": tmp_path / "modinfo",
        "systemctl_path": tmp_path / "systemctl",
        "zpool_path": tmp_path / "zpool",
    }


def test_zfs_runtime_verifier_binds_userspace_to_the_running_kernel(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    observed: list[list[str]] = []

    def run(command: list[str]):
        observed.append(command)
        if command[-1] == "-r":
            return _completed(command, stdout="6.12.94+deb13-amd64\n")
        if "is-active" in command:
            return _completed(command, stdout="active\n")
        return _completed(command)

    result = verifier._assert_zfs_kernel_runtime(command_runner=run, **paths)

    assert result == {
        "kernelRelease": "6.12.94+deb13-amd64",
        "moduleInstalled": True,
        "moduleLoaded": True,
        "loadServiceActive": True,
        "kernelInterfaceReady": True,
    }
    assert observed == [
        [str(paths["uname_path"]), "-r"],
        [str(paths["modinfo_path"]), "-k", "6.12.94+deb13-amd64", "zfs"],
        [str(paths["systemctl_path"]), "is-active", "zfs-load-module.service"],
        [str(paths["zpool_path"]), "list", "-H", "-o", "name"],
    ]


def test_zfs_runtime_verifier_rejects_missing_current_kernel_module(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    def run(command: list[str]):
        if command[-1] == "-r":
            return _completed(command, stdout="6.12.94+deb13-amd64\n")
        return _completed(command, returncode=1)

    with pytest.raises(verifier.VerificationError, match="unavailable for running kernel"):
        verifier._assert_zfs_kernel_runtime(command_runner=run, **paths)


def test_zfs_runtime_verifier_rejects_installed_but_unloaded_module(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths["module_path"].rmdir()

    def run(command: list[str]):
        output = "6.12.94+deb13-amd64\n" if command[-1] == "-r" else ""
        return _completed(command, stdout=output)

    with pytest.raises(verifier.VerificationError, match="installed but not loaded"):
        verifier._assert_zfs_kernel_runtime(command_runner=run, **paths)


@pytest.mark.parametrize(
    ("failed_tool", "message"),
    [
        ("systemctl", "load-module.service is not active"),
        ("zpool", "cannot communicate with the running kernel"),
    ],
)
def test_zfs_runtime_verifier_rejects_broken_service_or_kernel_api(
    tmp_path: Path, failed_tool: str, message: str
) -> None:
    paths = _paths(tmp_path)

    def run(command: list[str]):
        if command[-1] == "-r":
            return _completed(command, stdout="6.12.94+deb13-amd64\n")
        if command[0] == str(paths[failed_tool + "_path"]):
            return _completed(command, returncode=1, stdout="failed\n")
        if "is-active" in command:
            return _completed(command, stdout="active\n")
        return _completed(command)

    with pytest.raises(verifier.VerificationError, match=message):
        verifier._assert_zfs_kernel_runtime(command_runner=run, **paths)


def test_zfs_runtime_gate_is_explicit_for_generic_container_ci() -> None:
    parser = verifier._parser()
    assert parser.parse_args([]).require_zfs_runtime is False
    assert parser.parse_args(["--require-zfs-runtime"]).require_zfs_runtime is True
