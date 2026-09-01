from runtime.platform.capabilities.capability_registry import (
    CAPABILITY_STATE_FILE,
    CODEX_PLUGIN_CACHE,
    CONNECTOR_STATE_FILE,
    SKILLS_ROOT,
)
from runtime.platform.connectors.connector_registry import STATE_FILE
from runtime.platform.plugins.cloud_catalog import CloudCatalog


def test_capability_registry_reads_the_generation_cloud_catalog_commits() -> None:
    assert CODEX_PLUGIN_CACHE == CloudCatalog.PLUGIN_INSTALL_ROOT / "codex"
    assert CONNECTOR_STATE_FILE == CloudCatalog.CONNECTOR_STATE_FILE
    assert STATE_FILE == CloudCatalog.CONNECTOR_STATE_FILE
    assert CAPABILITY_STATE_FILE == CloudCatalog.CAPABILITY_STATE_FILE
    assert SKILLS_ROOT == CloudCatalog.SKILLS_ROOT
