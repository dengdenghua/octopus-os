"""Tests for the model-router block (ModelSelector) of the composition layer."""

from __future__ import annotations

from runtime.platform.models.selector import ModelSelection, ModelSelector
from runtime.sensing.model_router.selector import DefaultModelSelector


def _selector() -> DefaultModelSelector:
    return DefaultModelSelector()


def test_protocol_conformance():
    assert isinstance(_selector(), ModelSelector)


def test_explicit_context_override_wins():
    result = _selector().select(
        role="researcher",
        default_model="gpt-5",
        context={"model_name": "o3"},
        declared_model="gpt-5.1",
        use_cheap_model=True,
        cheap_model="mini",
    )
    assert result == ModelSelection(model="o3")


def test_declared_model_beats_cheap_and_default():
    result = _selector().select(
        role="coder",
        default_model="gpt-5",
        declared_model="claude-opus",
        use_cheap_model=True,
        cheap_model="mini",
    )
    assert result.model == "claude-opus"


def test_cheap_model_used_when_requested():
    result = _selector().select(
        role="researcher",
        default_model="gpt-5",
        use_cheap_model=True,
        cheap_model="mini",
    )
    assert result.model == "mini"


def test_default_model_fallback():
    result = _selector().select(role="planner", default_model="gpt-5")
    assert result.model == "gpt-5"


def test_blank_override_is_ignored():
    result = _selector().select(
        role="researcher",
        default_model="gpt-5",
        context={"model_name": "   "},
        cheap_model="mini",
        use_cheap_model=True,
    )
    assert result.model == "mini"


def test_empty_cheap_model_falls_through_to_default():
    result = _selector().select(
        role="researcher",
        default_model="gpt-5",
        use_cheap_model=True,
        cheap_model="",
    )
    assert result.model == "gpt-5"

