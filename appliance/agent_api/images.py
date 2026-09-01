"""Agent semantic-image compatibility surface consumed by Echo OS."""

from __future__ import annotations

import importlib
from typing import Any


def load_agent_image_index_module() -> Any | None:
    try:
        return importlib.import_module("runtime.memory.hemolymph.image_semantic_index")
    except (ImportError, ModuleNotFoundError):
        return None


__all__ = ["load_agent_image_index_module"]
