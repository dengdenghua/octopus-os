"""Index outcomes survive service replacement without silently replaying work."""

from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path

import pytest

from appliance.photos import PhotoIndexConflict, PhotoLibraryService
from appliance.photos.job_store import PhotoJobStore, idle_job


class _Backend:
    def __init__(self, result=None):
        self.result = result or {"ok": True, "indexed": 1, "semantic": True, "faces": 0}
        self.calls = 0

    def available(self):
        return True

    def build_index(self, *_args, **_kwargs):
        self.calls += 1
        return self.result

    def search_by_text(self, *_args, **_kwargs):
        return None


def _service(tmp_path, backend=None):
    root = tmp_path / "nas"
    root.mkdir(exist_ok=True)
    if not (root / "one.jpg").exists():
        (root / "one.jpg").write_bytes(b"synthetic file for index orchestration")
    return PhotoLibraryService(root, tmp_path / "state", backend=backend or _Backend())


def test_finished_job_and_identity_survive_service_recreation(tmp_path):
    service = _service(tmp_path)
    plan = service.plan_index()
    service.start_index(plan_id=plan["planId"], include_faces=False)
    completed = service.wait_for_idle()

    restarted = _service(tmp_path)

    assert completed["state"] == "succeeded"
    assert restarted.status()["job"] == completed
    assert restarted._backend.calls == 0
    assert not list(service.index_dir.glob(".echo-photo-job-*"))


def _hold_index_worker(directory, connection):
    started = threading.Event()
    finish = threading.Event()

    class HeldBackend(_Backend):
        def build_index(self, *_args, **_kwargs):
            started.set()
            if not finish.wait(15):
                raise RuntimeError("parent did not release test worker")
            return super().build_index()

    service = _service(Path(directory), HeldBackend())
    plan = service.plan_index()
    service.start_index(plan_id=plan["planId"], include_faces=False)
    if not started.wait(5):
        raise RuntimeError("index worker did not start")
    connection.send(service.status()["job"])
    try:
        if connection.recv() == "finish":
            finish.set()
            connection.send(service.wait_for_idle())
    finally:
        finish.set()
        connection.close()


@pytest.mark.parametrize("crash", [True, False])
def test_live_process_owns_index_until_completion_or_process_exit(tmp_path, crash):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe()
    process = context.Process(target=_hold_index_worker, args=(str(tmp_path), sender))
    process.start()
    sender.close()
    try:
        assert receiver.poll(10), "child did not enter its real background worker"
        running = receiver.recv()
        assert running["state"] == "running"
        backend = _Backend()
        observer = _service(tmp_path, backend)

        assert observer.status()["job"] == running
        assert observer.plan_index()["ready"] is False
        with pytest.raises(PhotoIndexConflict, match="already running"):
            observer.start_index(plan_id=running["planId"], include_faces=False)
        assert backend.calls == 0
        assert PhotoJobStore(observer.index_dir).load()["state"] == "running"

        if crash:
            process.terminate()  # OS must release the lock without Python cleanup.
        else:
            receiver.send("finish")
            assert receiver.poll(10)
            assert receiver.recv()["state"] == "succeeded"
        process.join(timeout=5)
        assert not process.is_alive()

        observed = observer.status()["job"]
        assert observed["jobId"] == running["jobId"]
        assert observed["planId"] == running["planId"]
        if crash:
            assert observed["state"] == "failed"
            assert observed["error"] == "service_restart_interrupted"
            assert _service(tmp_path).status()["job"] == observed
        else:
            assert observed["state"] == "succeeded"
        assert backend.calls == 0
        retry = observer.plan_index()
        assert retry["ready"] is True
        observer.start_index(plan_id=retry["planId"], include_faces=False)
        assert observer.wait_for_idle()["state"] == "succeeded"
        assert backend.calls == 1
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        receiver.close()


def test_no_worker_runs_when_durable_start_cannot_be_written(tmp_path, monkeypatch):
    backend = _Backend()
    service = _service(tmp_path, backend)
    plan = service.plan_index()

    def disk_full(_job):
        raise OSError("private path and disk details must not reach the API")

    monkeypatch.setattr(service._job_store, "save", disk_full)
    with pytest.raises(PhotoIndexConflict, match="job state could not be saved"):
        service.start_index(plan_id=plan["planId"], include_faces=False)

    assert backend.calls == 0
    assert service.status()["job"]["state"] == "failed"
    assert service.status()["job"]["error"] == "job_state_unavailable"


def test_two_service_objects_in_one_process_cannot_share_an_active_writer(tmp_path):
    entered = threading.Event()
    finish = threading.Event()

    class HeldBackend(_Backend):
        def build_index(self, *_args, **_kwargs):
            entered.set()
            assert finish.wait(5)
            return super().build_index()

    owner = _service(tmp_path, HeldBackend())
    plan = owner.plan_index()
    owner.start_index(plan_id=plan["planId"], include_faces=False)
    try:
        assert entered.wait(5)
        observer = _service(tmp_path)
        assert observer.status()["job"]["state"] == "running"
        with pytest.raises(PhotoIndexConflict, match="already running"):
            observer.start_index(plan_id=plan["planId"], include_faces=False)
    finally:
        finish.set()
        assert owner.wait_for_idle()["state"] == "succeeded"
    assert observer.status()["job"]["state"] == "succeeded"


def test_rejected_stale_plan_releases_its_process_lease(tmp_path):
    service = _service(tmp_path)
    plan = service.plan_index()
    (service.root / "two.jpg").write_bytes(b"changed library")

    with pytest.raises(PhotoIndexConflict, match="plan changed"):
        service.start_index(plan_id=plan["planId"], include_faces=False)

    lease = service._job_store.try_lease()
    assert lease is not None
    lease.release()
    assert service._backend.calls == 0


def test_completion_write_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    service = _service(tmp_path)
    original_save = service._job_store.save

    def fail_completion(job):
        if job["state"] != "running":
            raise OSError("full")
        return original_save(job)

    monkeypatch.setattr(service._job_store, "save", fail_completion)
    plan = service.plan_index()
    service.start_index(plan_id=plan["planId"], include_faces=False)

    assert service.wait_for_idle()["error"] == "job_state_unavailable"
    assert _service(tmp_path).status()["job"]["error"] == "service_restart_interrupted"


def test_corrupt_job_is_explicit_while_photo_browsing_still_works(tmp_path):
    service = _service(tmp_path)
    service._job_store.path.write_text("{not-json", encoding="utf-8")

    restarted = _service(tmp_path)

    assert restarted.status()["job"]["error"] == "job_state_unreadable"
    assert restarted.library()["total"] == 1


def test_persisted_result_and_errors_do_not_leak_private_backend_fields(tmp_path):
    store = PhotoJobStore(tmp_path)
    store.save(
        {
            **idle_job(),
            "state": "failed",
            "error": "/private/model/cache failed",
            "result": {
                "indexed": 7,
                "faces": 0,
                "semantic": True,
                "privatePath": "/private/model/cache",
                "token": "hidden",
            },
        }
    )

    loaded = store.load()
    assert loaded["error"] == "index_build_failed"
    assert loaded["result"] == {"indexed": 7, "faces": 0, "semantic": True}
    assert "private" not in store.path.read_text(encoding="utf-8")


def test_requested_face_model_failure_is_not_silently_successful(tmp_path):
    backend = _Backend({"ok": True, "indexed": 1, "semantic": True, "face_capable": False})
    service = _service(tmp_path, backend)
    plan = service.plan_index(include_faces=True)
    service.start_index(plan_id=plan["planId"], include_faces=True)

    completed = service.wait_for_idle()

    assert completed["state"] == "failed"
    assert completed["error"] == "face_model_unavailable"
    assert completed["result"]["partial"] is True
    assert completed["result"]["indexed"] == 1
    assert completed["result"]["semantic"] is True
    assert _service(tmp_path).status()["job"] == completed


def test_unknown_journal_version_is_not_treated_as_idle(tmp_path):
    service = _service(tmp_path)
    service._job_store.path.write_text(
        json.dumps(
            {
                "schema": "echo.photos.index-job-state.v99",
                "job": idle_job(),
            }
        ),
        encoding="utf-8",
    )

    assert _service(tmp_path).status()["job"]["error"] == "job_state_unreadable"


def test_failed_atomic_publish_preserves_previous_job_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    store = PhotoJobStore(tmp_path)
    previous = store.save({**idle_job(), "state": "failed", "error": "index_build_failed"})

    def fail_replace(*_args):
        raise OSError("simulated write failure")

    monkeypatch.setattr("appliance.photos.job_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated write failure"):
        store.save({**idle_job(), "state": "succeeded"})

    assert store.load() == previous
    assert not list(tmp_path.glob(".echo-photo-job-*"))
