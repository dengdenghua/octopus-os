from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from deploy.provision import host_migration


def _source_tree(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for unit, relative in host_migration.UNIT_SOURCES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[Unit]\nDescription={unit}\n", encoding="utf-8")
    config = root / host_migration.TIME_MACHINE_SOURCE
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(
        host_migration.TIME_MACHINE_HEADER + b"vfs objects = catia fruit streams_xattr\n"
    )
    wrapper = root / host_migration.REMOTE_WRAPPER_SOURCE
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("#!/usr/bin/python3\nprint('mount')\n", encoding="utf-8")
    return root


def _host_paths(tmp_path: Path) -> dict[str, Path]:
    samba_dir = tmp_path / "samba"
    samba_dir.mkdir(exist_ok=True)
    samba_config = samba_dir / "smb.conf"
    if not samba_config.exists():
        samba_config.write_text("[global]\n", encoding="utf-8")
    wrapper_dir = tmp_path / "lib" / "echo-os"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    etc_dir = tmp_path / "etc"
    etc_dir.mkdir(exist_ok=True)
    mnt_dir = tmp_path / "mnt"
    mnt_dir.mkdir(exist_ok=True)
    return {
        "samba_config_path": samba_config,
        "time_machine_config_path": samba_dir / "echo-os-time-machine.conf",
        "remote_wrapper_path": wrapper_dir / "rclone-backup-mount",
        "remote_credential_directory": etc_dir / "credstore.encrypted",
        "remote_mount_directory": mnt_dir / "echo-backup-remotes",
    }


def test_plan_reports_only_missing_packages_changed_units_and_disabled_services(
    tmp_path: Path,
) -> None:
    source = _source_tree(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    first_name, first_relative = next(iter(host_migration.UNIT_SOURCES.items()))
    (units / first_name).write_bytes((source / first_relative).read_bytes())

    plan = host_migration.plan_migration(
        **_host_paths(tmp_path),
        source_root=source,
        unit_directory=units,
        marker_path=tmp_path / "marker.json",
        trusted_uid=tmp_path.stat().st_uid,
        package_probe=lambda package: package != "nut-server",
        enabled_probe=lambda timer: timer == host_migration.TIMERS[0],
    )

    assert plan["operation"] == "migrate"
    assert plan["packages"]["missing"] == ["nut-server"]
    assert plan["enableTimers"] == list(host_migration.TIMERS[1:])
    assert plan["enableServices"] == list(host_migration.SERVICES)
    assert plan["restartServices"] == ["echo-appliance.service"]
    assert plan["units"][0]["operation"] == "none"
    assert all(item["operation"] == "install" for item in plan["units"][1:])
    assert len(plan["planId"]) == 64
    assert plan["rollback"]["newPackages"] == "retained"


def test_apply_installs_fixed_dependencies_units_and_enabled_services(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    marker = tmp_path / "host-migration.json"
    apt_get = tmp_path / "apt-get"
    systemctl = tmp_path / "systemctl"
    apt_get.write_text("binary", encoding="utf-8")
    systemctl.write_text("binary", encoding="utf-8")
    installed = {"smartmontools"}
    enabled: set[str] = set()
    calls: list[list[str]] = []

    def package_probe(package: str) -> bool:
        return package in installed

    def enabled_probe(timer: str) -> bool:
        return timer in enabled

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert "shell" not in kwargs
        if argv[0] == str(apt_get) and argv[1] == "install":
            installed.update(argv[4:])
        if argv[0] == str(systemctl) and argv[1:3] == ["enable", "--now"]:
            enabled.add(argv[3])
        return subprocess.CompletedProcess(argv, 0, "", "")

    plan = host_migration.plan_migration(
        **_host_paths(tmp_path),
        source_root=source,
        unit_directory=units,
        marker_path=marker,
        trusted_uid=tmp_path.stat().st_uid,
        package_probe=package_probe,
        enabled_probe=enabled_probe,
    )
    result = host_migration.apply_migration(
        plan["planId"],
        **_host_paths(tmp_path),
        source_root=source,
        unit_directory=units,
        marker_path=marker,
        trusted_uid=tmp_path.stat().st_uid,
        trusted_gid=tmp_path.stat().st_gid,
        package_probe=package_probe,
        enabled_probe=enabled_probe,
        active_probe=lambda: True,
        runner=run,
        apt_get=apt_get,
        systemctl=systemctl,
        testparm=systemctl,
        clock=lambda: datetime(2026, 9, 5, 1, 2, 3, tzinfo=UTC),
    )

    assert result["verified"] is True
    assert result["packagesInstalled"] == [
        "btrfs-progs",
        "fuse3",
        "hdparm",
        "nut-client",
        "nut-server",
        "rclone",
        "samba-vfs-modules",
    ]
    assert enabled == set(host_migration.ENABLED_UNITS)
    assert calls[0] == [str(apt_get), "update"]
    assert calls[1] == [
        str(apt_get),
        "install",
        "--yes",
        "--no-install-recommends",
        "btrfs-progs",
        "fuse3",
        "hdparm",
        "nut-client",
        "nut-server",
        "rclone",
        "samba-vfs-modules",
    ]
    assert [str(systemctl), "restart", "echo-appliance.service"] in calls
    for name, relative in host_migration.UNIT_SOURCES.items():
        assert (units / name).read_bytes() == (source / relative).read_bytes()
    host_paths = _host_paths(tmp_path)
    assert (
        host_paths["remote_wrapper_path"].read_bytes()
        == (source / host_migration.REMOTE_WRAPPER_SOURCE).read_bytes()
    )
    if host_migration.os.name == "posix":
        assert host_paths["remote_wrapper_path"].stat().st_mode & 0o777 == 0o755
    assert host_paths["remote_credential_directory"].is_dir()
    assert host_paths["remote_mount_directory"].is_dir()
    assert (
        host_paths["time_machine_config_path"].read_bytes()
        == (source / host_migration.TIME_MACHINE_SOURCE).read_bytes()
    )
    assert (
        host_migration._samba_include_state(
            host_paths["samba_config_path"].read_bytes(),
            host_paths["time_machine_config_path"],
        )
        == "present"
    )
    evidence = json.loads(marker.read_text(encoding="utf-8"))
    assert evidence["planId"] == plan["planId"]
    assert evidence["appliedAt"] == "2026-09-05T01:02:03Z"
    assert evidence["enabledServices"] == list(host_migration.SERVICES)


def test_apply_does_not_restart_units_that_were_already_enabled(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    for name, relative in host_migration.UNIT_SOURCES.items():
        (units / name).write_bytes((source / relative).read_bytes())
    marker = tmp_path / "host-migration.json"
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("binary", encoding="utf-8")
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    probes = {
        **_host_paths(tmp_path),
        "source_root": source,
        "unit_directory": units,
        "marker_path": marker,
        "trusted_uid": tmp_path.stat().st_uid,
        "package_probe": lambda _package: True,
        "enabled_probe": lambda _unit: True,
    }
    plan = host_migration.plan_migration(**probes)

    assert plan["enableTimers"] == []
    assert plan["enableServices"] == []
    result = host_migration.apply_migration(
        plan["planId"],
        **probes,
        trusted_gid=tmp_path.stat().st_gid,
        active_probe=lambda: True,
        runner=run,
        systemctl=systemctl,
        testparm=systemctl,
    )

    assert result["verified"] is True
    assert not any(argv[1:3] == ["enable", "--now"] for argv in calls)


def test_versioned_evidence_distinguishes_completed_migration(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    for name, relative in host_migration.UNIT_SOURCES.items():
        (units / name).write_bytes((source / relative).read_bytes())
    marker = tmp_path / "marker.json"
    probes = {
        **_host_paths(tmp_path),
        "source_root": source,
        "unit_directory": units,
        "marker_path": marker,
        "trusted_uid": tmp_path.stat().st_uid,
        "package_probe": lambda _package: True,
        "enabled_probe": lambda _timer: True,
    }

    assert host_migration.plan_migration(**probes)["operation"] == "migrate"
    marker.write_text(
        json.dumps(
            {
                "schemaVersion": host_migration.SCHEMA_VERSION,
                "migrationId": host_migration.MIGRATION_ID,
            }
        ),
        encoding="utf-8",
    )
    host_paths = _host_paths(tmp_path)
    host_paths["time_machine_config_path"].write_bytes(
        (source / host_migration.TIME_MACHINE_SOURCE).read_bytes()
    )
    host_paths["samba_config_path"].write_bytes(
        host_migration._insert_samba_include(
            host_paths["samba_config_path"].read_bytes(),
            host_paths["time_machine_config_path"],
        )
    )
    host_paths["remote_wrapper_path"].write_bytes(
        (source / host_migration.REMOTE_WRAPPER_SOURCE).read_bytes()
    )
    host_paths["remote_credential_directory"].mkdir()
    host_paths["remote_mount_directory"].mkdir()
    plan = host_migration.plan_migration(**probes)

    assert plan["operation"] == "none"
    assert plan["evidenceCurrent"] is True


def test_stale_plan_is_rejected_before_packages_or_units_change(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    plan = host_migration.plan_migration(
        **_host_paths(tmp_path),
        source_root=source,
        unit_directory=units,
        marker_path=tmp_path / "marker.json",
        trusted_uid=tmp_path.stat().st_uid,
        package_probe=lambda _package: True,
        enabled_probe=lambda _timer: True,
    )
    changed = source / next(iter(host_migration.UNIT_SOURCES.values()))
    changed.write_text("[Unit]\nDescription=changed\n", encoding="utf-8")

    with pytest.raises(host_migration.HostMigrationError, match="stale"):
        host_migration.apply_migration(
            plan["planId"],
            **_host_paths(tmp_path),
            source_root=source,
            unit_directory=units,
            marker_path=tmp_path / "marker.json",
            trusted_uid=tmp_path.stat().st_uid,
            trusted_gid=tmp_path.stat().st_gid,
            package_probe=lambda _package: True,
            enabled_probe=lambda _timer: True,
            active_probe=lambda: True,
        )
    assert not any(units.iterdir())


def test_systemd_failure_restores_original_units_and_enablement_state(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    originals: dict[str, bytes] = {}
    for name in host_migration.UNIT_SOURCES:
        payload = f"[Unit]\nDescription=old-{name}\n".encode()
        (units / name).write_bytes(payload)
        originals[name] = payload
    marker = tmp_path / "marker.json"
    host_paths = _host_paths(tmp_path)
    original_wrapper = b"#!/bin/sh\nexit 1\n"
    host_paths["remote_wrapper_path"].write_bytes(original_wrapper)
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("binary", encoding="utf-8")
    enabled: set[str] = set()
    enable_attempts = 0

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal enable_attempts
        if argv[1:3] == ["enable", "--now"]:
            enable_attempts += 1
            if enable_attempts == 1:
                enabled.add(argv[3])
            return subprocess.CompletedProcess(argv, 1 if enable_attempts == 2 else 0, "", "")
        if argv[1:3] == ["disable", "--now"]:
            enabled.discard(argv[3])
        return subprocess.CompletedProcess(argv, 0, "", "")

    def package_probe(_package: str) -> bool:
        return True

    def enabled_probe(timer: str) -> bool:
        return timer in enabled

    plan = host_migration.plan_migration(
        **host_paths,
        source_root=source,
        unit_directory=units,
        marker_path=marker,
        trusted_uid=tmp_path.stat().st_uid,
        package_probe=package_probe,
        enabled_probe=enabled_probe,
    )
    with pytest.raises(
        host_migration.HostMigrationError, match="units and Samba configuration were restored"
    ):
        host_migration.apply_migration(
            plan["planId"],
            **host_paths,
            source_root=source,
            unit_directory=units,
            marker_path=marker,
            trusted_uid=tmp_path.stat().st_uid,
            trusted_gid=tmp_path.stat().st_gid,
            package_probe=package_probe,
            enabled_probe=enabled_probe,
            active_probe=lambda: True,
            runner=run,
            systemctl=systemctl,
            testparm=systemctl,
        )
    assert {name: (units / name).read_bytes() for name in originals} == originals
    assert host_paths["samba_config_path"].read_text(encoding="utf-8") == "[global]\n"
    assert not host_paths["time_machine_config_path"].exists()
    assert host_paths["remote_wrapper_path"].read_bytes() == original_wrapper
    assert not host_paths["remote_credential_directory"].exists()
    assert not host_paths["remote_mount_directory"].exists()
    assert enabled == set()
    assert not marker.exists()


def test_plan_rejects_foreign_time_machine_config_and_misplaced_include(
    tmp_path: Path,
) -> None:
    source = _source_tree(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    host_paths = _host_paths(tmp_path)
    common = {
        **host_paths,
        "source_root": source,
        "unit_directory": units,
        "marker_path": tmp_path / "marker.json",
        "trusted_uid": tmp_path.stat().st_uid,
        "package_probe": lambda _package: True,
        "enabled_probe": lambda _timer: True,
    }

    host_paths["time_machine_config_path"].write_text("# foreign\n", encoding="utf-8")
    with pytest.raises(host_migration.HostMigrationError, match="not Echo-managed"):
        host_migration.plan_migration(**common)

    host_paths["time_machine_config_path"].unlink()
    host_paths["samba_config_path"].write_text(
        f"[media]\ninclude = {host_paths['time_machine_config_path']}\n",
        encoding="utf-8",
    )
    with pytest.raises(host_migration.HostMigrationError, match="outside Samba global"):
        host_migration.plan_migration(**common)


def test_symlink_source_or_target_is_rejected(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    first_name, first_relative = next(iter(host_migration.UNIT_SOURCES.items()))
    source_file = source / first_relative
    replacement = source_file.with_suffix(".real")
    source_file.rename(replacement)
    try:
        source_file.symlink_to(replacement)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(host_migration.HostMigrationError, match="unsafe migration source"):
        host_migration.plan_migration(
            **_host_paths(tmp_path),
            source_root=source,
            unit_directory=units,
            marker_path=tmp_path / "marker.json",
            trusted_uid=tmp_path.stat().st_uid,
            package_probe=lambda _package: True,
            enabled_probe=lambda _timer: True,
        )

    source_file.unlink()
    source_file.write_bytes(replacement.read_bytes())
    target = units / first_name
    target.symlink_to(replacement)
    with pytest.raises(host_migration.HostMigrationError, match="unsafe installed unit"):
        host_migration.plan_migration(
            **_host_paths(tmp_path),
            source_root=source,
            unit_directory=units,
            marker_path=tmp_path / "marker.json",
            trusted_uid=tmp_path.stat().st_uid,
            package_probe=lambda _package: True,
            enabled_probe=lambda _timer: True,
        )
