"""Cheap dependency evidence and controlled errors for optional media features.

Presence checks deliberately do not import codecs or claim a successful decode.
Optional modules are loaded only when the corresponding operation is requested.
"""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType
from typing import Any

_FEATURE_DEPENDENCIES = {
    "project_editing": (),
    "image_snapshot": (),  # Pillow is already loaded by the image renderer.
    "video_snapshot": ("av",),
    "video_export": ("av", "numpy"),
    "audio_analysis": ("av", "numpy"),
}


class OptionalMediaUnavailable(ValueError):
    def __init__(self, feature: str, dependency: str, *, load_failed: bool = False) -> None:
        super().__init__("The required media component is unavailable for this operation.")
        self.feature = feature
        self.dependency = dependency
        self.code = "dependency_load_failed" if load_failed else "optional_dependency_unavailable"

    def result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "code": self.code,
            "capability": self.feature,
            "unavailableDependencies": [self.dependency],
        }


def _dependency_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, OSError):
        return False


def media_readiness() -> dict[str, Any]:
    capabilities: dict[str, dict[str, Any]] = {}
    for feature, dependencies in _FEATURE_DEPENDENCIES.items():
        missing = [name for name in dependencies if not _dependency_present(name)]
        capabilities[feature] = {
            "available": not missing,
            "state": (
                "unavailable" if missing else "dependencies_present" if dependencies else "ready"
            ),
            "check": "dependency_presence" if dependencies else "loaded_code",
            "runtimeVerified": False,
            "missingDependencies": missing,
            "code": "optional_dependency_unavailable" if missing else None,
        }
    return {
        "state": (
            "partial"
            if any(not item["available"] for item in capabilities.values())
            else "dependencies_present"
        ),
        "capabilities": capabilities,
    }


def load_media_dependency(name: str, feature: str) -> ModuleType:
    """Import on demand and keep loader paths / native-library errors private."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise OptionalMediaUnavailable(feature, name) from exc
    except (ImportError, OSError) as exc:
        raise OptionalMediaUnavailable(feature, name, load_failed=True) from exc
