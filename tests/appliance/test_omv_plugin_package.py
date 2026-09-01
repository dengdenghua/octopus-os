from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPOSITORY = Path(__file__).resolve().parents[2]
_SCRIPT = _REPOSITORY / "deploy" / "omv" / "plugin_package.py"
_SPEC = importlib.util.spec_from_file_location("echo_omv_plugin_package", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
plugin = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = plugin
_SPEC.loader.exec_module(plugin)


def test_native_plugin_build_is_deterministic_and_self_verifying(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    first = plugin.build(_REPOSITORY, tmp_path / "first")
    second = plugin.build(_REPOSITORY, tmp_path / "second")
    first_path = Path(first["path"])
    second_path = Path(second["path"])

    assert first["package"] == "openmediavault-echo-os"
    assert first["version"] == "0.2.0-1"
    assert first["architecture"] == "all"
    assert first["pluginArchitectures"] == ["amd64", "arm64"]
    assert first["supportMatrix"] == "debian-13+omv-8"
    assert first["sha256"] == second["sha256"]
    assert first_path.read_bytes() == second_path.read_bytes()
    assert Path(first["checksumPath"]).read_text() == (f"{first['sha256']}  {first_path.name}\n")
    assert plugin.verify(first_path, source_root=_REPOSITORY) == {
        key: first[key]
        for key in (
            "package",
            "version",
            "architecture",
            "pluginArchitectures",
            "supportMatrix",
            "sha256",
            "size",
            "dataFileCount",
            "dataDirectoryCount",
            "supportMatrixInstallGate",
            "manualInstallerConflictGuard",
            "preservesNasDataOnRemoval",
        )
    }

    sbom = json.loads(Path(first["sbomPath"]).read_text())
    assert sbom["spdxVersion"] == "SPDX-2.3"
    assert sbom["packages"][0]["name"] == "openmediavault-echo-os"
    assert sbom["packages"][0]["versionInfo"] == "0.2.0-1"
    assert len(sbom["files"]) == first["dataFileCount"]


def test_native_plugin_contains_exact_omv_identity_ui_and_service_boundary(
    tmp_path: Path,
) -> None:
    report = plugin.build(_REPOSITORY, tmp_path)
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    control_files = plugin._read_tar_gz(members["control.tar.gz"], context="control archive")
    data_files = plugin._read_tar_gz(
        members["data.tar.gz"], context="data archive", include_directories=True
    )
    fields = plugin._parse_control(control_files["control"][0])

    assert fields["Package"] == "openmediavault-echo-os"
    assert fields["Architecture"] == "all"
    assert fields["XB-Plugin-Section"] == "utilities"
    assert fields["XB-Plugin-Architecture"] == "amd64, arm64"
    assert "openmediavault (>= 8.0)" in fields["Depends"]
    assert "openmediavault (<< 9.0)" in fields["Depends"]
    assert control_files["triggers"][0] == b"activate restart-engined\n"

    assert {
        "usr/lib/echo-os/omv-bridge/platform_preflight.py",
        "usr/share/openmediavault/workbench/navigation.d/services.echo-os.yaml",
        "usr/share/openmediavault/workbench/route.d/services.echo-os.yaml",
        "usr/share/openmediavault/workbench/component.d/omv-services-echo-os-form-page.yaml",
    }.issubset(data_files)
    navigation = yaml.safe_load(
        data_files["usr/share/openmediavault/workbench/navigation.d/services.echo-os.yaml"][0]
    )
    route = yaml.safe_load(
        data_files["usr/share/openmediavault/workbench/route.d/services.echo-os.yaml"][0]
    )
    component_document = yaml.safe_load(
        data_files[
            "usr/share/openmediavault/workbench/component.d/omv-services-echo-os-form-page.yaml"
        ][0]
    )
    assert navigation["type"] == "navigation-item"
    assert navigation["data"]["path"] == "services.echo-os"
    assert route["type"] == "route"
    assert route["data"]["component"] == "omv-services-echo-os-form-page"
    assert component_document["type"] == "component"
    assert component_document["data"]["type"] == "formPage"
    component = data_files[
        "usr/share/openmediavault/workbench/component.d/omv-services-echo-os-form-page.yaml"
    ][0]
    assert b"type: formPage" in component
    assert b"/run/echo-omv/omv.sock" in component
    assert b"filesystem user/group hard quotas" in component

    unit = data_files["usr/lib/systemd/system/echo-omv-bridge.service"][0]
    platform_preflight = data_files["usr/lib/echo-os/omv-bridge/platform_preflight.py"][0]
    assert b"MAX_HOSTNAME_LENGTH = 15" in platform_preflight
    assert b"omv_netplan_dns_field_mismatch" in platform_preflight
    assert b"ConditionFileIsExecutable=/usr/sbin/omv-rpc" in unit
    assert b"ConditionFileIsExecutable=/usr/bin/lsblk" in unit
    assert b"ConditionPathIsExecutable" not in unit
    assert b"PrivateNetwork=true" in unit
    assert b"PrivateDevices=true" in unit
    assert b"CapabilityBoundingSet=" in unit
    assert b"Group=echo-omv" in unit
    assert b"/opt/echo-os" not in unit


def test_native_plugin_maintainer_scripts_refuse_manual_install_and_preserve_data(
    tmp_path: Path,
) -> None:
    report = plugin.build(_REPOSITORY, tmp_path)
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    control_files = plugin._read_tar_gz(members["control.tar.gz"], context="control archive")

    preinst = control_files["preinst"][0]
    postinst = control_files["postinst"][0]
    postrm = control_files["postrm"][0]
    assert b"/usr/lib/os-release" in preinst
    assert b"/usr/bin/dpkg-query" in preinst
    assert b"supports only Debian 13 with OpenMediaVault 8" in preinst
    assert b"/var/lib/echo-os/omv-host/install-state.json" in preinst
    assert b"Refusing to overwrite" in preinst
    assert b"addgroup --system echo-omv" in postinst
    assert b"platform_preflight.py --quiet" in postinst
    assert b"dpkg-trigger update-workbench" in postinst
    assert b"deb-systemd-invoke restart" in postinst
    assert b"/run/echo-omv/omv.sock" in postinst
    assert b"bridge failed its post-install health check" in postinst
    assert b"Keep the echo-omv group" in postrm
    for script_name in ("preinst", "postinst", "prerm", "postrm"):
        script = control_files[script_name][0]
        assert b"rm -rf" not in script
        assert b"groupdel" not in script
        assert b"ECHO_NAS_ROOT" not in script


def test_native_plugin_rejects_tampered_payload_even_when_ar_is_well_formed(
    tmp_path: Path,
) -> None:
    report = plugin.build(_REPOSITORY, tmp_path / "original")
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    data_files = plugin._read_tar_gz(
        members["data.tar.gz"], context="data archive", include_directories=True
    )
    bridge_path = "usr/lib/echo-os/omv-bridge/appliance/omv_bridge.py"
    bridge, mode = data_files[bridge_path]
    data_files[bridge_path] = (bridge + b"\n# tampered\n", mode)
    tampered = tmp_path / "tampered.deb"
    tampered.write_bytes(
        plugin._deb(
            members["control.tar.gz"],
            plugin._tar_gz(data_files, include_directories=True),
        )
    )

    with pytest.raises(plugin.PluginPackageError, match="md5 inventory"):
        plugin.verify(tampered, source_root=_REPOSITORY)


def test_native_plugin_rejects_data_archive_without_parent_directories(
    tmp_path: Path,
) -> None:
    report = plugin.build(_REPOSITORY, tmp_path / "original")
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    data_files = plugin._read_tar_gz(
        members["data.tar.gz"], context="data archive", include_directories=True
    )
    malformed = tmp_path / "missing-directories.deb"
    malformed.write_bytes(plugin._deb(members["control.tar.gz"], plugin._tar_gz(data_files)))

    with pytest.raises(plugin.PluginPackageError, match="directory inventory"):
        plugin.verify(malformed)


def test_native_plugin_rejects_relaxed_omv_major_dependency(tmp_path: Path) -> None:
    report = plugin.build(_REPOSITORY, tmp_path / "original")
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    control_files = plugin._read_tar_gz(members["control.tar.gz"], context="control archive")
    control, mode = control_files["control"]
    control_files["control"] = (
        control.replace(b"openmediavault (<< 9.0)", b"openmediavault (<< 10.0)"),
        mode,
    )
    tampered = tmp_path / "relaxed.deb"
    tampered.write_bytes(plugin._deb(plugin._tar_gz(control_files), members["data.tar.gz"]))

    with pytest.raises(plugin.PluginPackageError, match="dependency boundary"):
        plugin.verify(tampered)


@pytest.mark.parametrize("version", ["0.2.0", "v0.2.0-1", "0.2.0-1 invalid", ""])
def test_native_plugin_rejects_invalid_debian_version(
    tmp_path: Path,
    version: str,
) -> None:
    with pytest.raises(plugin.PluginPackageError, match="invalid Debian plugin version"):
        plugin.build(_REPOSITORY, tmp_path, version=version)


def test_native_plugin_is_accepted_by_system_debian_archive_tools(
    tmp_path: Path,
) -> None:
    dpkg_deb = shutil.which("dpkg-deb")
    report = plugin.build(_REPOSITORY, tmp_path)
    package_path = Path(report["path"])
    if dpkg_deb is not None:
        info = subprocess.run(
            [dpkg_deb, "--info", str(package_path)],
            check=False,
            text=True,
            capture_output=True,
        )
        contents = subprocess.run(
            [dpkg_deb, "--contents", str(package_path)],
            check=False,
            text=True,
            capture_output=True,
        )

        assert info.returncode == 0, info.stderr
        assert "Package: openmediavault-echo-os" in info.stdout
        assert contents.returncode == 0, contents.stderr
        assert "usr/lib/systemd/system/echo-omv-bridge.service" in contents.stdout
        return

    # macOS does not ship dpkg-deb, but its system libarchive can independently
    # parse both the outer ar container and the two Debian tar members. Linux CI
    # still takes the stronger dpkg-deb branch above.
    archive_tool = shutil.which("bsdtar") or shutil.which("tar")
    assert archive_tool is not None, "a system libarchive tar is required"
    extracted = tmp_path / "system-archive"
    extracted.mkdir()
    outer = subprocess.run(
        [archive_tool, "-xf", str(package_path), "-C", str(extracted)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert outer.returncode == 0, outer.stderr
    assert (extracted / "debian-binary").read_text() == "2.0\n"

    control = subprocess.run(
        [archive_tool, "-xOf", str(extracted / "control.tar.gz"), "control"],
        check=False,
        text=True,
        capture_output=True,
    )
    contents = subprocess.run(
        [archive_tool, "-tf", str(extracted / "data.tar.gz")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert control.returncode == 0, control.stderr
    assert "Package: openmediavault-echo-os" in control.stdout
    assert contents.returncode == 0, contents.stderr
    assert "usr/lib/systemd/system/echo-omv-bridge.service" in contents.stdout


def test_native_plugin_maintainer_scripts_are_shell_valid_and_lifecycle_is_bounded(
    tmp_path: Path,
) -> None:
    report = plugin.build(_REPOSITORY, tmp_path / "package")
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    control_files = plugin._read_tar_gz(members["control.tar.gz"], context="control archive")
    health_check = tmp_path / "python3-health-check"
    health_check.write_text("#!/bin/sh\ncat >/dev/null\nexit 0\n")
    health_check.chmod(0o755)
    scripts = {}
    for name in ("preinst", "postinst", "prerm", "postrm"):
        path = tmp_path / name
        payload = control_files[name][0]
        if name == "postinst":
            payload = payload.replace(b"/usr/bin/python3", str(health_check).encode())
        path.write_bytes(payload)
        path.chmod(0o755)
        scripts[name] = path
        syntax = subprocess.run(
            ["/bin/sh", "-n", str(path)],
            check=False,
            text=True,
            capture_output=True,
        )
        assert syntax.returncode == 0, syntax.stderr

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "commands.log"
    for command in (
        "getent",
        "addgroup",
        "dpkg-trigger",
        "systemctl",
        "deb-systemd-helper",
        "deb-systemd-invoke",
    ):
        executable = fake_bin / command
        executable.write_text(
            f"#!/bin/sh\nprintf '%s %s\\n' '{command}' \"$*\" >> '{log_path}'\nexit 0\n"
        )
        executable.chmod(0o755)
    environment = {"PATH": f"{fake_bin}:/usr/bin:/bin"}

    configured = subprocess.run(
        ["/bin/sh", str(scripts["postinst"]), "configure", ""],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
    )
    removed = subprocess.run(
        ["/bin/sh", str(scripts["prerm"]), "remove"],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
    )
    purged = subprocess.run(
        ["/bin/sh", str(scripts["postrm"]), "purge"],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert configured.returncode == 0, configured.stderr
    assert removed.returncode == 0, removed.stderr
    assert purged.returncode == 0, purged.stderr
    commands = log_path.read_text()
    assert "addgroup " not in commands
    assert "dpkg-trigger update-workbench" in commands
    assert "deb-systemd-invoke restart echo-omv-bridge.service" in commands
    assert "deb-systemd-invoke stop echo-omv-bridge.service" in commands
    assert "deb-systemd-helper purge echo-omv-bridge.service" in commands
    assert "groupdel" not in commands


def test_native_plugin_postinst_platform_failure_has_no_service_side_effects(
    tmp_path: Path,
) -> None:
    report = plugin.build(_REPOSITORY, tmp_path / "package")
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    control_files = plugin._read_tar_gz(members["control.tar.gz"], context="control archive")
    failing_python = tmp_path / "python3-preflight-failure"
    failing_python.write_text("#!/bin/sh\nexit 42\n")
    failing_python.chmod(0o755)
    postinst = tmp_path / "postinst"
    postinst.write_bytes(
        control_files["postinst"][0].replace(
            b"/usr/bin/python3",
            str(failing_python).encode(),
        )
    )
    postinst.chmod(0o755)
    command_log = tmp_path / "unexpected-command.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in (
        "getent",
        "addgroup",
        "dpkg-trigger",
        "systemctl",
        "deb-systemd-helper",
        "deb-systemd-invoke",
    ):
        executable = fake_bin / command
        executable.write_text(f"#!/bin/sh\nprintf '%s\\n' '{command}' >> '{command_log}'\nexit 0\n")
        executable.chmod(0o755)

    result = subprocess.run(
        ["/bin/sh", str(postinst), "configure", ""],
        check=False,
        text=True,
        capture_output=True,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "left OpenMediaVault unchanged" in result.stderr
    assert not command_log.exists()


@pytest.mark.parametrize(
    ("distribution", "distribution_version", "omv_version", "expected_code"),
    [
        ("debian", "13", "8.3.1-1", 0),
        ("debian", "13", "1:8.3.1-1", 0),
        ("ubuntu", "24.04", "8.3.1-1", 1),
        ("debian", "12", "8.3.1-1", 1),
        ("debian", "13", "7.7.0-1", 1),
        ("debian", "13", "9.0.0-1", 1),
    ],
)
def test_native_plugin_preinst_enforces_support_matrix_before_install(
    tmp_path: Path,
    distribution: str,
    distribution_version: str,
    omv_version: str,
    expected_code: int,
) -> None:
    report = plugin.build(_REPOSITORY, tmp_path / "package")
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    control_files = plugin._read_tar_gz(members["control.tar.gz"], context="control archive")

    os_release = tmp_path / "os-release"
    os_release.write_text(f'ID={distribution}\nVERSION_ID="{distribution_version}"\n')
    dpkg_query = tmp_path / "dpkg-query"
    dpkg_query.write_text(f"#!/bin/sh\nprintf '%s' '{omv_version}'\n")
    dpkg_query.chmod(0o755)
    manual_root = tmp_path / "manual"
    preinst = (
        control_files["preinst"][0]
        .replace(b"/usr/lib/os-release", str(os_release).encode())
        .replace(b"/usr/bin/dpkg-query", str(dpkg_query).encode())
        .replace(b"/var/lib/echo-os/omv-host", str(manual_root / "state").encode())
        .replace(b"/etc/systemd/system", str(manual_root / "systemd").encode())
        .replace(b"/usr/lib/echo-os/omv-bridge", str(manual_root / "code").encode())
    )
    preinst_path = tmp_path / "preinst-under-test"
    preinst_path.write_bytes(preinst)

    result = subprocess.run(
        ["/bin/sh", str(preinst_path), "install"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == expected_code, result.stderr
    if expected_code:
        assert "supports only" in result.stderr


def test_native_plugin_preinst_abort_upgrade_remains_available_off_matrix(
    tmp_path: Path,
) -> None:
    report = plugin.build(_REPOSITORY, tmp_path / "package")
    members = plugin._read_ar(Path(report["path"]).read_bytes())
    control_files = plugin._read_tar_gz(members["control.tar.gz"], context="control archive")
    preinst_path = tmp_path / "preinst"
    preinst_path.write_bytes(control_files["preinst"][0])

    result = subprocess.run(
        ["/bin/sh", str(preinst_path), "abort-upgrade"],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
