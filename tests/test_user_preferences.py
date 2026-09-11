"""Tests for runtime.memory.users.user_preferences._load_user_preferences."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.memory import user_preferences as up


@pytest.fixture
def prefs_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch the resolver to point at a tmp file and return that file path."""
    target = tmp_path / "user_preferences.json"
    monkeypatch.setattr(up, "preferences_path", lambda: target)
    return target


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_actor_returns_empty(prefs_file: Path) -> None:
    _write(prefs_file, {"default": {"indent": "4 spaces"}})
    # actor=None → only default block applied
    assert up._load_user_preferences(None) == {"indent": "4 spaces"}
    # empty/whitespace actor → still returns default only
    assert up._load_user_preferences("") == {"indent": "4 spaces"}
    assert up._load_user_preferences("   ") == {"indent": "4 spaces"}


def test_missing_actor_no_default(prefs_file: Path) -> None:
    _write(prefs_file, {"actor_x": {"foo": "bar"}})
    assert up._load_user_preferences(None) == {}


def test_no_file_returns_empty(prefs_file: Path) -> None:
    # File never created
    assert not prefs_file.exists()
    assert up._load_user_preferences("anyone") == {}
    assert up._load_user_preferences(None) == {}


def test_default_only_returns_default(prefs_file: Path) -> None:
    _write(
        prefs_file,
        {"default": {"indent": "4 spaces", "commit_footer": "no Co-Authored-By"}},
    )
    result = up._load_user_preferences("nonexistent_actor")
    assert result == {
        "indent": "4 spaces",
        "commit_footer": "no Co-Authored-By",
    }


def test_actor_merges_and_wins_on_conflict(prefs_file: Path) -> None:
    _write(
        prefs_file,
        {
            "default": {
                "indent": "4 spaces",
                "commit_footer": "no Co-Authored-By",
            },
            "alice": {
                "indent": "2 spaces",  # actor overrides default
                "language": "Chinese first",
            },
        },
    )
    result = up._load_user_preferences("alice")
    assert result == {
        "indent": "2 spaces",
        "commit_footer": "no Co-Authored-By",
        "language": "Chinese first",
    }


def test_malformed_json_returns_empty(prefs_file: Path) -> None:
    prefs_file.parent.mkdir(parents=True, exist_ok=True)
    prefs_file.write_text("{not valid json", encoding="utf-8")
    assert up._load_user_preferences("alice") == {}
    assert up._load_user_preferences(None) == {}


def test_actor_value_not_dict_is_ignored(prefs_file: Path) -> None:
    _write(
        prefs_file,
        {
            "default": {"indent": "4 spaces"},
            "bob": "this is not a dict",  # malformed actor block
        },
    )
    result = up._load_user_preferences("bob")
    # falls back to default only
    assert result == {"indent": "4 spaces"}


def test_top_level_not_dict_returns_empty(prefs_file: Path) -> None:
    prefs_file.parent.mkdir(parents=True, exist_ok=True)
    prefs_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert up._load_user_preferences("alice") == {}


def test_explicit_path_arg_overrides_resolver(tmp_path: Path) -> None:
    target = tmp_path / "alt.json"
    target.write_text(
        json.dumps({"default": {"indent": "tabs"}}),
        encoding="utf-8",
    )
    # resolver not patched — using path= kwarg directly
    result = up._load_user_preferences("anyone", path=target)
    assert result == {"indent": "tabs"}


def test_non_string_values_are_coerced(prefs_file: Path) -> None:
    _write(
        prefs_file,
        {
            "default": {
                "max_lines": 80,
                "strict": True,
                "indent": "4 spaces",
                "skip_me": None,
            },
        },
    )
    result = up._load_user_preferences(None)
    # None values are dropped; everything else coerced to str
    assert result == {
        "max_lines": "80",
        "strict": "True",
        "indent": "4 spaces",
    }
