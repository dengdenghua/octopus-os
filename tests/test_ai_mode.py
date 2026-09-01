"""Tests for AI mode (Marvis-style efficiency / privacy wrapper)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from runtime.core.cerebrum import ai_mode


@pytest.fixture
def tmp_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "ai_mode.json"
    monkeypatch.setenv("ECHO_AI_MODE_PATH", str(state))
    monkeypatch.delenv("ECHO_AI_MODE", raising=False)
    return state


# ── current_ai_mode resolution ────────────────────────────────


def test_default_is_efficiency(tmp_state: Path) -> None:
    assert ai_mode.current_ai_mode() == "efficiency"


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_state: Path) -> None:
    monkeypatch.setenv("ECHO_AI_MODE", "privacy")
    assert ai_mode.current_ai_mode() == "privacy"


def test_unknown_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state: Path,
) -> None:
    monkeypatch.setenv("ECHO_AI_MODE", "turbo")
    assert ai_mode.current_ai_mode() == "efficiency"


def test_persisted_file_honored(tmp_state: Path) -> None:
    tmp_state.write_text(json.dumps({"mode": "privacy"}), encoding="utf-8")
    assert ai_mode.current_ai_mode() == "privacy"


def test_corrupt_file_returns_default(tmp_state: Path) -> None:
    tmp_state.write_text("not json {{{", encoding="utf-8")
    assert ai_mode.current_ai_mode() == "efficiency"


# ── set_ai_mode ───────────────────────────────────────────────


def test_set_ai_mode_persists(tmp_state: Path) -> None:
    canonical = ai_mode.set_ai_mode("privacy")
    assert canonical == "privacy"
    data = json.loads(tmp_state.read_text(encoding="utf-8"))
    assert data["mode"] == "privacy"
    assert "set_at" in data


def test_set_ai_mode_canonicalises(tmp_state: Path) -> None:
    assert ai_mode.set_ai_mode("Efficiency  ") == "efficiency"
    assert ai_mode.set_ai_mode("PRIVACY") == "privacy"


def test_set_ai_mode_rejects_unknown(tmp_state: Path) -> None:
    with pytest.raises(ValueError):
        ai_mode.set_ai_mode("turbo")
    with pytest.raises(ValueError):
        ai_mode.set_ai_mode(123)  # type: ignore[arg-type]


# ── apply override ────────────────────────────────────────────


def test_efficiency_passes_through(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state: Path,
) -> None:
    monkeypatch.setenv("ECHO_AI_MODE", "efficiency")
    assert ai_mode.apply_ai_mode_override("performance") == "performance"
    assert ai_mode.apply_ai_mode_override("local") == "local"
    assert ai_mode.apply_ai_mode_override("value") == "value"


def test_privacy_pins_to_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state: Path,
) -> None:
    monkeypatch.setenv("ECHO_AI_MODE", "privacy")
    for verdict in ("trivial", "local", "value", "performance", "research"):
        assert ai_mode.apply_ai_mode_override(verdict) == "local"


# ── device summary ────────────────────────────────────────────


def test_device_summary_returns_struct() -> None:
    """Detection is best-effort; we can't pin specific values
    cross-platform, but the dataclass shape must always come back."""
    summary = ai_mode.detect_device_summary()
    d = summary.to_dict()
    assert "has_local_model" in d
    assert "has_gpu" in d
    assert "ram_gb" in d
    assert "cpu_count" in d
    assert "cloud_reachable" in d
    assert "notes" in d
    assert isinstance(d["notes"], list)


def test_recommend_mode_no_cloud_with_local() -> None:
    summary = ai_mode.DeviceSummary(
        has_local_model=True,
        has_gpu=False,
        ram_gb=16.0,
        cpu_count=8,
        cloud_reachable=False,
        notes=[],
    )
    assert ai_mode.recommend_mode(summary) == "privacy"


def test_recommend_mode_default_efficiency() -> None:
    summary = ai_mode.DeviceSummary(
        has_local_model=False,
        has_gpu=False,
        ram_gb=8.0,
        cpu_count=4,
        cloud_reachable=True,
        notes=[],
    )
    assert ai_mode.recommend_mode(summary) == "efficiency"
