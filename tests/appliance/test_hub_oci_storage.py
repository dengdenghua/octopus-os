from __future__ import annotations

import pytest

from appliance.hub.catalog import HubCatalog
from deploy.appliance.hub_oci_storage import HubOciStorageError, verify_catalog_storage


def _resolver(image: str, architecture: str) -> dict[str, int]:
    shared = {"sha256:" + "a" * 64: 1024}
    unique = "b" if architecture == "amd64" else "c"
    if "demo" in image:
        return {**shared, "sha256:" + unique * 64: 2048}
    return shared


def _catalog(download_bytes: int = 3072) -> HubCatalog:
    image = "registry.example.com/echo/demo@sha256:" + "d" * 64
    return HubCatalog.from_mapping(
        {
            "schema": "echo.hub.catalog.v1",
            "version": "test.oci.1",
            "publisher": {"id": "echo-test", "name": "Echo Test"},
            "apps": [
                {
                    "id": "demo-app",
                    "name": "Demo",
                    "nameZh": "演示",
                    "version": "1.0.0",
                    "summary": "OCI storage verification fixture.",
                    "category": "system",
                    "icon": "system",
                    "sourceUrl": "https://example.com/demo",
                    "featured": False,
                    "imageStorage": {
                        "schema": "echo.hub.image-storage.v1",
                        "architectures": {
                            "amd64": {"downloadBytes": download_bytes, "blobCount": 2},
                            "arm64": {"downloadBytes": download_bytes, "blobCount": 2},
                        },
                    },
                    "package": {
                        "schema": "echo.hub.docker-package.v1",
                        "image": image,
                        "architectures": ["amd64", "arm64"],
                        "ports": [],
                        "volumes": [],
                        "environment": {},
                        "runtime": {
                            "memoryMiB": 128,
                            "pids": 64,
                            "shmSizeMiB": 64,
                            "readOnlyRootfs": True,
                        },
                    },
                    "bundle": None,
                    "integrationStatus": "available",
                    "integrationNote": "verified fixture",
                }
            ],
        }
    )


def test_catalog_oci_storage_verifier_binds_bytes_and_blob_count() -> None:
    result = verify_catalog_storage(_catalog(), resolver=_resolver)

    assert result["schema"] == "echo.hub.oci-storage-verification.v1"
    assert len(result["verified"]) == 2
    assert {item["downloadBytes"] for item in result["verified"]} == {3072}


def test_catalog_oci_storage_verifier_rejects_stale_attestation() -> None:
    with pytest.raises(HubOciStorageError, match="does not match"):
        verify_catalog_storage(_catalog(4096), resolver=_resolver)
