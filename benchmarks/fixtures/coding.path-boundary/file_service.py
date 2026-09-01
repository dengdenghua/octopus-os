"""Deliberately vulnerable file-service fixture."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote


class PathBoundaryError(ValueError):
    pass


class FileService:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def read_text(self, user_path: str) -> str:
        candidate = self.root / unquote(user_path)
        return candidate.read_text(encoding="utf-8")
