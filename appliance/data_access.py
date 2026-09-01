"""OMV-backed, fail-closed data-path authorization for Echo family members."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from appliance.omv_client import OmvControlRejected, OmvUnavailable

_PERMISSION_RANK = {"none": 0, "read": 1, "readWrite": 2}
_OMV_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


class DataAccessDenied(PermissionError):
    """The authenticated actor has no permission for the requested data path."""


class DataAccessUnavailable(RuntimeError):
    """The authoritative OMV permission projection could not be verified."""


class _AccountDirectory(Protocol):
    def omv_username_for_actor(self, actor: str) -> str | None: ...


class _OmvAccess(Protocol):
    def sharing_overview(self) -> dict[str, Any]: ...

    def share_privileges(self, share_uuid: str) -> list[dict[str, Any]]: ...


class DataAccessPolicy(Protocol):
    def scope_for_actor(self, actor: str) -> DataAccessScope: ...


def _path_parts(raw: str, *, allow_root: bool = True) -> tuple[str, ...]:
    if not isinstance(raw, str) or "\x00" in raw or "\\" in raw:
        raise DataAccessDenied("data path is not authorized")
    value = raw.strip().lstrip("/").rstrip("/")
    if not value or value == ".":
        if allow_root:
            return ()
        raise DataAccessDenied("data path is not authorized")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise DataAccessDenied("data path is not authorized")
    if any(part.startswith(".echo-") or part == ".echo-trash" for part in pure.parts):
        raise DataAccessDenied("data path is not authorized")
    return tuple(pure.parts)


@dataclass(frozen=True)
class DataPathRule:
    root: tuple[str, ...]
    permission: str


@dataclass(frozen=True)
class DataAccessScope:
    actor: str
    operator: bool
    rules: tuple[DataPathRule, ...] = ()
    root: Path | None = None

    @classmethod
    def unrestricted(cls, actor: str = "local:admin") -> DataAccessScope:
        return cls(actor=actor, operator=True)

    def _parts(self, path: str) -> tuple[str, ...]:
        parts = _path_parts(path)
        if self.root is None or not parts:
            return parts
        candidate = self.root.joinpath(*parts).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise DataAccessDenied("data path is not authorized")
        return () if candidate == self.root else candidate.relative_to(self.root).parts

    def _permission(self, path: tuple[str, ...]) -> str:
        if self.operator:
            return "readWrite"
        matches = [rule for rule in self.rules if path[: len(rule.root)] == rule.root]
        if not matches:
            return "none"
        longest = max(len(rule.root) for rule in matches)
        exact = [rule.permission for rule in matches if len(rule.root) == longest]
        # Duplicate OMV roots are rejected while building the scope; retaining
        # the conservative minimum here keeps a hand-built scope fail-closed.
        return min(exact, key=lambda value: _PERMISSION_RANK[value])

    def can_read(self, path: str) -> bool:
        if self.operator:
            return True
        try:
            parts = self._parts(path)
        except DataAccessDenied:
            return False
        return _PERMISSION_RANK[self._permission(parts)] >= _PERMISSION_RANK["read"]

    def can_write(self, path: str) -> bool:
        if self.operator:
            return True
        try:
            parts = self._parts(path)
        except DataAccessDenied:
            return False
        return self._permission(parts) == "readWrite"

    def can_list(self, path: str) -> bool:
        if self.operator:
            return True
        try:
            parts = self._parts(path)
        except DataAccessDenied:
            return False
        if self.can_read(path):
            return True
        return any(
            _PERMISSION_RANK[rule.permission] >= _PERMISSION_RANK["read"]
            and rule.root[: len(parts)] == parts
            for rule in self.rules
        )

    def visible(self, path: str) -> bool:
        return self.can_list(path)

    def require_read(self, path: str) -> None:
        if not self.can_read(path):
            raise DataAccessDenied("data path is not authorized")

    def require_write(self, path: str) -> None:
        if not self.can_write(path):
            raise DataAccessDenied("data path is not writable")

    def require_list(self, path: str) -> None:
        if not self.can_list(path):
            raise DataAccessDenied("data path is not authorized")

    def require_operator(self) -> None:
        if not self.operator:
            raise DataAccessDenied("device operator permission is required")

    @property
    def readable_roots(self) -> tuple[str, ...]:
        if self.operator:
            return ("",)
        return tuple(
            "/".join(rule.root)
            for rule in self.rules
            if _PERMISSION_RANK[rule.permission] >= _PERMISSION_RANK["read"]
        )


class OmvDataAccessPolicy:
    """Resolve current member permissions from bounded public OMV projections."""

    def __init__(
        self,
        *,
        accounts: _AccountDirectory,
        omv: _OmvAccess,
        root: str | Path | None = None,
        mounted_share_uuid: str | None = None,
        cache_seconds: float = 0.0,
    ) -> None:
        self._accounts = accounts
        self._omv = omv
        self._root = Path(root).expanduser().resolve() if root is not None else None
        if self._root is not None and not self._root.is_dir():
            raise OSError(f"NAS root is not a directory: {self._root}")
        mounted = str(mounted_share_uuid or "").strip().lower()
        if mounted and _OMV_UUID.fullmatch(mounted) is None:
            raise ValueError("mounted OMV shared folder UUID is invalid")
        self._mounted_share_uuid = mounted or None
        self._cache_seconds = max(0.0, min(float(cache_seconds), 5.0))
        self._cache: dict[str, tuple[float, DataAccessScope]] = {}
        self._lock = threading.Lock()

    def _rule_root(
        self,
        relative_path: str,
        *,
        require_directory: bool,
        mounted_prefix: tuple[str, ...] = (),
    ) -> tuple[str, ...] | None:
        lexical = _path_parts(relative_path, allow_root=False)
        if lexical[: len(mounted_prefix)] != mounted_prefix:
            return None
        lexical = lexical[len(mounted_prefix) :]
        if self._root is None:
            return lexical
        candidate = self._root.joinpath(*lexical).resolve()
        if (candidate != self._root and self._root not in candidate.parents) or (
            require_directory and not candidate.is_dir()
        ):
            raise DataAccessUnavailable(
                "OMV shared folder is not available in the mounted NAS root"
            )
        return candidate.relative_to(self._root).parts

    @staticmethod
    def _effective_permission(
        *,
        username: str,
        groups: set[str],
        privileges: list[dict[str, Any]],
    ) -> str:
        direct: str | None = None
        inherited: list[str] = []
        seen: set[tuple[str, str]] = set()
        for entry in privileges:
            if not isinstance(entry, dict) or set(entry) != {
                "type",
                "id",
                "name",
                "permission",
            }:
                raise DataAccessUnavailable("OMV returned an invalid permission projection")
            role_type = entry.get("type")
            name = entry.get("name")
            permission = entry.get("permission")
            identity = (str(role_type), str(name))
            if (
                role_type not in {"user", "group"}
                or not isinstance(name, str)
                or not name
                or identity in seen
                or permission not in {"inherit", "none", "read", "readWrite"}
            ):
                raise DataAccessUnavailable("OMV returned an invalid permission projection")
            seen.add(identity)
            if role_type == "user" and name == username:
                direct = str(permission)
            elif role_type == "group" and name in groups and permission != "inherit":
                inherited.append(str(permission))
        if direct is not None and direct != "inherit":
            return direct
        if not inherited:
            return "none"
        return max(inherited, key=lambda value: _PERMISSION_RANK[value])

    def _build_scope(self, actor: str) -> DataAccessScope:
        if actor == "local:admin":
            return DataAccessScope.unrestricted(actor)
        username = self._accounts.omv_username_for_actor(actor)
        if not username:
            raise DataAccessDenied("family data access is not authorized")
        try:
            overview = self._omv.sharing_overview()
        except (OmvControlRejected, OmvUnavailable, OSError) as exc:
            raise DataAccessUnavailable("OMV permission inventory is unavailable") from exc
        if not isinstance(overview, dict):
            raise DataAccessUnavailable("OMV permission inventory is invalid")
        users = overview.get("users")
        folders = overview.get("sharedFolders")
        if not isinstance(users, list) or not isinstance(folders, list):
            raise DataAccessUnavailable("OMV permission inventory is invalid")
        member = next(
            (entry for entry in users if isinstance(entry, dict) and entry.get("name") == username),
            None,
        )
        raw_groups = member.get("groups") if isinstance(member, dict) else None
        if not isinstance(raw_groups, list) or any(
            not isinstance(group, str) or not group for group in raw_groups
        ):
            raise DataAccessDenied("OMV family member is no longer available")
        groups = set(raw_groups)
        mounted_prefix: tuple[str, ...] = ()
        if self._mounted_share_uuid is not None:
            mounted = [
                folder
                for folder in folders
                if isinstance(folder, dict)
                and str(folder.get("uuid") or "").lower() == self._mounted_share_uuid
            ]
            if len(mounted) != 1:
                raise DataAccessUnavailable("mounted OMV shared folder is not enumerated")
            mounted_folder = mounted[0]
            if str(mounted_folder.get("status") or "").casefold() not in {"ok", "online"}:
                raise DataAccessUnavailable("mounted OMV shared folder is unavailable")
            raw_prefix = mounted_folder.get("relativePath")
            if not isinstance(raw_prefix, str):
                raise DataAccessUnavailable("mounted OMV shared folder path is invalid")
            try:
                mounted_prefix = _path_parts(raw_prefix, allow_root=False)
            except DataAccessDenied as exc:
                raise DataAccessUnavailable("mounted OMV shared folder path is invalid") from exc
        rules: list[DataPathRule] = []
        roots: set[tuple[str, ...]] = set()
        for folder in folders:
            if not isinstance(folder, dict) or str(folder.get("status") or "").casefold() not in {
                "ok",
                "online",
            }:
                continue
            share_uuid = folder.get("uuid")
            relative_path = folder.get("relativePath")
            if not isinstance(share_uuid, str) or not isinstance(relative_path, str):
                raise DataAccessUnavailable("OMV shared folder projection is invalid")
            try:
                lexical = _path_parts(relative_path, allow_root=False)
            except DataAccessDenied as exc:
                raise DataAccessUnavailable("OMV shared folder path is invalid") from exc
            if lexical[: len(mounted_prefix)] != mounted_prefix:
                continue
            try:
                privileges = self._omv.share_privileges(share_uuid)
            except DataAccessDenied as exc:
                raise DataAccessUnavailable("OMV shared folder path is invalid") from exc
            except (OmvControlRejected, OmvUnavailable, OSError) as exc:
                raise DataAccessUnavailable("OMV permission inventory is unavailable") from exc
            permission = self._effective_permission(
                username=username,
                groups=groups,
                privileges=privileges,
            )
            try:
                root = self._rule_root(
                    relative_path,
                    require_directory=permission != "none",
                    mounted_prefix=mounted_prefix,
                )
            except DataAccessDenied as exc:
                raise DataAccessUnavailable("OMV shared folder path is invalid") from exc
            if root is None:  # pragma: no cover - checked before the bridge call
                continue
            if root in roots:
                raise DataAccessUnavailable("OMV shared folder roots are ambiguous")
            roots.add(root)
            rules.append(DataPathRule(root=root, permission=permission))
        rules.sort(key=lambda rule: (len(rule.root), rule.root))
        return DataAccessScope(
            actor=actor,
            operator=False,
            rules=tuple(rules),
            root=self._root,
        )

    def scope_for_actor(self, actor: str) -> DataAccessScope:
        if actor == "local:admin":
            return DataAccessScope.unrestricted(actor)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(actor)
            if cached is not None and cached[0] >= now:
                return cached[1]
        scope = self._build_scope(actor)
        with self._lock:
            self._cache[actor] = (time.monotonic() + self._cache_seconds, scope)
        return scope


__all__ = [
    "DataAccessDenied",
    "DataAccessPolicy",
    "DataAccessScope",
    "DataAccessUnavailable",
    "DataPathRule",
    "OmvDataAccessPolicy",
]
