"""Read-only local file provider shared by desktop UI and Agent search.

The provider is intentionally enabled only for the native desktop process.
The optional echo-storage sibling remains the owner of indexed search on
appliance deployments; this module is a bounded filename/content fallback for
an installed desktop that has no sibling service.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from appliance.files.manager import FileManager, PathEscape
from echo_runtime.resource_identity import photo_library_id, storage_file_resource_id

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp"}
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".ogg"}
_INTERNAL_DIRECTORY_PREFIXES = (".echo-upload-", ".echo-organize-")


@dataclass(frozen=True, slots=True)
class DesktopStorageProvider:
    manager: FileManager
    source_id: str


@lru_cache(maxsize=4)
def _provider_for_root(root: str) -> DesktopStorageProvider:
    manager = FileManager(root)
    return DesktopStorageProvider(manager, photo_library_id(manager.root))


def desktop_file_manager() -> DesktopStorageProvider | None:
    """Return the embedded desktop provider, or ``None`` for other hosts."""

    if os.environ.get("ECHO_DESKTOP") != "1":
        return None
    data_dir = os.environ.get("ECHO_DATA_DIR") or "."
    configured_root = os.environ.get("ECHO_DESKTOP_FILES_ROOT") or os.path.join(
        data_dir, "desktop-files"
    )
    try:
        root = str(Path(configured_root).expanduser().resolve())
        return _provider_for_root(root)
    except OSError:
        return None


def desktop_file_entry(entry: Any, source_id: str) -> dict[str, Any]:
    path = str(getattr(entry, "path", ""))
    resource_id = storage_file_resource_id(source_id, path)
    kind = str(getattr(entry, "kind", ""))
    return {
        "name": str(getattr(entry, "name", "")),
        "path": path or "/",
        "type": "dir" if kind == "dir" else "file",
        "size": None if kind == "dir" else int(getattr(entry, "size", 0)),
        "source_id": source_id,
        "resource_id": resource_id,
    }


def desktop_files(provider: DesktopStorageProvider, *, limit: int = 500) -> list[dict[str, Any]]:
    """Collect a bounded, symlink-free asset list for the embedded provider."""

    items: list[dict[str, Any]] = []
    root = provider.manager.root
    for base, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if name != ".echo-trash" and not name.startswith(_INTERNAL_DIRECTORY_PREFIXES)
        ]
        for name in filenames:
            if len(items) >= limit:
                return items
            path = Path(base) / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                stat = path.stat()
            except (OSError, ValueError):
                continue
            resource_id = storage_file_resource_id(provider.source_id, relative)
            suffix = path.suffix.lower()
            kind = (
                "image"
                if suffix in _IMAGE_EXTENSIONS
                else "video"
                if suffix in _VIDEO_EXTENSIONS
                else "document"
            )
            items.append(
                {
                    "asset_id": resource_id,
                    "source_id": provider.source_id,
                    "resource_id": resource_id,
                    "name": name,
                    "path": relative,
                    "extension": path.suffix,
                    "kind": kind,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "indexed": False,
                    "ai_labels": [],
                }
            )
    items.sort(key=lambda item: str(item["path"]).casefold())
    return items


def desktop_source(provider: DesktopStorageProvider) -> dict[str, Any]:
    return {
        "source_id": provider.source_id,
        "path": "/",
        "display_name": "本机文件",
        "recursive": True,
        "include_globs": [],
        "exclude_globs": [],
        "status": "ready",
        "file_count": len(desktop_files(provider, limit=5000)),
        "chunk_count": 0,
        "last_indexed_at": None,
        "created_at": "",
    }


def desktop_search(
    provider: DesktopStorageProvider, query: str, *, top_k: int = 8
) -> list[dict[str, Any]]:
    """Perform bounded filename/text matching with shared resource identities."""

    clean_query = str(query or "").strip().casefold()
    if not clean_query:
        return []
    assets = desktop_files(provider, limit=5000)
    hits: list[dict[str, Any]] = []
    for asset in assets:
        path = str(asset["path"])
        snippet = ""
        score = 1.0 if clean_query in path.casefold() else 0.0
        if asset["kind"] == "document":
            try:
                target = provider.manager.file_for_download(path)
                with target.open(encoding="utf-8", errors="ignore") as stream:
                    text = stream.read(100_000)
            except (OSError, UnicodeError, PathEscape, ValueError):
                text = ""
            if clean_query in text.casefold():
                score = max(score, 0.8)
                index = text.casefold().find(clean_query)
                snippet = text[max(0, index - 80) : index + 240].replace("\n", " ")
        if score:
            hits.append(
                {
                    "chunk_id": asset["resource_id"],
                    "source_id": provider.source_id,
                    "path": path,
                    "title": asset["name"],
                    "snippet": snippet or asset["name"],
                    "score": score,
                    "resource_id": asset["resource_id"],
                    "citation": {"resource_id": asset["resource_id"]},
                }
            )
    hits.sort(key=lambda hit: (-float(hit["score"]), str(hit["path"]).casefold()))
    return hits[: max(1, min(50, int(top_k)))]


__all__ = [
    "DesktopStorageProvider",
    "desktop_file_entry",
    "desktop_file_manager",
    "desktop_files",
    "desktop_search",
    "desktop_source",
]
