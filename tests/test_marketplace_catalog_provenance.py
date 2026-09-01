"""Signed Hub catalog indexes are a trust boundary, not display-only JSON."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from runtime.platform.plugins import cloud_catalog
from runtime.platform.plugins.catalog_provenance import (
    CATALOG_SIGNATURE_SCHEMA,
    canonical_catalog_signature_payload,
    catalog_content_digest,
    verify_marketplace_catalog,
)
from runtime.platform.plugins.cloud_catalog import CloudCatalog


def _trust_material(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trust_store = tmp_path / "publishers.json"
    trust_store.write_text(
        json.dumps(
            {
                "schema": "echo.plugin_publisher_trust_store.v1",
                "publishers": [
                    {
                        "publisher_id": "echoai",
                        "keys": [
                            {
                                "key_id": "release-2026",
                                "algorithm": "ed25519",
                                "status": "active",
                                "public_key": base64.b64encode(public_key).decode("ascii"),
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return private_key, trust_store


def _sign(
    private_key: Ed25519PrivateKey,
    catalog: dict,
    *,
    name: str = "plugin-store.json",
) -> dict:
    digest = catalog_content_digest(catalog)
    payload = canonical_catalog_signature_payload(
        catalog_name=name,
        content_digest=digest,
        publisher_id="echoai",
        key_id="release-2026",
    )
    return {
        "schema": CATALOG_SIGNATURE_SCHEMA,
        "algorithm": "ed25519",
        "catalog": name,
        "content_digest": digest,
        "publisher_id": "echoai",
        "key_id": "release-2026",
        "signature": base64.b64encode(private_key.sign(payload)).decode("ascii"),
    }


def test_catalog_signature_rejects_metadata_tampering(tmp_path: Path) -> None:
    private_key, trust_store = _trust_material(tmp_path)
    catalog = {
        "meta": {"generated_at": "2026-08-29T00:00:00Z"},
        "items": [{"id": "wb_docs", "plugin": "docs", "version": "1.0.0"}],
    }
    envelope = _sign(private_key, catalog)

    trust = verify_marketplace_catalog(
        catalog,
        envelope,
        catalog_name="plugin-store.json",
        trust_store_path=trust_store,
    )
    assert trust["publisher_verified"] is True

    catalog["items"][0]["version"] = "9.9.9"
    with pytest.raises(ValueError, match="content digest"):
        verify_marketplace_catalog(
            catalog,
            envelope,
            catalog_name="plugin-store.json",
            trust_store_path=trust_store,
        )


def test_remote_catalog_is_verified_before_cache_or_use(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_key, trust_store = _trust_material(tmp_path)
    catalog = {
        "meta": {"generated_at": "2026-08-29T00:00:00Z"},
        "items": [{"id": "wb_docs", "plugin": "docs", "version": "1.0.0"}],
    }
    envelope = _sign(private_key, catalog)
    production_root = tmp_path / "installed-app"
    mirror = tmp_path / "mirror"
    cache = tmp_path / "cache"
    monkeypatch.setattr(cloud_catalog, "REPO", production_root)
    monkeypatch.setattr(cloud_catalog, "LOCAL_MIRROR_DIR", mirror)
    monkeypatch.setattr(cloud_catalog, "CACHE_DIR", cache)

    def load_remote(name: str):
        if name == "plugin-store.json":
            return catalog
        if name == "plugin-store.provenance.json":
            return envelope
        return None

    monkeypatch.setattr(cloud_catalog, "_load_remote", load_remote)
    instance = CloudCatalog("plugins", trust_store_path=trust_store)

    assert instance.items()[0]["plugin"] == "docs"
    assert instance.meta()["catalog_trust"]["status"] == "verified"
    assert (cache / "cloud-plugin-store.json").is_file()
    assert (cache / "cloud-plugin-store.provenance.json").is_file()

    # A later offline process may use only the verified pair.
    monkeypatch.setattr(cloud_catalog, "_load_remote", lambda _name: None)
    cached = CloudCatalog("plugins", trust_store_path=trust_store)
    assert cached.items()[0]["plugin"] == "docs"
    assert cached.meta()["catalog_trust"]["status"] == "verified"


def test_unsigned_remote_catalog_fails_closed_without_poisoning_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _private_key, trust_store = _trust_material(tmp_path)
    catalog = {"meta": {}, "items": [{"id": "evil", "plugin": "evil"}]}
    monkeypatch.setattr(cloud_catalog, "REPO", tmp_path / "installed-app")
    monkeypatch.setattr(cloud_catalog, "LOCAL_MIRROR_DIR", tmp_path / "mirror")
    monkeypatch.setattr(cloud_catalog, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        cloud_catalog,
        "_load_remote",
        lambda name: catalog if name == "plugin-store.json" else None,
    )

    with pytest.raises(RuntimeError, match="unavailable or untrusted"):
        CloudCatalog("plugins", trust_store_path=trust_store).items()
    assert not (tmp_path / "cache" / "cloud-plugin-store.json").exists()

