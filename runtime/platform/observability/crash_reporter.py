"""Capture unhandled failures and turn them into an actionable diagnosis.

Echo runs unattended on an appliance. When something dies at 03:00 nobody is
watching a terminal, so a bare traceback in a log is close to useless: the
interesting part is *why* it died and what to do about it.

This module does three things:

* records what happened — bounded and deduplicated, because a crash loop must
  never be able to fill the disk of a device nobody is babysitting;
* produces a first-pass diagnosis locally, with no model, no network and no
  API key, so it still works on an offline appliance;
* accepts an optional ``explainer`` callback so a richer LLM explanation can
  be layered on top when one is available.

It deliberately does not send anything anywhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import sys
import tempfile
import threading
import traceback
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DIRNAME = "crashes"
MAX_RECORDS = 40
MAX_TRACEBACK_CHARS = 8000
MAX_MESSAGE_CHARS = 800

# Failures that are normal control flow rather than a defect worth recording.
_IGNORED_EXCEPTIONS = (KeyboardInterrupt,)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class CrashDiagnosis:
    """A first-pass verdict on one crash record."""

    cause: str
    confidence: str  # "high" | "medium" | "low"
    explanation: str
    suggested_fix: str
    evidence: tuple[str, ...] = ()


@dataclass
class CrashRecord:
    """One class of failure, with how many times it has been seen."""

    fingerprint: str
    exception_type: str
    message: str
    traceback: str
    source: str  # excepthook | threading | asyncio | manual
    first_seen_at: str
    last_seen_at: str
    occurrence_count: int = 1
    thread_name: str = ""
    context: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "exception_type": self.exception_type,
            "message": self.message,
            "traceback": self.traceback,
            "source": self.source,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "occurrence_count": self.occurrence_count,
            "thread_name": self.thread_name,
            "context": dict(self.context),
            "environment": dict(self.environment),
        }

    @classmethod
    def from_wire(cls, payload: dict[str, Any]) -> CrashRecord:
        return cls(
            fingerprint=str(payload.get("fingerprint") or ""),
            exception_type=str(payload.get("exception_type") or ""),
            message=str(payload.get("message") or ""),
            traceback=str(payload.get("traceback") or ""),
            source=str(payload.get("source") or "manual"),
            first_seen_at=str(payload.get("first_seen_at") or ""),
            last_seen_at=str(payload.get("last_seen_at") or ""),
            occurrence_count=int(payload.get("occurrence_count") or 1),
            thread_name=str(payload.get("thread_name") or ""),
            context={str(k): str(v) for k, v in (payload.get("context") or {}).items()},
            environment={
                str(k): str(v) for k, v in (payload.get("environment") or {}).items()
            },
        )


def _fingerprint(exc_type: str, frames: list[str]) -> str:
    """Collapse a crash to a stable id so repeats update one record."""
    basis = "|".join([exc_type, *frames[-4:]])
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:16]


def _frame_keys(tb_text: str) -> list[str]:
    keys: list[str] = []
    for line in tb_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("File "):
            continue
        # Keep the file and function, drop line numbers so a harmless edit
        # nearby does not fork the record into a new fingerprint.
        parts = stripped.split(",")
        if len(parts) >= 2:
            keys.append(f"{parts[0].strip()}:{parts[-1].strip()}")
    return keys


def _environment() -> dict[str, str]:
    env = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pid": str(os.getpid()),
    }
    ram = os.environ.get("ECHO_FW_RAM_GB", "").strip()
    if ram:
        env["ram_tier_gb"] = ram
    return env


def build_record(
    exc: BaseException,
    *,
    source: str,
    context: dict[str, str] | None = None,
) -> CrashRecord:
    """Turn a live exception into a record.

    ``source`` says where it was caught (``excepthook``, ``threading``,
    ``asyncio`` or ``manual``) and is kept on the record because the same
    exception means different things in each place.
    """
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if len(tb_text) > MAX_TRACEBACK_CHARS:
        tb_text = tb_text[-MAX_TRACEBACK_CHARS:]
    message = str(exc)[:MAX_MESSAGE_CHARS]
    now = _utc_now()
    return CrashRecord(
        fingerprint=_fingerprint(type(exc).__name__, _frame_keys(tb_text)),
        exception_type=type(exc).__name__,
        message=message,
        traceback=tb_text,
        source=source,
        first_seen_at=now,
        last_seen_at=now,
        thread_name=threading.current_thread().name,
        context=dict(context or {}),
        environment=_environment(),
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        # Never let cleanup mask the real failure from the block above.
        if temp_path is not None and temp_path.exists():
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)


class CrashStore:
    """Bounded, deduplicated on-disk crash log.

    One file per fingerprint. Repeats bump ``occurrence_count`` instead of
    appending, and the oldest records are pruned once ``max_records`` is hit,
    so a crash loop writes a bounded amount of data.
    """

    def __init__(self, directory: Path | str, *, max_records: int = MAX_RECORDS) -> None:
        self._dir = Path(directory).expanduser().resolve(strict=False)
        self._max_records = max(1, int(max_records))
        self._lock = threading.RLock()

    @property
    def directory(self) -> Path:
        return self._dir

    def _path_for(self, fingerprint: str) -> Path:
        return self._dir / f"{fingerprint}.json"

    def save(self, record: CrashRecord) -> CrashRecord:
        with self._lock:
            path = self._path_for(record.fingerprint)
            if path.exists():
                try:
                    existing = CrashRecord.from_wire(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    existing = None
                if existing is not None:
                    merged = replace(
                        existing,
                        last_seen_at=record.last_seen_at,
                        occurrence_count=existing.occurrence_count + 1,
                        traceback=record.traceback,
                        message=record.message,
                        context=record.context or existing.context,
                    )
                    _write_json(path, merged.to_wire())
                    return merged
            _write_json(path, record.to_wire())
            self._prune()
            return record

    def _prune(self) -> None:
        records = self.all()
        if len(records) <= self._max_records:
            return
        for stale in records[self._max_records :]:
            with suppress(OSError):
                self._path_for(stale.fingerprint).unlink(missing_ok=True)

    def all(self) -> list[CrashRecord]:
        if not self._dir.is_dir():
            return []
        records: list[CrashRecord] = []
        for path in self._dir.glob("*.json"):
            try:
                records.append(
                    CrashRecord.from_wire(json.loads(path.read_text(encoding="utf-8")))
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
        records.sort(key=lambda r: r.last_seen_at, reverse=True)
        return records

    def latest(self) -> CrashRecord | None:
        records = self.all()
        return records[0] if records else None

    def get(self, fingerprint: str) -> CrashRecord | None:
        path = self._path_for(fingerprint)
        if not path.exists():
            return None
        try:
            return CrashRecord.from_wire(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None


# --------------------------------------------------------------------------
# Local, offline diagnosis
# --------------------------------------------------------------------------

_RULES: list[tuple[str, tuple[str, ...], CrashDiagnosis]] = [
    (
        "sandbox_delete_guard",
        ("sitecustomize", "_safe_path_unlink", "_check_bulk_delete_guard"),
        CrashDiagnosis(
            cause="sandbox_delete_guard",
            confidence="high",
            explanation=(
                "The process was killed while deleting a temporary file: a "
                "sandbox guard intercepted the delete and raised SystemExit. "
                "This happens on developer machines with the WorkBuddy "
                "safe-delete shim installed, not on shipped appliances."
            ),
            suggested_fix=(
                "Catch failures around the cleanup so a temp-file delete can "
                "never mask the real error, and stop writing state once the "
                "service is closing."
            ),
        ),
    ),
    (
        "base_exception_escape",
        ("SystemExit", "KeyboardInterrupt", "GeneratorExit"),
        CrashDiagnosis(
            cause="base_exception_escape",
            confidence="medium",
            explanation=(
                "A BaseException escaped. `except Exception` and "
                "contextlib.suppress(Exception) do not catch BaseException, so "
                "the failure passed straight through the handler that was "
                "supposed to contain it."
            ),
            suggested_fix=(
                "If this is shutdown cleanup, let it go; otherwise catch the "
                "specific BaseException subclass (or re-raise deliberately)."
            ),
        ),
    ),
    (
        "cancelled",
        ("asyncio.exceptions.CancelledError", "CancelledError"),
        CrashDiagnosis(
            cause="cancelled",
            confidence="high",
            explanation=(
                "The task was cancelled — normal during shutdown or when a "
                "request is abandoned, not usually a defect."
            ),
            suggested_fix="No fix needed unless it happens outside shutdown.",
        ),
    ),
    (
        "disk_full",
        ("No space left on device", "ENOSPC"),
        CrashDiagnosis(
            cause="disk_full",
            confidence="high",
            explanation="The device ran out of disk space while writing.",
            suggested_fix=(
                "Free space or prune caches; on an appliance, check whether a "
                "loop is writing unbounded state."
            ),
        ),
    ),
    (
        "permission_denied",
        ("PermissionError", "EACCES", "EPERM"),
        CrashDiagnosis(
            cause="permission_denied",
            confidence="high",
            explanation="A file or socket operation was denied.",
            suggested_fix=(
                "Check the service user owns the data directory and that "
                "unit hardening does not block the path."
            ),
        ),
    ),
    (
        "out_of_memory",
        ("MemoryError", "Cannot allocate memory", "Killed"),
        CrashDiagnosis(
            cause="out_of_memory",
            confidence="medium",
            explanation="The process ran out of memory (or was OOM-killed).",
            suggested_fix=(
                "On a 4 GB tier, disable the pet sidecar and heavy desktop "
                "effects, and cap model/resident memory."
            ),
        ),
    ),
    (
        "network",
        ("ConnectionRefusedError", "TimeoutError", "ConnectTimeout", "Name or service not known"),
        CrashDiagnosis(
            cause="network",
            confidence="medium",
            explanation="A network call failed and nothing handled it.",
            suggested_fix="Add a timeout and a retry, or degrade gracefully offline.",
        ),
    ),
]

_UNKNOWN = CrashDiagnosis(
    cause="unknown",
    confidence="low",
    explanation="No local rule matched this failure.",
    suggested_fix="Read the traceback, or attach an LLM explainer for a deeper analysis.",
)


def _match_rule(record: CrashRecord) -> tuple[CrashDiagnosis, tuple[str, ...]]:
    haystack = f"{record.exception_type}\n{record.message}\n{record.traceback}"
    for _cause, needles, diagnosis in _RULES:
        hits = tuple(n for n in needles if n in haystack)
        if hits:
            return diagnosis, hits
    return _UNKNOWN, ()


def analyze(
    record: CrashRecord,
    *,
    explainer: Callable[[str], str] | None = None,
) -> CrashDiagnosis:
    """Diagnose one record locally, optionally refined by a model.

    ``explainer`` receives a prompt and returns free text; it is wrapped so
    a broken or unavailable model degrades to the local verdict instead of
    turning a crash report into a second crash.
    """
    diagnosis, evidence = _match_rule(record)
    diagnosis = replace(diagnosis, evidence=evidence)

    if explainer is None:
        return diagnosis
    try:
        richer = (explainer(_prompt(record, diagnosis)) or "").strip()
    except Exception:  # noqa: BLE001 - diagnosis must not raise
        logger.warning("crash explainer failed; keeping local diagnosis", exc_info=True)
        return diagnosis
    if not richer:
        return diagnosis
    return replace(
        diagnosis,
        explanation=richer,
        confidence="high" if diagnosis.confidence != "low" else "medium",
    )


def _prompt(record: CrashRecord, diagnosis: CrashDiagnosis) -> str:
    return (
        "You are diagnosing a crash on an unattended Echo OS appliance.\n"
        f"Local verdict: {diagnosis.cause} (confidence {diagnosis.confidence}).\n"
        f"Exception: {record.exception_type}: {record.message}\n"
        f"Seen {record.occurrence_count} time(s), source={record.source}, "
        f"thread={record.thread_name}\n"
        f"Context: {json.dumps(record.context, ensure_ascii=False)}\n"
        "Traceback:\n"
        f"{record.traceback}\n"
        "Answer in two short paragraphs: the most likely root cause, then the "
        "single smallest change that would prevent it."
    )


def render_report(record: CrashRecord, diagnosis: CrashDiagnosis) -> str:
    """Human-readable report, in the style of `runtime doctor`."""
    lines = [
        f"Crash {record.fingerprint} · {record.exception_type}",
        f"  seen {record.occurrence_count}x · first {record.first_seen_at} · "
        f"last {record.last_seen_at}",
        f"  source: {record.source} · thread: {record.thread_name}",
    ]
    if record.context:
        lines.append(
            "  context: "
            + ", ".join(f"{k}={v}" for k, v in sorted(record.context.items()))
        )
    lines.extend(
        [
            "",
            f"  cause: {diagnosis.cause} (confidence {diagnosis.confidence})",
            f"  why:   {diagnosis.explanation}",
            f"  fix:   {diagnosis.suggested_fix}",
        ]
    )
    if diagnosis.evidence:
        lines.append(f"  matched: {', '.join(diagnosis.evidence)}")
    lines.extend(["", "  traceback:", *[f"    {line}" for line in record.traceback.splitlines()]])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------


def install(
    store: CrashStore,
    *,
    context: dict[str, str] | None = None,
    on_crash: Callable[[CrashRecord], None] | None = None,
) -> Callable[[], None]:
    """Hook the interpreter's fatal paths. Returns an uninstall callable.

    Covers the three places a failure can vanish: the main thread, worker
    threads (whose exceptions are printed and otherwise dropped) and asyncio
    tasks (whose exceptions are logged only when the task is garbage
    collected, which may be never).
    """
    base_context = dict(context or {})

    def capture(exc: BaseException, source: str) -> None:
        if isinstance(exc, _IGNORED_EXCEPTIONS):
            return
        try:
            record = build_record(exc, source=source, context=base_context)
            saved = store.save(record)
        except Exception:  # noqa: BLE001 - a crash reporter must never crash
            logger.warning("failed to record crash", exc_info=True)
            return
        if on_crash is not None:
            with suppress(Exception):
                on_crash(saved)

    previous_excepthook = sys.excepthook
    previous_threading_hook = threading.excepthook

    def excepthook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, _IGNORED_EXCEPTIONS):
            previous_excepthook(exc_type, exc, tb)
            return
        capture(exc if exc is not None else exc_type(), "excepthook")
        previous_excepthook(exc_type, exc, tb)

    def threading_hook(args) -> None:
        if args.exc_type is not None and not issubclass(args.exc_type, _IGNORED_EXCEPTIONS):
            exc = args.exc_value if args.exc_value is not None else args.exc_type()
            capture(exc, "threading")
        previous_threading_hook(args)

    sys.excepthook = excepthook
    threading.excepthook = threading_hook

    loop: asyncio.AbstractEventLoop | None = None
    previous_async_handler: Any = None
    with suppress(RuntimeError):
        loop = asyncio.get_running_loop()
    if loop is not None:
        previous_async_handler = loop.get_exception_handler()

        def async_handler(active_loop, async_context: dict[str, Any]) -> None:
            exc = async_context.get("exception")
            if exc is not None and not isinstance(exc, _IGNORED_EXCEPTIONS):
                capture(exc, "asyncio")
            if previous_async_handler is not None:
                previous_async_handler(active_loop, async_context)
            else:
                active_loop.default_exception_handler(async_context)

        loop.set_exception_handler(async_handler)

    def uninstall() -> None:
        sys.excepthook = previous_excepthook
        threading.excepthook = previous_threading_hook
        if loop is not None:
            loop.set_exception_handler(previous_async_handler)

    return uninstall


def install_best_effort(
    *,
    context: dict[str, str] | None = None,
    on_crash: Callable[[CrashRecord], None] | None = None,
) -> Callable[[], None]:
    """Install the hooks and never raise.

    A crash reporter that fails must not stop the service from booting, so
    every failure here degrades to a no-op uninstall.
    """
    try:
        return install(default_store(), context=context, on_crash=on_crash)
    except Exception:  # noqa: BLE001 - boot must continue
        logger.warning("crash reporter unavailable", exc_info=True)
        return lambda: None


def record_exception(
    store: CrashStore,
    exc: BaseException,
    *,
    source: str = "manual",
    context: dict[str, str] | None = None,
) -> CrashRecord:
    """Record a failure that was handled but should still be remembered."""
    return store.save(build_record(exc, source=source, context=context))


def default_store() -> CrashStore:
    from runtime.platform.process.paths import app_paths

    return CrashStore(app_paths().data_dir / DEFAULT_DIRNAME)


__all__ = [
    "CrashDiagnosis",
    "CrashRecord",
    "CrashStore",
    "analyze",
    "build_record",
    "default_store",
    "install",
    "install_best_effort",
    "record_exception",
    "render_report",
]
