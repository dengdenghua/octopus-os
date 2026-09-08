"""Model readiness follows real loading attempts, without loading from status."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from appliance.agent_api import images
from runtime.memory.hemolymph import _image_model_runtime as runtime
from runtime.memory.hemolymph import image_semantic_index as index


@pytest.fixture(autouse=True)
def isolated_model_state(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "_STATES", {})
    for name in ("_CLIP_TEXT", "_CLIP_IMAGE", "_FACE_APP"):
        monkeypatch.setattr(index, name, None)
    for name in (
        "ECHO_EMBED_QUANTIZE",
        "ECHO_ORT_PROVIDERS",
        "FASTEMBED_CACHE_PATH",
        "HF_HUB_OFFLINE",
        "ECHO_IMAGE_SEMANTIC",
        "ECHO_IMAGE_MAX_CONCURRENT_INFERENCE",
        "ECHO_IMAGE_INFERENCE_WAIT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(images, "_dependency_present", lambda _name: True)


def test_status_reports_sanitized_import_failure_and_explicit_retry_recovers(monkeypatch, tmp_path):
    received = []

    def fail(**_kwargs):
        raise ImportError("DLL failed at /private/photos?token=synthetic-secret")

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(ImageEmbedding=fail))
    assert images.agent_image_index_readiness(index)["semantic"]["state"] == "dependencies-ready"
    assert index._image_model() is None
    failed = images.agent_image_index_readiness(index)
    assert failed["semantic"]["state"] == "load-failed"
    assert failed["semantic"]["available"] is True  # explicit retry remains possible
    assert failed["semantic"]["modelsLoaded"] is False
    assert failed["semantic"]["modelErrors"] == [
        {"model": "vision", "code": "runtime_import_failed"}
    ]
    assert failed["modelDownloadMayBeRequired"] is False
    assert "synthetic-secret" not in json.dumps(failed)
    assert "/private/photos" not in json.dumps(failed)
    source = tmp_path / "photos"
    source.mkdir()
    Image.new("RGB", (8, 8), "red").save(source / "a.png")
    built = index.build_index(source, db_path=tmp_path / "failed.db", include_faces=False)
    assert built["model_error"] == "runtime_import_failed"
    assert not (tmp_path / "failed.db").exists()

    def load(**kwargs):
        received.append(kwargs)
        # The status read must not contend with the model initialization lock.
        assert images.agent_image_index_readiness(index)["semantic"]["state"] == "loading"
        return object()

    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(ImageEmbedding=load, TextEmbedding=load)
    )
    assert index._image_model() is not None
    assert index._text_model() is not None
    ready = images.agent_image_index_readiness(index)
    assert ready["semantic"]["state"] == "loaded"
    assert ready["semantic"]["modelErrors"] == []
    assert ready["modelDownloadMayBeRequired"] is False
    assert len(received) == 2
    assert all(
        Path(options["cache_dir"]) == tmp_path / "state" / "models" / "images"
        for options in received
    )
    assert not (tmp_path / "state").exists()  # configuration/status never write caches


@pytest.mark.parametrize(
    "setting,code",
    [
        ("int8", "unsupported_quantization"),
        ("uint8", "unsupported_quantization"),
        ("typo", "invalid_model_configuration"),
    ],
)
def test_unsupported_quantization_cannot_silently_run_full_precision(monkeypatch, setting, code):
    monkeypatch.setenv("ECHO_EMBED_QUANTIZE", setting)
    monkeypatch.setitem(
        sys.modules,
        "fastembed",
        SimpleNamespace(
            ImageEmbedding=lambda **_: pytest.fail(
                "unsupported configuration must not initialize or download"
            )
        ),
    )
    assert index._image_model() is None
    assert index.image_model_status()["vision"] == {"state": "load-failed", "code": code}


def test_explicit_cache_and_offline_options_reach_both_real_loader_boundaries(
    monkeypatch, tmp_path
):
    received = []
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "preloaded-models"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "YES")
    monkeypatch.setenv("ECHO_EMBED_QUANTIZE", "float32")
    monkeypatch.setenv("ECHO_ORT_PROVIDERS", "CUDAExecutionProvider, CPUExecutionProvider")

    def load(**kwargs):
        received.append(kwargs)
        return object()

    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(ImageEmbedding=load, TextEmbedding=load)
    )
    index._image_model()
    index._text_model()
    assert len(received) == 2
    for options in received:
        assert Path(options["cache_dir"]) == tmp_path / "preloaded-models"
        assert options["local_files_only"] is True
        assert options["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
        assert "quantization" not in options


def test_readiness_observes_face_failure_without_importing_or_retrying(monkeypatch):
    calls = []

    def fail(**_kwargs):
        calls.append(True)
        raise ValueError("private model download URL")

    monkeypatch.setitem(sys.modules, "insightface", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "insightface.app", SimpleNamespace(FaceAnalysis=fail))
    assert index._face_app() is None
    for _ in range(3):
        readiness = images.agent_image_index_readiness(index)
        assert readiness["faces"]["modelErrors"] == [
            {"model": "faces", "code": "model_load_failed"}
        ]
    assert calls == [True]
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "0")
    assert images.agent_image_index_readiness(index)["faces"]["state"] == "disabled"


def test_readiness_does_not_publish_unrecognized_runtime_error_fields():
    module = SimpleNamespace(
        build_index=lambda: None,
        search_by_text=lambda: None,
        _iter_images=lambda: None,
        _load_image=lambda: None,
        _mtime=lambda: None,
        image_model_status=lambda: {
            "vision": {
                "state": "load-failed",
                "code": "private-path",
                "message": "synthetic-secret",
            }
        },
    )
    result = images.agent_image_index_readiness(module)
    assert result["semantic"]["modelErrors"] == []
    assert "synthetic-secret" not in json.dumps(result)
