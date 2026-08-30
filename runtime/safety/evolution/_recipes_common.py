"""Shared schema constants and small utilities for browser/desktop repair recipes.

Extracted from ``browser_desktop_repair_recipes.py`` so that the recipe
cluster, API and evidence submodules can depend on these primitives without
importing the main module (which would create a circular import).
"""

from __future__ import annotations

from typing import Any

SCHEMA = "echo.browser_desktop_repair_recipes.v1"
RECIPE_SCHEMA = "echo.browser_desktop_repair_recipe.v1"
QUEUE_SCHEMA = "echo.browser_desktop_repair_recipe_queue.v1"
VERIFICATION_SCHEMA = "echo.browser_desktop_repair_recipe_verifications.v1"
EVIDENCE_SCHEMA = "echo.browser_desktop_repair_recipe_evidence.v1"
STALE_REJECTION_SCHEMA = "echo.browser_desktop_stale_replay_artifact_rejection.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unique_strings(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _priority_rank(priority: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2}.get(priority, 3)
