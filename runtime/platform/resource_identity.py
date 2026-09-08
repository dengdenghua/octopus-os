"""Compatibility re-export for shared resource identity helpers.

The canonical contract lives in :mod:`echo_runtime.resource_identity` so the
appliance and Agent runtime can use the same path-free identifiers without
crossing either layer's private dependency boundary.
"""

from echo_runtime.resource_identity import (
    APPLIANCE_FILE_SOURCE_KIND,
    PHOTO_SOURCE_KIND,
    STORAGE_FILE_SOURCE_KIND,
    WORKSPACE_FILE_SOURCE_KIND,
    PhotoAssetReference,
    appliance_file_resource_id,
    canonical_library_root,
    parse_appliance_file_resource_id,
    parse_storage_file_resource_id,
    parse_workspace_file_resource_id,
    photo_asset_reference,
    photo_asset_revision,
    photo_library_id,
    photo_source,
    storage_file_resource_id,
    workspace_file_resource_id,
)

__all__ = [
    "APPLIANCE_FILE_SOURCE_KIND",
    "PHOTO_SOURCE_KIND",
    "STORAGE_FILE_SOURCE_KIND",
    "WORKSPACE_FILE_SOURCE_KIND",
    "PhotoAssetReference",
    "appliance_file_resource_id",
    "canonical_library_root",
    "photo_asset_reference",
    "photo_asset_revision",
    "photo_library_id",
    "photo_source",
    "parse_appliance_file_resource_id",
    "parse_storage_file_resource_id",
    "parse_workspace_file_resource_id",
    "workspace_file_resource_id",
    "storage_file_resource_id",
]
