"""Response models and shared constants for the filesystem router.

Extracted from ``fs_router.py`` (god-file reduction). Holds the Pydantic
response schemas used by the ``/api/fs`` endpoints and the directory-name
ignore set applied when walking local trees.
"""

from __future__ import annotations

try:
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment, misc]


if FASTAPI_AVAILABLE:

    class FsTreeEntry(BaseModel):
        name: str
        path: str
        type: str  # "dir" | "file"
        depth: int
        size: int | None = None

    class FsTreeResponse(BaseModel):
        entries: list[FsTreeEntry]

    class FsRootsResponse(BaseModel):
        entries: list[FsTreeEntry]

    class FsReadResponse(BaseModel):
        path: str
        content: str
        lines: list[str]
        truncated: bool

    class FsWriteResponse(BaseModel):
        success: bool
        path: str
        bytes: int

    class FsImportDirectoryResponse(BaseModel):
        success: bool
        path: str
        files: int

    class FsPickDirectoryResponse(BaseModel):
        success: bool
        path: str | None = None
        canceled: bool = False
        error: str | None = None


TREE_IGNORED_DIRS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".echo",
    ".echo-browser-relay",
    ".echo-research",
    ".parcel-cache",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "logs",
    "node_modules",
    "playwright-report",
    "test-results",
    "tmp",
    "venv",
}
