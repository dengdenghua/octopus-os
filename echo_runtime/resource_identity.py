"""Stable, non-path resource identities shared by the OS data surfaces.

The appliance photo service and the Agent image skills deliberately keep
separate SQLite files.  They still need a common way to refer to the same
library when a task moves from a search result to an image operation.  This
module provides that small bridge without exposing a host path or merging the
two stores.

The identifiers are deployment-local: the canonical library root is hashed,
and the asset id includes the relative locator plus the observed file
revision.  A changed file therefore cannot silently reuse an old reference.
The relative path remains a display/opening locator in the existing API; the
hashes are the correlation keys used by Agent tasks and receipts.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

PHOTO_SOURCE_KIND = "photo-library"
WORKSPACE_FILE_SOURCE_KIND = "workspace-file"
APPLIANCE_FILE_SOURCE_KIND = "appliance-file"
STORAGE_FILE_SOURCE_KIND = "storage-file"
_HEX = re.compile(r"^[0-9a-f]{64}$")


def canonical_library_root(root: str | Path | None) -> str:
    """Return the same canonical root representation used by image indexes."""

    return os.path.normcase(str(Path(root or ".").expanduser().resolve()))


def photo_library_id(root: str | Path | None) -> str:
    """Return a deployment-local id without disclosing the host path."""

    return hashlib.sha256(os.fsencode(canonical_library_root(root))).hexdigest()


def _library_id(value: str) -> str:
    clean = str(value or "").strip().lower()
    if _HEX.fullmatch(clean) is None:
        raise ValueError("invalid photo library identity")
    return clean


def _relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("invalid photo asset locator")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("invalid photo asset locator")
    return path.as_posix()


def photo_asset_revision(*, size: int, mtime_ns: int) -> str:
    """Hash the bounded file observation used to validate a photo reference."""

    if type(size) is not int or size < 0 or type(mtime_ns) is not int or mtime_ns < 0:
        raise ValueError("invalid photo asset revision")
    value = f"{size}\x00{mtime_ns}".encode("ascii")
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class PhotoAssetReference:
    """Host-produced correlation metadata for one photo result."""

    source_id: str
    asset_id: str
    revision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceKind": PHOTO_SOURCE_KIND,
            "sourceId": self.source_id,
            "assetId": self.asset_id,
            "assetRevision": self.revision,
        }


def photo_asset_reference(
    library_id: str,
    relative_path: str,
    *,
    size: int,
    mtime_ns: int,
) -> PhotoAssetReference:
    """Build a reference from a vetted relative path and file observation."""

    source_id = _library_id(library_id)
    locator = _relative_path(relative_path)
    revision = photo_asset_revision(size=size, mtime_ns=mtime_ns)
    digest = hashlib.sha256(
        f"{source_id}\x00{locator}\x00{revision}".encode()
    ).hexdigest()
    return PhotoAssetReference(source_id, digest, revision)


def photo_source(library_id: str, *, revision: str | None = None) -> dict[str, Any]:
    """Return the public source envelope shared by photo APIs and Agent tools."""

    source = {"kind": PHOTO_SOURCE_KIND, "id": _library_id(library_id)}
    if revision:
        source["revision"] = str(revision)
    return source


def workspace_file_resource_id(thread_id: str, area: str, relative_path: str) -> str:
    """Return a portable identity for a file in a thread workspace.

    The id is deliberately independent from the host absolute path.  Thread,
    area and relative locator are encoded as URL-safe segments so the server
    can validate and resolve the reference without maintaining a second
    catalogue.  Callers must still authorize the thread before resolving it.
    """

    clean_thread = str(thread_id or "").strip()
    clean_area = str(area or "").strip().lower()
    locator = _relative_path(relative_path)
    if not clean_thread or not re.fullmatch(r"[a-z0-9_-]+", clean_area):
        raise ValueError("invalid workspace file identity")

    def encode(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")

    return f"{WORKSPACE_FILE_SOURCE_KIND}:v1:{encode(clean_thread)}:{encode(clean_area)}:{encode(locator)}"


def parse_workspace_file_resource_id(value: str) -> tuple[str, str, str] | None:
    """Decode a workspace file identity, returning ``None`` for invalid ids."""

    if not isinstance(value, str) or not value.startswith(f"{WORKSPACE_FILE_SOURCE_KIND}:v1:"):
        return None
    parts = value.split(":")
    if len(parts) != 5 or parts[0] != WORKSPACE_FILE_SOURCE_KIND or parts[1] != "v1":
        return None

    def decode(segment: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
            return None
        try:
            padded = segment + "=" * (-len(segment) % 4)
            return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    thread_id, area, locator = (decode(segment) for segment in parts[2:])
    if not thread_id or not area or not locator:
        return None
    try:
        return thread_id, area, _relative_path(locator)
    except ValueError:
        return None


def appliance_file_resource_id(root: str | Path | None, relative_path: str) -> str:
    """Return a portable identity for a file managed by the appliance."""

    locator = _relative_path(relative_path)
    source_id = photo_library_id(root)
    encoded = base64.urlsafe_b64encode(locator.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{APPLIANCE_FILE_SOURCE_KIND}:v1:{source_id}:{encoded}"


def parse_appliance_file_resource_id(value: str) -> tuple[str, str] | None:
    """Decode an appliance file identity, returning ``(root_id, path)``."""

    if not isinstance(value, str) or not value.startswith(f"{APPLIANCE_FILE_SOURCE_KIND}:v1:"):
        return None
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != APPLIANCE_FILE_SOURCE_KIND or parts[1] != "v1":
        return None
    source_id, segment = parts[2:]
    if _HEX.fullmatch(source_id) is None or not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
        return None
    try:
        padded = segment + "=" * (-len(segment) % 4)
        locator = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        return source_id, _relative_path(locator)
    except (ValueError, UnicodeDecodeError):
        return None


def storage_file_resource_id(source_id: str, path: str) -> str:
    """Return a stable identity for a file indexed by echo-storage."""

    clean_source = str(source_id or "").strip()
    raw_path = str(path or "").replace("\\", "/").strip()
    is_rooted = raw_path.startswith("/")
    clean_path = raw_path.lstrip("/")
    if not clean_source or not clean_path:
        raise ValueError("invalid storage file identity")
    locator = _relative_path(clean_path)
    if is_rooted:
        locator = "/" + locator

    def encode(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")

    return f"{STORAGE_FILE_SOURCE_KIND}:v1:{encode(clean_source)}:{encode(locator)}"


def parse_storage_file_resource_id(value: str) -> tuple[str, str] | None:
    """Decode a storage identity, returning ``(source_id, path)``."""

    if not isinstance(value, str) or not value.startswith(f"{STORAGE_FILE_SOURCE_KIND}:v1:"):
        return None
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != STORAGE_FILE_SOURCE_KIND or parts[1] != "v1":
        return None

    def decode(segment: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
            return None
        try:
            padded = segment + "=" * (-len(segment) % 4)
            return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    source, path = (decode(segment) for segment in parts[2:])
    if not source or not path:
        return None
    try:
        if path.startswith("/"):
            return source, "/" + _relative_path(path[1:])
        return source, _relative_path(path)
    except ValueError:
        return None


__all__ = [
    "PHOTO_SOURCE_KIND",
    "APPLIANCE_FILE_SOURCE_KIND",
    "STORAGE_FILE_SOURCE_KIND",
    "WORKSPACE_FILE_SOURCE_KIND",
    "PhotoAssetReference",
    "canonical_library_root",
    "photo_asset_reference",
    "photo_asset_revision",
    "photo_library_id",
    "photo_source",
    "workspace_file_resource_id",
    "parse_workspace_file_resource_id",
    "appliance_file_resource_id",
    "parse_appliance_file_resource_id",
    "storage_file_resource_id",
    "parse_storage_file_resource_id",
]
