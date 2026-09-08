"""Bounded document-process transport; no model-controlled commands or file paths."""

from __future__ import annotations

import contextlib
import json
import math
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DocumentExtractionBudget:
    memory_bytes: int = 256 * 1024 * 1024
    cpu_seconds: int = 10
    wall_seconds: float = 15.0
    scan_seconds: float = 60.0
    max_chars: int = 64000
    max_pages: int = 200
    max_expanded_bytes: int = 32 * 1024 * 1024
    max_input_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "memory_bytes",
            "cpu_seconds",
            "max_chars",
            "max_pages",
            "max_expanded_bytes",
            "max_input_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"invalid document extraction {name}")
        for name in ("wall_seconds", "scan_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid document extraction {name}")
        if self.max_input_bytes > 16 * 1024 * 1024 or self.max_chars > 1_000_000:
            raise ValueError("document transport budget is too large")

    @classmethod
    def from_environment(cls) -> DocumentExtractionBudget:
        fields = {
            "memory_bytes": ("ECHO_DOCUMENT_MEMORY_MIB", int, 1024 * 1024),
            "cpu_seconds": ("ECHO_DOCUMENT_CPU_SECONDS", int, 1),
            "wall_seconds": ("ECHO_DOCUMENT_WALL_SECONDS", float, 1),
            "scan_seconds": ("ECHO_DOCUMENT_SCAN_SECONDS", float, 1),
            "max_pages": ("ECHO_DOCUMENT_MAX_PAGES", int, 1),
            "max_expanded_bytes": ("ECHO_DOCUMENT_EXPANDED_MIB", int, 1024 * 1024),
        }
        values = {}
        for name, (variable, cast, unit) in fields.items():
            if variable in os.environ:
                try:
                    values[name] = cast(os.environ[variable]) * unit
                except ValueError as exc:
                    raise ValueError(f"invalid {variable}") from exc
        return cls(**values)


class DocumentWorkerCleanupError(RuntimeError):
    """A live worker could not be reclaimed; its admission slot must stay occupied."""


def _worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--echo-document-worker"]
    return [sys.executable, "-I", str(Path(__file__).with_name("document_worker.py"))]


def _worker_environment() -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    if getattr(sys, "frozen", False):
        env.update({key: value for key, value in os.environ.items() if key.startswith("_PYI_")})
        if sys.platform == "linux":
            import stat

            # A short-lived same-archive worker must reuse the bootloader's
            # extraction and remain our direct child (not a new onefile parent).
            # Only its trusted bundle directory belongs on the loader path.
            bundle_value = getattr(sys, "_MEIPASS", None)
            if not isinstance(bundle_value, str) or not bundle_value or ":" in bundle_value:
                raise ValueError("invalid frozen document bundle directory")
            bundle = Path(bundle_value)
            if not bundle.is_absolute() or bundle.resolve(strict=True) != bundle:
                raise ValueError("invalid frozen document bundle directory")
            metadata = bundle.stat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in {0, os.geteuid()}
                or metadata.st_mode & 0o022
            ):
                raise ValueError("untrusted frozen document bundle directory")
            for ancestor in bundle.parents:
                metadata = ancestor.stat()
                if metadata.st_uid not in {0, os.geteuid()} or (
                    metadata.st_mode & 0o022 and not metadata.st_mode & stat.S_ISVTX
                ):
                    raise ValueError("untrusted frozen document bundle ancestor")
            env["LD_LIBRARY_PATH"] = str(bundle)
        else:
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _frame(value: dict[str, Any]) -> bytes:
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > 16384:
        raise ValueError("oversized worker configuration")
    return struct.pack("!I", len(data)) + data


def _read_exact(stream, count: int) -> bytes:
    value = bytearray()
    while len(value) < count:
        part = stream.read(min(65536, count - len(value)))
        if not part:
            raise EOFError("incomplete worker response")
        value.extend(part)
    return bytes(value)


def _failure(outcome: str, **metadata) -> dict[str, Any]:
    return {
        "text": None,
        "truncated": False,
        "available": outcome != "unavailable",
        "outcome": outcome,
        **metadata,
    }


def extract_document_isolated(
    data: bytes,
    extension: str,
    *,
    budget: DocumentExtractionBudget | None = None,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
    pages: list[int] | tuple[int, ...] | None = None,
    include_page_markers: bool = False,
) -> dict[str, Any]:
    budget = budget or DocumentExtractionBudget.from_environment()
    if not isinstance(data, bytes) or len(data) > budget.max_input_bytes:
        return _failure("resource_limited")
    extension = extension.lower().lstrip(".")
    if extension not in {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "txt",
        "md",
        "csv",
        "tsv",
        "ipynb",
    }:
        return _failure("unavailable")
    selected_pages: list[int] | None = None
    if pages is not None:
        if extension != "pdf" or not isinstance(pages, (list, tuple)):
            return _failure("invalid_argument")
        selected_pages = []
        for page in pages:
            if type(page) is not int or page <= 0 or page in selected_pages:
                return _failure("invalid_argument")
            selected_pages.append(page)
        if len(selected_pages) > 200:
            return _failure("invalid_argument")
        selected_pages.sort()
    start = time.monotonic()
    expires = (
        min(start + budget.wall_seconds, deadline)
        if deadline is not None
        else start + budget.wall_seconds
    )
    if cancel is not None and cancel.is_set():
        return _failure("cancelled")
    if expires <= start:
        return _failure("timed_out")
    proc, guard = None, None
    stop = threading.Event()
    send_content = threading.Event()
    protocol_invalid = threading.Event()
    received: queue.Queue = queue.Queue(maxsize=3)
    threads: list[threading.Thread] = []
    result: dict[str, Any] = _failure("worker_failed")
    applied = None

    def publish(value):
        while not stop.is_set():
            try:
                received.put(value, timeout=0.05)
                return
            except queue.Full:
                pass

    def reader():
        try:
            for _ in range(2):
                length = struct.unpack("!I", _read_exact(proc.stdout, 4))[0]
                if not 0 < length <= budget.max_chars * 4 + 16384:
                    raise ValueError("oversized worker response")
                response = json.loads(_read_exact(proc.stdout, length).decode("utf-8"))
                if not isinstance(response, dict):
                    raise ValueError("invalid worker response")
                publish(response)
                if response.get("phase") == "result":
                    break
            # No third frame/output is valid, and no unbounded drain is permitted.
            if proc.stdout.read(1):
                raise ValueError("unexpected worker output")
        except (OSError, EOFError, ValueError, UnicodeError, struct.error, RecursionError):
            protocol_invalid.set()
            publish({"protocol_error": True})

    def write_all(payload):
        view = memoryview(payload)
        while view and not stop.is_set():
            written = proc.stdin.write(view[:65536])
            if not written:
                raise OSError("worker input closed")
            view = view[written:]

    def writer(config):
        try:
            write_all(_frame(config))
            while not stop.is_set() and not send_content.wait(0.05):
                pass
            if not stop.is_set():
                write_all(data)
        except (OSError, ValueError):
            protocol_invalid.set()
            publish({"protocol_error": True})
        finally:
            with contextlib.suppress(OSError):
                proc.stdin.close()

    def next_frame():
        while True:
            if cancel is not None and cancel.is_set():
                return {"stop": "cancelled"}
            remaining = expires - time.monotonic()
            if remaining <= 0:
                return {"stop": "timed_out"}
            try:
                return received.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue

    try:
        from runtime.execution.misc.document_process_limits import ParentProcessLimits

        guard = ParentProcessLimits(budget.memory_bytes, budget.cpu_seconds)
        kwargs = (
            {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        proc = subprocess.Popen(
            _worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_worker_environment(),
            cwd=Path(sys.executable).parent
            if getattr(sys, "frozen", False)
            else Path(__file__).parent,
            bufsize=0,
            **kwargs,
        )
        guard.attach(proc)
        config = {
            "version": 1,
            "extension": extension,
            "input_bytes": len(data),
            "pages": selected_pages,
            "include_page_markers": bool(include_page_markers),
            **{
                key: value
                for key, value in asdict(budget).items()
                if key
                in {"memory_bytes", "cpu_seconds", "max_chars", "max_pages", "max_expanded_bytes"}
            },
            "process": guard.worker_config(),
        }
        threads = [
            threading.Thread(target=reader, name="echo-document-output", daemon=True),
            threading.Thread(
                target=writer, args=(config,), name="echo-document-input", daemon=True
            ),
        ]
        for thread in threads:
            thread.start()
        first = next_frame()
        if first.get("stop"):
            result = _failure(first["stop"])
        elif first.get("phase") == "result" and first.get("outcome") == "unavailable":
            result = _failure("unavailable")
        elif (
            first.get("version") == 1
            and first.get("phase") == "ready"
            and isinstance(first.get("limits"), dict)
            and first["limits"].get("ready") is True
            and first["limits"].get("memory_bytes") == budget.memory_bytes
            and first["limits"].get("cpu_seconds") == budget.cpu_seconds
            and first["limits"].get("parent_pid") == os.getpid()
            and type(first["limits"].get("worker_pid")) is int
            and first["limits"]["worker_pid"] > 0
            and isinstance(first["limits"].get("worker_identity"), str)
        ):
            ready_limits = first["limits"]
            guard.bind_worker(ready_limits["worker_pid"], identity=ready_limits["worker_identity"])
            applied = ready_limits
            if cancel is not None and cancel.is_set():
                final = {"stop": "cancelled"}
            elif time.monotonic() >= expires:
                final = {"stop": "timed_out"}
            else:
                send_content.set()
                final = next_frame()
            if final.get("stop"):
                result = _failure(final["stop"])
            elif (
                final.get("version") == 1
                and final.get("phase") == "result"
                and final.get("outcome")
                in {
                    "ok",
                    "no_text",
                    "resource_limited",
                    "worker_failed",
                    "unavailable",
                    "invalid_argument",
                }
                and type(final.get("truncated")) is bool
                and (final.get("text") is None or isinstance(final.get("text"), str))
                and (
                    final.get("page_count") is None
                    or (type(final.get("page_count")) is int and final["page_count"] >= 0)
                )
                and (
                    final.get("pages_extracted") is None
                    or (
                        isinstance(final.get("pages_extracted"), list)
                        and all(type(page) is int and page > 0 for page in final["pages_extracted"])
                    )
                )
                and (final.get("notebook") is None or isinstance(final.get("notebook"), dict))
                and len(final.get("text") or "")
                <= budget.max_chars + (1024 if final.get("truncated") is True else 0)
            ):
                if final["outcome"] == "ok" and (
                    isinstance(final["text"], str) or isinstance(final.get("notebook"), dict)
                ):
                    result = {
                        "text": final["text"],
                        "truncated": final["truncated"],
                        "available": True,
                        "outcome": "ok",
                    }
                    if isinstance(final.get("notebook"), dict):
                        result["notebook"] = final["notebook"]
                    for key in ("page_count", "pages_extracted"):
                        if key in final:
                            result[key] = final[key]
                elif final["outcome"] == "invalid_argument":
                    result = _failure(
                        "invalid_argument",
                        error=(
                            str(final.get("error"))
                            if final.get("error") is not None
                            else "invalid document argument"
                        ),
                        **{
                            key: final[key]
                            for key in ("page_count", "pages_extracted")
                            if key in final
                        },
                    )
                elif final["outcome"] != "ok" and final["text"] is None and not final["truncated"]:
                    result = _failure(final["outcome"])
                while proc.poll() is None:
                    if cancel is not None and cancel.is_set():
                        result = _failure("cancelled")
                        break
                    if time.monotonic() >= expires:
                        result = _failure("timed_out")
                        break
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=min(0.05, max(0.001, expires - time.monotonic())))
                if proc.poll() is not None and proc.returncode != 0:
                    result = _failure("worker_failed")
    except (OSError, ValueError, RuntimeError):
        result = _failure("unavailable")
    finally:
        stop.set()
        send_content.set()
        stopped = True
        if proc is not None:
            try:
                stopped = guard.terminate(proc, timeout_s=2.0) if guard is not None else False
            except (OSError, RuntimeError):
                stopped = False
            # A dead launcher alone cannot prove that the owned process tree is gone.
            for thread in threads:
                thread.join(timeout=0.5)
            stopped = stopped and not any(thread.is_alive() for thread in threads)
            if stopped:
                for stream in (proc.stdin, proc.stdout):
                    with contextlib.suppress(OSError):
                        stream.close()
        if guard is not None:
            try:
                guard.close()
            except (OSError, RuntimeError):
                stopped = False
        if not stopped:
            raise DocumentWorkerCleanupError("document worker was not reclaimed")
    if protocol_invalid.is_set() and result["outcome"] in {"ok", "no_text", "resource_limited"}:
        result = _failure("worker_failed")
    result.update(
        elapsed_seconds=time.monotonic() - start,
        limits=applied,
        worker_pid=proc.pid if proc is not None else None,
    )
    return result


__all__ = ["DocumentExtractionBudget", "DocumentWorkerCleanupError", "extract_document_isolated"]
