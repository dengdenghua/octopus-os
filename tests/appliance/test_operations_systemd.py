from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from deploy.appliance import operations_systemd as systemd
from deploy.appliance import operations_systemd_lab as lab
from deploy.appliance import physical_acceptance_capture as capture
from deploy.appliance import verify_operations_systemd_units as native_verify
from deploy.appliance.external_storage import ExternalStorageError

REPOSITORY = Path(__file__).resolve().parents[2]
OPERATIONS_ARTIFACT_ID = "7" * 16
IMAGE_REFERENCE = f"ghcr.io/echo-os/echo-os@sha256:{'6' * 64}"


class CommandHarness:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.enabled = {name: False for name in systemd.ENABLED_UNIT_NAMES}
        self.active = {name: False for name in systemd.ENABLED_UNIT_NAMES}
        self.fail_enable: str | None = None
        self.fail_rollback = False
        self.fail_daemon_reload_count = 0

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        arguments = tuple(command[1:])
        self.calls.append(arguments)
        if Path(command[0]).name == "systemd-analyze":
            return subprocess.CompletedProcess(command, 0, "", "")
        action = arguments[0]
        name = arguments[-1] if len(arguments) > 1 else ""
        if action == "is-enabled":
            state = "enabled" if self.enabled[name] else "disabled"
            return subprocess.CompletedProcess(
                command, 0 if self.enabled[name] else 1, state + "\n", ""
            )
        if action == "is-active":
            state = "active" if self.active[name] else "inactive"
            return subprocess.CompletedProcess(
                command, 0 if self.active[name] else 3, state + "\n", ""
            )
        if action == "daemon-reload":
            if self.fail_daemon_reload_count:
                self.fail_daemon_reload_count -= 1
                return subprocess.CompletedProcess(command, 1, "", "reload failed")
            return subprocess.CompletedProcess(command, 0, "", "")
        if action == "enable" and arguments[1:2] == ("--now",):
            if name == self.fail_enable:
                return subprocess.CompletedProcess(command, 1, "", "failed")
            self.enabled[name] = True
            self.active[name] = name != systemd.RECOVERY_SERVICE_NAME
            return subprocess.CompletedProcess(command, 0, "", "")
        if action == "enable":
            self.enabled[name] = True
        elif action == "start":
            self.active[name] = True
        elif action == "stop":
            self.active[name] = False
        elif action == "disable":
            if self.fail_rollback:
                return subprocess.CompletedProcess(command, 2, "", "rollback failed")
            self.enabled[name] = False
            self.active[name] = False
        return subprocess.CompletedProcess(command, 0, "", "")


class LabCommandHarness(CommandHarness):
    def __init__(self) -> None:
        super().__init__()
        self.last_trigger = {name: "n/a" for name in systemd.TIMER_NAMES}
        self.mounts: dict[Path, bool] = {}
        self.service_mounts: dict[str, Path] = {}

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if Path(command[0]).name == "dpkg-query":
            return subprocess.CompletedProcess(command, 0, "8.7.3-1", "")
        if Path(command[0]).name == "umount":
            self.mounts[Path(command[-1])] = False
            return subprocess.CompletedProcess(command, 0, "", "")
        if Path(command[0]).name == "mount":
            self.mounts[Path(command[-1])] = True
            return subprocess.CompletedProcess(command, 0, "", "")
        arguments = command[1:]
        if arguments[:1] == ["show"] and "--property=LastTriggerUSec" in arguments:
            return subprocess.CompletedProcess(
                command, 0, self.last_trigger[arguments[1]] + "\n", ""
            )
        if arguments[:1] == ["show"] and "--property=Result" in arguments:
            return subprocess.CompletedProcess(command, 0, "success\n", "")
        if (
            arguments[:1] == ["start"]
            and arguments[1] in self.service_mounts
            and not self.mounts[self.service_mounts[arguments[1]]]
        ):
            return subprocess.CompletedProcess(command, 1, "", "missing mount")
        return super().__call__(command)


def _fixture(
    tmp_path: Path,
) -> tuple[
    systemd.OperationsConfig,
    systemd.SystemLayout,
    systemd.SystemTools,
    list[dict[str, str]],
]:
    bundle_root = tmp_path / f"echo-appliance-operations-{OPERATIONS_ARTIFACT_ID}"
    bundle_root.mkdir()
    (bundle_root / "data").mkdir()
    (bundle_root / "storage").mkdir()
    for name in (
        "backup-state.sh",
        "docker-compose.yml",
        "export-audit-evidence.sh",
        "external_storage.py",
        "recover-appliance-upgrade.sh",
        "upgrade_transaction.py",
    ):
        shutil.copyfile(REPOSITORY / "deploy/appliance" / name, bundle_root / name)
        (bundle_root / name).chmod(0o644 if name == "docker-compose.yml" else 0o755)
    (bundle_root / "echo-release.env").write_text(f"ECHO_OS_IMAGE={IMAGE_REFERENCE}\n")
    (bundle_root / "echo-release.env").chmod(0o600)
    lab_tool = bundle_root / "operations_systemd_lab.py"
    shutil.copyfile(REPOSITORY / "deploy/appliance/operations_systemd_lab.py", lab_tool)
    lab_tool.chmod(0o755)
    lab_bytes = lab_tool.read_bytes()
    (bundle_root / "bundle-manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "artifact": {
                    "id": OPERATIONS_ARTIFACT_ID,
                    "name": bundle_root.name,
                    "architectures": ["amd64", "arm64"],
                    "imageReference": IMAGE_REFERENCE,
                    "entrypoints": {"operationsSystemdLab": "./operations_systemd_lab.py plan|run"},
                },
                "files": {
                    "operations_systemd_lab.py": {
                        "sha256": hashlib.sha256(lab_bytes).hexdigest(),
                        "size": len(lab_bytes),
                        "mode": "0755",
                    }
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    backup_mount = tmp_path / "backup-mount"
    backup_directory = backup_mount / "echo-os"
    backup_directory.mkdir(parents=True)
    audit_mount = tmp_path / "audit-mount"
    audit_directory = audit_mount / "evidence"
    audit_directory.mkdir(parents=True)
    credentials = tmp_path / "credentials"
    credentials.mkdir(mode=0o700)
    backup_credential = credentials / "echo-backup-passphrase"
    audit_credential = credentials / "echo-audit-export-passphrase"
    backup_credential.write_bytes(b"encrypted-backup-credential")
    audit_credential.write_bytes(b"encrypted-audit-credential")
    backup_credential.chmod(0o600)
    audit_credential.chmod(0o600)

    tools_root = tmp_path / "tools"
    tools_root.mkdir()
    systemctl = tools_root / "systemctl"
    systemd_analyze = tools_root / "systemd-analyze"
    for tool in (systemctl, systemd_analyze):
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    unit_directory = tmp_path / "units"
    unit_directory.mkdir()
    storage_calls: list[dict[str, str]] = []

    config = systemd.OperationsConfig(
        bundle_root=bundle_root,
        backup_directory=backup_directory,
        backup_mountpoint=backup_mount,
        audit_directory=audit_directory,
        audit_mountpoint=audit_mount,
        backup_credential=backup_credential,
        audit_credential=audit_credential,
    )
    return (
        config,
        systemd.SystemLayout(unit_directory=unit_directory),
        systemd.SystemTools(systemctl=systemctl, systemd_analyze=systemd_analyze),
        storage_calls,
    )


def _candidate_index(tmp_path: Path) -> Path:
    value = {
        "schemaVersion": 1,
        "kind": "echo.delivery-release-evidence-index",
        "source": {
            "repository": "dengdenghua/echo-os",
            "commit": "1" * 40,
            "agentRepository": "dengdenghua/echo-agent",
            "agentCommit": "2" * 40,
            "releaseTag": "echo-appliance-v1.0.0",
        },
        "evidence": {
            "candidatePreflight": {"reportId": "4" * 64},
            "appliance": {
                "manifestSha256": "5" * 64,
                "immutableReference": IMAGE_REFERENCE,
                "operationsBundle": {
                    "artifactId": OPERATIONS_ARTIFACT_ID,
                    "sha256": "8" * 64,
                    "imageReference": IMAGE_REFERENCE,
                },
            },
        },
        "ciReleaseCandidateReady": True,
        "nasProductDeliveryReady": False,
        "physicalAcceptance": {
            "complete": False,
            "remainingGates": [
                "physical_x86_64_install_and_cold_boot",
                "supported_arm64_hardware_install_and_cold_boot",
                "real_disk_smart_and_raid_degradation_recovery",
                "external_smb_and_nfs_client_interoperability",
                "power_loss_during_update_and_state_restore",
                "recovery_media_bare_metal_restore",
            ],
        },
    }
    value["indexId"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = tmp_path / "echo-delivery-release-evidence-index.json"
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o444)
    return path


def _storage_verifier(calls: list[dict[str, str]]):
    def verify(**values: object) -> dict[str, str]:
        result = {
            "destination": str(values["destination"]),
            "mountpoint": str(values["mountpoint"]),
            "filesystem": "ext4",
            "source": "/dev/external",
            "deviceId": "20",
        }
        calls.append(result)
        return result

    return verify


def _lab_storage_verifier(calls: list[dict[str, str]]):
    def verify(**values: object) -> dict[str, str]:
        backup = "backup" in str(values["mountpoint"])
        result = {
            "destination": str(values["destination"]),
            "mountpoint": str(values["mountpoint"]),
            "filesystem": "ext4",
            "source": "/dev/backup-lab" if backup else "/dev/audit-lab",
            "deviceId": "20" if backup else "21",
        }
        calls.append(result)
        return result

    return verify


def _installer(
    config: systemd.OperationsConfig,
    layout: systemd.SystemLayout,
    tools: systemd.SystemTools,
    calls: list[dict[str, str]],
    *,
    command_runner: CommandHarness | None = None,
    effective_uid: int = 0,
    storage_verifier=None,
) -> systemd.OperationsSystemdInstaller:
    return systemd.OperationsSystemdInstaller(
        config,
        layout=layout,
        tools=tools,
        command_runner=command_runner or CommandHarness(),
        storage_verifier=storage_verifier or _storage_verifier(calls),
        effective_uid=effective_uid,
        trusted_uid=os.getuid(),
        system_name="Linux",
    )


def _remover(
    layout: systemd.SystemLayout,
    tools: systemd.SystemTools,
    *,
    command_runner: CommandHarness,
    effective_uid: int = 0,
) -> systemd.OperationsSystemdRemover:
    return systemd.OperationsSystemdRemover(
        layout=layout,
        tools=tools,
        command_runner=command_runner,
        effective_uid=effective_uid,
        trusted_uid=os.getuid(),
        system_name="Linux",
    )


def _install_reference_units(
    config: systemd.OperationsConfig, layout: systemd.SystemLayout
) -> dict[str, bytes]:
    rendered = systemd._units(config)
    for name, payload in rendered.items():
        path = layout.unit_directory / name
        path.write_bytes(payload)
        path.chmod(0o644)
    return rendered


def _lab_tools(tools: systemd.SystemTools) -> lab.LabTools:
    root = tools.systemctl.parent
    created = {}
    for name in ("dpkg-query", "mount", "umount"):
        path = root / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        created[name] = path
    return lab.LabTools(
        systemctl=tools.systemctl,
        systemd_analyze=tools.systemd_analyze,
        dpkg_query=created["dpkg-query"],
        mount=created["mount"],
        umount=created["umount"],
    )


def test_physical_lab_rejects_a_replaced_candidate_bound_executor(tmp_path: Path) -> None:
    config, _layout, _tools, _calls = _fixture(tmp_path)
    candidate = lab._candidate_identity(_candidate_index(tmp_path), trusted_uid=os.getuid())

    identity = lab._operations_bundle_identity(
        config.bundle_root,
        candidate,
        trusted_uid=os.getuid(),
    )
    assert identity["artifactId"] == OPERATIONS_ARTIFACT_ID

    executor = config.bundle_root / "operations_systemd_lab.py"
    executor.write_bytes(executor.read_bytes() + b"\n# replaced after extraction\n")
    executor.chmod(0o755)

    with pytest.raises(lab.OperationsSystemdLabError, match="not from the release candidate"):
        lab._operations_bundle_identity(
            config.bundle_root,
            candidate,
            trusted_uid=os.getuid(),
        )


def test_physical_lab_plan_and_install_fault_use_real_transaction_rollback_contract(
    tmp_path: Path,
) -> None:
    config, layout, tools, calls = _fixture(tmp_path)
    lab_tools = _lab_tools(tools)
    commands = LabCommandHarness()
    commands.mounts = {
        config.backup_mountpoint: True,
        config.audit_mountpoint: True,
    }
    commands.service_mounts = {
        systemd.UNIT_NAMES[0]: config.backup_mountpoint,
        systemd.UNIT_NAMES[2]: config.audit_mountpoint,
    }
    evidence = tmp_path / "physical-evidence"
    evidence.mkdir()
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=debian\nVERSION_ID="13"\n', encoding="utf-8")
    preservation_arguments = []
    for label in lab.PRESERVATION_LABELS:
        path = tmp_path / f"preserve-{label}"
        path.write_text(f"stable {label}\n", encoding="utf-8")
        preservation_arguments.append(f"{label}={path}")
    output = tmp_path / "operations-lab-plan.json"

    plan = lab.build_plan(
        candidate_index=_candidate_index(tmp_path),
        config=config,
        evidence_directory=evidence,
        preservation_arguments=preservation_arguments,
        output=output,
        tools=lab_tools,
        runner=commands,
        os_release=os_release,
        unit_directory=layout.unit_directory,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        storage_verifier=_lab_storage_verifier(calls),
    )

    assert plan["platform"] == {
        "id": "debian",
        "versionId": "13",
        "omvVersion": "8.7.3-1",
    }
    assert plan["phases"] == list(lab.PHASES)
    assert output.stat().st_mode & 0o777 == 0o400
    confirmation = plan["confirmations"]["install-rollback"]
    report = lab.run_phase(
        plan_path=output,
        phase="install-rollback",
        confirmation=confirmation,
        tools=lab_tools,
        runner=commands,
        unit_directory=layout.unit_directory,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=os_release,
        storage_verifier=_lab_storage_verifier(calls),
    )

    assert report["phase"] == "install-rollback"
    result = json.loads((evidence / "operations-install-rollback.log").read_text())
    assert result["passed"] is True
    assert result["details"] == {"baselineRestored": True}
    assert all(not (layout.unit_directory / name).exists() for name in systemd.UNIT_NAMES)

    install = lab.run_phase(
        plan_path=output,
        phase="install",
        confirmation=plan["confirmations"]["install"],
        tools=lab_tools,
        runner=commands,
        unit_directory=layout.unit_directory,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=os_release,
        storage_verifier=_lab_storage_verifier(calls),
    )

    assert install["phase"] == "install"
    install_evidence = json.loads((evidence / "operations-install.log").read_text())
    assert install_evidence["details"]["installed"] is True
    assert all((layout.unit_directory / name).is_file() for name in systemd.UNIT_NAMES)
    assert commands.enabled == {name: True for name in systemd.ENABLED_UNIT_NAMES}
    assert commands.active == {
        name: name != systemd.RECOVERY_SERVICE_NAME for name in systemd.ENABLED_UNIT_NAMES
    }

    commands.last_trigger[systemd.TIMER_NAMES[0]] = "Thu 2026-08-27 03:30:00 CST"
    backup_product = config.backup_directory / "echo-state-test.echo-backup"
    backup_product.write_bytes(b"verified encrypted backup")
    backup_observation = lab.run_phase(
        plan_path=output,
        phase="observe-backup-timer",
        confirmation=plan["confirmations"]["observe-backup-timer"],
        tools=lab_tools,
        runner=commands,
        unit_directory=layout.unit_directory,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=os_release,
        storage_verifier=_lab_storage_verifier(calls),
    )
    assert backup_observation["phase"] == "observe-backup-timer"

    commands.last_trigger[systemd.TIMER_NAMES[1]] = "Thu 2026-08-27 04:15:00 CST"
    audit_product = config.audit_directory / "echo-audit-test.echo-audit"
    audit_product.write_bytes(b"verified encrypted audit evidence")
    audit_observation = lab.run_phase(
        plan_path=output,
        phase="observe-audit-timer",
        confirmation=plan["confirmations"]["observe-audit-timer"],
        tools=lab_tools,
        runner=commands,
        unit_directory=layout.unit_directory,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=os_release,
        storage_verifier=_lab_storage_verifier(calls),
    )
    assert audit_observation["phase"] == "observe-audit-timer"

    for phase in ("backup-mount-loss", "audit-mount-loss"):
        mount_loss = lab.run_phase(
            plan_path=output,
            phase=phase,
            confirmation=plan["confirmations"][phase],
            tools=lab_tools,
            runner=commands,
            mount_checker=lambda path: commands.mounts[path],
            fallback_file_lister=lambda _path: (),
            unit_directory=layout.unit_directory,
            effective_uid=0,
            trusted_uid=os.getuid(),
            system_name="Linux",
            os_release=os_release,
            storage_verifier=_lab_storage_verifier(calls),
        )
        assert mount_loss["phase"] == phase

    remove_rollback = lab.run_phase(
        plan_path=output,
        phase="remove-rollback",
        confirmation=plan["confirmations"]["remove-rollback"],
        tools=lab_tools,
        runner=commands,
        unit_directory=layout.unit_directory,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=os_release,
        storage_verifier=_lab_storage_verifier(calls),
    )
    assert remove_rollback["phase"] == "remove-rollback"
    assert all((layout.unit_directory / name).is_file() for name in systemd.UNIT_NAMES)

    removed = lab.run_phase(
        plan_path=output,
        phase="remove",
        confirmation=plan["confirmations"]["remove"],
        tools=lab_tools,
        runner=commands,
        unit_directory=layout.unit_directory,
        effective_uid=0,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release=os_release,
        storage_verifier=_lab_storage_verifier(calls),
    )
    assert removed["phase"] == "remove"
    remove_evidence = json.loads((evidence / "operations-remove.log").read_text())
    assert remove_evidence["details"]["unitsAndTimersAbsent"] is True
    assert set(remove_evidence["details"]["preserved"]) == set(lab.PRESERVATION_LABELS)
    assert all(not (layout.unit_directory / name).exists() for name in systemd.UNIT_NAMES)
    arguments = capture.operations_lab_evidence_arguments(
        evidence.resolve(),
        candidate_index=Path(plan["releaseCandidate"]["indexPath"]),
        lab_plan=output,
    )
    assert len(arguments) == len(capture.OPERATIONS_SYSTEMD_LIFECYCLE_CHECKS)


def test_plan_is_deterministic_binds_credentials_storage_and_rendered_units(
    tmp_path: Path,
) -> None:
    config, layout, tools, calls = _fixture(tmp_path)
    installer = _installer(config, layout, tools, calls)

    first = installer.plan()
    second = installer.plan()

    assert first == second
    assert first["schemaVersion"] == systemd.SCHEMA_VERSION
    assert first["kind"] == "echo.operations-systemd-install-plan"
    assert first["installConfirmation"] == f"INSTALL ECHO OPERATIONS {first['planId']}"
    assert first["timers"] == list(systemd.TIMER_NAMES)
    assert first["recoveryService"] == systemd.RECOVERY_SERVICE_NAME
    assert set(first["units"]) == set(systemd.UNIT_NAMES)
    assert first["credentials"]["backup"]["sha256"] == systemd._sha256(
        config.backup_credential.read_bytes()
    )
    assert first["storage"]["audit"]["mountpoint"] == str(config.audit_mountpoint)
    assert first["hostTools"]["systemctl"]["path"] == str(tools.systemctl)
    assert len(calls) == 4
    rendered = systemd._units(config)
    assert str(config.bundle_root).encode() in rendered["echo-state-backup.service"]
    assert str(config.backup_mountpoint).encode() in rendered["echo-state-backup.service"]
    assert config.backup_credential.read_bytes() not in systemd._canonical_json(first)


def test_plan_rejects_unsafe_storage_permissions_and_symlinks(tmp_path: Path) -> None:
    config, layout, tools, calls = _fixture(tmp_path)

    def reject_storage(**_values: object) -> dict[str, str]:
        raise ExternalStorageError("shares a filesystem with protected device state data")

    with pytest.raises(systemd.OperationsSystemdError, match="external operations storage"):
        _installer(
            config,
            layout,
            tools,
            calls,
            storage_verifier=reject_storage,
        ).plan()

    config.backup_credential.chmod(0o644)
    with pytest.raises(systemd.OperationsSystemdError, match="unsafe ownership, mode, or size"):
        _installer(config, layout, tools, calls).plan()
    config.backup_credential.chmod(0o600)

    (config.bundle_root / "backup-state.sh").chmod(0o775)
    with pytest.raises(systemd.OperationsSystemdError, match="unsafe ownership, mode, or size"):
        _installer(config, layout, tools, calls).plan()

    target = config.audit_credential
    replacement = target.with_name("replacement")
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(0o600)
    target.unlink()
    target.symlink_to(replacement)
    with pytest.raises(systemd.OperationsSystemdError, match="symbolic link"):
        _installer(config, layout, tools, calls).plan()


def test_install_requires_current_plan_exact_confirmation_and_root(tmp_path: Path) -> None:
    config, layout, tools, calls = _fixture(tmp_path)
    installer = _installer(config, layout, tools, calls)
    plan = installer.plan()

    with pytest.raises(systemd.OperationsSystemdError, match="confirmation"):
        installer.install(plan, "INSTALL ECHO OPERATIONS wrong")
    changed = json.loads(json.dumps(plan))
    changed["timers"].reverse()
    with pytest.raises(systemd.OperationsSystemdError, match="no longer matches"):
        installer.install(changed, plan["installConfirmation"])
    with pytest.raises(systemd.OperationsSystemdError, match="requires root"):
        _installer(config, layout, tools, calls, effective_uid=1000).install(
            plan, plan["installConfirmation"]
        )


def test_install_verifies_then_atomically_writes_units_and_enables_timers(
    tmp_path: Path,
) -> None:
    config, layout, tools, calls = _fixture(tmp_path)
    commands = CommandHarness()
    installer = _installer(config, layout, tools, calls, command_runner=commands)
    plan = installer.plan()

    report = installer.install(plan, plan["installConfirmation"])

    assert report["installed"] is True
    assert report["planId"] == plan["planId"]
    assert commands.calls[0][0] == "verify"
    assert ("daemon-reload",) in commands.calls
    assert commands.enabled == {name: True for name in systemd.ENABLED_UNIT_NAMES}
    assert commands.active == {
        name: name != systemd.RECOVERY_SERVICE_NAME for name in systemd.ENABLED_UNIT_NAMES
    }
    assert report["recoveryServiceEnabled"] == systemd.RECOVERY_SERVICE_NAME
    rendered = systemd._units(config)
    for name in systemd.UNIT_NAMES:
        path = layout.unit_directory / name
        assert path.read_bytes() == rendered[name]
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_failed_enable_restores_all_units_modes_and_timer_states(tmp_path: Path) -> None:
    config, layout, tools, calls = _fixture(tmp_path)
    originals: dict[str, tuple[bytes, int]] = {}
    for index, name in enumerate(systemd.UNIT_NAMES):
        payload = f"old-{name}\n".encode()
        mode = 0o600 + index
        path = layout.unit_directory / name
        path.write_bytes(payload)
        path.chmod(mode)
        originals[name] = (payload, mode)
    commands = CommandHarness()
    commands.enabled[systemd.TIMER_NAMES[0]] = True
    commands.active[systemd.TIMER_NAMES[1]] = True
    commands.fail_enable = systemd.TIMER_NAMES[1]
    installer = _installer(config, layout, tools, calls, command_runner=commands)
    plan = installer.plan()

    with pytest.raises(systemd.OperationsSystemdError, match="previous units were restored"):
        installer.install(plan, plan["installConfirmation"])

    for name, (payload, mode) in originals.items():
        path = layout.unit_directory / name
        assert path.read_bytes() == payload
        assert stat.S_IMODE(path.stat().st_mode) == mode
    assert commands.enabled[systemd.TIMER_NAMES[0]] is True
    assert commands.active[systemd.TIMER_NAMES[0]] is False
    assert commands.enabled[systemd.TIMER_NAMES[1]] is False
    assert commands.active[systemd.TIMER_NAMES[1]] is True


def test_failed_install_reports_incomplete_rollback(tmp_path: Path) -> None:
    config, layout, tools, calls = _fixture(tmp_path)
    commands = CommandHarness()
    commands.fail_enable = systemd.TIMER_NAMES[1]
    commands.fail_rollback = True
    installer = _installer(config, layout, tools, calls, command_runner=commands)
    plan = installer.plan()

    with pytest.raises(systemd.OperationsSystemdError, match="rollback was incomplete"):
        installer.install(plan, plan["installConfirmation"])


def test_plan_becomes_stale_when_credential_or_storage_identity_changes(tmp_path: Path) -> None:
    config, layout, tools, calls = _fixture(tmp_path)
    storage = _storage_verifier(calls)
    installer = _installer(config, layout, tools, calls, storage_verifier=storage)
    plan = installer.plan()
    config.backup_credential.write_bytes(b"rotated-encrypted-credential")
    config.backup_credential.chmod(0o600)

    with pytest.raises(systemd.OperationsSystemdError, match="no longer matches"):
        installer.install(plan, plan["installConfirmation"])

    config.backup_credential.write_bytes(b"encrypted-backup-credential")

    def changed_storage(**values: object) -> dict[str, str]:
        return {
            "destination": str(values["destination"]),
            "mountpoint": str(values["mountpoint"]),
            "filesystem": "cifs",
            "source": "server:/changed",
            "deviceId": "21",
        }

    with pytest.raises(systemd.OperationsSystemdError, match="no longer matches"):
        _installer(
            config,
            layout,
            tools,
            calls,
            storage_verifier=changed_storage,
        ).install(plan, plan["installConfirmation"])


def test_plan_loader_rejects_duplicate_keys_and_invalid_config_types(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text('{"schemaVersion":1,"schemaVersion":1}\n')
    plan_path.chmod(0o400)
    with pytest.raises(systemd.OperationsSystemdError, match="repeats JSON key"):
        systemd._load_plan(plan_path)

    with pytest.raises(systemd.OperationsSystemdError, match="invalid value types"):
        systemd._config_from_payload(
            {
                "bundleRoot": 12,
                "backupDirectory": "/backup",
                "backupMountpoint": "/",
                "auditDirectory": "/audit",
                "auditMountpoint": "/audit",
                "backupCredential": "/credential-one",
                "auditCredential": "/credential-two",
                "backupKeep": True,
                "auditKeepDays": 365,
                "auditKeepMinimum": 12,
            }
        )


def test_plan_writer_creates_one_private_file_without_overwrite(tmp_path: Path) -> None:
    plan_path = (tmp_path / "new-plan.json").resolve()
    systemd._write_plan(plan_path, {"planId": "a" * 64})

    assert json.loads(plan_path.read_text()) == {"planId": "a" * 64}
    assert stat.S_IMODE(plan_path.stat().st_mode) == 0o400
    with pytest.raises(systemd.OperationsSystemdError, match="new private file"):
        systemd._write_plan(plan_path, {"planId": "b" * 64})
    assert json.loads(plan_path.read_text()) == {"planId": "a" * 64}


def test_default_rendering_matches_shipped_reference_units() -> None:
    config = systemd.OperationsConfig(
        bundle_root=Path("/opt/echo-os/deploy/appliance"),
        backup_directory=Path("/var/backups/echo-os"),
        backup_mountpoint=Path("/var/backups"),
        audit_directory=Path("/mnt/echo-audit-evidence"),
        audit_mountpoint=Path("/mnt/echo-audit-evidence"),
        backup_credential=Path("/etc/credstore.encrypted/echo-backup-passphrase"),
        audit_credential=Path("/etc/credstore.encrypted/echo-audit-export-passphrase"),
    )
    rendered = systemd._units(config)

    for name in systemd.UNIT_NAMES:
        assert (
            rendered[name]
            == (REPOSITORY / "deploy/appliance/systemd" / f"{name}.example").read_bytes()
        )


def test_remove_plan_binds_units_timer_states_and_preservation(tmp_path: Path) -> None:
    config, layout, tools, _calls = _fixture(tmp_path)
    rendered = _install_reference_units(config, layout)
    commands = CommandHarness()
    commands.enabled[systemd.TIMER_NAMES[0]] = True
    commands.active[systemd.TIMER_NAMES[0]] = True
    remover = _remover(layout, tools, command_runner=commands)

    plan = remover.plan()

    assert plan["kind"] == "echo.operations-systemd-remove-plan"
    assert plan["removeConfirmation"] == f"REMOVE ECHO OPERATIONS {plan['planId']}"
    assert plan["units"][systemd.UNIT_NAMES[0]]["sha256"] == systemd._sha256(
        rendered[systemd.UNIT_NAMES[0]]
    )
    assert plan["timerStates"][systemd.TIMER_NAMES[0]] == {
        "enabled": True,
        "active": True,
    }
    assert plan["recoveryState"] == {"enabled": False, "active": False}
    assert plan["preservation"]["preserved"] == [
        "encryptedCredentials",
        "deviceState",
        "NASData",
        "stateBackups",
        "auditEvidence",
    ]


def test_remove_requires_current_plan_confirmation_and_root(tmp_path: Path) -> None:
    config, layout, tools, _calls = _fixture(tmp_path)
    _install_reference_units(config, layout)
    commands = CommandHarness()
    remover = _remover(layout, tools, command_runner=commands)
    plan = remover.plan()

    with pytest.raises(systemd.OperationsSystemdError, match="confirmation"):
        remover.remove(plan, "REMOVE ECHO OPERATIONS wrong")
    with pytest.raises(systemd.OperationsSystemdError, match="requires root"):
        _remover(layout, tools, command_runner=commands, effective_uid=1000).remove(
            plan, plan["removeConfirmation"]
        )
    selected = layout.unit_directory / systemd.UNIT_NAMES[0]
    selected.write_bytes(selected.read_bytes() + b"# drift\n")
    selected.chmod(0o644)
    with pytest.raises(systemd.OperationsSystemdError, match="no longer matches"):
        remover.remove(plan, plan["removeConfirmation"])


def test_remove_disables_timers_removes_only_units_and_preserves_data(tmp_path: Path) -> None:
    config, layout, tools, _calls = _fixture(tmp_path)
    _install_reference_units(config, layout)
    commands = CommandHarness()
    for name in systemd.ENABLED_UNIT_NAMES:
        commands.enabled[name] = True
        commands.active[name] = name != systemd.RECOVERY_SERVICE_NAME
    remover = _remover(layout, tools, command_runner=commands)
    plan = remover.plan()

    report = remover.remove(plan, plan["removeConfirmation"])

    assert report["removed"] is True
    assert all(not (layout.unit_directory / name).exists() for name in systemd.UNIT_NAMES)
    assert commands.enabled == {name: False for name in systemd.ENABLED_UNIT_NAMES}
    assert commands.active == {name: False for name in systemd.ENABLED_UNIT_NAMES}
    assert report["recoveryServiceDisabled"] == systemd.RECOVERY_SERVICE_NAME
    assert config.backup_credential.read_bytes() == b"encrypted-backup-credential"
    assert config.audit_credential.read_bytes() == b"encrypted-audit-credential"
    assert (config.bundle_root / "data").is_dir()
    assert config.backup_directory.is_dir()
    assert config.audit_directory.is_dir()


def test_remove_failure_restores_units_modes_and_timer_states(tmp_path: Path) -> None:
    config, layout, tools, _calls = _fixture(tmp_path)
    rendered = _install_reference_units(config, layout)
    commands = CommandHarness()
    commands.enabled[systemd.TIMER_NAMES[0]] = True
    commands.active[systemd.TIMER_NAMES[1]] = True
    remover = _remover(layout, tools, command_runner=commands)
    plan = remover.plan()
    commands.fail_daemon_reload_count = 1

    with pytest.raises(systemd.OperationsSystemdError, match="managed units were restored"):
        remover.remove(plan, plan["removeConfirmation"])

    for name, payload in rendered.items():
        path = layout.unit_directory / name
        assert path.read_bytes() == payload
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert commands.enabled[systemd.TIMER_NAMES[0]] is True
    assert commands.active[systemd.TIMER_NAMES[0]] is False
    assert commands.enabled[systemd.TIMER_NAMES[1]] is False
    assert commands.active[systemd.TIMER_NAMES[1]] is True


def test_remove_reports_incomplete_rollback(tmp_path: Path) -> None:
    config, layout, tools, _calls = _fixture(tmp_path)
    _install_reference_units(config, layout)
    commands = CommandHarness()
    remover = _remover(layout, tools, command_runner=commands)
    plan = remover.plan()
    commands.fail_daemon_reload_count = 2

    with pytest.raises(systemd.OperationsSystemdError, match="rollback was incomplete"):
        remover.remove(plan, plan["removeConfirmation"])


def test_native_systemd_verifier_renders_all_units_for_real_parser(tmp_path: Path) -> None:
    tool = tmp_path / "systemd-analyze"
    tool.write_bytes(b"native-parser-fixture")
    tool.chmod(0o755)
    captured: dict[str, bytes] = {}

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "systemd 257 (257.8-1)\n", "")
        assert command[1] == "verify"
        for raw_path in command[2:]:
            path = Path(raw_path)
            assert path.is_file()
            captured[path.name] = path.read_bytes()
        return subprocess.CompletedProcess(command, 0, "", "")

    report = native_verify.verify_native_systemd_units(
        systemd_analyze=tool,
        command_runner=runner,
        trusted_uid=os.getuid(),
        system_name="Linux",
        os_release={"ID": "debian", "VERSION_ID": "13", "VERSION_CODENAME": "trixie"},
        require_os_id="debian",
        require_version_id="13",
        source_revision="d" * 40,
    )

    assert report["verified"] is True
    assert report["systemdVersion"] == "systemd 257 (257.8-1)"
    assert report["os"] == {"id": "debian", "versionId": "13", "codename": "trixie"}
    assert report["sourceRevision"] == "d" * 40
    assert set(captured) == {"docker.service", *systemd.UNIT_NAMES}
    assert set(report["units"]) == set(systemd.UNIT_NAMES)
    assert b"LoadCredentialEncrypted=" in captured["echo-state-backup.service"]


def test_native_systemd_verifier_fails_closed_on_parser_error(tmp_path: Path) -> None:
    tool = tmp_path / "systemd-analyze"
    tool.write_bytes(b"native-parser-fixture")
    tool.chmod(0o755)

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "systemd 257\n", "")
        return subprocess.CompletedProcess(command, 1, "", "bad unit setting")

    with pytest.raises(
        native_verify.NativeSystemdVerificationError,
        match="native systemd rejected generated units: bad unit setting",
    ):
        native_verify.verify_native_systemd_units(
            systemd_analyze=tool,
            command_runner=runner,
            trusted_uid=os.getuid(),
            system_name="Linux",
            os_release={"ID": "debian", "VERSION_ID": "13"},
            source_revision="d" * 40,
        )


def test_native_systemd_verifier_rejects_wrong_debian_release(tmp_path: Path) -> None:
    tool = tmp_path / "systemd-analyze"
    tool.write_bytes(b"native-parser-fixture")
    tool.chmod(0o755)

    with pytest.raises(
        native_verify.NativeSystemdVerificationError,
        match="host version does not match",
    ):
        native_verify.verify_native_systemd_units(
            systemd_analyze=tool,
            trusted_uid=os.getuid(),
            system_name="Linux",
            os_release={"ID": "debian", "VERSION_ID": "12"},
            require_os_id="debian",
            require_version_id="13",
            source_revision="d" * 40,
        )
