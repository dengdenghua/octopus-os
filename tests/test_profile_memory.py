from __future__ import annotations

from runtime.memory.users.profile import (
    extract_profile_memories,
    merge_profile_memories,
    render_profile_memories,
)


def test_extracts_explicit_english_memory() -> None:
    assert extract_profile_memories(
        "remember that I prefer concise Chinese answers",
    ) == ["I prefer concise Chinese answers"]


def test_extracts_explicit_chinese_memory() -> None:
    assert extract_profile_memories("记住：我喜欢中文回答") == ["我喜欢中文回答"]


def test_merge_dedupes_case_insensitively() -> None:
    assert merge_profile_memories(
        ["I prefer concise answers"],
        ["i prefer concise answers", "My project is echo-agent"],
    ) == ["I prefer concise answers", "My project is echo-agent"]


def test_render_profile_section() -> None:
    section = render_profile_memories(["I prefer concise answers"])
    assert "USER PROFILE MEMORY" in section
    assert "- I prefer concise answers" in section
