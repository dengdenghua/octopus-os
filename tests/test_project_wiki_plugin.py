from __future__ import annotations

import json
from pathlib import Path

from runtime.platform.plugins.bundled.project_wiki.service import (
    MANIFEST_SCHEMA,
    PLUGIN_ID,
    contract,
)
from runtime.platform.plugins.plugin_hub import PluginHub
from runtime.sensing.gateway import wiki_generic


def test_bundled_project_wiki_is_discoverable_and_loadable() -> None:
    hub = PluginHub()
    matches = [item for item in hub.discover() if item["id"] == PLUGIN_ID]

    assert len(matches) == 1
    assert matches[0]["bundled"] is True
    assert hub.load(PLUGIN_ID) is not None
    assert hub.update_plugin_config(PLUGIN_ID, {"output_dir": "elsewhere"}) is False


def test_commercial_mode_rejects_in_process_plugin_loading(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "commercial")
    hub = PluginHub()

    assert hub.load(PLUGIN_ID) is None


def test_project_wikis_share_contract_but_keep_data_isolated(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "main.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (second / "main.ts").write_text("export const second = 2;\n", encoding="utf-8")

    first_manifest = wiki_generic.generate(first)
    second_manifest = wiki_generic.generate(second)

    assert first_manifest["schema"] == second_manifest["schema"] == MANIFEST_SCHEMA
    assert first_manifest["policy_digest"] == second_manifest["policy_digest"]
    assert first_manifest["project_id"] != second_manifest["project_id"]
    assert (first / contract()["output_dir"] / "by-language" / "python.md").is_file()
    assert (second / contract()["output_dir"] / "by-language" / "typescript.md").is_file()
    assert wiki_generic.status(first)["consistent"] is True
    assert wiki_generic.status(second)["consistent"] is True


def test_legacy_manifest_is_marked_outdated(tmp_path: Path) -> None:
    wiki = tmp_path / ".echo-wiki"
    wiki.mkdir()
    (wiki / "index.json").write_text(
        json.dumps({"generated_at": 1, "files_analyzed": 2}),
        encoding="utf-8",
    )

    result = wiki_generic.status(tmp_path)

    assert result["exists"] is True
    assert result["status"] == "outdated"
    assert result["consistent"] is False

