"""Canonical validation for identifiers shared across appliance boundaries."""

from __future__ import annotations

import re

_CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{12,64}")


def is_container_id(value: object) -> bool:
    """Return whether *value* is a bounded lowercase Docker container ID."""

    return isinstance(value, str) and _CONTAINER_ID_PATTERN.fullmatch(value) is not None


__all__ = ["is_container_id"]
