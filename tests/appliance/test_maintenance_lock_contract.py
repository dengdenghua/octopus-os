"""All state-changing host workflows serialize on one appliance maintenance lock."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parents[2]
_SCRIPTS = (
    "backup-state.sh",
    "export-audit-evidence.sh",
    "restore-state.sh",
    "upgrade-appliance.sh",
)


def test_all_host_maintenance_workflows_share_and_validate_one_lock() -> None:
    for name in _SCRIPTS:
        source = (_ROOT / "deploy" / "appliance" / name).read_text()
        assert "/run/lock/echo-os-appliance-maintenance.lock" in source
        assert "ECHO_MAINTENANCE_LOCK_FD=7" in source
        assert (
            'flock -n 7 || fail "another Echo maintenance operation is already running"' in source
        )
        assert "maintenance lock file is unsafe" in source
        assert "/proc/$$/fd/$inherited_maintenance_fd" in source


def test_upgrade_holds_the_common_lock_across_its_nested_verified_backup() -> None:
    source = (_ROOT / "deploy" / "appliance" / "upgrade-appliance.sh").read_text()
    assert source.index("export ECHO_MAINTENANCE_LOCK_FD=7") < source.index(
        '"$SCRIPT_DIR/backup-state.sh"'
    )


def test_all_host_workflows_keep_local_config_separate_from_release_identity() -> None:
    for name in (*_SCRIPTS, "start-tls.sh", "install-appliance.sh"):
        source = (_ROOT / "deploy" / "appliance" / name).read_text()
        assert "ECHO_APPLIANCE_ENV" in source
        assert "appliance.env" in source
        assert "ECHO_RELEASE_ENV" in source
        assert "echo-release.env" in source


def test_systemd_jobs_allow_only_the_lock_and_their_output_directory() -> None:
    systemd = _ROOT / "deploy" / "appliance" / "systemd"
    backup = (systemd / "echo-state-backup.service.example").read_text()
    audit = (systemd / "echo-audit-evidence.service.example").read_text()

    assert "ReadWritePaths=/run/lock" in backup
    assert "ReadWritePaths=/var/backups/echo-os" in backup
    assert "RequiresMountsFor=/var/backups" in backup
    assert "Environment=ECHO_BACKUP_MOUNTPOINT=/var/backups" in backup
    assert "ReadWritePaths=/run/lock" in audit
    assert "ReadWritePaths=/mnt/echo-audit-evidence" in audit
    assert "RequiresMountsFor=/mnt/echo-audit-evidence" in audit
    assert "Environment=ECHO_AUDIT_EXPORT_MOUNTPOINT=/mnt/echo-audit-evidence" in audit
