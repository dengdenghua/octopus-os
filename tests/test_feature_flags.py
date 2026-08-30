"""Tests for runtime.platform.runtime_policy.feature_flags."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from runtime.platform import feature_flags as ff


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Snapshot the registry + env so tests can freely mutate them.

    ``_BUILTIN`` registers ~13 flags at import time; individual tests
    can register more via ``ff.register``. We snapshot/restore the
    spec dict and any env vars we touch.
    """
    original_specs = dict(ff._SPECS)
    yield
    ff._SPECS.clear()
    ff._SPECS.update(original_specs)
    ff._SNAPSHOT = None
    ff._FILE_PATH = None


# ─── defaults ────────────────────────────────────────────────


def test_built_in_flags_are_registered() -> None:
    specs = {s.name for s in ff.all_specs()}
    assert "regeneration.enabled" in specs
    assert "safety.invariants_enabled" in specs
    assert "ui.ambient_suggestions" in specs


def test_default_wins_when_nothing_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_FF_SAFETY_INVARIANTS_ENABLED", raising=False)
    monkeypatch.delenv("ECHO_INVARIANTS", raising=False)
    ff.reload()
    assert ff.is_on("safety.invariants_enabled") is True
    assert ff.source_of("safety.invariants_enabled") == "default"


# ─── env precedence ─────────────────────────────────────────


def test_primary_env_beats_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_FF_REGENERATION_ENABLED", "0")
    monkeypatch.delenv("ECHO_REGEN_ENABLED", raising=False)
    ff.reload()
    assert ff.is_on("regeneration.enabled") is False
    assert ff.source_of("regeneration.enabled") == "env"


def test_legacy_env_used_when_primary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_FF_REGENERATION_ENABLED", raising=False)
    monkeypatch.setenv("ECHO_REGEN_ENABLED", "0")
    ff.reload()
    assert ff.is_on("regeneration.enabled") is False
    assert ff.source_of("regeneration.enabled") == "legacy_env:ECHO_REGEN_ENABLED"


def test_primary_env_wins_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_FF_REGENERATION_ENABLED", "1")
    monkeypatch.setenv("ECHO_REGEN_ENABLED", "0")
    ff.reload()
    assert ff.is_on("regeneration.enabled") is True
    assert ff.source_of("regeneration.enabled") == "env"


# ─── file precedence ────────────────────────────────────────


def test_file_override_beats_default_but_not_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "feature_flags.json"
    path.write_text(json.dumps({"camouflage.enabled": True}), encoding="utf-8")
    monkeypatch.delenv("ECHO_FF_CAMOUFLAGE_ENABLED", raising=False)
    monkeypatch.delenv("ECHO_CAMOUFLAGE_ENABLED", raising=False)
    ff.configure(path)
    assert ff.is_on("camouflage.enabled") is True
    assert ff.source_of("camouflage.enabled") == "file"

    # Env override now wins.
    monkeypatch.setenv("ECHO_CAMOUFLAGE_ENABLED", "0")
    ff.reload()
    assert ff.is_on("camouflage.enabled") is False


def test_missing_file_is_not_an_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ff.configure(tmp_path / "does-not-exist.json")
    assert ff.is_on("safety.invariants_enabled") is True


def test_file_reload_picks_up_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_FF_CAMOUFLAGE_ENABLED", raising=False)
    monkeypatch.delenv("ECHO_CAMOUFLAGE_ENABLED", raising=False)
    path = tmp_path / "flags.json"
    path.write_text(json.dumps({"camouflage.enabled": False}))
    ff.configure(path)
    assert ff.is_on("camouflage.enabled") is False

    path.write_text(json.dumps({"camouflage.enabled": True}))
    ff.reload()
    assert ff.is_on("camouflage.enabled") is True


# ─── coercion ───────────────────────────────────────────────


def test_int_flags_parse_env_to_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_INTELLIGENCE_POLL_SECONDS", "3600")
    ff.reload()
    assert ff.value("intelligence.poll_interval_sec") == 3600


def test_int_flags_fall_back_when_env_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_INTELLIGENCE_POLL_SECONDS", "not-a-number")
    ff.reload()
    # Falls through to default (1800), doesn't crash.
    assert ff.value("intelligence.poll_interval_sec") == 1800


def test_custom_coerce_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    ff.register(
        ff.FlagSpec(
            name="custom.mode",
            default="balanced",
            legacy_env=("ECHO_MODE",),
            coerce=lambda raw: raw.strip().lower(),
        )
    )
    monkeypatch.setenv("ECHO_MODE", "AGGRESSIVE")
    ff.reload()
    assert ff.value("custom.mode") == "aggressive"


# ─── special: invariants coerce ─────────────────────────────


def test_invariants_legacy_off_is_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_FF_SAFETY_INVARIANTS_ENABLED", raising=False)
    monkeypatch.setenv("ECHO_INVARIANTS", "off")
    ff.reload()
    assert ff.is_on("safety.invariants_enabled") is False


# ─── describe() contract ───────────────────────────────────


def test_describe_returns_catalog_for_each_flag() -> None:
    catalog = ff.describe()
    names = {entry["name"] for entry in catalog}
    assert "regeneration.enabled" in names
    for entry in catalog:
        assert set(entry.keys()) >= {
            "name",
            "value",
            "source",
            "default",
            "description",
            "experimental",
            "primary_env",
            "legacy_env",
        }


def test_describe_source_reflects_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_REGENERATION_ENABLED", "0")
    ff.reload()
    entry = next(e for e in ff.describe() if e["name"] == "regeneration.enabled")
    assert entry["value"] is False
    assert entry["source"] == "env"


# ─── unknown names ─────────────────────────────────────────


def test_unknown_flag_returns_fallback() -> None:
    ff.reload()
    assert ff.value("nope.does.not.exist", "fallback") == "fallback"
    assert ff.is_on("nope.does.not.exist") is False
