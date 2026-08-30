from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / "extensions" / "workbuddy-connectors"
SCRIPT = MARKETPLACE / "scripts" / "materialize-binary-assets.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("connector_binary_assets", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_binary_asset_sources_are_independent_and_complete() -> None:
    lock = json.loads((MARKETPLACE / "binary-assets.lock.json").read_text(encoding="utf-8"))
    remote = {entry["path"] for entry in lock["assets"]}
    embedded_root = MARKETPLACE / ".binary-assets"
    embedded = {
        path.relative_to(embedded_root).as_posix()[: -len(".b64")]
        for path in embedded_root.rglob("*.b64")
    }

    assert lock["schema"] == "echo.connector_binary_assets.v1"
    assert len(remote) == 20
    assert len(embedded) == 76
    assert not (remote & embedded)
    assert all(str(entry["url"]).startswith("https://") for entry in lock["assets"])
    source = SCRIPT.read_text(encoding="utf-8")
    assert "octopus-agent" not in source
    assert "../octopus" not in source


def test_embedded_asset_materialization_is_byte_exact(tmp_path: Path) -> None:
    root = tmp_path / "marketplace"
    source = root / ".binary-assets/icons/example.png.b64"
    source.parent.mkdir(parents=True)
    payload = b"\x89PNG\r\n\x1a\nEcho fixture"
    encoded = base64.b64encode(payload)
    source.write_bytes(
        b"\n".join(encoded[index : index + 8] for index in range(0, len(encoded), 8)) + b"\n"
    )
    (root / "binary-assets.lock.json").write_text(
        json.dumps({"schema": "echo.connector_binary_assets.v1", "assets": []}),
        encoding="utf-8",
    )

    result = _load_module().materialize(root)
    target = root / "icons/example.png"

    assert result == {"materialized": 1, "verified": 0}
    assert target.read_bytes() == payload
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(payload).digest()
    assert _load_module().materialize(root, verify_only=True) == {
        "materialized": 0,
        "verified": 1,
    }


def test_embedded_asset_rejects_non_base64_bytes_with_source_path(tmp_path: Path) -> None:
    root = tmp_path / "marketplace"
    source = root / ".binary-assets/icons/broken.png.b64"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"aGVsbG8=\nnot-base64!\n")
    (root / "binary-assets.lock.json").write_text(
        json.dumps({"schema": "echo.connector_binary_assets.v1", "assets": []}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"broken\.png\.b64"):
        _load_module().materialize(root)

    assert not (root / "icons/broken.png").exists()
