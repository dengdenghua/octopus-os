from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from runtime.adapters.scheduler.cron import CronExpression
from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.process.paths import app_paths
from runtime.safety.evolution.governance_audit import (
    append_governance_audit_event,
    export_governance_audit_bundle,
)

_CONFIG_SCHEMA = "echo.governance_audit_rotation_config.v1"
_STATUS_SCHEMA = "echo.governance_audit_rotation_status.v1"
_RUN_SCHEMA = "echo.governance_audit_rotation_run.v1"
_DEFAULT_CRON = "0 2 * * *"
_DEFAULT_RETENTION_COUNT = 30


def configure_governance_audit_rotation(
    *,
    enabled: bool,
    cron_expression: str = _DEFAULT_CRON,
    retention_count: int = _DEFAULT_RETENTION_COUNT,
    actor: str = "local_operator",
    audit_path: str | Path | None = None,
    config_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist a safe, shell-free schedule for governance audit exports."""

    expression = str(cron_expression or "").strip()
    CronExpression.parse(expression)
    keep = int(retention_count)
    if keep < 1 or keep > 3650:
        raise ValueError("retention_count must be between 1 and 3650")
    current = _utc(now)
    audit = _audit_path(audit_path)
    target = _config_path(audit, config_path)
    previous = _read_dict(target)
    configured_at = _iso(current)
    payload = {
        "schema": _CONFIG_SCHEMA,
        "enabled": bool(enabled),
        "cron_expression": expression,
        "timezone": "UTC",
        "retention_count": keep,
        "configured_at": configured_at,
        "configured_by": str(actor or "local_operator")[:120],
        "previous_configured_at": str(previous.get("configured_at") or ""),
    }
    atomic_write_json(target, payload)
    append_governance_audit_event(
        event_type="governance_audit_rotation_config",
        target="governance_audit_export",
        status="enabled" if enabled else "disabled",
        artifact={
            "config_path": str(target),
            "cron_expression": expression,
            "retention_count": keep,
        },
        decision_context={
            "schema": "echo.governance_audit_rotation_config_context.v1",
            "actor": payload["configured_by"],
            "shell_execution": False,
        },
        audit_path=audit,
        now=current,
    )
    return payload


def governance_audit_rotation_status(
    *,
    audit_path: str | Path | None = None,
    config_path: str | Path | None = None,
    export_dir: str | Path | None = None,
    state_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    audit = _audit_path(audit_path)
    config_target = _config_path(audit, config_path)
    export_target = _export_dir(audit, export_dir)
    state_target = _state_path(audit, state_path)
    config = _config(_read_dict(config_target))
    state = _read_dict(state_target)
    current = _utc(now)
    next_run = _next_run(config, state, current)
    exports = _export_files(export_target)
    return {
        "schema": _STATUS_SCHEMA,
        "enabled": config["enabled"],
        "due": bool(config["enabled"] and next_run is not None and next_run <= current),
        "checked_at": _iso(current),
        "next_run_at": _iso(next_run) if next_run is not None else None,
        "config_path": str(config_target),
        "state_path": str(state_target),
        "export_dir": str(export_target),
        "export_count": len(exports),
        "latest_export_path": str(exports[-1]) if exports else None,
        "config": config,
        "state": state,
    }


def run_due_governance_audit_rotation(
    *,
    force: bool = False,
    actor: str = "governance_scheduler",
    audit_path: str | Path | None = None,
    audit_chain_path: str | Path | None = None,
    config_path: str | Path | None = None,
    export_dir: str | Path | None = None,
    state_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a verified export when due and rotate old bundles by policy."""

    current = _utc(now)
    audit = _audit_path(audit_path)
    config_target = _config_path(audit, config_path)
    export_target = _export_dir(audit, export_dir)
    state_target = _state_path(audit, state_path)
    status = governance_audit_rotation_status(
        audit_path=audit,
        config_path=config_target,
        export_dir=export_target,
        state_path=state_target,
        now=current,
    )
    if not status["enabled"] and not force:
        return _skipped_run(status, reason="rotation_disabled")
    if not status["due"] and not force:
        return _skipped_run(status, reason="not_due")

    bundle = export_governance_audit_bundle(
        audit_path=audit,
        audit_chain_path=audit_chain_path,
    )
    integrity = bundle.get("integrity") if isinstance(bundle.get("integrity"), dict) else {}
    if integrity.get("ok") is not True:
        error = str(integrity.get("error") or "governance audit integrity check failed")
        failed_state = {
            "schema": _STATUS_SCHEMA,
            "last_checked_at": _iso(current),
            "last_status": "integrity_failed",
            "last_error": error,
            "last_export_at": str(status["state"].get("last_export_at") or ""),
        }
        atomic_write_json(state_target, failed_state)
        return {
            "schema": _RUN_SCHEMA,
            "status": "failed",
            "created": False,
            "reason": "integrity_failed",
            "error": error,
            "integrity": integrity,
        }

    export_target.mkdir(parents=True, exist_ok=True)
    filename = _export_filename(current, str(bundle.get("audit_sha256") or ""))
    output_path = export_target / filename
    created = not output_path.exists()
    atomic_write_json(output_path, bundle)
    config = status["config"]
    pruned = _prune_exports(
        export_target,
        retention_count=int(config["retention_count"]),
    )
    receipt = append_governance_audit_event(
        event_type="governance_audit_export_rotation",
        target="governance_audit_export",
        status="exported",
        artifact={
            "path": str(output_path),
            "audit_sha256": bundle.get("audit_sha256"),
            "chain_sha256": bundle.get("chain_sha256"),
            "pruned_count": len(pruned),
        },
        decision_context={
            "schema": "echo.governance_audit_rotation_run_context.v1",
            "actor": str(actor or "governance_scheduler")[:120],
            "scheduled": not force,
            "cron_expression": config["cron_expression"],
            "retention_count": config["retention_count"],
        },
        audit_path=audit,
        now=current,
    )
    state = {
        "schema": _STATUS_SCHEMA,
        "last_checked_at": _iso(current),
        "last_export_at": _iso(current),
        "last_status": "exported",
        "last_error": "",
        "last_export_path": str(output_path),
        "last_audit_sha256": bundle.get("audit_sha256"),
        "last_chain_sha256": bundle.get("chain_sha256"),
        "last_receipt_id": receipt.get("id"),
        "pruned_paths": pruned,
    }
    atomic_write_json(state_target, state)
    return {
        "schema": _RUN_SCHEMA,
        "status": "exported",
        "created": created,
        "reason": "forced" if force else "scheduled_due",
        "export_path": str(output_path),
        "pruned_paths": pruned,
        "integrity": integrity,
        "audit_sha256": bundle.get("audit_sha256"),
        "chain_sha256": bundle.get("chain_sha256"),
        "receipt_id": receipt.get("id"),
    }


def register_governance_audit_rotation_task(runner: Any) -> int:
    """Attach the policy-driven rotation tick to the persistent scheduler."""

    runner.add_periodic(
        "governance-audit-export-rotation",
        60.0,
        run_due_governance_audit_rotation,
        run_on_start=True,
    )
    return 1


def _config(raw: dict[str, Any]) -> dict[str, Any]:
    expression = str(raw.get("cron_expression") or _DEFAULT_CRON)
    try:
        CronExpression.parse(expression)
    except ValueError:
        expression = _DEFAULT_CRON
    retention = int(raw.get("retention_count") or _DEFAULT_RETENTION_COUNT)
    return {
        "schema": _CONFIG_SCHEMA,
        "enabled": raw.get("enabled") is True,
        "cron_expression": expression,
        "timezone": "UTC",
        "retention_count": min(3650, max(1, retention)),
        "configured_at": str(raw.get("configured_at") or ""),
        "configured_by": str(raw.get("configured_by") or ""),
    }


def _next_run(
    config: dict[str, Any],
    state: dict[str, Any],
    current: datetime,
) -> datetime | None:
    if not config["enabled"]:
        return None
    reference = _parse_iso(str(state.get("last_export_at") or ""))
    if reference is None:
        reference = _parse_iso(str(config.get("configured_at") or ""))
    if reference is None:
        reference = current - timedelta(minutes=1)
    return CronExpression.parse(str(config["cron_expression"])).next_after(reference)


def _skipped_run(status: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "schema": _RUN_SCHEMA,
        "status": "skipped",
        "created": False,
        "reason": reason,
        "next_run_at": status.get("next_run_at"),
    }


def _prune_exports(export_dir: Path, *, retention_count: int) -> list[str]:
    files = _export_files(export_dir)
    remove = files[: max(0, len(files) - retention_count)]
    pruned: list[str] = []
    for path in remove:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        pruned.append(str(path))
    return pruned


def _export_files(export_dir: Path) -> list[Path]:
    if not export_dir.is_dir():
        return []
    return sorted(
        path
        for path in export_dir.glob("governance-audit-*.json")
        if path.is_file() and not path.is_symlink()
    )


def _export_filename(current: datetime, audit_sha256: str) -> str:
    stamp = current.strftime("%Y%m%dT%H%M%SZ")
    digest = audit_sha256[:12] or "empty"
    return f"governance-audit-{stamp}-{digest}.json"


def _audit_path(value: str | Path | None) -> Path:
    return Path(value) if value is not None else app_paths().promotion_audit_path


def _config_path(audit_path: Path, value: str | Path | None) -> Path:
    return (
        Path(value) if value is not None else audit_path.with_name("governance_audit_rotation.json")
    )


def _state_path(audit_path: Path, value: str | Path | None) -> Path:
    return (
        Path(value)
        if value is not None
        else audit_path.with_name("governance_audit_rotation_state.json")
    )


def _export_dir(audit_path: Path, value: str | Path | None) -> Path:
    return Path(value) if value is not None else audit_path.parent / "governance_audit_exports"


def _read_dict(path: Path) -> dict[str, Any]:
    raw = read_json_with_backup(path, default=None)
    return raw if isinstance(raw, dict) else {}


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


__all__ = [
    "configure_governance_audit_rotation",
    "governance_audit_rotation_status",
    "register_governance_audit_rotation_task",
    "run_due_governance_audit_rotation",
]
