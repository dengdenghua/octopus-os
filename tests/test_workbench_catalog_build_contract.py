from __future__ import annotations

import runpy
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_workbench_catalog_uses_public_ids_and_runtime_identity() -> None:
    builder = runpy.run_path(
        str(REPO / "extensions/workbuddy-experts/scripts/build-plugin-store.py")
    )
    items = builder["scan_workbench_apps"]()
    by_plugin = {item["plugin"]: item for item in items}

    assert by_plugin["narrative_studio"]["id"] == "workbench_narrative"
    assert by_plugin["narrative_studio"]["runtime_plugin"] == "narrative_studio"
    assert by_plugin["self_evolution"]["id"] == "workbench_self-evolution"
    assert by_plugin["paper-trading"]["runtime_plugin"] == "paper_trading"
