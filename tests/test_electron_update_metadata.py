from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging/desktop/verify-electron-update-metadata.py"
SPEC = importlib.util.spec_from_file_location("verify_electron_update_metadata", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _asset(root: Path, name: str, payload: bytes) -> dict[str, object]:
    path = root / name
    path.write_bytes(payload)
    blockmap = root / f"{name}.blockmap"
    blockmap.write_bytes(b"blockmap:" + payload)
    return {
        "url": name,
        "sha512": base64.b64encode(hashlib.sha512(payload).digest()).decode(),
        "size": len(payload),
        "blockMapSize": blockmap.stat().st_size,
    }


def _metadata(tmp_path: Path, platform: str = "mac") -> tuple[Path, list[dict[str, object]]]:
    revision = "a" * 40
    if platform == "mac":
        name = "latest-mac.yml"
        files = [
            _asset(tmp_path, f"Echo-0.2.0-{revision}.zip", b"zip"),
            _asset(tmp_path, f"Echo-0.2.0-{revision}.dmg", b"dmg"),
        ]
        primary = files[0]
    elif platform == "windows":
        name = "latest.yml"
        files = [_asset(tmp_path, f"Echo-0.2.0-{revision}.exe", b"exe")]
        primary = files[0]
    else:
        name = "latest-linux.yml"
        files = [_asset(tmp_path, f"Echo-0.2.0-{revision}.AppImage", b"appimage")]
        primary = files[0]
    metadata = tmp_path / name
    metadata.write_text(
        yaml.safe_dump(
            {
                "version": "0.2.0",
                "files": files,
                "path": primary["url"],
                "sha512": primary["sha512"],
                "releaseDate": "2026-08-30T00:00:00.000Z",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return metadata, files


@pytest.mark.parametrize("platform", ["mac", "windows", "linux"])
def test_verified_metadata_binds_every_platform_asset(platform: str, tmp_path: Path) -> None:
    metadata, files = _metadata(tmp_path, platform)
    result = MODULE.verify_metadata(
        metadata,
        tmp_path,
        platform=platform,
        expected_version="0.2.0",
        expected_revision="a" * 40,
    )
    assert result["files"] == sorted(str(entry["url"]) for entry in files)


@pytest.mark.parametrize("mutation", ["digest", "size", "revision", "blockmap", "traversal"])
def test_metadata_rejects_tampering_and_incomplete_assets(mutation: str, tmp_path: Path) -> None:
    metadata, files = _metadata(tmp_path)
    value = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    if mutation == "digest":
        value["files"][0]["sha512"] = base64.b64encode(b"x" * 64).decode()
    elif mutation == "size":
        value["files"][0]["size"] += 1
    elif mutation == "revision":
        value["files"][0]["url"] = "Echo-0.2.0-other.zip"
    elif mutation == "blockmap":
        (tmp_path / f"{files[0]['url']}.blockmap").unlink()
    else:
        value["files"][0]["url"] = "../outside.zip"
    metadata.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(MODULE.UpdateMetadataError):
        MODULE.verify_metadata(
            metadata,
            tmp_path,
            platform="mac",
            expected_version="0.2.0",
            expected_revision="a" * 40,
        )
