"""Empty-library cleanup through the actual adapter, SQLite transaction and job journal."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.agent_api import images
from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.data_access import DataAccessScope
from appliance.photos import AgentImageIndexAdapter, PhotoIndexConflict, PhotoLibraryService
from appliance.photos.router import create_photos_router
from runtime.memory.hemolymph import _image_index_builder as builder
from runtime.memory.hemolymph import image_semantic_index as index
from runtime.safety.auth.identity import encode_jwt_hs256

TABLES = (
    "image_clip",
    "image_faces",
    "image_meta",
    "image_tags",
    "image_ocr",
    "image_hashes",
    "image_quality",
    "image_fingerprints",
    "image_face_sources",
)
SECRET = "synthetic-empty-photo-cleanup-auth-key"
PASSWORD = "synthetic-approval-password"


def database_rows(service):
    with closing(sqlite3.connect(service.db_path)) as conn:
        return {
            name: conn.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in (*TABLES, "image_people", "image_categories", "image_index_settings")
        }


def make_empty_library(tmp_path: Path, monkeypatch):
    """Reusable synthetic service fixture; no image decoder or model may be called."""
    root = tmp_path / "nas"
    root.mkdir()
    original = root / "removed.jpg"
    original.write_bytes(b"synthetic original; never decoded")
    (root / "keep.txt").write_bytes(b"unrelated user document")
    trash = root / ".echo-trash"
    trash.mkdir()
    (trash / "keep.jpg").write_bytes(b"recycled original must not be deleted")

    def forbidden(*_args, **_kwargs):
        pytest.fail("empty cleanup must not initialize models, infer, or decode originals")

    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "off")
    monkeypatch.setattr(images, "_dependency_present", lambda _name: False)
    for name in (
        "_image_model",
        "_face_app",
        "_text_model",
        "_model_index_identity",
        "_load_image",
    ):
        monkeypatch.setattr(index, name, forbidden)
    adapter = AgentImageIndexAdapter()
    monkeypatch.setattr(adapter, "_module", lambda: index)
    service = PhotoLibraryService(root, tmp_path / "state", backend=adapter)
    vector = index._vec_to_blob([1.0, 0.0])
    with closing(index._open(service.db_path)) as conn, conn:
        conn.execute("INSERT INTO image_clip VALUES ('removed.jpg',?)", (vector,))
        conn.execute("INSERT INTO image_faces VALUES ('removed.jpg',0,?)", (vector,))
        conn.execute("INSERT INTO image_meta VALUES ('removed.jpg',8,8,1,'','.jpg','')")
        conn.execute("INSERT INTO image_tags VALUES ('removed.jpg','family',1)")
        conn.execute("INSERT INTO image_ocr VALUES ('removed.jpg','text')")
        conn.execute("INSERT INTO image_hashes VALUES ('removed.jpg','hash')")
        conn.execute("INSERT INTO image_quality VALUES ('removed.jpg',80)")
        conn.execute("INSERT INTO image_fingerprints VALUES ('removed.jpg','fingerprint')")
        conn.execute(
            "INSERT INTO image_face_sources VALUES ('removed.jpg','fingerprint','face-v1')"
        )
        conn.execute("INSERT INTO image_people VALUES ('Alice',?,0.45)", (vector,))
        conn.execute("INSERT INTO image_categories VALUES ('Family',?)", (vector,))
        conn.executemany(
            "INSERT INTO image_index_settings VALUES (?,?)",
            [
                ("root", str(root.resolve())),
                ("vision_identity", "vision-v1"),
                ("faces_identity", "face-v1"),
                ("library-note", "retain-applicable-settings"),
            ],
        )
    original.unlink()
    return service


def make_client(service, tmp_path, *, data_access=None):
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=SECRET)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(PASSWORD.encode()).hexdigest(),
        jwt_secret=SECRET,
        audit=audit,
        boot_nonce=b"synthetic-cleanup-boot-nonce" * 2,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=SECRET))
    app.include_router(
        create_photos_router(
            service,
            jwt_secret=SECRET,
            approval=approval,
            audit=audit,
            data_access=data_access,
        )
    )
    client = TestClient(app)
    client.cookies.set(
        "echo_session",
        encode_jwt_hs256(
            {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999},
            secret=SECRET,
        ),
    )
    return client


@pytest.fixture
def empty_library(tmp_path, monkeypatch):
    return make_empty_library(tmp_path, monkeypatch)


def test_empty_plan_exposes_exact_impact_without_any_model_dependencies(empty_library):
    service = empty_library
    plan = service.plan_index(include_faces=True)
    assert plan["ready"] is True and plan["cleanupOnly"] is True
    assert plan["imageCount"] == 0 and plan["includeFaces"] is False
    assert plan["requiresApproval"] and plan["approvalAction"] == "photos.index.build"
    assert plan["changes"] == [
        {"field": "indexedPhotos", "before": 1, "after": 0},
        {"field": "faceRecords", "before": 1, "after": 0},
        {"field": "derivedRecords", "before": 9, "after": 0},
    ]
    assert service.plan_index()["planId"] == plan["planId"]
    assert [warning["code"] for warning in plan["warnings"]] == ["EMPTY_LIBRARY_CLEANUP"]
    status = service.status()
    assert status["index"]["cleanupAvailable"] is True
    assert status["index"]["backendAvailable"] is False
    assert service.status(path_visible=lambda _path: True)["index"]["cleanupAvailable"] is False


def test_cleanup_commits_zero_rows_and_receipt_but_preserves_settings_prototypes_and_files(
    empty_library,
):
    service = empty_library
    before = database_rows(service)
    originals = {
        path.relative_to(service.root): path.read_bytes()
        for path in service.root.rglob("*")
        if path.is_file()
    }
    plan = service.plan_index(include_faces=True)
    service.start_index(plan_id=plan["planId"], include_faces=True)
    job = service.wait_for_idle()
    assert job["state"] == "succeeded" and job["cleanupOnly"] is True
    assert job["includeFaces"] is False
    assert job["result"] == {
        "ok": True,
        "indexed": 0,
        "faces": 0,
        "semantic": True,
        "face_capable": False,
        "reused": 0,
        "embedded": 0,
        "removed": 1,
        "skipped": 0,
    }
    after = database_rows(service)
    assert all(after[table] == [] for table in TABLES)
    for table in ("image_people", "image_categories"):
        assert after[table] == before[table]
    settings = dict(after["image_index_settings"])
    assert all(settings[key] == value for key, value in before["image_index_settings"])
    assert service._receipt_for_job(job) == job["result"]
    assert originals == {
        path.relative_to(service.root): path.read_bytes()
        for path in service.root.rglob("*")
        if path.is_file()
    }
    assert service.status()["index"]["cleanupAvailable"] is False
    assert "NO_IMAGES" in {item["code"] for item in service.plan_index()["blockers"]}


def test_cancel_before_commit_retains_all_sqlite_rows_and_settings(empty_library, monkeypatch):
    service = empty_library
    before = database_rows(service)
    entered, release = threading.Event(), threading.Event()
    original = builder.write_receipt

    def pause(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        original(*args, **kwargs)

    monkeypatch.setattr(builder, "write_receipt", pause)
    plan = service.plan_index()
    job = service.start_index(plan_id=plan["planId"], include_faces=False)
    try:
        assert entered.wait(5)
        assert service.cancel_index(job["jobId"])["state"] == "cancelling"
    finally:
        release.set()
    final = service.wait_for_idle()
    assert final["state"] == "cancelled" and final["cleanupOnly"] is True
    assert database_rows(service) == before
    assert service._receipt_for_job(job) is None


def test_restart_uses_commit_receipt_when_terminal_journal_write_failed(empty_library, monkeypatch):
    service = empty_library
    original = service._job_store.save

    def fail_terminal(job):
        if job["state"] == "succeeded":
            raise OSError("controlled journal persistence failure")
        return original(job)

    monkeypatch.setattr(service._job_store, "save", fail_terminal)
    plan = service.plan_index()
    service.start_index(plan_id=plan["planId"], include_faces=False)
    assert service.wait_for_idle()["error"] == "job_state_unavailable"
    assert service._job_store.load()["state"] == "running"
    restarted = PhotoLibraryService(
        service.root, service.index_dir.parent, backend=service._backend
    )
    job = restarted.status()["job"]
    assert job["state"] == "succeeded" and job["cleanupOnly"] is True
    assert job["result"]["indexed"] == 0 and job["result"]["removed"] == 1
    assert all(database_rows(service)[table] == [] for table in TABLES)


@pytest.mark.parametrize("failure", ["missing-root", "replaced-root", "unreadable-child"])
def test_unobservable_root_or_directory_never_becomes_an_empty_cleanup(
    empty_library, monkeypatch, failure
):
    service = empty_library
    before = database_rows(service)
    if failure == "missing-root":
        service.root.rename(service.root.with_name("temporarily-unmounted"))
    elif failure == "replaced-root":
        service.root.rename(service.root.with_name("previous-mounted-root"))
        service.root.mkdir()
    else:
        blocked = service.root / "unreadable"
        blocked.mkdir()
        (blocked / "still-here.jpg").write_bytes(b"must remain indexed until observable")
        original = os.scandir

        def scandir(path):
            if Path(path) == blocked:
                raise PermissionError("controlled directory read failure")
            return original(path)

        monkeypatch.setattr(os, "scandir", scandir)
    plan = service.plan_index()
    assert not plan["ready"] and plan["scanErrors"] > 0
    assert "PHOTO_SCAN_INCOMPLETE" in {item["code"] for item in plan["blockers"]}
    assert service.status()["index"]["cleanupAvailable"] is False
    with pytest.raises(PhotoIndexConflict):
        service.start_index(plan_id=plan["planId"], include_faces=False)
    assert database_rows(service) == before


def test_photo_arriving_after_approval_invalidates_the_plan(empty_library):
    service = empty_library
    plan = service.plan_index()
    before = database_rows(service)
    new = service.root / "arrived.jpg"
    new.write_bytes(b"new original must not be deleted")
    with pytest.raises(PhotoIndexConflict):
        service.start_index(plan_id=plan["planId"], include_faces=False)
    assert (
        database_rows(service) == before and new.read_bytes() == b"new original must not be deleted"
    )


def test_worker_rechecks_empty_library_before_touching_sqlite(empty_library, monkeypatch):
    service = empty_library
    plan = service.plan_index()
    before = database_rows(service)
    original = service._run_index_owned

    def changed(paths, include_faces, callback):
        (service.root / "arrived.jpg").write_bytes(b"arrived after job journal was saved")
        return original(paths, include_faces, callback)

    monkeypatch.setattr(service, "_run_index_owned", changed)
    service.start_index(plan_id=plan["planId"], include_faces=False)
    assert service.wait_for_idle()["state"] == "failed"
    assert database_rows(service) == before


def test_legacy_backend_cannot_silently_ignore_the_explicit_cleanup_contract(
    empty_library, monkeypatch
):
    service = empty_library
    monkeypatch.setattr(
        service._backend, "_module", lambda: SimpleNamespace(build_index=lambda **_kwargs: {})
    )
    plan = service.plan_index()
    assert plan["cleanupOnly"] is True and plan["ready"] is False
    assert "EMPTY_INDEX_CLEANUP_UNAVAILABLE" in {item["code"] for item in plan["blockers"]}


def test_explicit_engine_empty_only_refuses_new_images_before_models_or_database_writes(
    empty_library,
):
    service = empty_library
    before = database_rows(service)
    (service.root / "arrived.jpg").write_bytes(b"do not remove")
    result = index.build_index(
        service.root, db_path=service.db_path, empty_only=True, include_faces=False
    )
    assert result["error"] == "image_library_not_empty" and result["retained_previous"] is True
    assert database_rows(service) == before


def test_http_cleanup_requires_exact_single_use_approval_and_administrator(empty_library, tmp_path):
    service = empty_library
    client = make_client(service, tmp_path)
    endpoint = "/api/appliance/photos/plans/index"
    plan = client.post(endpoint, json={"includeFaces": True}).json()
    body = {"planId": plan["planId"], "includeFaces": False}
    assert client.post(endpoint + "/apply", json=body).status_code == 403
    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "photos.index.build",
            "target": plan["planId"],
            "password": PASSWORD,
        },
    )
    assert issued.status_code == 200
    headers = {APPROVAL_HEADER: issued.json()["approvalToken"]}
    assert client.post(endpoint + "/apply", json=body, headers=headers).status_code == 200
    assert service.wait_for_idle()["state"] == "succeeded"
    assert client.post(endpoint + "/apply", json=body, headers=headers).status_code == 409
    assert all(database_rows(service)[table] == [] for table in TABLES)


def test_cleanup_remains_available_when_only_ocr_or_tags_are_left(empty_library):
    service = empty_library
    with closing(sqlite3.connect(service.db_path)) as conn, conn:
        for table in TABLES:
            if table not in {"image_ocr", "image_tags"}:
                conn.execute(f"DELETE FROM {table}")
    plan = service.plan_index()
    assert plan["ready"] and plan["cleanupOnly"]
    assert plan["changes"] == [
        {"field": "indexedPhotos", "before": 0, "after": 0},
        {"field": "faceRecords", "before": 0, "after": 0},
        {"field": "derivedRecords", "before": 2, "after": 0},
    ]
    service.start_index(plan_id=plan["planId"], include_faces=False)
    assert service.wait_for_idle()["state"] == "succeeded"
    assert all(database_rows(service)[table] == [] for table in TABLES)


def test_approval_hash_binds_cleanup_impact_even_when_only_wal_changes(empty_library):
    service = empty_library
    with closing(sqlite3.connect(service.db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = service.plan_index()
        revision = service._db_revision()
        conn.execute("INSERT INTO image_ocr VALUES ('another-deleted.jpg','other text')")
        conn.commit()
        assert service._db_revision() == revision
        after = service.plan_index()
        assert after["planId"] != before["planId"]
        assert after["changes"][-1]["before"] == 10
        with pytest.raises(PhotoIndexConflict):
            service.start_index(plan_id=before["planId"], include_faces=False)


def test_worker_rechecks_root_identity_after_job_was_saved(empty_library, monkeypatch):
    service = empty_library
    plan = service.plan_index()
    before = database_rows(service)
    original = service._run_index_owned

    def changed(paths, include_faces, callback):
        service.root.rename(service.root.with_name("detached-original-root"))
        service.root.mkdir()
        return original(paths, include_faces, callback)

    monkeypatch.setattr(service, "_run_index_owned", changed)
    service.start_index(plan_id=plan["planId"], include_faces=False)
    assert service.wait_for_idle()["state"] == "failed"
    assert database_rows(service) == before


def test_member_cannot_plan_or_execute_whole_library_cleanup(empty_library, tmp_path):
    service = empty_library
    before = database_rows(service)
    client = make_client(
        service,
        tmp_path,
        data_access=SimpleNamespace(
            scope_for_actor=lambda actor: DataAccessScope(actor=actor, operator=False, rules=()),
        ),
    )
    client.cookies.set(
        "echo_session",
        encode_jwt_hs256(
            {"sub": "local:alice", "iat": 0, "exp": 9_999_999_999},
            secret=SECRET,
        ),
    )
    status = client.get("/api/appliance/photos/status").json()
    assert status["index"]["cleanupAvailable"] is False and status["index"]["indexed"] == 0
    endpoint = "/api/appliance/photos/plans/index"
    assert client.post(endpoint, json={"includeFaces": False}).status_code == 403
    plan = service.plan_index()
    assert client.post(endpoint + "/apply", json={"planId": plan["planId"]}).status_code == 403
    assert database_rows(service) == before
