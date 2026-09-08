"""Preview admission with real leases/files; injected failures test policy only.

The cancellation case uses a fixed sleeping subprocess and actual OS protection.
Injected outcome/deadline cases do not claim to prove OS CPU or memory limits.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from appliance.agent_api import documents
from appliance.files import organization_plan as plans
from appliance.files.manager import FileManager
from appliance.files.organization import FileOrganizationService, OrganizationError
from runtime.execution.misc import document_extraction as extraction
from runtime.execution.misc import document_process_limits as process_limits
from runtime.platform.process.task_supervisor import TaskSupervisor

_ROOT = Path(__file__).resolve().parents[2]
_INVOICE = b"Invoice\nInvoice Date: 2026-09-05\nTotal: CNY 42.00\n"
_ENVIRONMENT = (
    "ECHO_DOCUMENT_MEMORY_MIB",
    "ECHO_DOCUMENT_CPU_SECONDS",
    "ECHO_DOCUMENT_WALL_SECONDS",
    "ECHO_DOCUMENT_SCAN_SECONDS",
    "ECHO_DOCUMENT_MAX_PAGES",
    "ECHO_DOCUMENT_EXPANDED_MIB",
)


@pytest.fixture(autouse=True)
def controlled_environment(monkeypatch):
    for name in _ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def library(tmp_path):
    root, state = tmp_path / "nas", tmp_path / "state"
    folder = root / "Invoices"
    folder.mkdir(parents=True)
    state.mkdir()
    tasks = TaskSupervisor.from_path(state / "tasks.json", holder_id="preview-test")
    service = FileOrganizationService(FileManager(root), state, supervisor=tasks)
    return service, folder, state, tasks


def _reopen(library):
    service, _, state, tasks = library
    return FileOrganizationService(service.manager, state, supervisor=tasks)


def _invoice(folder, name="one.txt"):
    path = folder / name
    path.write_bytes(_INVOICE)
    return path


def _ok(data=_INVOICE):
    return {"text": data.decode(), "truncated": False, "available": True, "outcome": "ok"}


def _expect_busy_without_original_read(service, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a rejected preview must not read original bytes")

    with monkeypatch.context() as patch:
        patch.setattr(plans.file_io, "read_file_snapshot", forbidden)
        with pytest.raises(OrganizationError) as busy:
            service.create_plan("alice", "Invoices")
        assert (busy.value.status, busy.value.error) == (409, "operation_busy")


@contextmanager
def _thread_holds(context):
    entered, release = threading.Event(), threading.Event()
    errors = []

    def run():
        try:
            with context():
                entered.set()
                assert release.wait(15), "test holder was not released"
        except BaseException as exc:
            errors.append(exc)
            entered.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert entered.wait(10) and not errors
        yield
    finally:
        release.set()
        thread.join(10)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize("reopened", [False, True])
def test_same_thread_preview_slot_rejects_before_reading_original(library, monkeypatch, reopened):
    service, folder, _, _ = library
    source = _invoice(folder)
    contender = _reopen(library) if reopened else service
    with service._preview.acquire():
        _expect_busy_without_original_read(contender, monkeypatch)
    assert source.read_bytes() == _INVOICE
    assert contender.create_plan("alice", "Invoices")["ready"] is True


def test_other_thread_and_instance_preview_slot_rejects_before_reading(library, monkeypatch):
    service, folder, _, _ = library
    _invoice(folder)
    contender = _reopen(library)
    with _thread_holds(service._preview.acquire):
        _expect_busy_without_original_read(contender, monkeypatch)
    assert contender.create_plan("alice", "Invoices")["ready"] is True


_HOLD_SLOT = """
import sys
from pathlib import Path
from appliance.files.organization_preview import OrganizationPreviewAdmission
with OrganizationPreviewAdmission(Path(sys.argv[1])).acquire():
    print('held', flush=True)
    sys.stdin.read(1)
"""


def test_independent_process_slot_rejects_before_reading_then_releases(library, monkeypatch):
    service, folder, state, _ = library
    _invoice(folder)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", _HOLD_SLOT, str(state)],
        cwd=_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    received = queue.Queue(maxsize=1)
    reader = threading.Thread(target=lambda: received.put(proc.stdout.readline(100)), daemon=True)
    reader.start()
    try:
        assert received.get(timeout=10).strip() == "held"
        _expect_busy_without_original_read(service, monkeypatch)
    finally:
        try:
            _, error = proc.communicate("x", timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, error = proc.communicate(timeout=5)
        reader.join(5)
    assert proc.returncode == 0, error
    assert not reader.is_alive()
    assert service.create_plan("alice", "Invoices")["ready"] is True


def test_preview_slot_is_independent_from_actual_file_move_lease(library):
    service, folder, _, _ = library
    source = _invoice(folder)
    plan = service.create_plan("alice", "Invoices")
    contender = _reopen(library)
    # The separate cross-process lock can be held while a real approved move runs.
    with contender._preview.acquire():
        result = service.apply("alice", plan["planId"])
    assert result["counts"]["moved"] == 1
    assert not source.exists() and (folder / "2026/09/one.txt").read_bytes() == _INVOICE
    # The converse also works when the actual mutation lease is held elsewhere.
    with _thread_holds(service.store.lease), contender._preview.acquire():
        pass


def test_scan_deadline_preserves_evidence_but_forbids_any_apply(library, monkeypatch):
    service, folder, _, _ = library
    for name in ("a.txt", "b.txt", "c.txt"):
        _invoice(folder, name)
    clock = SimpleNamespace(value=time.monotonic())
    monkeypatch.setattr(
        plans, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock.value)
    )
    calls = []

    def observed(data, extension):
        calls.append(data)
        if len(calls) == 2:
            clock.value += service.extraction_budget.scan_seconds + 1
            # Deliberately hostile partial text must not classify a failed file.
            return {**_ok(), "outcome": "timed_out"}
        return _ok(data)

    monkeypatch.setattr(plans, "extract_invoice_document", observed)
    plan = service.create_plan("alice", "Invoices")
    assert plan["scanComplete"] is False and plan["ready"] is False
    assert "scan_time_limit" in plan["blockers"]
    assert plan["summary"]["ready"] == 1
    assert len(calls) == 2 and len(plan["entries"]) == 2
    with pytest.raises(OrganizationError) as rejected:
        service.apply("alice", plan["planId"])
    assert (rejected.value.status, rejected.value.error) == (409, "plan_not_ready")
    assert not (folder / "2026").exists()
    assert all((folder / name).read_bytes() == _INVOICE for name in ("a.txt", "b.txt", "c.txt"))


@pytest.mark.parametrize("outcome", ["timed_out", "resource_limited", "worker_failed"])
def test_failed_file_partial_text_is_never_used_but_other_real_file_can_move(
    library, monkeypatch, outcome
):
    service, folder, _, _ = library
    bad = folder / "a.txt"
    bad.write_bytes(b"synthetic failed extraction source")
    good = _invoice(folder, "b.txt")
    extract = plans.extract_invoice_document

    def selective(data, extension):
        if data == bad.read_bytes():
            return {**_ok(), "outcome": outcome}
        return extract(data, extension)

    monkeypatch.setattr(plans, "extract_invoice_document", selective)
    plan = service.create_plan("alice", "Invoices")
    bad_item, good_item = plan["entries"]
    assert bad_item["status"] == "needs_review"
    assert bad_item["reason"] == f"extraction_{outcome}"
    assert bad_item["target"] is None and bad_item["date"] is None
    assert good_item["status"] == "ready" and good_item["date"] == "2026-09-05"
    assert plan["scanComplete"] is True and plan["ready"] is True
    result = service.apply("alice", plan["planId"])
    assert result["counts"]["moved"] == 1 and result["counts"]["skipped"] == 1
    assert bad.read_bytes() == b"synthetic failed extraction source"
    assert not good.exists() and (folder / "2026/09/b.txt").read_bytes() == _INVOICE


def test_unconfirmed_worker_cleanup_quarantines_slot_across_service_instances(library, monkeypatch):
    service, folder, _, _ = library
    source = _invoice(folder)
    contender = _reopen(library)

    def unconfirmed(*args, **kwargs):
        raise documents.DocumentWorkerCleanupError("synthetic cleanup uncertainty")

    monkeypatch.setattr(plans, "extract_invoice_document", unconfirmed)
    try:
        with pytest.raises(OrganizationError) as failed:
            service.create_plan("alice", "Invoices")
        assert (failed.value.status, failed.value.error) == (503, "extraction_unavailable")
        assert len(service._preview._quarantined) == 1
        assert service._preview._mutex.locked()
        _expect_busy_without_original_read(service, monkeypatch)
        _expect_busy_without_original_read(contender, monkeypatch)
        assert not list(service.store.directory.glob("*.json"))
        assert source.read_bytes() == _INVOICE
    finally:
        # No process was launched. Explicitly release only this injected test's
        # real lease, on its owning thread, so no synthetic quarantine survives.
        for lease in service._preview._quarantined:
            lease.__exit__(None, None, None)
        service._preview._quarantined.clear()
        if service._preview._mutex.locked():
            service._preview._mutex.release()
    monkeypatch.setattr(plans, "extract_invoice_document", lambda *args: _ok())
    assert contender.create_plan("alice", "Invoices")["ready"] is True


@pytest.mark.parametrize(
    "name,value",
    [
        ("ECHO_DOCUMENT_MEMORY_MIB", "0"),
        ("ECHO_DOCUMENT_MEMORY_MIB", "not-a-number"),
        ("ECHO_DOCUMENT_CPU_SECONDS", "1.5"),
        ("ECHO_DOCUMENT_CPU_SECONDS", "-1"),
        ("ECHO_DOCUMENT_WALL_SECONDS", "nan"),
        ("ECHO_DOCUMENT_WALL_SECONDS", "inf"),
        ("ECHO_DOCUMENT_SCAN_SECONDS", "0"),
        ("ECHO_DOCUMENT_SCAN_SECONDS", "-inf"),
        ("ECHO_DOCUMENT_MAX_PAGES", "-1"),
        ("ECHO_DOCUMENT_EXPANDED_MIB", "0"),
    ],
)
def test_invalid_environment_budget_rejects_service_before_original_reads(
    library, monkeypatch, name, value
):
    service, folder, _, _ = library
    source = _invoice(folder)

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid server resource configuration must not read documents")

    monkeypatch.setenv(name, value)
    monkeypatch.setattr(plans.file_io, "read_file_snapshot", forbidden)
    with pytest.raises(ValueError):
        _reopen(library)
    assert source.read_bytes() == _INVOICE
    assert not list(service.store.directory.glob("*.json"))


def test_parallel_service_request_contexts_are_isolated_and_reset(tmp_path, monkeypatch):
    services, folders = [], []
    for index in (0, 1):
        root, state = tmp_path / f"nas-{index}", tmp_path / f"state-{index}"
        folder = root / "Invoices"
        folder.mkdir(parents=True)
        _invoice(folder)
        services.append(
            FileOrganizationService(
                FileManager(root),
                state,
                extraction_budget=extraction.DocumentExtractionBudget(max_pages=index + 1),
            )
        )
        folders.append(folder)
    rendezvous = threading.Barrier(2, timeout=10)
    observed, errors, results = {}, [], []

    def capture(data, extension, **context):
        if context:
            observed[threading.current_thread().name] = dict(context)
            rendezvous.wait()
        return _ok(data)

    monkeypatch.setattr(extraction, "extract_document_isolated", capture)

    def run(index):
        try:
            results.append(services[index].create_plan(f"actor-{index}", "Invoices"))
            assert documents._context.get() is None
            assert documents.extract_invoice_document(_INVOICE, "txt")["outcome"] == "ok"
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,), name=f"request-{i}") for i in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not errors and all(not thread.is_alive() for thread in threads)
    assert len(results) == 2 and all(plan["ready"] for plan in results)
    for index, service in enumerate(services):
        context = observed[f"request-{index}"]
        assert context["budget"] is service.extraction_budget
        assert context["cancel"] is service._preview.cancel
        assert context["deadline"] > time.monotonic()
        assert (folders[index] / "one.txt").read_bytes() == _INVOICE
    assert documents._context.get() is None


def test_nested_extraction_context_is_restored_after_exception(monkeypatch):
    seen = []
    monkeypatch.setattr(
        extraction,
        "extract_document_isolated",
        lambda data, extension, **context: seen.append(context) or _ok(),
    )
    outer, inner = object(), object()
    cancel = threading.Event()
    with documents.invoice_extraction_context(budget=outer, deadline=100, cancel=cancel):
        with (
            pytest.raises(RuntimeError),
            documents.invoice_extraction_context(budget=inner, deadline=200, cancel=cancel),
        ):
            documents.extract_invoice_document(_INVOICE, "txt")
            raise RuntimeError("synthetic request failure")
        documents.extract_invoice_document(_INVOICE, "txt")
    documents.extract_invoice_document(_INVOICE, "txt")
    assert seen[0]["budget"] is inner and seen[0]["deadline"] == 200
    assert seen[1]["budget"] is outer and seen[1]["deadline"] == 100
    assert seen[2] == {} and documents._context.get() is None


_SLEEP_WORKER = """
import json, os, struct, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from runtime.execution.misc.document_process_limits import apply_worker_limits
def read(count):
    value = bytearray()
    while len(value) < count:
        chunk = sys.stdin.buffer.read(count - len(value))
        if not chunk: raise EOFError
        value.extend(chunk)
    return bytes(value)
config = json.loads(read(struct.unpack('!I', read(4))[0]))
facts = apply_worker_limits(config['process'], config['memory_bytes'], config['cpu_seconds'])
response = json.dumps({'version': 1, 'phase': 'ready', 'limits': facts}).encode()
sys.stdout.buffer.write(struct.pack('!I', len(response)) + response)
sys.stdout.buffer.flush()
read(config['input_bytes'])
marker = Path(sys.argv[2])
temporary = marker.with_suffix('.tmp')
temporary.write_text(json.dumps(facts), encoding='utf-8')
os.replace(temporary, marker)
time.sleep(30)
"""


def _alive(pid):
    if os.name == "nt":
        api = process_limits._windows_api()
        handle = api.OpenProcess(process_limits._PARENT_RIGHTS, False, pid)
        if not handle:
            assert ctypes.get_last_error() == 87
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == process_limits._WAIT_TIMEOUT
        finally:
            api.CloseHandle(handle)
    try:
        return process_limits._linux_stat(pid)["state"] not in {"Z", "X"}
    except FileNotFoundError:
        return False


@pytest.mark.skipif(sys.platform not in {"win32", "linux"}, reason="requires implemented OS limits")
def test_shutdown_cancels_actual_protected_sleep_worker_and_refuses_new_preview(
    library, tmp_path, monkeypatch
):
    service, folder, _, _ = library
    source = _invoice(folder)
    marker = tmp_path / "actual-worker-limits.json"
    monkeypatch.setattr(
        extraction,
        "_worker_command",
        lambda: [sys.executable, "-u", "-c", _SLEEP_WORKER, str(_ROOT), str(marker)],
    )
    attached, cleanup, results, errors = [], [], [], []
    original_attach = process_limits.ParentProcessLimits.attach
    original_terminate = process_limits.ParentProcessLimits.terminate

    def attach(guard, proc):
        attached.append((guard, proc))
        return original_attach(guard, proc)

    def terminate(guard, proc, **kwargs):
        stopped = original_terminate(guard, proc, **kwargs)
        cleanup.append(stopped)
        return stopped

    monkeypatch.setattr(process_limits.ParentProcessLimits, "attach", attach)
    monkeypatch.setattr(process_limits.ParentProcessLimits, "terminate", terminate)

    def run():
        try:
            results.append(service.create_plan("alice", "Invoices"))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), errors
        facts = json.loads(marker.read_text(encoding="utf-8"))
        assert facts["ready"] is True and facts["parent_pid"] == os.getpid()
        assert facts["worker_pid"] != os.getpid()
        assert facts["memory_bytes"] == service.extraction_budget.memory_bytes
        assert _alive(facts["worker_pid"])
        service.shutdown()
    finally:
        service.shutdown()
        thread.join(10)
        for guard, proc in attached:
            if proc.poll() is None:
                try:
                    original_terminate(guard, proc, timeout_s=3)
                finally:
                    guard.close()
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=3)
    assert not thread.is_alive() and not errors
    assert cleanup == [True] and not _alive(facts["worker_pid"])
    assert all(proc.poll() is not None for _, proc in attached)
    (plan,) = results
    assert plan["ready"] is False and plan["scanComplete"] is False
    assert "scan_cancelled" in plan["blockers"]
    assert plan["entries"][0]["reason"] == "extraction_cancelled"
    assert source.read_bytes() == _INVOICE and not (folder / "2026").exists()
    _expect_busy_without_original_read(service, monkeypatch)
    assert not service._preview._quarantined
    # Cancellation released the OS slot; a separately initialized live service
    # can acquire it, without reviving the shut-down service instance.
    with _reopen(library)._preview.acquire():
        pass
