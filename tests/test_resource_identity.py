from __future__ import annotations

from echo_runtime.resource_identity import (
    appliance_file_resource_id,
    parse_appliance_file_resource_id,
    parse_storage_file_resource_id,
    parse_workspace_file_resource_id,
    storage_file_resource_id,
    workspace_file_resource_id,
)


def test_appliance_file_identity_round_trips_without_host_path(tmp_path) -> None:
    resource_id = appliance_file_resource_id(tmp_path, "docs/报告.md")

    assert resource_id.startswith("appliance-file:v1:")
    assert str(tmp_path) not in resource_id
    source_id, locator = parse_appliance_file_resource_id(resource_id) or (None, None)
    assert len(source_id or "") == 64
    assert locator == "docs/报告.md"


def test_appliance_file_identity_rejects_traversal() -> None:
    assert parse_appliance_file_resource_id("appliance-file:v1:" + "0" * 64 + ":Li4vYmFk") is None


def test_storage_file_identity_preserves_storage_root_semantics() -> None:
    resource_id = storage_file_resource_id("source-a", "/docs/报告.md")
    assert parse_storage_file_resource_id(resource_id) == ("source-a", "/docs/报告.md")


def test_workspace_file_identity_round_trips_without_host_path() -> None:
    resource_id = workspace_file_resource_id(
        "thread/with spaces",
        "Final",
        "reports/报告.md",
    )

    assert resource_id.startswith("workspace-file:v1:")
    assert "报告" not in resource_id
    assert parse_workspace_file_resource_id(resource_id) == (
        "thread/with spaces",
        "final",
        "reports/报告.md",
    )


def test_workspace_file_identity_rejects_traversal_and_malformed_values() -> None:
    assert parse_workspace_file_resource_id("workspace-file:v1:bad") is None
    assert parse_workspace_file_resource_id(
        workspace_file_resource_id("thread", "output", "safe.txt").replace(
            "c2FmZS50eHQ", "Li4vbm90LXNlY3VyZQ"
        )
    ) is None
