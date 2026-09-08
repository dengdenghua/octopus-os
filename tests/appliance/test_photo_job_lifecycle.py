"""Real worker/SQLite/OS-lease coverage for cancellation and commit recovery."""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.audit import ApplianceAudit
from appliance.data_access import DataAccessScope
from appliance.photos import AgentImageIndexAdapter, PhotoIndexConflict, PhotoLibraryService
from appliance.photos.job_store import PhotoJobStore, idle_job
from appliance.photos.router import create_photos_router
from runtime.safety.auth.identity import encode_jwt_hs256

_SUCCESS = {
    "ok": True,
    "indexed": 1,
    "semantic": True,
    "faces": 0,
    "face_capable": True,
    "reused": 0,
    "embedded": 1,
    "removed": 1,
}
_CANCELLED = {"ok": False, "error": "index_cancelled", "cancelled": True, "retained_previous": True}
_SECRET = "synthetic-photo-lifecycle-jwt-key-for-tests-only"


def _database(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS image_clip(path TEXT PRIMARY KEY);"
        "CREATE TABLE IF NOT EXISTS fixture_receipts("
        "root TEXT, job_id TEXT, plan_id TEXT, include_faces INTEGER, result TEXT);"
    )
    return conn


def _receipt_write(conn, root, job_id, plan_id, include_faces, result):
    conn.execute("DELETE FROM fixture_receipts")
    conn.execute(
        "INSERT INTO fixture_receipts VALUES (?, ?, ?, ?, ?)",
        (str(root), job_id, plan_id, int(include_faces), json.dumps(result)),
    )


class _Backend:
    """A transactional backend double; service, SQL and worker locks are real."""

    def __init__(self, *, commit_on_cancel=False, raise_after_commit=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.commit_on_cancel = commit_on_cancel
        self.raise_after_commit = raise_after_commit
        self.builds = 0
        self.reads = []
        self.identities = []

    def available(self):
        return True

    def search_by_text(self, *_args, **_kwargs):
        return None

    def build_index(
        self,
        root,
        db_path,
        image_paths,
        *,
        include_faces,
        max_files,
        job_id=None,
        plan_id=None,
        should_cancel=None,
    ):
        self.builds += 1
        self.identities.append((job_id, plan_id, should_cancel))
        with closing(_database(db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM image_clip")
            for image_path in image_paths[:max_files]:
                conn.execute("INSERT INTO image_clip VALUES (?)", (image_path,))
            self.entered.set()
            if not self.release.wait(10):
                raise RuntimeError("test did not release worker")
            if should_cancel is not None and should_cancel() and not self.commit_on_cancel:
                conn.rollback()
                return dict(_CANCELLED)
            _receipt_write(conn, root, job_id, plan_id, include_faces, _SUCCESS)
            conn.commit()
        if self.raise_after_commit:
            raise RuntimeError("synthetic post-commit worker failure")
        return dict(_SUCCESS)

    def index_job_receipt(self, root, *, db_path, job_id, plan_id, include_faces):
        self.reads.append((root, db_path, job_id, plan_id, include_faces))
        with closing(sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)) as conn:
            row = conn.execute(
                "SELECT result FROM fixture_receipts "
                "WHERE root=? AND job_id=? AND plan_id=? AND include_faces=?",
                (str(root), job_id, plan_id, int(include_faces)),
            ).fetchone()
        return json.loads(row[0]) if row else None


def _service(directory: Path, backend=None):
    root = directory / "nas"
    root.mkdir(exist_ok=True)
    if not (root / "one.jpg").exists():
        (root / "one.jpg").write_bytes(b"synthetic orchestration image")
    return PhotoLibraryService(root, directory / "state", backend=backend or _Backend())


def _start(service):
    plan = service.plan_index()
    job = service.start_index(plan_id=plan["planId"], include_faces=False)
    assert service._backend.entered.wait(5)
    return job


def _paths(service):
    with closing(sqlite3.connect(service.db_path)) as conn:
        return [row[0] for row in conn.execute("SELECT path FROM image_clip ORDER BY path")]


def _seed_previous(service):
    with closing(_database(service.db_path)) as conn, conn:
        conn.execute("INSERT INTO image_clip VALUES ('previous.jpg')")


def test_cancel_persists_intent_then_rolls_back_without_replacing_previous_index(tmp_path):
    backend = _Backend()
    service = _service(tmp_path, backend)
    _seed_previous(service)
    completed = []
    plan = service.plan_index()
    job = service.start_index(
        plan_id=plan["planId"], include_faces=False, on_complete=completed.append
    )
    try:
        assert backend.entered.wait(5)
        cancelling = service.cancel_index(job["jobId"])
        assert cancelling["state"] == "cancelling"
        assert PhotoJobStore(service.index_dir).load() == cancelling
        assert service.cancel_index(job["jobId"]) == cancelling
        assert service.plan_index()["ready"] is False
        assert backend.identities[0][:2] == (job["jobId"], job["planId"])
        assert backend.identities[0][2]() is True
    finally:
        backend.release.set()
    cancelled = service.wait_for_idle()
    assert cancelled["state"] == "cancelled"
    assert cancelled["error"] == "index_cancelled"
    assert cancelled["result"] == {
        key: value for key, value in _CANCELLED.items() if key != "error"
    }
    assert completed == [cancelled]
    assert _paths(service) == ["previous.jpg"]
    assert service.cancel_index(job["jobId"]) == cancelled
    assert _service(tmp_path).status()["job"] == cancelled


def test_pause_rolls_back_at_checkpoint_releases_lease_and_resumes(tmp_path):
    class PauseBackend:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.calls = 0

        def available(self):
            return True

        def supports_pause(self):
            return True

        def search_by_text(self, *_args, **_kwargs):
            return None

        def build_index(
            self,
            _root,
            _db_path,
            _image_paths,
            *,
            include_faces,
            max_files,
            job_id=None,
            plan_id=None,
            should_cancel=None,
            should_pause=None,
        ):
            del include_faces, max_files, job_id, plan_id, should_cancel
            self.calls += 1
            self.entered.set()
            if self.calls == 1:
                assert self.release.wait(5)
                if should_pause is not None and should_pause():
                    return {
                        "ok": False,
                        "error": "index_paused",
                        "paused": True,
                        "retained_previous": True,
                    }
            return dict(_SUCCESS)

    backend = PauseBackend()
    service = _service(tmp_path, backend)
    plan = service.plan_index()
    job = service.start_index(plan_id=plan["planId"], include_faces=False)
    assert backend.entered.wait(5)

    pausing = service.pause_index(job["jobId"])
    assert pausing["state"] == "pausing"
    assert PhotoJobStore(service.index_dir).load()["state"] == "pausing"
    backend.release.set()

    paused = service.wait_for_idle()
    assert paused["state"] == "paused"
    assert paused["completedAt"] is None
    assert paused["result"] == {"ok": False, "paused": True, "retained_previous": True}
    lease = service._job_store.try_lease()
    assert lease is not None
    lease.release()

    restarted = _service(tmp_path, backend)
    resumed = restarted.resume_index(job["jobId"])
    assert resumed["state"] == "running"
    completed = restarted.wait_for_idle()
    assert completed["state"] == "succeeded"
    assert backend.calls == 2


@pytest.mark.parametrize("raise_after_commit", [False, True])
def test_commit_evidence_wins_over_late_cancel_and_post_commit_exception(
    tmp_path, raise_after_commit
):
    backend = _Backend(commit_on_cancel=True, raise_after_commit=raise_after_commit)
    service = _service(tmp_path, backend)
    _seed_previous(service)
    job = _start(service)
    try:
        assert service.cancel_index(job["jobId"])["state"] == "cancelling"
    finally:
        backend.release.set()
    result = service.wait_for_idle()
    assert result["state"] == "succeeded"
    assert result["result"] == _SUCCESS
    assert result["error"] is None
    assert _paths(service) == ["one.jpg"]


def test_cancel_cannot_target_new_job_using_a_stale_job_id(tmp_path):
    backend = _Backend()
    service = _service(tmp_path, backend)
    job = _start(service)
    try:
        with pytest.raises(PhotoIndexConflict) as error:
            service.cancel_index("0" * 24)
        assert error.value.code == "photo_index_job_changed"
        assert service.status()["job"] == job
        assert not service._cancel_event.is_set()
    finally:
        backend.release.set()
        service.wait_for_idle()


def test_cancel_write_failure_does_not_send_signal_or_claim_cancellation(tmp_path, monkeypatch):
    backend = _Backend()
    service = _service(tmp_path, backend)
    job = _start(service)
    original_save = service._job_store.save

    def fail_cancelling(value):
        if value["state"] == "cancelling":
            raise OSError("synthetic journal failure")
        return original_save(value)

    monkeypatch.setattr(service._job_store, "save", fail_cancelling)
    try:
        with pytest.raises(PhotoIndexConflict) as error:
            service.cancel_index(job["jobId"])
        assert error.value.code == "photo_index_job_state_unavailable"
        assert service.status()["job"]["state"] == "running"
        assert not service._cancel_event.is_set()
        assert PhotoJobStore(service.index_dir).load()["state"] == "running"
    finally:
        backend.release.set()
    assert service.wait_for_idle()["state"] == "succeeded"


def test_legacy_kwargs_backend_keeps_working_without_claiming_cancel_support(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class LegacyBackend:
        def available(self):
            return True

        def build_index(self, *_args, **kwargs):
            assert set(kwargs) == {"include_faces", "max_files"}
            entered.set()
            assert release.wait(5)
            return dict(_SUCCESS)

    service = _service(tmp_path, LegacyBackend())
    plan = service.plan_index()
    job = service.start_index(plan_id=plan["planId"], include_faces=False)
    try:
        assert entered.wait(5)
        with pytest.raises(PhotoIndexConflict) as error:
            service.cancel_index(job["jobId"])
        assert error.value.code == "photo_index_cancel_unavailable"
        assert service.status()["job"]["state"] == "running"
    finally:
        release.set()
    assert service.wait_for_idle()["state"] == "succeeded"


def _process_owner(directory, pipe):
    backend = _Backend()
    service = _service(Path(directory), backend)
    job = _start(service)
    pipe.send(job)
    try:
        assert pipe.recv() == "cancel"
        pipe.send(service.cancel_index(job["jobId"]))
        assert pipe.recv() == "finish"
        backend.release.set()
        pipe.send(service.wait_for_idle())
    finally:
        backend.release.set()
        service.wait_for_idle()
        pipe.close()


def test_cancel_belongs_to_actual_worker_process_and_observer_never_interrupts_it(tmp_path):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe()
    process = context.Process(target=_process_owner, args=(str(tmp_path), sender))
    process.start()
    sender.close()
    try:
        assert receiver.poll(10)
        job = receiver.recv()
        observer = _service(tmp_path)
        with pytest.raises(PhotoIndexConflict) as error:
            observer.cancel_index(job["jobId"])
        assert error.value.code == "photo_index_not_owner"
        receiver.send("cancel")
        assert receiver.poll(5)
        cancelling = receiver.recv()
        assert cancelling["state"] == "cancelling"
        assert observer.status()["job"] == cancelling
        with pytest.raises(PhotoIndexConflict) as error:
            observer.cancel_index(job["jobId"])
        assert error.value.code == "photo_index_not_owner"
        assert observer._backend.reads == []
        receiver.send("finish")
        assert receiver.poll(5)
        finished = receiver.recv()
        assert finished["state"] == "cancelled"
        process.join(5)
        assert not process.is_alive() and process.exitcode == 0
        assert observer.status()["job"] == finished
        assert observer.cancel_index(job["jobId"]) == finished
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
        receiver.close()


def _abandoned_job(service, *, state="running", include_faces=False):
    return service._job_store.save(
        {
            **idle_job(),
            "state": state,
            "jobId": "a" * 24,
            "planId": "b" * 64,
            "includeFaces": include_faces,
            "startedAt": 1.0,
        }
    )


@pytest.mark.parametrize("state", ["running", "cancelling"])
@pytest.mark.parametrize("face_capable", [True, False])
def test_restart_recovers_only_committed_result_and_preserves_partial_face_semantics(
    tmp_path, state, face_capable
):
    service = _service(tmp_path)
    job = _abandoned_job(service, state=state, include_faces=True)
    receipt = {**_SUCCESS, "face_capable": face_capable, "privatePath": "/fixture/private"}
    with closing(_database(service.db_path)) as conn, conn:
        _receipt_write(conn, service.root, job["jobId"], job["planId"], True, receipt)
    backend = _Backend()
    recovered = _service(tmp_path, backend).status()["job"]
    assert recovered["jobId"] == job["jobId"]
    assert recovered["planId"] == job["planId"]
    assert recovered["state"] == ("succeeded" if face_capable else "failed")
    assert recovered["error"] == (None if face_capable else "face_model_unavailable")
    assert recovered["result"]["embedded"] == 1
    assert recovered["result"].get("partial", False) is (not face_capable)
    assert "privatePath" not in recovered["result"]
    assert backend.builds == 0
    assert len(backend.reads) == 1
    assert PhotoJobStore(service.index_dir).load() == recovered


@pytest.mark.parametrize("mismatch", ["root", "job_id", "plan_id", "include_faces", "missing"])
def test_restart_requires_exact_receipt_identity_otherwise_reports_interrupted(tmp_path, mismatch):
    service = _service(tmp_path)
    job = _abandoned_job(service, state="cancelling")
    with closing(_database(service.db_path)) as conn, conn:
        if mismatch != "missing":
            _receipt_write(
                conn,
                tmp_path if mismatch == "root" else service.root,
                "c" * 24 if mismatch == "job_id" else job["jobId"],
                "d" * 64 if mismatch == "plan_id" else job["planId"],
                mismatch == "include_faces",
                _SUCCESS,
            )
    backend = _Backend()
    recovered = _service(tmp_path, backend).status()["job"]
    assert recovered["state"] == "failed"
    assert recovered["error"] == "service_restart_interrupted"
    assert recovered["result"] is None
    assert backend.builds == 0


def _adapter(monkeypatch, build, receipt=None):
    def original(*_args, **_kwargs):
        return []

    module = SimpleNamespace(
        build_index=build,
        index_job_receipt=receipt,
        _iter_images=original,
        _load_image=original,
        _mtime=original,
    )
    adapter = AgentImageIndexAdapter()
    monkeypatch.setattr(adapter, "_module", lambda: module)
    monkeypatch.setattr(
        adapter,
        "readiness",
        lambda: {
            "semantic": {"available": True},
            "faces": {"available": True},
        },
    )
    return adapter, module, original


@pytest.mark.parametrize("modern", [True, False])
def test_adapter_forwards_only_explicit_runtime_protocol_parameters(tmp_path, monkeypatch, modern):
    received = []
    signal = threading.Event()

    def new_build(
        root, *, db_path, include_faces, max_files, job_id=None, plan_id=None, should_cancel=None
    ):
        received.append((job_id, plan_id, should_cancel))
        return dict(_SUCCESS)

    def old_build(root, *, db_path, include_faces, max_files):
        received.append("legacy")
        return dict(_SUCCESS)

    adapter, module, original = _adapter(monkeypatch, new_build if modern else old_build)
    assert adapter.supports_cancellation() is modern
    result = adapter.build_index(
        tmp_path,
        tmp_path / "index.db",
        [],
        include_faces=False,
        max_files=10,
        job_id="a" * 24,
        plan_id="b" * 64,
        should_cancel=signal.is_set,
    )
    assert result == _SUCCESS
    assert module._iter_images is original and module._load_image is original
    if modern:
        assert received[0][:2] == ("a" * 24, "b" * 64)
        signal.set()
        assert received[0][2]() is True
    else:
        assert received == ["legacy"]


def test_adapter_reads_receipt_without_model_readiness_or_writes(tmp_path, monkeypatch):
    calls = []

    def reader(root, *, db_path, job_id, plan_id, include_faces):
        calls.append((root, db_path, job_id, plan_id, include_faces))
        return dict(_SUCCESS)

    adapter, _, _ = _adapter(monkeypatch, None, reader)
    monkeypatch.setattr(adapter, "readiness", lambda: pytest.fail("receipt must not load models"))
    missing = tmp_path / "missing.db"
    assert (
        adapter.index_job_receipt(
            tmp_path, db_path=missing, job_id="a" * 24, plan_id="b" * 64, include_faces=False
        )
        is None
    )
    assert not missing.exists() and calls == []
    db_path = tmp_path / "index.db"
    db_path.write_bytes(b"fixture read-only boundary")
    before = db_path.read_bytes()
    assert (
        adapter.index_job_receipt(
            tmp_path, db_path=db_path, job_id="a" * 24, plan_id="b" * 64, include_faces=False
        )
        == _SUCCESS
    )
    assert len(calls) == 1 and db_path.read_bytes() == before


def _client(service, tmp_path, *, operator=True, audit=True):
    audit_store = (
        ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=_SECRET) if audit else None
    )

    class Policy:
        def scope_for_actor(self, actor):
            return DataAccessScope(actor=actor, operator=operator, rules=())

    app = FastAPI()
    app.include_router(
        create_photos_router(
            service,
            jwt_secret=_SECRET,
            audit=audit_store,
            data_access=Policy(),
        )
    )
    client = TestClient(app)
    client.cookies.set(
        "echo_session",
        encode_jwt_hs256(
            {"sub": "local:fixture", "iat": 0, "exp": 9_999_999_999},
            secret=_SECRET,
        ),
    )
    return client, audit_store


def test_cancel_route_uses_exact_job_id_permission_and_audit_without_new_approval(tmp_path):
    backend = _Backend()
    service = _service(tmp_path, backend)
    job = _start(service)
    client, audit = _client(service, tmp_path)
    try:
        response = client.post(f"/api/appliance/photos/index-jobs/{job['jobId']}/cancel")
        assert response.status_code == 200
        assert response.json()["schema"] == "echo.photos.index-job.v1"
        assert response.json()["job"]["state"] == "cancelling"
        events = [event["payload"] for event in audit.recent(10)]
        assert [event["outcome"] for event in events] == ["attempted", "succeeded"]
        assert all(event["action"] == "photos.index.cancel" for event in events)
        assert all(event["target"] == job["jobId"] for event in events)
        stale = client.post(f"/api/appliance/photos/index-jobs/{'f' * 24}/cancel")
        assert stale.status_code == 409
        assert stale.json()["detail"]["error"] == "photo_index_job_changed"
    finally:
        backend.release.set()
        service.wait_for_idle()


@pytest.mark.parametrize(("operator", "audit", "status"), [(False, True, 403), (True, False, 503)])
def test_cancel_route_denial_or_missing_audit_sends_no_cancellation_signal(
    tmp_path, operator, audit, status
):
    backend = _Backend()
    service = _service(tmp_path, backend)
    job = _start(service)
    client, _ = _client(service, tmp_path, operator=operator, audit=audit)
    try:
        response = client.post(f"/api/appliance/photos/index-jobs/{job['jobId']}/cancel")
        assert response.status_code == status
        assert service.status()["job"]["state"] == "running"
        assert not service._cancel_event.is_set()
    finally:
        backend.release.set()
        service.wait_for_idle()
