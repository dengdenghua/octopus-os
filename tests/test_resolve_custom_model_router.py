"""Tests for runtime.sensing.gateway.openai_gateway.request_parser._resolve_custom_model_router.

Specifically the variant-name lookup branch added so the picker can
expose ``mimo-v2.5-pro`` / ``mimo-v2.5-flash`` as standalone rows
(rather than the entry alias ``mimo2.5``) while still routing the
request through the entry's base_url + api_key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _custom_models_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Point app_paths().custom_models_path to a hermetic tmp file
    so each test starts with a clean slate. Tests then write their
    own JSON to that file before invoking the resolver."""
    target = tmp_path / "custom_models.json"

    from runtime.platform.process.paths import app_paths

    original = app_paths()

    class _Patched:
        custom_models_path = target

        def __getattr__(self, name: str) -> object:
            return getattr(original, name)

    monkeypatch.setattr(
        "runtime.platform.process.paths.app_paths",
        lambda: _Patched(),
    )
    return target


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_entry_alias_resolves_to_first_variant(
    _custom_models_path: Path,
) -> None:
    """Calling with the entry id (``mimo2.5``) returns the first
    variant (``mimo-v2.5-pro``) — preserves legacy behavior so the
    auto-derive fallback still has a stable answer."""
    from runtime.sensing.gateway.openai_gateway.request_parser import (
        _resolve_custom_model_router,
    )

    _write(
        _custom_models_path,
        {
            "mimo2.5": {
                "provider": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "fake-key",
                "models": ["mimo-v2.5-pro", "mimo-v2.5-flash"],
            },
        },
    )
    sentinel = object()
    new_router, resolved = _resolve_custom_model_router("mimo2.5", sentinel)
    assert resolved == "mimo-v2.5-pro"
    assert new_router is not sentinel  # built a fresh OpenAI router


def test_variant_name_resolves_to_itself(
    _custom_models_path: Path,
) -> None:
    """When the picker passes ``mimo-v2.5-flash`` (a variant inside
    an entry's models list), the resolver finds the parent entry,
    builds a router with that entry's credentials, and uses the
    variant name as the upstream model — so the request goes to
    mimo-v2.5-flash, NOT to whatever models[0] happens to be."""
    from runtime.sensing.gateway.openai_gateway.request_parser import (
        _resolve_custom_model_router,
    )

    _write(
        _custom_models_path,
        {
            "mimo2.5": {
                "provider": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "fake-key",
                "models": ["mimo-v2.5-pro", "mimo-v2.5-flash"],
            },
        },
    )
    sentinel = object()
    new_router, resolved = _resolve_custom_model_router(
        "mimo-v2.5-flash",
        sentinel,
    )
    assert resolved == "mimo-v2.5-flash"
    assert new_router is not sentinel
    # Router uses the variant as default_model so a downstream
    # ModelRequest with model="" still talks to flash.
    assert getattr(new_router, "default_model", None) == "mimo-v2.5-flash"


def test_unknown_name_passes_through(
    _custom_models_path: Path,
) -> None:
    """A name that's neither an entry id nor a known variant returns
    the router unchanged. Lets the dispatcher hand off to its
    fallback (built-in presets / etc.)."""
    from runtime.sensing.gateway.openai_gateway.request_parser import (
        _resolve_custom_model_router,
    )

    _write(
        _custom_models_path,
        {
            "mimo2.5": {
                "provider": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "fake-key",
                "models": ["mimo-v2.5-pro", "mimo-v2.5-flash"],
            },
        },
    )
    sentinel = object()
    new_router, resolved = _resolve_custom_model_router(
        "claude-sonnet-4",
        sentinel,
    )
    assert resolved == "claude-sonnet-4"
    assert new_router is sentinel


def test_variant_lookup_walks_multiple_entries(
    _custom_models_path: Path,
) -> None:
    """If the user has multiple custom_models entries, the variant
    lookup walks all of them and picks the entry whose models[]
    contains the requested variant."""
    from runtime.sensing.gateway.openai_gateway.request_parser import (
        _resolve_custom_model_router,
    )

    _write(
        _custom_models_path,
        {
            "first-provider": {
                "provider": "openai",
                "base_url": "https://first.example.com/v1",
                "api_key": "first-key",
                "models": ["first-small", "first-large"],
            },
            "second-provider": {
                "provider": "openai",
                "base_url": "https://second.example.com/v1",
                "api_key": "second-key",
                "models": ["second-small", "second-large"],
            },
        },
    )
    sentinel = object()
    new_router, resolved = _resolve_custom_model_router(
        "second-large",
        sentinel,
    )
    assert resolved == "second-large"
    # Should have built the second provider's router (its base_url).
    assert "second.example.com" in getattr(new_router, "base_url", "")


def test_legacy_single_model_field_still_works(
    _custom_models_path: Path,
) -> None:
    """Older entries had a single ``model`` field instead of a
    ``models`` list. Entry-id lookup must still find them."""
    from runtime.sensing.gateway.openai_gateway.request_parser import (
        _resolve_custom_model_router,
    )

    _write(
        _custom_models_path,
        {
            "legacy-entry": {
                "provider": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "fake-key",
                "model": "legacy-single-model",
            },
        },
    )
    sentinel = object()
    new_router, resolved = _resolve_custom_model_router(
        "legacy-entry",
        sentinel,
    )
    assert resolved == "legacy-single-model"
    assert new_router is not sentinel


def test_no_custom_models_file_returns_unchanged(
    _custom_models_path: Path,
) -> None:
    """File doesn't exist → router + name pass through verbatim.
    The autouse fixture creates a tmp_path that doesn't have the
    file, so this is the natural "fresh install" case."""
    from runtime.sensing.gateway.openai_gateway.request_parser import (
        _resolve_custom_model_router,
    )

    sentinel = object()
    new_router, resolved = _resolve_custom_model_router("anything", sentinel)
    assert resolved == "anything"
    assert new_router is sentinel
