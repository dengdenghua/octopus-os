"""Tests for path denylist (Marvis-style "不可读取文件夹")."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from runtime.safety.auth import path_denylist as pdn
from runtime.safety.auth.path_guard import check_path


@pytest.fixture(autouse=True)
def _clear_turn_denylist():
    # Clear ContextVar state between tests
    yield
    pdn._TURN_DENYLIST.set(())


@pytest.fixture
def tmp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "denylist.json"
    monkeypatch.setenv("ECHO_PATH_DENYLIST_PATH", str(state))
    return state


# ── basic file persistence ────────────────────────────────────


def test_empty_when_no_file(tmp_state: Path) -> None:
    assert pdn.get_user_denylist() == []


def test_add_entry_persists(tmp_state: Path) -> None:
    pdn.add_user_denylist_entry("C:/secret")
    assert tmp_state.is_file()
    data = json.loads(tmp_state.read_text(encoding="utf-8"))
    assert "C:/secret" in data["paths"]


def test_add_dedupes(tmp_state: Path) -> None:
    pdn.add_user_denylist_entry("C:/x")
    pdn.add_user_denylist_entry("C:/x")
    pdn.add_user_denylist_entry("C:/x")
    assert pdn.get_user_denylist() == ["C:/x"]


def test_remove_entry(tmp_state: Path) -> None:
    pdn.add_user_denylist_entry("C:/a")
    pdn.add_user_denylist_entry("C:/b")
    pdn.remove_user_denylist_entry("C:/a")
    assert pdn.get_user_denylist() == ["C:/b"]


def test_corrupted_file_returns_empty(tmp_state: Path) -> None:
    tmp_state.write_text("not json {{{", encoding="utf-8")
    assert pdn.get_user_denylist() == []


# ── matching ──────────────────────────────────────────────────


def test_user_added_path_blocks(tmp_path: Path, tmp_state: Path) -> None:
    secret = tmp_path / "vault" / "tokens.json"
    secret.parent.mkdir(parents=True)
    secret.write_text("x")
    pdn.add_user_denylist_entry(str(tmp_path / "vault"))
    blocked, prefix = pdn.is_blocked(str(secret))
    assert blocked is True
    assert "vault" in (prefix or "")


def test_sibling_directory_not_blocked(tmp_path: Path, tmp_state: Path) -> None:
    """``/foo/bar`` blocks must not match ``/foo/barbecue``."""
    target = tmp_path / "barbecue" / "x.txt"
    target.parent.mkdir()
    target.write_text("x")
    pdn.add_user_denylist_entry(str(tmp_path / "bar"))
    blocked, _ = pdn.is_blocked(str(target))
    assert blocked is False


def test_exact_match_blocks(tmp_path: Path, tmp_state: Path) -> None:
    f = tmp_path / "exact.txt"
    f.write_text("x")
    pdn.add_user_denylist_entry(str(f))
    blocked, _ = pdn.is_blocked(str(f))
    assert blocked is True


# ── per-turn (ContextVar) layering ────────────────────────────


def test_turn_denylist_blocks(tmp_path: Path, tmp_state: Path) -> None:
    secret = tmp_path / "extra" / "secret.env"
    secret.parent.mkdir()
    secret.write_text("x")
    token = pdn.push_turn_denylist([str(tmp_path / "extra")])
    try:
        blocked, prefix = pdn.is_blocked(str(secret))
        assert blocked is True
        assert "extra" in (prefix or "")
    finally:
        pdn.pop_turn_denylist(token)


def test_turn_denylist_pop_restores(tmp_path: Path, tmp_state: Path) -> None:
    f = tmp_path / "x" / "f.txt"
    f.parent.mkdir()
    f.write_text("x")
    token = pdn.push_turn_denylist([str(tmp_path / "x")])
    pdn.pop_turn_denylist(token)
    blocked, _ = pdn.is_blocked(str(f))
    assert blocked is False


# ── path_guard integration ────────────────────────────────────


def test_path_guard_blocks_user_added(tmp_path: Path, tmp_state: Path) -> None:
    """End-to-end: a user-added denylist entry causes
    ``check_path`` to refuse the file."""
    secret = tmp_path / "vault" / "tokens.json"
    secret.parent.mkdir()
    secret.write_text("x")
    pdn.add_user_denylist_entry(str(tmp_path / "vault"))
    verdict = check_path(str(secret))
    assert verdict.allow is False
    assert "denylist_blocked" in verdict.reason


def test_path_guard_allow_sensitive_bypasses_denylist(
    tmp_path: Path,
    tmp_state: Path,
) -> None:
    """``allow_sensitive=True`` is the explicit override gate;
    callers that pass it have signed off on whatever they're
    doing. Bypasses both sensitive-path checks AND the denylist."""
    secret = tmp_path / "vault" / "tokens.json"
    secret.parent.mkdir()
    secret.write_text("x")
    pdn.add_user_denylist_entry(str(tmp_path / "vault"))
    verdict = check_path(str(secret), allow_sensitive=True)
    assert verdict.allow is True


def test_path_guard_unrelated_path_passes(
    tmp_path: Path,
    tmp_state: Path,
) -> None:
    """Files outside the denylist still resolve normally."""
    f = tmp_path / "innocent.txt"
    f.write_text("x")
    verdict = check_path(str(f))
    assert verdict.allow is True
