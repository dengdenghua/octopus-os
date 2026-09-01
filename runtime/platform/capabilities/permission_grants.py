"""Durable permission grants for signed marketplace capabilities.

The marketplace manifest says what a package may need.  This store records
what the local operator actually granted for that exact signed generation and
whether the capability is currently active.  Runtime gates consume this file;
catalog text alone never authorizes execution.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.io import JsonMutation, mutate_json_file, read_json_file
from runtime.platform.plugins.marketplace_package import MARKETPLACE_PERMISSIONS
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import tenant_scoped_path

PERMISSION_GRANT_SCHEMA = "echo.capability_permission_grants.v1"
PERMISSION_GRANT_STATE_FILE = app_paths().data_dir / "capabilities" / "permission-grants.json"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MAX_RECORDS = 2_000
_MAX_LIST = 64


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _clean_permissions(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)) or len(values) > _MAX_LIST:
        raise ValueError("capability permissions are invalid")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or value not in MARKETPLACE_PERMISSIONS:
            raise ValueError("capability permissions are invalid")
        if value not in normalized:
            normalized.append(value)
    return sorted(normalized)


def _clean_sources(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set, frozenset)) or len(values) > _MAX_LIST:
        raise ValueError("capability runtime sources are invalid")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("capability runtime sources are invalid")
        item = value.strip()
        if (
            not item
            or len(item) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
        ):
            raise ValueError("capability runtime sources are invalid")
        if item not in normalized:
            normalized.append(item)
    return sorted(normalized)


def _validate_payload(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != PERMISSION_GRANT_SCHEMA:
        raise RuntimeError("capability permission grant state is invalid")
    records = payload.get("records")
    if not isinstance(records, dict) or len(records) > _MAX_RECORDS:
        raise RuntimeError("capability permission grant state is invalid")
    for capability_id, record in records.items():
        if not isinstance(capability_id, str) or not _SAFE_ID.fullmatch(capability_id):
            raise RuntimeError("capability permission grant state is invalid")
        if not isinstance(record, dict):
            raise RuntimeError("capability permission grant state is invalid")
        if record.get("id") != capability_id:
            raise RuntimeError("capability permission grant state is invalid")
        try:
            required = _clean_permissions(record.get("required", []))
            granted = _clean_permissions(record.get("granted", []))
            _clean_sources(record.get("runtime_sources", []))
        except ValueError as exc:
            raise RuntimeError("capability permission grant state is invalid") from exc
        if not set(granted) <= set(required):
            raise RuntimeError("capability permission grant state is invalid")
        if record.get("kind") not in {"codex", "connector"}:
            raise RuntimeError("capability permission grant state is invalid")
        if not isinstance(record.get("installed"), bool) or not isinstance(
            record.get("active"), bool
        ):
            raise RuntimeError("capability permission grant state is invalid")
        if record["active"] and (not record["installed"] or set(required) != set(granted)):
            raise RuntimeError("capability permission grant state is invalid")
        digest = record.get("manifest_digest")
        if not isinstance(digest, str) or len(digest) > 128:
            raise RuntimeError("capability permission grant state is invalid")
        updated_at = record.get("updated_at")
        if not isinstance(updated_at, str) or not 1 <= len(updated_at) <= 64:
            raise RuntimeError("capability permission grant state is invalid")


def _default_payload() -> dict[str, Any]:
    return {"schema": PERMISSION_GRANT_SCHEMA, "records": {}}


class CapabilityPermissionStore:
    """Persist exact-generation grants and answer runtime authorization checks."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or PERMISSION_GRANT_STATE_FILE)

    def _effective_path(self) -> Path:
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        return tenant_scoped_path(self.path, current_capability_scope())

    @staticmethod
    def _read_path(path: Path) -> dict[str, Any]:
        return read_json_file(
            path,
            default_factory=_default_payload,
            validate=_validate_payload,
        )

    @staticmethod
    def _mutate_path(path: Path, operation: Any) -> Any:
        return mutate_json_file(
            path,
            default_factory=_default_payload,
            validate=_validate_payload,
            mutate=operation,
        )

    def _read(self) -> dict[str, Any]:
        return self._read_path(self._effective_path())

    def _mutate(self, operation: Any) -> Any:
        return self._mutate_path(self._effective_path(), operation)

    @staticmethod
    def _same_generation(first: Any, second: Any) -> bool:
        return bool(
            isinstance(first, dict)
            and isinstance(second, dict)
            and first.get("installed") is True
            and second.get("installed") is True
            and first.get("manifest_digest") == second.get("manifest_digest")
            and first.get("required") == second.get("required")
        )

    def _global_record(self, capability_id: str) -> dict[str, Any] | None:
        record = self._read_path(self.path)["records"].get(capability_id)
        return dict(record) if isinstance(record, dict) else None

    def get(self, capability_id: str) -> dict[str, Any] | None:
        record = self._read()["records"].get(capability_id)
        return dict(record) if isinstance(record, dict) else None

    def generation_current(self, capability_id: str) -> bool:
        record = self.get(capability_id)
        if self._effective_path() == self.path:
            return bool(isinstance(record, dict) and record.get("installed") is True)
        return self._same_generation(record, self._global_record(capability_id))

    def stage_principal(self, capability_id: str) -> dict[str, Any]:
        """Stage the current device generation for the active principal."""

        effective_path = self._effective_path()
        generation = self._global_record(capability_id)
        if effective_path == self.path:
            if not isinstance(generation, dict) or generation.get("installed") is not True:
                raise KeyError(f"capability permission record not found: {capability_id}")
            return generation
        if not isinstance(generation, dict) or generation.get("installed") is not True:
            raise KeyError(f"capability permission generation not found: {capability_id}")
        return self.stage(
            capability_id,
            kind=str(generation["kind"]),
            required=generation.get("required") or [],
            manifest_digest=str(generation.get("manifest_digest") or ""),
            runtime_sources=generation.get("runtime_sources") or [],
        )

    def stage(
        self,
        capability_id: str,
        *,
        kind: str,
        required: Any,
        manifest_digest: str = "",
        runtime_sources: Any = None,
    ) -> dict[str, Any]:
        if not _SAFE_ID.fullmatch(capability_id) or kind not in {"codex", "connector"}:
            raise ValueError("capability permission identity is invalid")
        permissions = _clean_permissions(required)
        sources = _clean_sources(runtime_sources)
        digest = str(manifest_digest or "").strip()
        if len(digest) > 128:
            raise ValueError("capability permission manifest digest is invalid")

        def update(payload: dict[str, Any]) -> JsonMutation[dict[str, Any]]:
            records = payload["records"]
            previous = records.get(capability_id)
            same_generation = bool(
                isinstance(previous, dict)
                and previous.get("manifest_digest") == digest
                and previous.get("required") == permissions
            )
            granted = list(previous.get("granted") or []) if same_generation else []
            record = {
                "id": capability_id,
                "kind": kind,
                "installed": True,
                "active": False,
                "required": permissions,
                "granted": granted,
                "manifest_digest": digest,
                "runtime_sources": sources,
                "updated_at": _now(),
            }
            records[capability_id] = record
            return JsonMutation(dict(record))

        effective_path = self._effective_path()
        if effective_path == self.path:
            return dict(self._mutate_path(self.path, update))

        # The base record is the device-wide installed generation. Principal
        # grants live in the hashed tenant partition. Updating the generation
        # makes every older principal record fail closed without enumerating or
        # rewriting other tenants' private state.
        global_before = self._read_path(self.path)["records"].get(capability_id)

        def update_generation(payload: dict[str, Any]) -> JsonMutation[None]:
            records = payload["records"]
            previous = records.get(capability_id)
            if (
                isinstance(previous, dict)
                and previous.get("installed") is True
                and previous.get("manifest_digest") == digest
                and previous.get("required") == permissions
            ):
                if previous.get("runtime_sources") == sources:
                    return JsonMutation(None, changed=False)
                previous["runtime_sources"] = sources
                previous["updated_at"] = _now()
                return JsonMutation(None)
            records[capability_id] = {
                "id": capability_id,
                "kind": kind,
                "installed": True,
                "active": False,
                "required": permissions,
                "granted": [],
                "manifest_digest": digest,
                "runtime_sources": sources,
                "updated_at": _now(),
            }
            return JsonMutation(None)

        self._mutate_path(self.path, update_generation)
        try:
            return dict(self._mutate_path(effective_path, update))
        except Exception:
            self._restore_path_record(self.path, capability_id, global_before)
            raise

    def grant(self, capability_id: str, permissions: Any) -> dict[str, Any]:
        requested = _clean_permissions(permissions)
        effective_path = self._effective_path()
        if effective_path != self.path:
            current = self.get(capability_id)
            generation = self._global_record(capability_id)
            if not self._same_generation(current, generation):
                raise PermissionError(f"capability permissions require review: {capability_id}")

        def update(payload: dict[str, Any]) -> JsonMutation[dict[str, Any]]:
            record = payload["records"].get(capability_id)
            if not isinstance(record, dict) or record.get("installed") is not True:
                raise KeyError(f"capability permission record not found: {capability_id}")
            required = list(record.get("required") or [])
            if requested != required:
                raise ValueError("granted permissions must exactly match signed requirements")
            record["granted"] = requested
            record["updated_at"] = _now()
            return JsonMutation(dict(record))

        return dict(self._mutate(update))

    def set_active(self, capability_id: str, active: bool) -> dict[str, Any]:
        effective_path = self._effective_path()
        if active and effective_path != self.path:
            current = self.get(capability_id)
            generation = self._global_record(capability_id)
            if not self._same_generation(current, generation):
                raise PermissionError(f"capability permissions require review: {capability_id}")

        def update(payload: dict[str, Any]) -> JsonMutation[dict[str, Any]]:
            record = payload["records"].get(capability_id)
            if not isinstance(record, dict) or record.get("installed") is not True:
                raise KeyError(f"capability permission record not found: {capability_id}")
            if active and set(record.get("required") or []) != set(record.get("granted") or []):
                raise PermissionError(f"capability permissions require review: {capability_id}")
            record["active"] = bool(active)
            record["updated_at"] = _now()
            return JsonMutation(dict(record))

        return dict(self._mutate(update))

    def mark_uninstalled(self, capability_id: str) -> None:
        def update(payload: dict[str, Any]) -> JsonMutation[None]:
            record = payload["records"].get(capability_id)
            if not isinstance(record, dict):
                return JsonMutation(None, changed=False)
            record.update(installed=False, active=False, granted=[], updated_at=_now())
            return JsonMutation(None)

        effective_path = self._effective_path()
        self._mutate_path(self.path, update)
        if effective_path != self.path:
            self._mutate_path(effective_path, update)

    def snapshot(self, capability_id: str) -> dict[str, Any]:
        """Capture both device generation and current-principal grant state."""

        effective_path = self._effective_path()
        global_record = self._read_path(self.path)["records"].get(capability_id)
        scoped_record = (
            self._read_path(effective_path)["records"].get(capability_id)
            if effective_path != self.path
            else global_record
        )
        return {
            "schema": "echo.capability_permission_snapshot.v1",
            "partitioned": effective_path != self.path,
            "global": dict(global_record) if isinstance(global_record, dict) else None,
            "scoped": dict(scoped_record) if isinstance(scoped_record, dict) else None,
        }

    @classmethod
    def _restore_path_record(
        cls,
        path: Path,
        capability_id: str,
        snapshot: Any,
    ) -> None:
        restored = dict(snapshot) if isinstance(snapshot, dict) else None
        if restored is not None:
            candidate = _default_payload()
            candidate["records"][capability_id] = restored
            _validate_payload(candidate)

        def update(payload: dict[str, Any]) -> JsonMutation[None]:
            records = payload["records"]
            if restored is None:
                if capability_id not in records:
                    return JsonMutation(None, changed=False)
                records.pop(capability_id, None)
            else:
                if records.get(capability_id) == restored:
                    return JsonMutation(None, changed=False)
                records[capability_id] = dict(restored)
            return JsonMutation(None)

        cls._mutate_path(path, update)

    def restore(self, capability_id: str, snapshot: Any) -> None:
        """Restore an exact transaction snapshot (or remove a new record)."""

        if not _SAFE_ID.fullmatch(capability_id):
            raise ValueError("capability permission identity is invalid")
        if isinstance(snapshot, dict) and snapshot.get("schema") == (
            "echo.capability_permission_snapshot.v1"
        ):
            partitioned = snapshot.get("partitioned")
            if not isinstance(partitioned, bool):
                raise ValueError("capability permission snapshot is invalid")
            effective_path = self._effective_path()
            if partitioned != (effective_path != self.path):
                raise ValueError("capability permission snapshot scope changed")
            global_before = self._read_path(self.path)["records"].get(capability_id)
            self._restore_path_record(self.path, capability_id, snapshot.get("global"))
            try:
                if partitioned:
                    self._restore_path_record(
                        effective_path,
                        capability_id,
                        snapshot.get("scoped"),
                    )
            except Exception:
                self._restore_path_record(self.path, capability_id, global_before)
                raise
            return
        restored: dict[str, Any] | None
        if snapshot is None:
            restored = None
        elif isinstance(snapshot, dict):
            restored = dict(snapshot)
            candidate = _default_payload()
            candidate["records"][capability_id] = restored
            _validate_payload(candidate)
        else:
            raise ValueError("capability permission snapshot is invalid")

        self._restore_path_record(self._effective_path(), capability_id, restored)

    def require_granted(
        self,
        capability_id: str,
        permissions: Any = (),
        *,
        require_active: bool = False,
    ) -> dict[str, Any]:
        record = self.get(capability_id)
        if record is None or record.get("installed") is not True:
            raise PermissionError(f"capability permission record unavailable: {capability_id}")
        if self._effective_path() != self.path and not self._same_generation(
            record,
            self._global_record(capability_id),
        ):
            raise PermissionError(f"capability permissions require review: {capability_id}")
        required = set(record.get("required") or [])
        granted = set(record.get("granted") or [])
        requested = set(_clean_permissions(permissions))
        if required != granted or not requested <= granted:
            raise PermissionError(f"capability permissions require review: {capability_id}")
        if require_active and record.get("active") is not True:
            raise PermissionError(f"capability is not active: {capability_id}")
        return record

    def runtime_allows(self, skill_id: str, trusted_source: str) -> tuple[bool, str | None]:
        try:
            records = self._read()["records"]
            effective_path = self._effective_path()
            generations = (
                self._read_path(self.path)["records"] if effective_path != self.path else records
            )
        except (RuntimeError, ValueError):
            if trusted_source.startswith(("plugin://", "mcp://")):
                return False, "capability permission state is invalid"
            return True, None
        matches: list[tuple[str, dict[str, Any] | None, dict[str, Any]]] = []
        plugin_id = ""
        if trusted_source.startswith("plugin://"):
            plugin_id = trusted_source.removeprefix("plugin://").split("/", 1)[0]
        for capability_id in set(records) | set(generations):
            record = records.get(capability_id)
            generation = generations.get(capability_id)
            identity = generation if isinstance(generation, dict) else record
            if not isinstance(identity, dict):
                continue
            sources = set(identity.get("runtime_sources") or [])
            if (
                capability_id == plugin_id
                or skill_id.startswith(f"{capability_id}__")
                or any(trusted_source.startswith(source) for source in sources)
            ):
                matches.append((capability_id, record, identity))
        if not matches:
            return True, None
        for capability_id, record, generation in sorted(
            matches,
            key=lambda item: (-len(item[0]), item[0]),
        ):
            if (
                not isinstance(record, dict)
                or not self._same_generation(record, generation)
                or record.get("installed") is not True
                or record.get("active") is not True
                or set(record.get("required") or []) != set(record.get("granted") or [])
            ):
                return False, f"capability permission denied: {capability_id}"
        return True, None


_store_override: contextvars.ContextVar[CapabilityPermissionStore | None] = contextvars.ContextVar(
    "capability_permission_store",
    default=None,
)


def current_capability_permission_store() -> CapabilityPermissionStore:
    return _store_override.get() or CapabilityPermissionStore()


@contextlib.contextmanager
def use_capability_permission_store(store: CapabilityPermissionStore) -> Iterator[None]:
    token = _store_override.set(store)
    try:
        yield
    finally:
        _store_override.reset(token)


def is_marketplace_skill_allowed(skill: Any) -> tuple[bool, str | None]:
    return current_capability_permission_store().runtime_allows(
        str(getattr(skill, "name", "") or ""),
        str(getattr(skill, "trusted_source", "") or ""),
    )


__all__ = [
    "CapabilityPermissionStore",
    "PERMISSION_GRANT_SCHEMA",
    "current_capability_permission_store",
    "is_marketplace_skill_allowed",
    "use_capability_permission_store",
]
