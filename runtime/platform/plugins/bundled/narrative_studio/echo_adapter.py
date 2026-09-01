"""Read-only importer for an existing ECHO universe repository."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from .models import WorldResource

ECHO_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "bible": (".md", ".yaml", ".yml", ".txt"),
    "characters": (".md", ".yaml", ".yml"),
    "factions": (".md", ".yaml", ".yml"),
    "locations": (".md", ".yaml", ".yml"),
    "technologies": (".md", ".yaml", ".yml"),
    "relationships": (".md", ".yaml", ".yml"),
    "stories": (".md", ".yaml", ".yml", ".txt"),
    "timeline": (".md", ".yaml", ".yml"),
}


class EchoUniverseAdapter:
    """Imports bounded text snapshots without writing to the source tree."""

    def __init__(
        self,
        source_root: Path | str | None,
        *,
        max_files: int = 500,
        max_chars_per_file: int = 24_000,
        max_bytes_per_file: int = 2 * 1024 * 1024,
    ) -> None:
        raw = str(source_root or "").strip()
        self.source_root = Path(raw).expanduser().resolve() if raw else None
        self.max_files = max(1, min(int(max_files), 5000))
        self.max_chars_per_file = max(256, min(int(max_chars_per_file), 200_000))
        self.max_bytes_per_file = max(1024, min(int(max_bytes_per_file), 20 * 1024 * 1024))

    def probe(self) -> dict[str, Any]:
        inventory = {category: 0 for category in ECHO_COLLECTIONS}
        root = self.source_root
        if root is None:
            return self._unavailable("ECHO source_path is not configured", inventory)
        if not root.is_dir():
            return self._unavailable(
                "ECHO source_path does not exist or is not a directory", inventory
            )
        total = 0
        for category, suffixes in ECHO_COLLECTIONS.items():
            directory = root / category
            if not self._safe_directory(directory, root):
                continue
            count = sum(
                1
                for path in directory.rglob("*")
                if self._safe_file(path, root) and path.suffix.lower() in suffixes
            )
            inventory[category] = count
            total += count
        inventory["total_files"] = total
        if total == 0:
            return self._unavailable("ECHO source contains no supported universe files", inventory)
        return {
            "available": True,
            "reason": "",
            "source_root": str(root),
            "inventory": inventory,
        }

    def import_resources(self, *, include_content: bool = True) -> dict[str, Any]:
        probe = self.probe()
        if not probe["available"]:
            return {**probe, "resources": [], "truncated": False}
        assert self.source_root is not None
        resources: list[WorldResource] = []
        all_paths: list[tuple[str, Path]] = []
        for category, suffixes in ECHO_COLLECTIONS.items():
            directory = self.source_root / category
            if not self._safe_directory(directory, self.source_root):
                continue
            for path in sorted(directory.rglob("*")):
                if self._safe_file(path, self.source_root) and path.suffix.lower() in suffixes:
                    all_paths.append((category, path))
        truncated = len(all_paths) > self.max_files
        skipped_oversize = 0
        for category, path in all_paths[: self.max_files]:
            raw = self._read_bounded(path)
            if raw is None:
                skipped_oversize += 1
                continue
            text = ""
            file_truncated = False
            if include_content:
                decoded = raw.decode("utf-8", errors="replace")
                file_truncated = len(decoded) > self.max_chars_per_file
                text = decoded[: self.max_chars_per_file]
            media_type = mimetypes.guess_type(path.name)[0] or "text/plain"
            resources.append(
                WorldResource(
                    category=category,
                    relative_path=path.relative_to(self.source_root).as_posix(),
                    sha256=hashlib.sha256(raw).hexdigest(),
                    media_type=media_type,
                    excerpt=text,
                    truncated=file_truncated,
                )
            )
        return {
            **probe,
            "resources": resources,
            "truncated": truncated,
            "skipped_oversize": skipped_oversize,
        }

    @staticmethod
    def _safe_directory(path: Path, root: Path) -> bool:
        if path.is_symlink() or not path.is_dir():
            return False
        try:
            return path.resolve().is_relative_to(root.resolve())
        except OSError:
            return False

    @staticmethod
    def _safe_file(path: Path, root: Path) -> bool:
        if path.is_symlink() or not path.is_file():
            return False
        try:
            return path.resolve().is_relative_to(root.resolve())
        except OSError:
            return False

    def _read_bounded(self, path: Path) -> bytes | None:
        """Read at most max+1 bytes, closing the stat/read race for growing files."""
        with path.open("rb") as handle:
            raw = handle.read(self.max_bytes_per_file + 1)
        return None if len(raw) > self.max_bytes_per_file else raw

    def _unavailable(self, reason: str, inventory: dict[str, int]) -> dict[str, Any]:
        inventory["total_files"] = sum(inventory.values())
        return {
            "available": False,
            "reason": reason,
            "source_root": str(self.source_root) if self.source_root else "",
            "inventory": inventory,
        }


__all__ = ["ECHO_COLLECTIONS", "EchoUniverseAdapter"]
