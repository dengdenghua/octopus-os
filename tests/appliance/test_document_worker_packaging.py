"""The frozen worker must never import or recursively start the normal CLI."""

from __future__ import annotations

import builtins
import json
import os
import queue
import runpy
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "packaging/windows/echo_backend_entry.py"


def test_worker_flag_dispatches_before_any_cli_import(monkeypatch):
    import sys

    calls = []
    worker = ModuleType("runtime.execution.misc.document_worker")

    def main():
        calls.append(tuple(sys.argv))
        return 37

    worker.main = main
    monkeypatch.setitem(sys.modules, worker.__name__, worker)
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "runtime.cli" or name.startswith("runtime.cli."):
            raise AssertionError("worker imported the application CLI")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "argv", [str(ENTRY), "--echo-document-worker"])
    with monkeypatch.context() as imports:
        imports.setattr(builtins, "__import__", checked_import)
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(str(ENTRY), run_name="__main__")
    assert exit_info.value.code == 37
    assert calls == [(str(ENTRY),)]


@pytest.mark.parametrize(
    "args",
    [
        ["--echo-document-worker", "--port", "8000"],
        ["serve", "--echo-document-worker"],
        ["--echo-document-worker", "--echo-document-worker"],
    ],
)
def test_malformed_worker_invocation_cannot_fall_through_to_server(monkeypatch, args):
    import sys

    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "runtime" or name.startswith("runtime."):
            raise AssertionError("malformed worker invocation imported runtime")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "argv", [str(ENTRY), *args])
    with monkeypatch.context() as imports:
        imports.setattr(builtins, "__import__", checked_import)
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(str(ENTRY), run_name="__main__")
    assert exit_info.value.code == 2


def test_normal_backend_invocation_retains_cli_arguments(monkeypatch):
    import sys

    calls = []
    cli = ModuleType("runtime.cli")
    cli.main = lambda: calls.append(tuple(sys.argv)) or 29
    monkeypatch.setitem(sys.modules, "runtime.cli", cli)
    monkeypatch.setattr(sys, "argv", [str(ENTRY), "serve", "--port", "8000"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(ENTRY), run_name="__main__")
    assert exit_info.value.code == 29
    assert calls == [(str(ENTRY), "serve", "--port", "8000")]


@pytest.mark.parametrize("platform", ["windows", "linux", "macos"])
def test_release_spec_keeps_worker_and_extractor_in_frozen_bundle(monkeypatch, platform):
    """Evaluate each real spec with packaging objects replaced, not build an OS."""
    import sys

    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_submodules = lambda _name: []
    hooks.collect_data_files = lambda *_args, **_kwargs: []
    monkeypatch.setitem(sys.modules, hooks.__name__, hooks)
    captured = {}

    class Analysis:
        def __init__(self, scripts, **kwargs):
            captured.update(scripts=scripts, **kwargs)
            self.pure = self.scripts = self.binaries = self.datas = []

    spec = ROOT / f"packaging/{platform}/echo-backend.spec"
    runpy.run_path(
        str(spec),
        init_globals={
            "SPECPATH": str(spec.parent),
            "Analysis": Analysis,
            "PYZ": lambda *_args, **_kwargs: object(),
            "EXE": lambda *_args, **_kwargs: object(),
        },
    )
    assert captured["scripts"] == [str(ENTRY)]
    assert {
        "runtime.execution.misc.document_extraction",
        "runtime.execution.misc.document_worker",
        "runtime.execution.misc.document_process_limits",
        "runtime.execution.misc.document_text_extractor",
        "runtime.execution.misc.notebook_extractor",
        "pypdf",
        "defusedxml",
    } <= set(captured["hiddenimports"])
    assert not {"pypdf", "defusedxml"} & set(captured["excludes"])


@pytest.fixture
def frozen_worker():
    value = os.environ.get("ECHO_TEST_DOCUMENT_WORKER_EXE")
    if sys.platform != "win32" or not value:
        pytest.skip("opt-in real Windows PyInstaller worker artifact required")
    executable = Path(value).resolve(strict=True)
    assert executable.suffix.lower() == ".exe"
    return executable


def _pdf_bytes() -> bytes:
    content = b"BT /F1 12 Tf 36 740 Td (Frozen invoice 2026-09-05 Amount 128.50) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    data, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(data)


def _read_worker_frame(stream, timeout=15):
    output = queue.Queue()

    def read():
        try:
            header = stream.read(4)
            size = struct.unpack("!I", header)[0]
            assert 0 < size < 1024 * 1024
            output.put(json.loads(stream.read(size)))
        except BaseException as exc:
            output.put(exc)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    value = output.get(timeout=timeout)
    if isinstance(value, BaseException):
        raise value
    thread.join(timeout=1)
    assert not thread.is_alive()
    return value


def _record_frozen_evidence(name, value):
    output = os.environ.get("ECHO_TEST_DOCUMENT_WORKER_EVIDENCE")
    if output:
        directory = Path(output).resolve(strict=True)
        (directory / f"{name}.json").write_text(json.dumps(value, indent=2), encoding="utf-8")


def _start_frozen(executable, input_bytes):
    from runtime.execution.misc.document_process_limits import ParentProcessLimits

    guard = ParentProcessLimits(256 * 1024 * 1024, 10)
    proc = subprocess.Popen(
        [str(executable), "--echo-document-worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        guard.attach(proc)
        config = {
            "version": 1,
            "extension": "pdf",
            "input_bytes": input_bytes,
            "memory_bytes": 256 * 1024 * 1024,
            "cpu_seconds": 10,
            "max_chars": 64000,
            "max_pages": 10,
            "max_expanded_bytes": 8 * 1024 * 1024,
            "process": guard.worker_config(),
        }
        payload = json.dumps(config).encode()
        proc.stdin.write(struct.pack("!I", len(payload)) + payload)
        proc.stdin.flush()
        ready = _read_worker_frame(proc.stdout)
        assert ready["phase"] == "ready", ready
        return guard, proc, ready
    except BaseException:
        guard.terminate(proc)
        guard.close()
        raise


def _open_process(pid):
    from runtime.execution.misc.document_process_limits import _PARENT_RIGHTS, _windows_api

    api = _windows_api()
    handle = api.OpenProcess(_PARENT_RIGHTS, False, pid)
    assert handle, f"actual worker PID {pid} is not queryable"
    return api, handle


def test_frozen_bootloader_and_actual_worker_share_job_before_parsing_pdf(frozen_worker):
    from runtime.execution.misc.document_process_limits import _win_in_job

    data = _pdf_bytes()
    guard, proc, ready = _start_frozen(frozen_worker, len(data))
    handles = []
    try:
        worker_pid = ready["limits"]["worker_pid"]
        assert worker_pid != proc.pid, "onefile bootloader and parser must be independently checked"
        membership = {}
        for pid in (proc.pid, worker_pid):
            api, handle = _open_process(pid)
            handles.append(handle)
            membership[str(pid)] = _win_in_job(api, handle, guard._job)
        assert all(membership.values())
        proc.stdin.write(data)
        proc.stdin.flush()
        proc.stdin.close()
        result = _read_worker_frame(proc.stdout)
        assert result["outcome"] == "ok", result
        assert "Frozen invoice 2026-09-05 Amount 128.50" in result["text"]
        assert proc.wait(timeout=10) == 0
        assert guard.terminate(proc, timeout_s=2)
        assert all(api.WaitForSingleObject(handle, 1000) == 0 for handle in handles)
        _record_frozen_evidence(
            "frozen-job-and-pdf",
            {
                "bootloaderPid": proc.pid,
                "ready": ready,
                "membershipBeforeInput": membership,
                "result": result,
                "cleanupConfirmed": True,
            },
        )
    finally:
        guard.terminate(proc, timeout_s=2)
        guard.close()
        for handle in handles:
            api.CloseHandle(handle)


def test_real_orchestrator_uses_frozen_worker_and_enforces_wall_deadline(
    frozen_worker, monkeypatch
):
    from runtime.execution.misc import document_extraction as extraction

    original_command = extraction._worker_command
    with monkeypatch.context() as frozen_command:
        frozen_command.setattr(
            extraction, "_worker_command", lambda: [str(frozen_worker), "--echo-document-worker"]
        )
        result = extraction.extract_document_isolated(_pdf_bytes(), "pdf")
        assert result["outcome"] == "ok", result
        assert "Frozen invoice" in result["text"]
        timeout = extraction.extract_document_isolated(
            _pdf_bytes(), "pdf", budget=extraction.DocumentExtractionBudget(wall_seconds=0.001)
        )
    assert timeout["outcome"] == "timed_out", timeout
    assert timeout["text"] is None
    assert timeout["elapsed_seconds"] < 5
    assert extraction._worker_command is original_command
    source_result = extraction.extract_document_isolated(b"Source worker restored", "txt")
    assert source_result["outcome"] == "ok", source_result
    assert source_result["text"] == "Source worker restored"
    _record_frozen_evidence(
        "frozen-orchestrator",
        {"normal": result, "wallDeadline": timeout, "restoredSourceWorker": source_result},
    )


def test_frozen_job_kills_bootloader_and_waiting_worker_when_parent_exits(frozen_worker, tmp_path):
    helper = tmp_path / "frozen-parent.py"
    helper.write_text(
        "import importlib.util,json,os,sys\n"
        f"sys.path.insert(0,{str(ROOT)!r})\n"
        f"spec=importlib.util.spec_from_file_location('frozen_tests',{str(Path(__file__).resolve())!r})\n"
        "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n"
        "guard,proc,ready=module._start_frozen(sys.argv[1],1024)\n"
        "print(json.dumps({'parent':os.getpid(),'bootloader':proc.pid,'ready':ready}),flush=True)\n"
        "assert sys.stdin.readline().strip()=='exit'\n"
        "os._exit(73)\n",
        encoding="utf-8",
    )
    parent = subprocess.Popen(
        [sys.executable, str(helper), str(frozen_worker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    handles = []
    try:
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(parent.stdout.readline()), daemon=True).start()
        line = lines.get(timeout=20)
        assert line, parent.stderr.read()
        facts = json.loads(line)
        for pid in (facts["bootloader"], facts["ready"]["limits"]["worker_pid"]):
            api, handle = _open_process(pid)
            handles.append(handle)
        started = time.monotonic()
        parent.stdin.write("exit\n")
        parent.stdin.flush()
        assert parent.wait(timeout=10) == 73
        assert all(api.WaitForSingleObject(handle, 5000) == 0 for handle in handles)
        _record_frozen_evidence(
            "frozen-parent-exit",
            {
                **facts,
                "parentExit": 73,
                "bothWorkersSignalled": True,
                "elapsedSeconds": time.monotonic() - started,
            },
        )
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        for handle in handles:
            api.CloseHandle(handle)
