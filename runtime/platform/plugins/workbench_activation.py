"""Persistent activation records for factory-seeded workbench plugins.

Factory seeds are immutable application resources.  Installing a seed writes
an activation descriptor under the writable app data directory; uninstalling
only changes that descriptor.  User-created work remains separate and is kept
by default, with an explicit recoverable-trash option.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.process.paths import app_paths

ACTIVATION_SCHEMA = "echo.workbench_activation.v1"
FACTORY_WORKBENCHES: dict[str, dict[str, str]] = {
    "narrative_studio": {
        "version": "0.2.0",
        "data_dir_name": "narrative-studio",
    }
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class WorkbenchActivationStore:
    """Read and mutate workbench activation descriptors.

    A missing descriptor means an available factory seed keeps the historical
    installed-and-enabled behaviour.  The first lifecycle mutation writes an
    explicit descriptor, so disable/uninstall remains durable across restarts.
    """

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        data_root: str | Path | None = None,
        factory_root: str | Path | None = None,
        trash_root: str | Path | None = None,
    ) -> None:
        paths = app_paths()
        self.root = Path(root or (paths.data_dir / "plugins" / "workbench")).resolve()
        self.data_root = Path(data_root or paths.data_dir).resolve()
        self.factory_root = Path(
            factory_root or (Path(__file__).resolve().parent / "bundled")
        ).resolve()
        self.trash_root = Path(trash_root or (self.root.parent / ".trash")).resolve()

    @staticmethod
    def is_factory(plugin_id: str) -> bool:
        return plugin_id in FACTORY_WORKBENCHES

    def factory_path(self, plugin_id: str) -> Path:
        self._require_factory(plugin_id)
        return self.factory_root / plugin_id

    def activation_path(self, plugin_id: str) -> Path:
        self._require_factory(plugin_id)
        return self.root / plugin_id / "activation.json"

    def data_path(self, plugin_id: str) -> Path:
        spec = self._require_factory(plugin_id)
        return self.data_root / spec["data_dir_name"]

    def state(self, plugin_id: str) -> dict[str, Any]:
        spec = self._require_factory(plugin_id)
        activation = self.activation_path(plugin_id)
        if activation.exists():
            try:
                raw = json.loads(activation.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # A present but corrupt primary descriptor is authoritative:
                # falling back to an older enabled backup could resurrect an
                # explicitly uninstalled plugin.
                raw = None
        else:
            raw = read_json_with_backup(activation, default=None)
        descriptor_exists = (
            activation.exists() or activation.with_suffix(activation.suffix + ".bak").exists()
        )
        error = None
        if not isinstance(raw, dict) or raw.get("schema") != ACTIVATION_SCHEMA:
            available = self.factory_path(plugin_id).is_dir()
            installed = available and not descriptor_exists
            enabled = installed
            updated_at = None
            explicit = descriptor_exists
            if descriptor_exists:
                error = "invalid_activation_descriptor"
        else:
            installed = bool(raw.get("installed"))
            enabled = installed and bool(raw.get("enabled"))
            updated_at = raw.get("updated_at")
            explicit = True
        return {
            "schema": ACTIVATION_SCHEMA,
            "plugin_id": plugin_id,
            "installed": installed,
            "enabled": enabled,
            "source": "factory",
            "version": spec["version"],
            "factory_path": str(self.factory_path(plugin_id)),
            "activation_path": str(activation),
            "data_path": str(self.data_path(plugin_id)),
            "updated_at": updated_at,
            "explicit": explicit,
            "error": error,
            "recoveries": self.recoveries(plugin_id),
        }

    def install(
        self,
        plugin_id: str,
        *,
        enabled: bool = True,
        restore_data: bool = False,
        recovery_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.factory_path(plugin_id).is_dir():
            raise KeyError(f"factory workbench is unavailable: {plugin_id}")
        previous = self.state(plugin_id)
        data_result: dict[str, Any] = {"status": "kept", "path": str(self.data_path(plugin_id))}
        if restore_data:
            data_result = self.restore(plugin_id, recovery_id=recovery_id)
        self._write(plugin_id, installed=True, enabled=enabled)
        result = self.state(plugin_id)
        result.update(
            {
                "ok": True,
                "already_exists": bool(previous["installed"]),
                "data": data_result,
            }
        )
        return result

    def enable(self, plugin_id: str) -> dict[str, Any]:
        state = self.state(plugin_id)
        if not state["installed"]:
            raise ValueError(f"workbench is not installed: {plugin_id}")
        self._write(plugin_id, installed=True, enabled=True)
        return {"ok": True, **self.state(plugin_id)}

    def disable(self, plugin_id: str) -> dict[str, Any]:
        state = self.state(plugin_id)
        if not state["installed"]:
            raise ValueError(f"workbench is not installed: {plugin_id}")
        self._write(plugin_id, installed=True, enabled=False)
        return {"ok": True, **self.state(plugin_id)}

    def uninstall(
        self,
        plugin_id: str,
        *,
        data_policy: str = "keep",
        confirm_data_move: bool = False,
    ) -> dict[str, Any]:
        previous = self.state(plugin_id)
        if not previous["installed"]:
            raise ValueError(f"workbench is not installed: {plugin_id}")
        if data_policy not in {"keep", "trash"}:
            raise ValueError("data_policy must be 'keep' or 'trash'")
        if data_policy == "trash" and not confirm_data_move:
            raise ValueError("confirm_data_move=true is required for data_policy=trash")

        data_result: dict[str, Any] = {
            "status": "kept",
            "path": str(self.data_path(plugin_id)),
        }
        # Persist the tombstone before moving optional data.  A process loss at
        # any later point must never make an explicitly uninstalled factory
        # seed come back to life on the next boot.
        self._write(plugin_id, installed=False, enabled=False)
        try:
            if data_policy == "trash":
                data_result = self._move_data_to_trash(plugin_id)
        except Exception:
            self.restore_state(plugin_id, previous)
            raise
        return {
            "ok": True,
            "uninstalled": True,
            **self.state(plugin_id),
            "data_policy": data_policy,
            "data": data_result,
        }

    def restore(self, plugin_id: str, *, recovery_id: str | None = None) -> dict[str, Any]:
        target = self.data_path(plugin_id)
        if target.is_symlink():
            raise ValueError(f"refusing to restore through a symlink: {target}")
        if target.exists():
            try:
                has_content = any(target.iterdir()) if target.is_dir() else True
            except OSError:
                has_content = True
            if has_content:
                raise FileExistsError(
                    f"live workbench data already exists; refusing to overwrite: {target}"
                )
            if target.is_dir():
                target.rmdir()

        recovery = self._select_recovery(plugin_id, recovery_id)
        if recovery is None:
            raise KeyError(f"no recoverable workbench data found: {plugin_id}")
        source = recovery / FACTORY_WORKBENCHES[plugin_id]["data_dir_name"]
        if source.is_symlink() or not source.is_dir():
            raise KeyError(f"recovery data is unavailable: {recovery.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        with contextlib.suppress(OSError):
            (recovery / "recovery.json").unlink()
        with contextlib.suppress(OSError):
            recovery.rmdir()
        return {
            "status": "restored",
            "recovery_id": recovery.name,
            "path": str(target),
        }

    def recoveries(self, plugin_id: str) -> list[dict[str, Any]]:
        self._require_factory(plugin_id)
        base = self.trash_root / plugin_id
        if not base.is_dir():
            return []
        values: list[dict[str, Any]] = []
        for recovery in sorted(base.iterdir(), reverse=True):
            if not recovery.is_dir():
                continue
            data_path = recovery / FACTORY_WORKBENCHES[plugin_id]["data_dir_name"]
            if data_path.is_symlink() or not data_path.is_dir():
                continue
            meta = read_json_with_backup(recovery / "recovery.json", default={})
            values.append(
                {
                    "recovery_id": recovery.name,
                    "created_at": meta.get("created_at") if isinstance(meta, dict) else None,
                    "path": str(data_path),
                }
            )
        return values

    def restore_state(self, plugin_id: str, state: dict[str, Any]) -> None:
        """Restore installed/enabled bits after a failed runtime transition."""

        if not state.get("explicit") and state.get("installed") and state.get("enabled"):
            activation = self.activation_path(plugin_id)
            with contextlib.suppress(OSError):
                activation.unlink()
            with contextlib.suppress(OSError):
                activation.with_suffix(activation.suffix + ".bak").unlink()
            with contextlib.suppress(OSError):
                activation.parent.rmdir()
            return
        self._write(
            plugin_id,
            installed=bool(state.get("installed")),
            enabled=bool(state.get("enabled")),
        )

    def _move_data_to_trash(self, plugin_id: str) -> dict[str, Any]:
        source = self.data_path(plugin_id)
        if not source.exists():
            return {"status": "absent", "path": str(source)}
        if source.is_symlink():
            raise ValueError(f"refusing to trash symlinked workbench data: {source}")
        recovery_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        recovery = self.trash_root / plugin_id / recovery_id
        recovery.mkdir(parents=True, exist_ok=False)
        target = recovery / FACTORY_WORKBENCHES[plugin_id]["data_dir_name"]
        try:
            source.replace(target)
            atomic_write_json(
                recovery / "recovery.json",
                {
                    "schema": ACTIVATION_SCHEMA,
                    "plugin_id": plugin_id,
                    "recovery_id": recovery_id,
                    "created_at": _utc_now(),
                    "original_path": str(source),
                },
                sort_keys=True,
            )
        except Exception:
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
            shutil.rmtree(recovery, ignore_errors=True)
            raise
        return {"status": "trashed", "recovery_id": recovery_id, "path": str(target)}

    def _select_recovery(self, plugin_id: str, recovery_id: str | None) -> Path | None:
        base = self.trash_root / plugin_id
        if recovery_id is not None:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", recovery_id):
                raise ValueError("invalid recovery_id")
            known = {row["recovery_id"] for row in self.recoveries(plugin_id)}
            return (base / recovery_id) if recovery_id in known else None
        rows = self.recoveries(plugin_id)
        return (base / rows[0]["recovery_id"]) if rows else None

    def _write(self, plugin_id: str, *, installed: bool, enabled: bool) -> None:
        spec = self._require_factory(plugin_id)
        path = self.activation_path(plugin_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            path,
            {
                "schema": ACTIVATION_SCHEMA,
                "plugin_id": plugin_id,
                "installed": installed,
                "enabled": bool(installed and enabled),
                "source": "factory",
                "version": spec["version"],
                "factory_path": str(self.factory_path(plugin_id)),
                "data_path": str(self.data_path(plugin_id)),
                "updated_at": _utc_now(),
            },
            sort_keys=True,
        )

    @staticmethod
    def _require_factory(plugin_id: str) -> dict[str, str]:
        try:
            return FACTORY_WORKBENCHES[plugin_id]
        except KeyError as exc:
            raise KeyError(f"unknown factory workbench: {plugin_id}") from exc


__all__ = ["ACTIVATION_SCHEMA", "FACTORY_WORKBENCHES", "WorkbenchActivationStore"]
