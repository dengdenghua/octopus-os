"""profile.jsonc::model resolution in the agent loader.

Every shipped agent declares ``{"provider": "auto", "name": "auto"}`` —
"let the dispatch router decide" — which must resolve to None so the
loader's behavior is unchanged. A concrete name (string or object form)
is honored. Returning the raw object would poison ``Agent.model`` (a
string) and the downstream turn-model resolvers.
"""

from __future__ import annotations

import pytest

from runtime.execution.agents.loader import _resolve_profile_model


@pytest.mark.parametrize(
    "profile,expected",
    [
        # The shipped shape — no preference.
        ({"model": {"provider": "auto", "name": "auto"}}, None),
        # Missing / empty.
        ({}, None),
        ({"model": None}, None),
        ({"model": {}}, None),
        ({"model": "   "}, None),
        ({"model": {"name": "  "}}, None),
        # Bare-string concrete model.
        ({"model": "claude-fable-5"}, "claude-fable-5"),
        ({"model": "  gpt-5.5  "}, "gpt-5.5"),
        # Object form with a concrete name.
        ({"model": {"provider": "anthropic", "name": "claude-x"}}, "anthropic/claude-x"),
        # Concrete name but "auto" provider → just the name.
        ({"model": {"provider": "auto", "name": "claude-x"}}, "claude-x"),
        ({"model": {"name": "glm-4-flash"}}, "glm-4-flash"),
    ],
)
def test_resolve_profile_model(profile, expected):
    assert _resolve_profile_model(profile) == expected


def test_object_with_auto_name_is_no_preference():
    # A provider set but name "auto" is still "no preference" — the router
    # picks the model, so we must not fabricate a "provider/auto" id.
    assert _resolve_profile_model({"model": {"provider": "anthropic", "name": "auto"}}) is None


def test_non_string_non_dict_is_none():
    assert _resolve_profile_model({"model": 42}) is None
    assert _resolve_profile_model({"model": ["a"]}) is None

