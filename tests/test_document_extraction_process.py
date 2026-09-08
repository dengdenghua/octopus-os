"""Real transport/OS-worker tests with synthetic bytes and fixed tmp scripts.

Only the command factory is replaced for protocol fault injection. Popen, pipe
IO, deadlines, cancellation and OS process limits all execute for real.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from runtime.execution.misc import document_extraction as extraction
from runtime.execution.misc import document_process_limits as limits

pytestmark = pytest.mark.skipif(
    sys.platform not in {"win32", "linux"}, reason="requires implemented real OS process protection"
)
_ROOT = Path(__file__).resolve().parents[1]
_TEXT = "INVOICE\nInvoice Date: 2026-09-05\nTotal: USD 1234.50"

# Injected scripts execute only modes selected by this test file. No shell,
# document path, service state or untrusted input chooses their code.
_SCRIPT = r"""
import hashlib, json, os, struct, sys, threading, time
from pathlib import Path
sys.path.insert(0, ROOT)
from runtime.execution.misc.document_process_limits import apply_worker_limits

def exact(count):
    result = bytearray()
    while len(result) < count:
        piece = sys.stdin.buffer.read(min(65536, count - len(result)))
        if not piece:
            raise EOFError('synthetic worker input closed')
        result.extend(piece)
    return bytes(result)

def send(value):
    data = json.dumps(value, ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('!I', len(data)) + data)
    sys.stdout.buffer.flush()

size = struct.unpack('!I', exact(4))[0]
assert 0 < size <= 16384
c = json.loads(exact(size))
facts = apply_worker_limits(c['process'], c['memory_bytes'], c['cpu_seconds'])
audit = {
    'limits': facts, 'immediate_parent_pid': os.getppid(),
    'service_secret_present': 'ECHO_SYNTHETIC_SERVICE_SECRET' in os.environ,
    'api_key_present': 'OPENAI_API_KEY' in os.environ,
    'pythonpath_present': 'PYTHONPATH' in os.environ,
}
if sys.platform == 'win32':
    from runtime.execution.misc.document_process_limits import _windows_api, _win_identity, _PARENT_RIGHTS
    api = _windows_api()
    launcher = api.OpenProcess(_PARENT_RIGHTS, False, os.getppid())
    try:
        audit['launcher_identity'] = _win_identity(api, launcher)
    finally:
        api.CloseHandle(launcher)
def save():
    pending = Path(AUDIT).with_suffix('.pending')
    pending.write_text(json.dumps(audit), encoding='utf-8')
    # The test's concurrent reader may briefly hold the previous marker without
    # FILE_SHARE_DELETE. This retry concerns only synthetic phase evidence.
    for attempt in range(100):
        try:
            os.replace(pending, AUDIT)
            break
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.005)

def ready():
    reported = dict(facts)
    if MODE == 'bad_identity':
        reported['worker_identity'] = str(int(reported['worker_identity']) + 1)
    send({'version': 1, 'phase': 'ready', 'limits': reported})

def result(**override):
    value = {'version': 1, 'phase': 'result', 'outcome': 'ok',
             'text': 'synthetic partial must be discarded', 'truncated': False}
    value.update(override)
    send(value)

save()
if MODE in ('pre_ready', 'no_ready'):
    byte = []
    def read_one():
        byte.append(exact(1))
    thread = threading.Thread(target=read_one, daemon=True)
    thread.start()
    time.sleep(0.2)
    audit['received_before_ready'] = bool(byte)
    save()
    if MODE == 'no_ready':
        time.sleep(30)
    ready()
    thread.join(5)
    assert len(byte) == 1
    data = byte[0] + exact(c['input_bytes'] - 1)
elif MODE == 'early_result':
    result()
    sys.exit(0)
else:
    ready()
    if MODE == 'no_input':
        # Observe actual pipe bytes without consuming them; the parent is now
        # trying to write the larger-than-pipe payload, not merely awaiting ready.
        if sys.platform == 'win32':
            import ctypes, msvcrt
            peek = ctypes.WinDLL('kernel32', use_last_error=True).PeekNamedPipe
            peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                             ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
            peek.restype = ctypes.c_int
            available = ctypes.c_uint32()
            while not available.value:
                assert peek(msvcrt.get_osfhandle(sys.stdin.fileno()), None, 0,
                            None, ctypes.byref(available), None)
                time.sleep(0.01)
        else:
            import select
            assert select.select([sys.stdin.fileno()], [], [], 3)[0]
        audit['input_buffered'] = True
        save()
        time.sleep(30)
    data = exact(c['input_bytes'])

audit['received_bytes'] = len(data)
audit['sha256'] = hashlib.sha256(data).hexdigest()
save()
if MODE == 'no_result':
    time.sleep(30)
elif MODE == 'result_no_exit':
    result()
    audit['result_sent'] = True
    save()
    time.sleep(30)
elif MODE == 'huge_frame':
    sys.stdout.buffer.write(struct.pack('!I', c['max_chars'] * 4 + 16385))
    sys.stdout.buffer.flush()
    time.sleep(30)
elif MODE == 'no_newline_output':
    for _ in range(8):
        sys.stdout.buffer.write(b'x' * 65536)
        sys.stdout.buffer.flush()
    time.sleep(30)
elif MODE in ('bad_json', 'bad_utf8', 'not_object'):
    payload = {'bad_json': b'{oops', 'bad_utf8': b'\xff', 'not_object': b'[]'}[MODE]
    sys.stdout.buffer.write(struct.pack('!I', len(payload)) + payload)
    sys.stdout.buffer.flush()
elif MODE == 'extra_frame':
    result()
    result()
elif MODE == 'partial_fail':
    result(outcome='worker_failed', truncated=True)
elif MODE == 'invalid_truncated':
    result(truncated='false')
elif MODE == 'oversized_text':
    result(text='x' * (c['max_chars'] + 1), truncated=False)
elif MODE == 'nonzero_exit':
    result()
    sys.exit(7)
else:
    from runtime.execution.misc.document_text_extractor import extract_document_text
    parsed = extract_document_text(data, c['extension'], max_chars=c['max_chars'],
        max_pages=c['max_pages'], max_expanded_bytes=c['max_expanded_bytes'])
    result(text=parsed.text if parsed else None,
           truncated=parsed.truncated if parsed else False,
           outcome='ok' if parsed else 'no_text')
"""


def _budget(**overrides):
    return extraction.DocumentExtractionBudget(
        **{
            "memory_bytes": 256 * 1024 * 1024,
            "cpu_seconds": 6,
            "wall_seconds": 4.0,
            "max_chars": 4096,
            "max_pages": 10,
            "max_expanded_bytes": 1024 * 1024,
            **overrides,
        }
    )


def _dead(pid: int, identity=None) -> bool:
    assert pid > 0 and pid != os.getpid()
    if sys.platform == "win32":
        api = limits._windows_api()
        handle = api.OpenProcess(limits._PARENT_RIGHTS, False, pid)
        if not handle:
            assert ctypes.get_last_error() == 87
            return True
        try:
            if identity is not None and limits._win_identity(api, handle) != identity:
                return True  # the original exited; this PID now names a different process
            return api.WaitForSingleObject(handle, 0) == 0
        finally:
            api.CloseHandle(handle)
    try:
        return limits._linux_stat(pid)["state"] in {"Z", "X"}
    except FileNotFoundError:
        return True


def _assert_clean(result, audit=None):
    launcher_identity = audit.get("launcher_identity") if audit else None
    assert _dead(result["worker_pid"], launcher_identity), "launcher must be reclaimed"
    facts = audit["limits"] if audit else result["limits"]
    assert facts["ready"] is True
    assert _dead(facts["worker_pid"], facts["worker_identity"]), (
        "actual parser must be reclaimed too"
    )
    assert not any(
        thread.is_alive() and thread.name in {"echo-document-input", "echo-document-output"}
        for thread in threading.enumerate()
    ), "pipe threads must not leak after cleanup"


@pytest.fixture
def script(tmp_path, monkeypatch):
    def configure(mode):
        source = tmp_path / "fixed-document-test-worker.py"
        audit = tmp_path / "worker-facts.json"
        source.write_text(
            f"ROOT = {str(_ROOT)!r}\nMODE = {mode!r}\nAUDIT = {str(audit)!r}\n" + _SCRIPT,
            encoding="utf-8",
        )
        monkeypatch.setattr(
            extraction, "_worker_command", lambda: [sys.executable, "-I", str(source)]
        )
        return audit

    return configure


def _read_audit(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _wait_audit(path, *, field=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            value = _read_audit(path)
            if field is None or field in value:
                return value
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.01)
    raise AssertionError("fixed worker did not reach the expected real IO phase")


def _pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 40 700 Td (Invoice Date: 2026-09-05 Total: USD 1234.50) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


@pytest.mark.parametrize("extension", ["txt", "pdf"])
def test_real_fixed_product_worker_extracts_and_reports_applied_limits(extension):
    data = _TEXT.encode() if extension == "txt" else _pdf()
    result = extraction.extract_document_isolated(data, extension, budget=_budget())
    assert result["outcome"] == "ok", result
    assert "Invoice Date: 2026-09-05" in result["text"]
    assert "1234.50" in result["text"]
    assert result["truncated"] is False
    assert result["limits"]["memory_bytes"] == 256 * 1024 * 1024
    assert result["limits"]["cpu_seconds"] == 6
    _assert_clean(result)


def test_real_pipes_never_send_document_before_ready_and_do_not_inherit_service_env(
    script, monkeypatch
):
    path = script("pre_ready")
    monkeypatch.setenv("ECHO_SYNTHETIC_SERVICE_SECRET", "synthetic-test-value")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-api-key")
    monkeypatch.setenv("PYTHONPATH", "synthetic-untrusted-search-path")
    data = _TEXT.encode()
    result = extraction.extract_document_isolated(data, "txt", budget=_budget())
    audit = _read_audit(path)
    assert result["outcome"] == "ok", result
    assert audit["received_before_ready"] is False
    assert audit["received_bytes"] == len(data)
    assert audit["sha256"] == hashlib.sha256(data).hexdigest()
    assert not audit["service_secret_present"]
    assert not audit["api_key_present"]
    assert not audit["pythonpath_present"]
    _assert_clean(result, audit)


def test_parent_verifies_reported_worker_identity_before_sending_any_document(script):
    path = script("bad_identity")
    result = extraction.extract_document_isolated(_TEXT.encode(), "txt", budget=_budget())
    assert result["outcome"] == "unavailable"
    assert result["text"] is None
    assert result["limits"] is None, (
        "rejected identity must not be reported as verified ready facts"
    )
    audit = _read_audit(path)
    assert "received_bytes" not in audit
    _assert_clean(result, audit)


@pytest.mark.parametrize("mode", ["no_ready", "no_input", "no_result", "result_no_exit"])
def test_wall_deadline_reclaims_each_stalled_pipe_phase_without_partial_text(script, mode):
    path = script(mode)
    # Well above pipe capacity, so no_input exercises a blocked real write.
    data = b"synthetic\n" * 600_000 if mode == "no_input" else _TEXT.encode()
    started = time.monotonic()
    result = extraction.extract_document_isolated(data, "txt", budget=_budget(wall_seconds=1.2))
    assert result["outcome"] == "timed_out", result
    assert result["text"] is None and result["truncated"] is False
    assert time.monotonic() - started < 4.5
    audit = _read_audit(path)
    if mode == "no_ready":
        assert audit["received_before_ready"] is False
    _assert_clean(result, audit)


@pytest.mark.parametrize("mode", ["no_ready", "no_input", "no_result", "result_no_exit"])
def test_cancel_during_real_stalled_io_reclaims_worker_and_never_keeps_partial_text(script, mode):
    path = script(mode)
    cancel = threading.Event()
    data = b"synthetic\n" * 600_000 if mode == "no_input" else _TEXT.encode()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            extraction.extract_document_isolated,
            data,
            "txt",
            budget=_budget(),
            cancel=cancel,
        )
        audit = _wait_audit(
            path,
            field={
                "no_ready": "received_before_ready",
                "no_input": "input_buffered",
                "no_result": "received_bytes",
                "result_no_exit": "result_sent",
            }[mode],
        )
        process_handle = None
        if sys.platform == "win32":
            api = limits._windows_api()
            process_handle = api.OpenProcess(
                limits._PARENT_RIGHTS, False, audit["limits"]["worker_pid"]
            )
            assert process_handle
            assert limits._win_identity(api, process_handle) == audit["limits"]["worker_identity"]
        started = time.monotonic()
        try:
            cancel.set()
            result = future.result(timeout=4)
            if process_handle:
                assert api.WaitForSingleObject(process_handle, 0) == 0
        finally:
            if process_handle:
                api.CloseHandle(process_handle)
    assert result["outcome"] == "cancelled", result
    assert result["text"] is None and result["truncated"] is False
    assert time.monotonic() - started < 4
    _assert_clean(result, _read_audit(path))


@pytest.mark.parametrize(
    "mode",
    [
        "huge_frame",
        "no_newline_output",
        "bad_json",
        "bad_utf8",
        "not_object",
        "extra_frame",
        "partial_fail",
        "invalid_truncated",
        "oversized_text",
        "early_result",
        "nonzero_exit",
    ],
)
def test_real_protocol_failures_discard_text_and_reclaim_processes(script, mode):
    path = script(mode)
    result = extraction.extract_document_isolated(_TEXT.encode(), "txt", budget=_budget())
    assert result["outcome"] == "worker_failed", result
    assert result["text"] is None and result["truncated"] is False
    _assert_clean(result, _read_audit(path))


def test_already_cancelled_does_not_invoke_command_or_start_process(monkeypatch):
    def forbidden():
        raise AssertionError("cancelled request must not launch a worker")

    monkeypatch.setattr(extraction, "_worker_command", forbidden)
    cancel = threading.Event()
    cancel.set()
    result = extraction.extract_document_isolated(
        _TEXT.encode(), "txt", budget=_budget(), cancel=cancel
    )
    assert result == {"text": None, "truncated": False, "available": True, "outcome": "cancelled"}


def test_expired_scan_deadline_does_not_start_process(monkeypatch):
    def forbidden():
        raise AssertionError("expired scan budget must not launch a worker")

    monkeypatch.setattr(extraction, "_worker_command", forbidden)
    result = extraction.extract_document_isolated(
        _TEXT.encode(),
        "txt",
        budget=_budget(),
        deadline=time.monotonic() - 1,
    )
    assert result["outcome"] == "timed_out" and result["text"] is None
