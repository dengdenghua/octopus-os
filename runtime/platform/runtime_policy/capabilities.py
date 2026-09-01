from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json
from runtime.platform.process.paths import app_paths

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _repo_root() -> Path:
    """Walk up from this file to find the repo root (has runtime/ + data/)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "runtime").is_dir():
            return parent
    from runtime.platform.process.paths import project_root

    return project_root()


def _store_path() -> Path:
    root = app_paths().data_dir
    root.mkdir(parents=True, exist_ok=True)
    return root / "capabilities.json"


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class Capabilities:
    browser_automation: bool = True
    desktop_automation: bool = True

    @classmethod
    def defaults(cls) -> Capabilities:
        return cls()

    def to_dict(self) -> dict[str, bool]:
        return {
            "browser_automation": self.browser_automation,
            "desktop_automation": self.desktop_automation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Capabilities:
        return cls(
            browser_automation=bool(data.get("browser_automation", True)),
            desktop_automation=bool(data.get("desktop_automation", True)),
        )

    def disabled_skill_groups(self) -> set[str]:
        out: set[str] = set()
        if not self.browser_automation:
            # Both groups are user-facing browser automation ·
            # ``browser`` is the headless Playwright pool and
            # ``browser_act`` is the live bridge into the desktop
            # Electron webview. Disabling one without the other left
            # ``live_browser_*`` skills reachable even after the user
            # opted out, which is exactly the privacy posture they
            # asked the toggle to enforce. Gate them together.
            out.add("browser")
            out.add("browser_act")
        if not self.desktop_automation:
            out.add("computer")
        return out


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _load_path(path: Path) -> Capabilities:
    if not path.is_file():
        return Capabilities.defaults()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            _log.warning(
                "capabilities.json: expected object at top level, got %s "
                "— falling back to defaults",
                type(data).__name__,
            )
            return Capabilities.defaults()
        return Capabilities.from_dict(data)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "capabilities.json load failed (%s: %s) — falling back to defaults",
            type(exc).__name__,
            exc,
        )
        return Capabilities.defaults()


def load() -> Capabilities:
    return _load_path(_store_path())


def _save_path(path: Path, caps: Capabilities) -> None:
    atomic_write_json(path, caps.to_dict())


def save(caps: Capabilities) -> None:
    _save_path(_store_path(), caps)
