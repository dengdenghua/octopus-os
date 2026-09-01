from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[2]
_SCRIPT = _REPOSITORY / "deploy" / "omv" / "platform_preflight.py"
_SPEC = importlib.util.spec_from_file_location("echo_omv_platform_preflight", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
preflight = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = preflight
_SPEC.loader.exec_module(preflight)


def _fixture_paths(tmp_path: Path, *, importer_field: str = "dnsnameservers"):
    os_release = tmp_path / "usr" / "lib" / "os-release"
    dpkg_query = tmp_path / "usr" / "bin" / "dpkg-query"
    netplan_directory = tmp_path / "etc" / "netplan"
    importer = (
        tmp_path / "usr" / "share" / "openmediavault" / "confdb" / "populate.d" / "40netplan.sh"
    )
    model = (
        tmp_path
        / "usr"
        / "share"
        / "openmediavault"
        / "datamodels"
        / "conf.system.network.interface.json"
    )
    for path in (os_release, dpkg_query, importer, model):
        path.parent.mkdir(parents=True, exist_ok=True)
    netplan_directory.mkdir(parents=True)
    os_release.write_text('ID=debian\nVERSION_ID="13"\n')
    dpkg_query.write_text("fixture\n")
    importer.write_text(f'obj.set("{importer_field}", dnsnameservers)\n')
    model.write_text('{"properties":{"dnsnameservers":{"type":"array"}}}\n')
    return preflight.PlatformPaths(
        os_release=os_release,
        dpkg_query=dpkg_query,
        netplan_directory=netplan_directory,
        netplan_importer=importer,
        network_interface_model=model,
    )


def test_fixed_omv_importer_and_smb_hostname_are_ready(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    report = preflight.probe_nas_readiness(
        paths=paths,
        hostname="echo-nas-01",
        trusted_uid=os.getuid(),
    )

    assert report["ready"] is True
    assert report["smbHostnameCompatible"] is True
    assert report["netplan"]["compatible"] is True
    assert report["issues"] == []
    assert report["warnings"] == []


def test_active_netplan_dns_blocks_known_omv_field_mismatch(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path, importer_field="dnsservers")
    (paths.netplan_directory / "50-cloud-init.yaml").write_text(
        "network:\n  ethernets:\n    eth0:\n      nameservers:\n        addresses: [1.1.1.1]\n"
    )

    report = preflight.probe_nas_readiness(
        paths=paths,
        hostname="echo-nas",
        trusted_uid=os.getuid(),
    )

    assert report["ready"] is False
    assert report["netplan"]["knownFieldMismatch"] is True
    assert report["netplan"]["compatible"] is False
    assert report["netplan"]["activeNameserverFiles"] == ["50-cloud-init.yaml"]
    assert [issue["code"] for issue in report["issues"]] == ["omv_netplan_dns_field_mismatch"]


def test_known_omv_field_mismatch_is_only_a_warning_without_netplan_dns(
    tmp_path: Path,
) -> None:
    paths = _fixture_paths(tmp_path, importer_field="dnsservers")

    report = preflight.probe_nas_readiness(
        paths=paths,
        hostname="echo-nas",
        trusted_uid=os.getuid(),
    )

    assert report["ready"] is True
    assert report["netplan"]["knownFieldMismatch"] is True
    assert [warning["code"] for warning in report["warnings"]] == [
        "omv_netplan_dns_field_mismatch_latent"
    ]


def test_hostname_over_smb_limit_is_rejected(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    report = preflight.probe_nas_readiness(
        paths=paths,
        hostname="echo-storage-node-01",
        trusted_uid=os.getuid(),
    )

    assert report["ready"] is False
    assert report["smbHostnameCompatible"] is False
    assert [issue["code"] for issue in report["issues"]] == ["smb_hostname_too_long"]


def test_untrusted_netplan_file_fails_closed(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    netplan = paths.netplan_directory / "50-cloud-init.yaml"
    netplan.write_text("network:\n  version: 2\n")
    netplan.chmod(0o666)

    with pytest.raises(preflight.PlatformPreflightError, match="unsafe ownership"):
        preflight.probe_nas_readiness(
            paths=paths,
            hostname="echo-nas",
            trusted_uid=os.getuid(),
        )


def test_full_platform_probe_enforces_debian_13_and_omv_8(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "8.5.6-1\n", "")

    report = preflight.probe_platform(
        paths=paths,
        hostname="echo-nas",
        trusted_uid=os.getuid(),
        command_runner=runner,
    )

    assert report["supported"] is True
    assert report["supportMatrix"] == "debian-13+omv-8"
    assert report["omvVersion"] == "8.5.6-1"
    assert commands == [[str(paths.dpkg_query), "-W", "-f=${Version}", "openmediavault"]]


@pytest.mark.parametrize(
    ("release", "version", "match"),
    [
        ('ID=ubuntu\nVERSION_ID="24.04"\n', "8.5.6-1", "requires Debian 13"),
        ('ID=debian\nVERSION_ID="13"\n', "9.0.0-1", "requires OMV 8"),
    ],
)
def test_full_platform_probe_rejects_off_matrix_host(
    tmp_path: Path,
    release: str,
    version: str,
    match: str,
) -> None:
    paths = _fixture_paths(tmp_path)
    paths.os_release.write_text(release)

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, version, "")

    with pytest.raises(preflight.PlatformPreflightError, match=match):
        preflight.probe_platform(
            paths=paths,
            hostname="echo-nas",
            trusted_uid=os.getuid(),
            command_runner=runner,
        )
