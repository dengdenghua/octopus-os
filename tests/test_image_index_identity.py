"""Encoder cache identity is derived from assets and versions, never mtimes."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from runtime.memory.hemolymph import _image_model_runtime as models


def _provider(root, providers=None):
    wrapper_type = type("ImageEmbedding", (), {"__module__": "fastembed.image.image_embedding"})
    encoder_type = type("OnnxImageEmbedding", (), {"__module__": "fastembed.image.onnx_embedding"})
    wrapper = wrapper_type()
    encoder = encoder_type()
    encoder._model_dir = root
    encoder.model_description = SimpleNamespace(model_file="model.onnx")
    encoder.model_name = "Qdrant/clip-ViT-B-32-vision"
    encoder.model = SimpleNamespace(get_providers=lambda: providers or ["CPUExecutionProvider"])
    wrapper.model = encoder
    return wrapper


@pytest.fixture
def assets(tmp_path, monkeypatch):
    (tmp_path / "model.onnx").write_bytes(b"synthetic-weight-A")
    (tmp_path / "preprocessor_config.json").write_text('{"size":224}', encoding="utf-8")
    monkeypatch.setattr(models.importlib.metadata, "version", lambda _name: "test-v1")
    return tmp_path


@pytest.mark.parametrize("asset", ["model.onnx", "preprocessor_config.json"])
def test_same_mtime_replacement_invalidates_new_session_identity(assets, asset):
    first = _provider(assets)
    identity = models.model_index_identity(first)
    assert identity and identity.startswith("encoder-v1:")
    assert models.model_index_identity(_provider(assets)) == identity
    path = assets / asset
    info = path.stat()
    path.write_bytes(path.read_bytes().replace(b"A", b"B").replace(b"224", b"256"))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert models.model_index_identity(_provider(assets)) != identity
    # The first object represents the session that already loaded the old
    # assets. A later disk edit cannot change those in-memory weights.
    assert models.model_index_identity(first) == identity


def test_dependency_version_and_execution_provider_invalidate_identity(assets, monkeypatch):
    initial = models.model_index_identity(_provider(assets))
    assert models.model_index_identity(_provider(assets, ["CUDAExecutionProvider"])) != initial
    monkeypatch.setattr(models.importlib.metadata, "version", lambda _name: "test-v2")
    assert models.model_index_identity(_provider(assets)) != initial


def test_external_model_asset_is_included_but_download_metadata_is_not(assets):
    initial = models.model_index_identity(_provider(assets))
    metadata = assets / ".cache"
    metadata.mkdir()
    (metadata / "download.lock").write_bytes(b"transfer state")
    assert models.model_index_identity(_provider(assets)) == initial
    (assets / "weights.external").write_bytes(b"external tensor data")
    assert models.model_index_identity(_provider(assets)) != initial


def test_missing_assets_and_unknown_providers_cannot_claim_cache_identity(assets):
    assert models.model_index_identity(SimpleNamespace(model=_provider(assets).model)) is None
    (assets / "preprocessor_config.json").unlink()
    assert models.model_index_identity(_provider(assets)) is None


def test_identity_does_not_leak_asset_paths(assets):
    identity = models.model_index_identity(_provider(assets))
    assert identity and len(identity) == len("encoder-v1:") + 64
    assert str(assets) not in identity
