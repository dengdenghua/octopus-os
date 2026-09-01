from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.plugins.codex_discovery import discover_codex_plugins
from runtime.safety.evolution.plugin_migration_readiness import (
    compute_plugin_migration_readiness,
)

SCHEMA = "echo.plugin_lifecycle_transaction.v1"
HISTORY_SCHEMA = "echo.plugin_lifecycle_history.v1"
_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LOCK = threading.RLock()


def install_local_plugin(
    source_path: str | Path,
    *,
    plugin_root: str | Path,
    publisher_trust_store_path: str | Path | None = None,
    confirm_install: bool = False,
    allow_downgrade: bool = False,
    require_trusted_publisher: bool = False,
) -> dict[str, Any]:
    if not confirm_install:
        raise ValueError("confirm_install=true is required")
    from runtime.safety.sandboxing.sandbox import commercial_execution_mode

    # Shared/commercial processes never install executable plugin code without
    # a publisher identity anchored in the configured trust store.  Keeping
    # this at the lifecycle chokepoint covers local-path, registry, and future
    # callers instead of relying on every HTTP adapter to remember the flag.
    require_trusted_publisher = require_trusted_publisher or commercial_execution_mode()
    source = Path(source_path).expanduser().resolve(strict=True)
    root = Path(plugin_root).expanduser().resolve(strict=False)
    manifest = _manifest(source)
    plugin_id = str(manifest.get("name") or "").strip()
    version = str(manifest.get("version") or "").strip()
    if not _PLUGIN_ID.fullmatch(plugin_id):
        raise ValueError("plugin manifest name is not a safe plugin id")
    if not version:
        raise ValueError("plugin manifest version is required")
    _reject_symlinks(source)

    destination = (root / plugin_id).resolve(strict=False)
    try:
        destination.relative_to(root)
    except ValueError:
        raise ValueError("plugin destination escapes the configured root") from None
    if source == destination or root in source.parents:
        raise ValueError("source plugin must be outside the managed plugin root")

    transaction_id = uuid.uuid4().hex
    lifecycle_root = root / ".lifecycle"
    staging_container = lifecycle_root / "staging" / transaction_id
    staging = staging_container / plugin_id
    backup = lifecycle_root / "backups" / transaction_id / plugin_id
    history_path = lifecycle_root / "history.jsonl"

    with _LOCK:
        root.mkdir(parents=True, exist_ok=True)
        staging_container.mkdir(parents=True, exist_ok=True)
        previous_version = _installed_version(destination)
        operation = "upgrade" if destination.exists() else "install"
        if previous_version:
            comparison = _compare_versions(version, previous_version)
            if comparison == 0:
                raise ValueError(f"plugin {plugin_id} {version} is already installed")
            if comparison < 0 and not allow_downgrade:
                raise ValueError(
                    f"plugin downgrade {previous_version} -> {version} requires allow_downgrade=true"
                )

        try:
            shutil.copytree(source, staging)
            candidate = _discover_candidate(
                staging,
                publisher_trust_store_path=publisher_trust_store_path,
            )
            raw_smoke = candidate.get("smoke")
            smoke: dict[str, Any] = raw_smoke if isinstance(raw_smoke, dict) else {}
            raw_publisher = smoke.get("publisher_provenance")
            publisher: dict[str, Any] = raw_publisher if isinstance(raw_publisher, dict) else {}
            if require_trusted_publisher and not (
                publisher.get("verified") is True and publisher.get("trusted") is True
            ):
                reason = str(publisher.get("reason") or "publisher provenance is unavailable")
                raise ValueError("trusted publisher signature is required: " + reason)
            if smoke.get("ok") is not True:
                raise ValueError(
                    "plugin smoke gate failed: " + "; ".join(smoke.get("issues") or [])
                )
            raw_provenance = smoke.get("content_provenance")
            provenance: dict[str, Any] = raw_provenance if isinstance(raw_provenance, dict) else {}
            if provenance.get("complete") is not True:
                raise ValueError("plugin content provenance is incomplete")
            migration = compute_plugin_migration_readiness(plugins=[candidate])
            if operation == "upgrade" and migration.get("ready") is not True:
                blockers = (migration.get("plugins") or [{}])[0].get("blockers") or []
                raise ValueError("plugin migration gate failed: " + "; ".join(blockers))

            if destination.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(backup)
            try:
                staging.replace(destination)
            except Exception:
                if backup.exists() and not destination.exists():
                    backup.replace(destination)
                raise
        except Exception:
            shutil.rmtree(staging_container, ignore_errors=True)
            raise
        shutil.rmtree(staging_container, ignore_errors=True)

        record = {
            "schema": SCHEMA,
            "ts": datetime.now(UTC).isoformat(),
            "transaction_id": transaction_id,
            "plugin_id": plugin_id,
            "operation": operation,
            "status": "committed",
            "previous_version": previous_version,
            "version": version,
            "destination": str(destination),
            "backup": str(backup) if backup.exists() else "",
            "smoke_ok": True,
            "publisher_verified": bool(publisher.get("verified")),
            "migration_ready": bool(migration.get("ready")),
            "rollback_available": operation == "install" or backup.exists(),
        }
        _append_history(history_path, record)
        return record


def rollback_plugin_transaction(
    transaction_id: str,
    *,
    plugin_root: str | Path,
    confirm_rollback: bool = False,
) -> dict[str, Any]:
    if not confirm_rollback:
        raise ValueError("confirm_rollback=true is required")
    if not transaction_id.strip():
        raise ValueError("transaction_id is required")
    root = Path(plugin_root).expanduser().resolve(strict=False)
    history_path = root / ".lifecycle" / "history.jsonl"
    with _LOCK:
        transaction = next(
            (
                row
                for row in reversed(plugin_lifecycle_history(plugin_root=root, limit=1000)["items"])
                if row.get("transaction_id") == transaction_id
                and row.get("schema") == SCHEMA
                and row.get("status") == "committed"
            ),
            None,
        )
        if transaction is None:
            raise ValueError("committed plugin lifecycle transaction not found")
        if any(
            row.get("rolled_back_transaction_id") == transaction_id
            for row in plugin_lifecycle_history(plugin_root=root, limit=1000)["items"]
        ):
            raise ValueError("plugin lifecycle transaction is already rolled back")

        plugin_id = str(transaction["plugin_id"])
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise ValueError("plugin lifecycle history contains an unsafe plugin id")
        destination = (root / plugin_id).resolve(strict=False)
        try:
            destination.relative_to(root)
        except ValueError:
            raise ValueError("plugin rollback destination escapes the configured root") from None
        backup_raw = str(transaction.get("backup") or "")
        backup = Path(backup_raw).resolve(strict=False) if backup_raw else None
        if backup is not None:
            backup_root = (root / ".lifecycle" / "backups").resolve(strict=False)
            try:
                backup.relative_to(backup_root)
            except ValueError:
                raise ValueError(
                    "plugin lifecycle history contains an unsafe backup path"
                ) from None
        retained = root / ".lifecycle" / "replaced" / uuid.uuid4().hex / plugin_id
        if destination.exists():
            retained.parent.mkdir(parents=True, exist_ok=True)
            destination.replace(retained)
        try:
            if backup is not None:
                if not backup.is_dir():
                    raise ValueError("plugin rollback backup is missing")
                backup.replace(destination)
        except Exception:
            if retained.exists() and not destination.exists():
                retained.replace(destination)
            raise

        record = {
            "schema": SCHEMA,
            "ts": datetime.now(UTC).isoformat(),
            "transaction_id": uuid.uuid4().hex,
            "rolled_back_transaction_id": transaction_id,
            "plugin_id": plugin_id,
            "operation": "rollback",
            "status": "rolled_back",
            "restored_version": str(transaction.get("previous_version") or ""),
            "removed_version": str(transaction.get("version") or ""),
            "destination": str(destination),
        }
        _append_history(history_path, record)
        return record


def plugin_lifecycle_history(
    *,
    plugin_root: str | Path,
    limit: int = 100,
) -> dict[str, Any]:
    path = Path(plugin_root).expanduser().resolve(strict=False) / ".lifecycle" / "history.jsonl"
    rows: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("schema") == SCHEMA:
                rows.append(row)
    selected = rows[-max(1, min(int(limit), 1000)) :]
    return {"schema": HISTORY_SCHEMA, "total": len(rows), "items": selected}


def _manifest(source: Path) -> dict[str, Any]:
    if not source.is_dir():
        raise ValueError("plugin source path must be a directory")
    path = source / ".codex-plugin" / "plugin.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("plugin source has no valid .codex-plugin/plugin.json") from exc
    if not isinstance(payload, dict):
        raise ValueError("plugin manifest must be a JSON object")
    return payload


def _reject_symlinks(source: Path) -> None:
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("plugin source must not contain symbolic links")


def _discover_candidate(
    staging: Path,
    *,
    publisher_trust_store_path: str | Path | None,
) -> dict[str, Any]:
    plugins = discover_codex_plugins(
        [staging.parent],
        publisher_trust_store_path=publisher_trust_store_path,
    )
    if len(plugins) != 1:
        raise ValueError("staged plugin could not be uniquely discovered")
    return plugins[0]


def _installed_version(destination: Path) -> str:
    if not destination.exists():
        return ""
    return str(_manifest(destination).get("version") or "").strip()


def _compare_versions(left: str, right: str) -> int:
    def parts(value: str) -> tuple[tuple[int, Any], ...]:
        return tuple(
            (0, int(token)) if token.isdigit() else (1, token.lower())
            for token in re.split(r"[.+-]", value)
            if token != ""
        )

    return (parts(left) > parts(right)) - (parts(left) < parts(right))


def _append_history(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


__all__ = [
    "HISTORY_SCHEMA",
    "SCHEMA",
    "install_local_plugin",
    "plugin_lifecycle_history",
    "rollback_plugin_transaction",
]
