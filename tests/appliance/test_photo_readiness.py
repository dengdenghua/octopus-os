"""Photo dependency discovery must stay local and gate requested capabilities."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.agent_api import images
from appliance.photos import AgentImageIndexAdapter, PhotoLibraryService, create_photos_router


def _module(**overrides):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("readiness must not initialize models or execute indexing")

    return SimpleNamespace(
        **{
            "build_index": forbidden,
            "search_by_text": forbidden,
            "_iter_images": forbidden,
            "_load_image": forbidden,
            "_mtime": forbidden,
            "_text_model": forbidden,
            "_image_model": forbidden,
            "_face_app": forbidden,
            "face_capable": forbidden,
            **overrides,
        }
    )


def _adapter(monkeypatch, *, missing=(), **module_overrides):
    module = _module(**module_overrides)
    monkeypatch.delenv("ECHO_IMAGE_SEMANTIC", raising=False)
    monkeypatch.setattr(images, "_dependency_present", lambda name: name not in missing)
    adapter = AgentImageIndexAdapter()
    monkeypatch.setattr(adapter, "_module", lambda: module)
    monkeypatch.setattr(adapter, "_safe_file_access_available", lambda: True)
    return adapter


def _service(tmp_path, adapter):
    root = tmp_path / "nas"
    root.mkdir()
    (root / "holiday.jpg").write_bytes(b"synthetic image; scanning never decodes this")
    return PhotoLibraryService(root, tmp_path / "state", backend=adapter)


def test_symbol_presence_does_not_advertise_missing_semantic_dependencies(monkeypatch):
    adapter = _adapter(monkeypatch, missing=("fastembed", "onnxruntime"))

    assert adapter.available() is False
    readiness = adapter.readiness()
    assert readiness["browseAvailable"] is True
    assert readiness["previewAvailable"] is True
    assert readiness["semantic"]["state"] == "unavailable"
    assert readiness["semantic"]["missingDependencies"] == ["fastembed", "onnxruntime"]
    assert readiness["modelDownloadMayBeRequired"] is False


def test_dependency_probe_does_not_import_packages_or_initialize_models(monkeypatch):
    visited = []

    def discover(name):
        visited.append(name)
        return object()

    monkeypatch.delenv("ECHO_IMAGE_SEMANTIC", raising=False)
    monkeypatch.setattr(images.importlib.util, "find_spec", discover)
    result = images.agent_image_index_readiness(_module())

    assert visited == ["PIL", "fastembed", "onnxruntime", "insightface", "numpy"]
    assert result["semantic"]["state"] == "dependencies-ready"
    assert result["semantic"]["modelsLoaded"] is False
    assert result["faces"]["modelsLoaded"] is False
    assert result["modelDownloadMayBeRequired"] is True


def test_only_already_loaded_models_are_reported_loaded(monkeypatch):
    adapter = _adapter(monkeypatch, _CLIP_TEXT=object(), _CLIP_IMAGE=object(), _FACE_APP=object())

    readiness = adapter.readiness()

    assert readiness["semantic"]["state"] == "loaded"
    assert readiness["faces"]["state"] == "loaded"
    assert readiness["modelDownloadMayBeRequired"] is False


def test_readiness_exposes_only_bounded_inference_observations(monkeypatch):
    adapter = _adapter(
        monkeypatch,
        image_inference_status=lambda: {
            "maxConcurrent": 1,
            "active": 1,
            "waiting": 2,
            "available": 0,
            "processShared": True,
            "thread": "private",
        },
    )

    readiness = adapter.readiness()

    assert readiness["inference"] == {
        "maxConcurrent": 1,
        "active": 1,
        "waiting": 2,
        "available": 0,
        "processShared": True,
    }


@pytest.mark.parametrize("value", ["0", "false", "OFF", " no "])
def test_explicit_disable_wins_over_loaded_models(monkeypatch, value):
    adapter = _adapter(monkeypatch, _CLIP_TEXT=object(), _CLIP_IMAGE=object())
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", value)

    assert adapter.available() is False
    assert adapter.readiness()["semantic"]["state"] == "disabled"


def test_missing_faces_only_blocks_a_plan_that_requests_them(monkeypatch, tmp_path):
    adapter = _adapter(monkeypatch, missing=("insightface",))
    service = _service(tmp_path, adapter)

    semantic_plan = service.plan_index(include_faces=False)
    face_plan = service.plan_index(include_faces=True)

    assert semantic_plan["ready"] is True
    assert face_plan["ready"] is False
    assert [blocker["code"] for blocker in face_plan["blockers"]] == ["FACE_DEPENDENCIES_MISSING"]
    assert semantic_plan["readiness"]["faces"]["available"] is False
    assert semantic_plan["warnings"][0]["code"] == "MODEL_DOWNLOAD_MAY_BE_REQUIRED"


def test_status_plan_and_apply_expose_consistent_missing_dependency_state(monkeypatch, tmp_path):
    adapter = _adapter(monkeypatch, missing=("fastembed",))
    service = _service(tmp_path, adapter)
    app = FastAPI()
    app.include_router(create_photos_router(service))
    client = TestClient(app)

    status = client.get("/api/appliance/photos/status").json()
    plan = client.post("/api/appliance/photos/plans/index", json={}).json()
    applied = client.post(
        "/api/appliance/photos/plans/index/apply",
        json={"planId": plan["planId"], "includeFaces": False},
    )

    assert status["index"]["backendAvailable"] is False
    assert status["index"]["readiness"] == plan["readiness"]
    assert plan["blockers"][0]["code"] == "SEMANTIC_DEPENDENCIES_MISSING"
    assert applied.status_code == 409
    assert service.status()["job"]["state"] == "idle"
    assert client.get("/api/appliance/photos/library").json()["total"] == 1
    assert (
        client.post("/api/appliance/photos/search", json={"query": "holiday"}).json()["mode"]
        == "filename"
    )
    assert not service.db_path.exists()


def test_disabled_index_remains_browsable_and_blocks_without_model_calls(monkeypatch, tmp_path):
    adapter = _adapter(monkeypatch)
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "off")
    service = _service(tmp_path, adapter)

    assert service.plan_index()["blockers"][0]["code"] == "SEMANTIC_DISABLED"
    assert service.library()["items"][0]["name"] == "holiday.jpg"
    assert (
        adapter.build_index(
            service.root, service.db_path, ["holiday.jpg"], include_faces=False, max_files=1
        )["ok"]
        is False
    )


def test_incompatible_module_does_not_become_available_from_installed_packages(monkeypatch):
    adapter = _adapter(monkeypatch, build_index=None)

    assert adapter.available() is False
    assert adapter.readiness()["semantic"]["missingDependencies"] == []


def test_missing_pillow_only_disables_preview_not_filesystem_browsing(monkeypatch):
    readiness = _adapter(monkeypatch, missing=("PIL",)).readiness()

    assert readiness["browseAvailable"] is True
    assert readiness["previewAvailable"] is False
    assert readiness["semantic"]["available"] is False


def test_unsupported_safe_file_reads_do_not_advertise_preview_or_index(monkeypatch):
    adapter = _adapter(monkeypatch)
    monkeypatch.setattr(adapter, "_safe_file_access_available", lambda: False)

    readiness = adapter.readiness()

    assert readiness["browseAvailable"] is True
    assert readiness["previewAvailable"] is False
    assert readiness["semantic"]["available"] is False
    assert "secure-file-access" in readiness["semantic"]["missingDependencies"]
    assert readiness["modelDownloadMayBeRequired"] is False
