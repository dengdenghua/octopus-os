"""Dense coverage for recipe_forge snapshot/status helpers (audit Q-05)."""

from __future__ import annotations

from pathlib import Path

from runtime.sensing.gateway.evolution_ops import recipe_forge as rf


def test_applied_snapshot_missing_and_present(tmp_path: Path, monkeypatch) -> None:
    import runtime.safety.recovery.gepa_addendum_store as addendum

    missing = tmp_path / "none.json"
    monkeypatch.setattr(addendum, "legacy_global_path", lambda: missing)
    snap = rf._forge_applied_snapshot()
    assert snap["applied"] is False and snap["source"] == "gepa"

    present = tmp_path / "addendum.json"
    present.write_text('{"k": "v"}', encoding="utf-8")
    monkeypatch.setattr(addendum, "legacy_global_path", lambda: present)
    snap2 = rf._forge_applied_snapshot()
    assert snap2["applied"] is True
    assert snap2["size"] == len('{"k": "v"}')
    assert '{"k": "v"}' in snap2["content_preview"]


def test_runs_snapshot(monkeypatch) -> None:
    import runtime.safety.recovery.gepa_runs as runs

    class _FakeStore:
        def list_recent(self, *, limit):
            return [{"run_id": "r1"}]

    monkeypatch.setattr(runs, "get_default_store", lambda: _FakeStore())
    monkeypatch.setattr(runs, "enrich_run_records", lambda records: list(records))
    out = rf._forge_runs_snapshot(limit=5)
    assert out["source"] == "gepa"
    assert out["runs"] == [{"run_id": "r1"}]


def test_addendums_and_recipes_snapshot(monkeypatch) -> None:
    import runtime.safety.recovery.gepa_addendum_store as addendum
    import runtime.safety.recovery.gepa_variants as variants

    monkeypatch.setattr(addendum, "list_all", lambda: ["a1"])
    monkeypatch.setattr(variants, "list_all_manifests", lambda: [{"id": "m1"}])
    assert rf._forge_addendums_snapshot()["addendums"] == ["a1"]
    assert rf._forge_recipes_snapshot()["recipes"] == [{"id": "m1"}]


def test_auto_tick_status_and_error_paths(monkeypatch) -> None:
    import runtime.safety.recovery.forge_auto_tick as fat

    monkeypatch.setattr(fat, "get_status", lambda: {"enabled": True, "interval_hours": 24})
    out = rf._forge_auto_tick_status()
    assert out["enabled"] is True and out["interval_hours"] == 24

    def _boom_status():
        raise ImportError("boom")

    monkeypatch.setattr(fat, "get_status", _boom_status)
    err = rf._forge_auto_tick_status()
    assert err["enabled"] is False and "error" in err

    def _boom():
        raise ImportError("no module")

    monkeypatch.setattr(
        __import__("runtime.safety.recovery.gepa_addendum_store", fromlist=["legacy_global_path"]),
        "legacy_global_path",
        _boom,
    )
    snapped = rf._forge_applied_snapshot()
    assert snapped["applied"] is False and "error" in snapped

