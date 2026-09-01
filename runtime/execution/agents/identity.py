"""Stable identity metadata for built-in and user-created agents."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any

IDENTITY_CODE_PREFIX = "ECHO-AGT"


def legacy_identity_code(agent_id: str) -> str:
    """Stable fallback for pre-identity profiles without rewriting them."""

    legacy_namespace = "octo" + "pus-agent"
    digest = hashlib.sha256(f"{legacy_namespace}:{agent_id}".encode()).hexdigest()
    return f"{IDENTITY_CODE_PREFIX}-{digest[:16].upper()}"


def generate_identity_code(agents_root: Path | None = None) -> str:
    """Return a non-semantic, collision-resistant agent identity code.

    The code never includes the mutable display name or role. When an agents
    root is supplied we also reject the extremely unlikely collision with an
    existing profile before returning it.
    """

    existing: set[str] = set()
    if agents_root is not None and agents_root.is_dir():
        from runtime.platform.process.utils import parse_jsonc

        for profile_path in agents_root.glob("*/profile.jsonc"):
            try:
                profile = parse_jsonc(profile_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            code = str(profile.get("identity_code") or profile.get("did") or "").strip()
            if code:
                existing.add(code)

    while True:
        code = f"{IDENTITY_CODE_PREFIX}-{secrets.token_hex(8).upper()}"
        if code not in existing:
            return code


def default_personality_anchors() -> dict[str, Any]:
    """Safe defaults when a creator has not supplied birth/persona data."""

    return {
        "mode": "creator_defined",
        "western_zodiac": None,
        "chinese_zodiac": None,
        "five_elements": [],
        "bazi_archetype": None,
        "traits": [],
        "note": "待角色创建者补充；未提供出生资料时不推算真实八字。",
    }


def build_identity_profile(
    identity_code: str,
    personality_anchors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    anchors = default_personality_anchors()
    if personality_anchors:
        anchors.update(personality_anchors)
    return {
        "code": identity_code,
        "code_version": 1,
        "immutable": True,
        "personality_anchors": anchors,
    }
