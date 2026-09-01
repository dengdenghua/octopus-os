from __future__ import annotations

import shutil
from pathlib import Path

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.plugin_hub import PluginHub


def test_frozen_style_bundled_narrative_loads_without_physical_init(
    tmp_path: Path,
) -> None:
    """PyInstaller materialises data files but keeps Python in its PYZ."""

    bundled_root = tmp_path / "frozen" / "runtime" / "platform" / "plugins" / "bundled"
    plugin_dir = bundled_root / "narrative_studio"
    plugin_dir.mkdir(parents=True)
    data_dir = tmp_path / "app-data" / "narrative-studio"
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                "name: narrative_studio",
                "display_name: 叙事工坊",
                "version: 0.2.0",
                "description: frozen desktop smoke",
                "config:",
                f"  data_dir: {data_dir}",
                f"  echo_source_path: {tmp_path / 'missing-echo'}",
            ]
        ),
        encoding="utf-8",
    )
    source_skills = (
        Path(__file__).resolve().parents[1]
        / "runtime"
        / "platform"
        / "plugins"
        / "bundled"
        / "narrative_studio"
        / "skills"
    )
    shutil.copytree(source_skills, plugin_dir / "skills")
    assert not (plugin_dir / "__init__.py").exists()

    registry = SkillRegistry()
    hub = PluginHub(
        plugin_dir=tmp_path / "user-plugins",
        bundled_plugin_dir=bundled_root,
        skill_registry=registry,
    )

    discovered = {item["id"]: item for item in hub.discover()}
    assert discovered["narrative_studio"]["version"] == "0.2.0"
    loaded = hub.load("narrative_studio")

    assert loaded is not None
    assert loaded.__class__.__module__ == ("runtime.platform.plugins.bundled.narrative_studio")
    assert loaded.store is not None
    assert loaded.store.data_dir == data_dir.resolve()
    assert registry.has("narrative_studio.project_create")
    assert registry.has("narrative_studio.narrative_authoring")

