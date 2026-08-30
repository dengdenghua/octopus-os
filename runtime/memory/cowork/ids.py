"""Validation helpers for cowork/collaboration storage keys."""

from __future__ import annotations

import re

MAX_COWORK_ID_LENGTH = 240
MAX_COWORK_ACTOR_LENGTH = 240
MAX_COWORK_DISPLAY_NAME_LENGTH = 256
MAX_COWORK_MESSAGE_TEXT_LENGTH = 65_536
MAX_COWORK_SEARCH_QUERY_LENGTH = 512

_SAFE_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,239}$")


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def require_cowork_id(value: object, *, label: str = "id") -> str:
    """Validate ids that become sqlite primary/index keys.

    These values are not used as filesystem paths, but keeping them as bounded
    slugs avoids projection drift when collaboration sessions, team rooms, and
    project tasks are folded into one path later.
    """
    text = str(value or "").strip()
    if not _SAFE_RECORD_ID_RE.fullmatch(text):
        raise ValueError(
            f"invalid {label}: use 1-{MAX_COWORK_ID_LENGTH} letters, numbers, "
            "dot, underscore, colon, @, or hyphen"
        )
    return text


def optional_cowork_id(value: object, *, label: str = "id") -> str:
    text = str(value or "").strip()
    return require_cowork_id(text, label=label) if text else ""


def normalize_actor_id(value: object, *, label: str = "actor") -> str:
    """Bound attribution ids without rejecting legacy provider-style actors."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_COWORK_ACTOR_LENGTH or _has_control_chars(text):
        raise ValueError(f"invalid {label}: must be non-control text up to 240 chars")
    return text


def normalize_display_name(value: object, *, label: str = "display_name") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_COWORK_DISPLAY_NAME_LENGTH or _has_control_chars(text):
        raise ValueError(f"invalid {label}: must be non-control text up to 256 chars")
    return text


def require_message_text(value: object, *, label: str = "text") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > MAX_COWORK_MESSAGE_TEXT_LENGTH or _has_control_chars(text):
        raise ValueError(f"invalid {label}: must be 1-65536 non-control chars")
    return text


def normalize_search_query(value: object) -> str:
    text = str(value or "").strip().lower()
    if _has_control_chars(text):
        return ""
    return text[:MAX_COWORK_SEARCH_QUERY_LENGTH]
