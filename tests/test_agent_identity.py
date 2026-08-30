from __future__ import annotations

import json

from runtime.execution.agents.identity import (
    IDENTITY_CODE_PREFIX,
    build_identity_profile,
    generate_identity_code,
    legacy_identity_code,
)


def test_legacy_identity_code_is_stable_and_non_semantic() -> None:
    first = legacy_identity_code("general")

    assert first == legacy_identity_code("general")
    assert first.startswith(f"{IDENTITY_CODE_PREFIX}-")
    assert "general" not in first.lower()


def test_generate_identity_code_avoids_existing_profiles(tmp_path) -> None:
    existing = f"{IDENTITY_CODE_PREFIX}-0000000000000000"
    agent_dir = tmp_path / "existing"
    agent_dir.mkdir()
    (agent_dir / "profile.jsonc").write_text(
        json.dumps({"identity_code": existing}),
        encoding="utf-8",
    )

    generated = generate_identity_code(tmp_path)

    assert generated.startswith(f"{IDENTITY_CODE_PREFIX}-")
    assert generated != existing


def test_identity_profile_preserves_code_and_merges_personality() -> None:
    code = f"{IDENTITY_CODE_PREFIX}-1234567890ABCDEF"

    profile = build_identity_profile(code, {"traits": ["curious"], "custom": "kept"})

    assert profile["code"] == code
    assert profile["immutable"] is True
    assert profile["personality_anchors"]["traits"] == ["curious"]
    assert profile["personality_anchors"]["custom"] == "kept"
    assert profile["personality_anchors"]["western_zodiac"] is None
