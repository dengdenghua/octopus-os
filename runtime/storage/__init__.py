"""Shared local storage providers used by the desktop and Agent surfaces."""

from .desktop_provider import (
    DesktopStorageProvider,
    desktop_file_entry,
    desktop_file_manager,
    desktop_files,
    desktop_search,
    desktop_source,
)

__all__ = [
    "DesktopStorageProvider",
    "desktop_file_entry",
    "desktop_file_manager",
    "desktop_files",
    "desktop_search",
    "desktop_source",
]
