"""Small desktop-platform helpers used by the browser API router."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def browser_system_info(detect_browsers: Callable[[], Any]) -> dict[str, Any]:
    return {
        "system": {
            "os": platform.system(),
            "os_version": platform.version(),
            "os_release": platform.release(),
            "architecture": platform.machine() or platform.architecture()[0],
            "python_version": sys.version.split()[0],
        },
        "browsers": detect_browsers(),
    }


def open_extension_folder(extension_path: Path) -> dict[str, Any]:
    try:
        if os.name == "nt":
            os.startfile(str(extension_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(extension_path)])
        else:
            subprocess.Popen(["xdg-open", str(extension_path)])
    except (OSError, ValueError):  # noqa: BLE001 — opening the optional folder is best-effort
        pass
    return {"opened": True, "path": str(extension_path)}


__all__ = ["browser_system_info", "open_extension_folder"]
