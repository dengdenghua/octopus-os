"""Tests for the ``ask_user_question`` skill."""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.suckers.ask_user_question import (
    _ask_user_question,
    register_ask_user_question_skill,
)
from runtime.execution.suckers.registry import SkillRegistry


def test_empty_question_invalid() -> None:
    out = _ask_user_question(question="", options=["a", "b"])
    assert out["ok"] is False
    assert out["error_type"] == "invalid_argument"


def test_no_options_invalid() -> None:
    out = _ask_user_question(question="pick one")
    assert out["ok"] is False
    assert out["error_type"] == "invalid_argument"


def test_too_few_options_invalid() -> None:
    out = _ask_user_question(question="pick one", options=["a"])
    assert out["ok"] is False
    assert out["error_type"] == "invalid_argument"


def test_too_many_options_invalid() -> None:
    out = _ask_user_question(
        question="pick one",
        options=["a", "b", "c", "d", "e", "f", "g"],
    )
    assert out["ok"] is False
    assert out["error_type"] == "invalid_argument"


def test_options_must_be_list() -> None:
    out = _ask_user_question(question="pick", options="a,b,c")  # type: ignore[arg-type]
    assert out["ok"] is False
    assert out["error_type"] == "invalid_argument"


def test_happy_path_no_session() -> None:
    """In headless / no-session mode the skill still validates and
    returns ``posted=False``. The caller knows the question wasn't
    actually emitted."""
    out = _ask_user_question(
        question="Use PostgreSQL or SQLite?",
        options=["PostgreSQL", "SQLite"],
    )
    assert out["ok"] is True
    assert out["posted"] is False  # no session/emitter wired
    assert out["question"] == "Use PostgreSQL or SQLite?"
    assert out["options"] == ["PostgreSQL", "SQLite"]
    assert out["allow_other"] is True


def test_happy_path_emits_to_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """When session has an event emitter, the skill posts a structured
    user_question event."""
    captured: list[dict[str, Any]] = []

    class _FakeSession:
        metadata = {"event_emitter": lambda ev: captured.append(ev)}

    from runtime.platform import session as session_mod

    monkeypatch.setattr(session_mod, "current_session", lambda: _FakeSession())

    out = _ask_user_question(
        question="Vite or Webpack?",
        options=["Vite", "Webpack"],
        allow_other=False,
    )
    assert out["ok"] is True
    assert out["posted"] is True
    assert len(captured) == 1
    ev = captured[0]
    assert ev["type"] == "user_question"
    assert ev["question"] == "Vite or Webpack?"
    assert ev["options"] == ["Vite", "Webpack"]
    assert ev["allow_other"] is False


def test_emitter_exception_does_not_break_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Best-effort emit: if the emitter raises, skill still returns ok=True
    with posted=False so the model knows the message didn't reach the user."""

    class _BoomSession:
        metadata = {"event_emitter": lambda ev: (_ for _ in ()).throw(RuntimeError("boom"))}

    from runtime.platform import session as session_mod

    monkeypatch.setattr(session_mod, "current_session", lambda: _BoomSession())

    out = _ask_user_question(
        question="A or B?",
        options=["A", "B"],
    )
    assert out["ok"] is True
    assert out["posted"] is False


def test_options_strip_whitespace_and_filter_empty() -> None:
    out = _ask_user_question(
        question="pick",
        options=["  a  ", "", "b", "   ", "c"],
    )
    assert out["ok"] is True
    assert out["options"] == ["a", "b", "c"]


def test_register_returns_one() -> None:
    reg = SkillRegistry()
    n = register_ask_user_question_skill(reg)
    assert n == 1
    assert reg.has("ask_user_question")
    sk = reg.get("ask_user_question")
    assert "ask_user" in sk.affinity
