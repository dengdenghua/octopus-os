from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[2]
_SCRIPT = _REPOSITORY / "deploy" / "omv" / "echo_omv_host.py"
sys.path.insert(0, str(_SCRIPT.parent))
_SPEC = importlib.util.spec_from_file_location("echo_omv_host_installer", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
host = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = host
_SPEC.loader.exec_module(host)


class HostHarness:
    def __init__(self, gid: int) -> None:
        self.gid = gid
        self.group: host.GroupInfo | None = None
        self.active = False
        self.commands: list[list[str]] = []
        self.fail_enable = False
        self.fail_next_daemon_reload = False

    def lookup_group(self, name: str) -> host.GroupInfo | None:
        assert name == host.GROUP_NAME
        return self.group

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        arguments = command[1:]
        if Path(command[0]).name == "dpkg-query":
            assert arguments == ["-W", "-f=${Version}", "openmediavault"]
            return subprocess.CompletedProcess(command, 0, "8.0.4-1\n", "")
        if arguments[:2] == ["is-active", "--quiet"]:
            return subprocess.CompletedProcess(command, 0 if self.active else 3, "", "")
        if arguments == ["daemon-reload"] and self.fail_next_daemon_reload:
            self.fail_next_daemon_reload = False
            return subprocess.CompletedProcess(command, 1, "", "reload failed")
        if arguments[:2] == ["enable", "--now"]:
            if self.fail_enable:
                return subprocess.CompletedProcess(command, 1, "", "start failed")
            self.active = True
        elif arguments[:2] == ["disable", "--now"]:
            self.active = False
        elif Path(command[0]).name == "groupadd":
            self.group = host.GroupInfo(host.GROUP_NAME, self.gid, ())
        elif Path(command[0]).name == "groupdel":
            self.group = None
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def installed_socket():
    short_root = Path(tempfile.mkdtemp(prefix="echo-omv-", dir="/tmp"))
    path = short_root / "omv.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chown(path, -1, os.getgid())
    path.chmod(0o660)
    try:
        yield path
    finally:
        listener.close()
        shutil.rmtree(short_root)


@pytest.fixture
def installer_factory(tmp_path: Path, installed_socket: Path):
    gid = os.getgid()
    harness = HostHarness(gid)
    layout = host.HostLayout(
        unit_path=tmp_path / "etc" / "systemd" / "system" / host.SERVICE_NAME,
        code_root=tmp_path / "usr" / "lib" / "echo-os" / "omv-bridge",
        state_root=tmp_path / "var" / "lib" / "echo-os" / "omv-host",
        socket_path=installed_socket,
    )
    tool_root = tmp_path / "tools"
    tool_root.mkdir()
    tool_paths: dict[str, Path] = {}
    for name in host.ToolPaths.__dataclass_fields__:
        path = tool_root / name.replace("_", "-")
        path.write_text("fixture\n")
        path.chmod(0o755)
        tool_paths[name] = path
    tools = host.ToolPaths(**tool_paths)
    os_release = tmp_path / "usr" / "lib" / "os-release"
    os_release.parent.mkdir(parents=True)
    os_release.write_text('ID=debian\nVERSION_ID="13"\nPRETTY_NAME="Debian GNU/Linux 13"\n')
    os_release.chmod(0o644)
    platform_root = tmp_path / "platform"
    netplan_directory = platform_root / "etc" / "netplan"
    netplan_importer = (
        platform_root
        / "usr"
        / "share"
        / "openmediavault"
        / "confdb"
        / "populate.d"
        / "40netplan.sh"
    )
    network_model = (
        platform_root
        / "usr"
        / "share"
        / "openmediavault"
        / "datamodels"
        / "conf.system.network.interface.json"
    )
    netplan_directory.mkdir(parents=True)
    netplan_importer.parent.mkdir(parents=True)
    network_model.parent.mkdir(parents=True)
    netplan_importer.write_text('obj.set("dnsnameservers", dnsnameservers)\n')
    network_model.write_text('{"properties":{"dnsnameservers":{"type":"array"}}}\n')
    platform_paths = host.PlatformPaths(
        os_release=os_release,
        dpkg_query=tools.dpkg_query,
        netplan_directory=netplan_directory,
        netplan_importer=netplan_importer,
        network_interface_model=network_model,
    )

    def factory(**overrides):
        options = {
            "layout": layout,
            "tools": tools,
            "command_runner": harness.run,
            "group_lookup": harness.lookup_group,
            "health_check": lambda _path: True,
            "executable_check": lambda _path: None,
            "system_name": "Linux",
            "architecture": "x86_64",
            "os_release_path": os_release,
            "platform_paths": platform_paths,
            "hostname": "echo-omv-ci",
            "effective_uid": 0,
            "trusted_uid": os.getuid(),
            "sleep": lambda _seconds: None,
        }
        options.update(overrides)
        return host.OmvHostInstaller(_REPOSITORY, gid, **options)

    return factory, harness, layout


def _install(installer: host.OmvHostInstaller) -> dict[str, object]:
    plan = installer.plan()
    return installer.install(plan["installConfirmation"])


def test_install_copies_only_managed_bridge_and_is_idempotent(installer_factory) -> None:
    factory, harness, layout = installer_factory
    installer = factory()

    report = _install(installer)

    assert report["action"] == "install"
    assert report["distribution"] == "debian"
    assert report["distributionVersion"] == "13"
    assert report["omvVersion"] == "8.0.4-1"
    assert report["omvMajor"] == 8
    assert report["supportMatrix"] == "debian-13+omv-8"
    assert harness.active is True
    assert harness.group == host.GroupInfo(host.GROUP_NAME, os.getgid(), ())
    unit = layout.unit_path.read_text()
    assert "/usr/lib/echo-os/omv-bridge" in unit
    assert "/opt/echo-os" not in unit
    assert stat.S_IMODE(layout.unit_path.stat().st_mode) == 0o644
    assert stat.S_IMODE(layout.state_path.stat().st_mode) == 0o600
    assert {
        path.relative_to(layout.code_root).as_posix()
        for path in layout.code_root.rglob("*")
        if path.is_file()
    } == set(host.SOURCE_FILES)
    assert all(
        stat.S_IMODE((layout.code_root / relative).stat().st_mode) == 0o644
        for relative in host.SOURCE_FILES
    )
    state = json.loads(layout.state_path.read_text())
    assert state["bundleId"] == report["bundleId"]
    assert installer.plan()["action"] == "unchanged"


def test_uninstall_removes_only_bridge_and_records_preserved_data(installer_factory) -> None:
    factory, harness, layout = installer_factory
    installer = factory()
    _install(installer)
    plan = installer.plan()
    nas_data = layout.state_root.parent / "nas-data"
    nas_data.write_text("must survive")

    receipt = installer.uninstall(plan["uninstallConfirmation"])

    assert receipt["preservedNasData"] is True
    assert receipt["groupRemoved"] is True
    assert harness.group is None
    assert not layout.unit_path.exists()
    assert not layout.code_root.exists()
    assert not layout.state_path.exists()
    assert nas_data.read_text() == "must survive"
    assert json.loads(layout.uninstall_receipt_path.read_text()) == receipt


def test_uninstall_retains_group_with_members_without_failing(installer_factory) -> None:
    factory, harness, layout = installer_factory
    installer = factory()
    _install(installer)
    harness.group = host.GroupInfo(host.GROUP_NAME, os.getgid(), ("nas-user",))
    confirmation = installer.plan()["uninstallConfirmation"]

    receipt = installer.uninstall(confirmation)

    assert receipt["groupRemoved"] is False
    assert receipt["groupRetainedReason"] == "group still has explicit members"
    assert harness.group is not None
    assert not layout.unit_path.exists()


def test_unmanaged_files_are_never_adopted(installer_factory) -> None:
    factory, _harness, layout = installer_factory
    layout.unit_path.parent.mkdir(parents=True)
    layout.unit_path.write_text("unmanaged")

    with pytest.raises(host.HostInstallError, match="unmanaged"):
        factory().plan()

    assert layout.unit_path.read_text() == "unmanaged"


def test_modified_managed_file_blocks_upgrade_and_uninstall(installer_factory) -> None:
    factory, _harness, layout = installer_factory
    installer = factory()
    _install(installer)
    target = layout.code_root / "appliance" / "omv_bridge.py"
    target.write_text(target.read_text() + "\n# modified outside installer\n")

    with pytest.raises(host.HostInstallError, match="differs from its managed manifest"):
        installer.plan()
    state = json.loads(layout.state_path.read_text())
    with pytest.raises(host.HostInstallError, match="differs from its managed manifest"):
        installer.uninstall(f"UNINSTALL ECHO OMV BRIDGE {state['bundleId']}")

    assert target.exists()


def test_failed_service_start_rolls_back_files_state_and_group(installer_factory) -> None:
    factory, harness, layout = installer_factory
    harness.fail_enable = True
    installer = factory()
    confirmation = installer.plan()["installConfirmation"]

    with pytest.raises(host.HostInstallError, match="installation failed"):
        installer.install(confirmation)

    assert harness.group is None
    assert harness.active is False
    assert not layout.unit_path.exists()
    assert not layout.code_root.exists()
    assert not layout.state_path.exists()


def test_failed_uninstall_restores_files_state_and_running_service(installer_factory) -> None:
    factory, harness, layout = installer_factory
    installer = factory()
    _install(installer)
    confirmation = installer.plan()["uninstallConfirmation"]
    harness.fail_next_daemon_reload = True

    with pytest.raises(host.HostInstallError, match="uninstall was rolled back"):
        installer.uninstall(confirmation)

    assert harness.active is True
    assert harness.group is not None
    assert layout.unit_path.exists()
    assert layout.state_path.exists()
    assert all((layout.code_root / relative).exists() for relative in host.SOURCE_FILES)


def test_failed_uninstall_state_commit_removes_false_receipt_and_restores(
    installer_factory,
    monkeypatch,
) -> None:
    factory, harness, layout = installer_factory
    installer = factory()
    _install(installer)
    confirmation = installer.plan()["uninstallConfirmation"]
    original_unlink = Path.unlink
    state_failure_pending = True

    def fail_state_unlink(path: Path, *args, **kwargs) -> None:
        nonlocal state_failure_pending
        if path == layout.state_path and state_failure_pending:
            state_failure_pending = False
            raise OSError("state commit failed")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_state_unlink)

    with pytest.raises(host.HostInstallError, match="uninstall was rolled back"):
        installer.uninstall(confirmation)

    assert harness.active is True
    assert harness.group is not None
    assert layout.unit_path.exists()
    assert layout.state_path.exists()
    assert not layout.uninstall_receipt_path.exists()


def test_confirmation_is_bound_to_gid_and_bundle(installer_factory) -> None:
    factory, harness, layout = installer_factory

    with pytest.raises(host.HostInstallError, match="confirmation"):
        factory().install("INSTALL ECHO OMV BRIDGE")

    assert harness.commands == [
        [str(factory().tools.dpkg_query), "-W", "-f=${Version}", "openmediavault"]
    ]
    assert harness.group is None
    assert not layout.unit_path.exists()


def test_plan_rejects_non_linux_and_normalizes_arm64(installer_factory) -> None:
    factory, _harness, _layout = installer_factory
    with pytest.raises(host.HostInstallError, match="requires Linux"):
        factory(system_name="Darwin").plan()

    assert factory(architecture="aarch64").plan()["architecture"] == "arm64"


@pytest.mark.parametrize(
    ("release", "match"),
    [
        ('ID=debian\nVERSION_ID="12"\n', "requires Debian 13"),
        ('ID=ubuntu\nVERSION_ID="24.04"\n', "requires Debian 13"),
        ('ID=debian\nVERSION_ID="13"\nID=ubuntu\n', "duplicate key"),
        ('ID=debian\nVERSION_ID="13\n', "invalid value"),
    ],
)
def test_plan_rejects_unsupported_or_malformed_os_release(
    installer_factory,
    release,
    match,
) -> None:
    factory, harness, _layout = installer_factory
    installer = factory()
    installer._os_release_path.write_text(release)

    with pytest.raises(host.HostInstallError, match=match):
        installer.plan()

    assert harness.commands == []


@pytest.mark.parametrize(
    ("version", "match"),
    [
        ("7.7.24-1", "requires OMV 8"),
        ("9.0.0-1", "requires OMV 8"),
        ("8 invalid", "package version is invalid"),
        ("8.0.0-1\n9.0.0-1", "package version is invalid"),
    ],
)
def test_plan_rejects_unsupported_or_malformed_omv_version(
    installer_factory,
    version,
    match,
) -> None:
    factory, harness, _layout = installer_factory

    def version_runner(command):
        harness.commands.append(command)
        return subprocess.CompletedProcess(command, 0, version, "")

    with pytest.raises(host.HostInstallError, match=match):
        factory(command_runner=version_runner).plan()

    assert len(harness.commands) == 1
    assert Path(harness.commands[0][0]).name == "dpkg-query"


def test_plan_rejects_missing_omv_package_without_mutation(installer_factory) -> None:
    factory, harness, layout = installer_factory

    def missing_package(command):
        harness.commands.append(command)
        return subprocess.CompletedProcess(command, 1, "", "no packages found")

    with pytest.raises(host.HostInstallError, match="could not be queried"):
        factory(command_runner=missing_package).plan()

    assert len(harness.commands) == 1
    assert not layout.unit_path.exists()
    assert not layout.code_root.exists()
    assert not layout.state_path.exists()


def test_plan_rejects_hostname_that_smb_would_truncate_without_mutation(
    installer_factory,
) -> None:
    factory, harness, layout = installer_factory

    with pytest.raises(host.HostInstallError, match="smb_hostname_too_long"):
        factory(hostname="echo-storage-node-01").plan()

    assert len(harness.commands) == 1
    assert not layout.unit_path.exists()
    assert not layout.code_root.exists()
    assert not layout.state_path.exists()


def test_plan_rejects_active_omv_netplan_dns_field_mismatch_without_mutation(
    installer_factory,
) -> None:
    factory, harness, layout = installer_factory
    installer = factory()
    installer._platform_paths.netplan_importer.write_text('obj.set("dnsservers", dnsnameservers)\n')
    (installer._platform_paths.netplan_directory / "50-cloud-init.yaml").write_text(
        "network:\n  ethernets:\n    eth0:\n      nameservers:\n        addresses: [1.1.1.1]\n"
    )

    with pytest.raises(host.HostInstallError, match="omv_netplan_dns_field_mismatch"):
        installer.plan()

    assert len(harness.commands) == 1
    assert not layout.unit_path.exists()
    assert not layout.code_root.exists()
    assert not layout.state_path.exists()


def test_plan_reports_latent_omv_netplan_mismatch_without_blocking(
    installer_factory,
) -> None:
    factory, _harness, _layout = installer_factory
    installer = factory()
    installer._platform_paths.netplan_importer.write_text('obj.set("dnsservers", dnsnameservers)\n')

    plan = installer.plan()

    assert plan["platformPreflight"]["ready"] is True
    assert plan["platformPreflight"]["netplan"]["knownFieldMismatch"] is True
    assert [warning["code"] for warning in plan["platformPreflight"]["warnings"]] == [
        "omv_netplan_dns_field_mismatch_latent"
    ]


def test_plan_rejects_non_text_package_query_response(installer_factory) -> None:
    factory, harness, _layout = installer_factory

    def invalid_query(command):
        harness.commands.append(command)
        return subprocess.CompletedProcess(command, 0, None, None)

    with pytest.raises(host.HostInstallError, match="could not be queried"):
        factory(command_runner=invalid_query).plan()

    assert len(harness.commands) == 1


def test_plan_rejects_untrusted_os_release_file(installer_factory) -> None:
    factory, harness, _layout = installer_factory
    installer = factory()
    installer._os_release_path.chmod(0o666)

    with pytest.raises(host.HostInstallError, match="ownership or mode is unsafe"):
        installer.plan()

    assert harness.commands == []


def test_uninstall_remains_available_after_host_leaves_support_matrix(
    installer_factory,
) -> None:
    factory, harness, layout = installer_factory
    installer = factory()
    _install(installer)
    state = json.loads(layout.state_path.read_text())
    installer._os_release_path.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')

    receipt = installer.uninstall(f"UNINSTALL ECHO OMV BRIDGE {state['bundleId']}")

    assert receipt["bundleId"] == state["bundleId"]
    assert receipt["preservedNasData"] is True
    assert harness.active is False
    assert not layout.unit_path.exists()
    assert not layout.code_root.exists()


def test_unit_policy_rejects_repository_execution_and_duplicate_start() -> None:
    original = (_REPOSITORY / "deploy/omv/echo-omv-bridge.service.example").read_bytes()
    host._validate_unit(original)

    with pytest.raises(host.HostInstallError, match="managed code boundary"):
        host._validate_unit(original.replace(b"/usr/lib/echo-os/omv-bridge", b"/opt/echo-os"))
    with pytest.raises(host.HostInstallError, match="duplicate ExecStart"):
        host._validate_unit(original + b"\nExecStart=/usr/bin/python3 -c pass\n")
