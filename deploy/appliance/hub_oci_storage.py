#!/usr/bin/env python3
"""Verify release-attested Hub image download bytes against OCI registries.

The Hub catalog is bundled into the signed OS image.  This verifier resolves
each immutable manifest-list digest, selects the requested Linux architecture,
deduplicates config/layer blobs across all services in one app, and requires
the resulting byte and blob counts to match the catalog exactly.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from appliance.hub.catalog import HubApp, HubCatalog

_MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_MAX_REGISTRY_JSON_BYTES = 8 * 1024 * 1024
_MAX_BLOB_BYTES = 64 * 1024**3
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class HubOciStorageError(RuntimeError):
    """An OCI measurement is unavailable, ambiguous, or disagrees with the catalog."""


def _artifact_images(app: HubApp) -> tuple[str, ...]:
    if app.package is not None:
        return (app.package.image,)
    if app.bundle is not None:
        return tuple(dict.fromkeys(service.image for service in app.bundle.services))
    return ()


def _registry_coordinates(image: str) -> tuple[str, str, str, str, str]:
    host, remainder = image.split("/", 1)
    repository, digest = remainder.split("@", 1)
    if host == "docker.io":
        return (
            "registry-1.docker.io",
            "https://auth.docker.io/token",
            "registry.docker.io",
            repository,
            digest,
        )
    if host == "ghcr.io":
        return ("ghcr.io", "https://ghcr.io/token", "ghcr.io", repository, digest)
    raise HubOciStorageError(f"unsupported registry host for {image!r}")


def _json_request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    attempts: int = 4,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Echo-OS-Hub-OCI-Verifier/1", **(headers or {})},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read(_MAX_REGISTRY_JSON_BYTES + 1)
            if not raw or len(raw) > _MAX_REGISTRY_JSON_BYTES:
                raise HubOciStorageError("OCI registry response size is invalid")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise HubOciStorageError("OCI registry response is not an object")
            return value
        except (OSError, ValueError, HubOciStorageError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(attempt + 1)
    raise HubOciStorageError("OCI registry request failed") from last_error


def _registry_blobs(image: str, architecture: str) -> dict[str, int]:
    registry, auth_url, service, repository, root_digest = _registry_coordinates(image)
    token_url = (
        auth_url
        + "?"
        + urllib.parse.urlencode({"service": service, "scope": f"repository:{repository}:pull"})
    )
    token_payload = _json_request(token_url)
    token = token_payload.get("token")
    if not isinstance(token, str) or not token:
        raise HubOciStorageError("OCI registry did not issue a pull token")
    headers = {"Authorization": f"Bearer {token}", "Accept": _MANIFEST_ACCEPT}

    def manifest(reference: str) -> dict[str, Any]:
        quoted_repository = "/".join(
            urllib.parse.quote(segment, safe="") for segment in repository.split("/")
        )
        return _json_request(
            f"https://{registry}/v2/{quoted_repository}/manifests/{reference}",
            headers=headers,
        )

    root = manifest(root_digest)
    manifests = root.get("manifests")
    if not isinstance(manifests, list):
        raise HubOciStorageError(f"{image!r} must resolve through a platform manifest index")
    matches = [
        item
        for item in manifests
        if isinstance(item, dict)
        and isinstance(item.get("platform"), dict)
        and item["platform"].get("os") == "linux"
        and item["platform"].get("architecture") == architecture
        and (architecture != "arm64" or item["platform"].get("variant") in {None, "", "v8"})
    ]
    if len(matches) != 1:
        raise HubOciStorageError(f"{image!r} has {len(matches)} Linux {architecture} manifests")
    selected_digest = matches[0].get("digest")
    if not isinstance(selected_digest, str) or _SHA256.fullmatch(selected_digest) is None:
        raise HubOciStorageError("OCI manifest descriptor omitted its digest")
    selected = manifest(selected_digest)

    config = selected.get("config")
    layers = selected.get("layers")
    if not isinstance(config, dict) or not isinstance(layers, list):
        raise HubOciStorageError("OCI image manifest omitted config or layers")
    blobs: dict[str, int] = {}
    for descriptor in (config, *layers):
        if not isinstance(descriptor, dict):
            raise HubOciStorageError("OCI blob descriptor is invalid")
        digest = descriptor.get("digest")
        size = descriptor.get("size")
        if (
            not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= _MAX_BLOB_BYTES
        ):
            raise HubOciStorageError("OCI blob descriptor has invalid identity or size")
        previous = blobs.setdefault(digest, size)
        if previous != size:
            raise HubOciStorageError("one OCI digest declared conflicting sizes")
    return blobs


def verify_catalog_storage(
    catalog: HubCatalog,
    *,
    resolver: Callable[[str, str], dict[str, int]] = _registry_blobs,
) -> dict[str, Any]:
    verified: list[dict[str, Any]] = []
    for app in catalog.apps:
        images = _artifact_images(app)
        if not images:
            continue
        if app.image_storage is None:
            raise HubOciStorageError(f"{app.id} omitted image storage metadata")
        artifact = app.package or app.bundle
        assert artifact is not None
        for architecture in artifact.architectures:
            blobs: dict[str, int] = {}
            for image in images:
                for digest, size in resolver(image, architecture).items():
                    previous = blobs.setdefault(digest, size)
                    if previous != size:
                        raise HubOciStorageError(
                            f"{app.id} has conflicting size for OCI blob {digest}"
                        )
            expected = app.image_storage.for_architecture(architecture)
            if expected is None:
                raise HubOciStorageError(f"{app.id} omitted {architecture} storage metadata")
            download_bytes = sum(blobs.values())
            if expected.download_bytes != download_bytes or expected.blob_count != len(blobs):
                raise HubOciStorageError(
                    f"{app.id} {architecture} OCI storage metadata does not match its digests"
                )
            verified.append(
                {
                    "appId": app.id,
                    "architecture": architecture,
                    "downloadBytes": download_bytes,
                    "blobCount": len(blobs),
                }
            )
    return {
        "schema": "echo.hub.oci-storage-verification.v1",
        "catalogDigest": catalog.digest,
        "verified": verified,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", help="catalog JSON path; defaults to the bundled catalog")
    args = parser.parse_args(argv)
    result = verify_catalog_storage(HubCatalog.load(args.catalog))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
