from __future__ import annotations

import sys
import threading

import pytest

from runtime.platform.observability.crash_reporter import (
    CrashRecord,
    CrashStore,
    analyze,
    build_record,
    install,
    record_exception,
    render_report,
)


def _record(
    exc_type: str,
    message: str = "",
    traceback_text: str = "",
    *,
    source: str = "excepthook",
) -> CrashRecord:
    return CrashRecord(
        fingerprint="test-fingerprint",
        exception_type=exc_type,
        message=message,
        traceback=traceback_text,
        source=source,
        first_seen_at="2026-01-01T00:00:00+00:00",
        last_seen_at="2026-01-01T00:00:00+00:00",
    )


def _boom() -> None:
    raise RuntimeError("kaboom")


def test_repeat_failures_deduplicate_into_one_record(tmp_path):
    store = CrashStore(tmp_path / "crashes", max_records=5)

    try:
        _boom()
    except RuntimeError as exc:
        first = record_exception(store, exc)
    try:
        _boom()
    except RuntimeError as exc:
        second = record_exception(store, exc)

    assert first.fingerprint == second.fingerprint
    assert second.occurrence_count == 2
    assert second.first_seen_at == first.first_seen_at
    assert len(list(store.directory.glob("*.json"))) == 1


def test_store_is_bounded_so_a_crash_loop_cannot_fill_the_disk(tmp_path):
    store = CrashStore(tmp_path / "crashes", max_records=3)
    for index in range(6):
        store.save(
            CrashRecord(
                fingerprint=f"fp{index:03d}",
                exception_type="RuntimeError",
                message=f"failure {index}",
                traceback="x" * 10,
                source="manual",
                first_seen_at="2026-01-01T00:00:00+00:00",
                last_seen_at=f"2026-01-0{index + 1}T00:00:00+00:00",
            )
        )

    assert len(store.all()) == 3
    # The newest three survive.
    assert {r.fingerprint for r in store.all()} == {"fp005", "fp004", "fp003"}


def test_diagnoses_sandbox_delete_guard():
    record = _record(
        "SystemExit",
        "1",
        'File "C:/shim/sitecustomize.py", line 851, in _check_bulk_delete_guard\n'
        "    _exit_bulk_guard_control(abs_path)\n"
        'File "C:/shim/sitecustomize.py", line 826, in _exit_bulk_guard_control\n'
        "    raise SystemExit(1)\n"
        "SystemExit: 1\n",
    )

    diagnosis = analyze(record)

    assert diagnosis.cause == "sandbox_delete_guard"
    assert diagnosis.confidence == "high"
    assert diagnosis.explanation
    assert "sitecustomize" in diagnosis.evidence


def test_diagnoses_cancellation_as_benign():
    record = _record("CancelledError", "", source="asyncio")

    diagnosis = analyze(record)

    assert diagnosis.cause == "cancelled"
    assert diagnosis.confidence == "high"


def test_unmatched_failure_falls_back_to_unknown():
    record = _record("VerySpecificEchoError", "something odd")

    diagnosis = analyze(record)

    assert diagnosis.cause == "unknown"
    assert diagnosis.confidence == "low"


def test_explainer_refines_but_never_crashes_the_report():
    record = _record("RuntimeError", "mystery")

    refined = analyze(record, explainer=lambda _prompt: "Root cause: a bad config value.")
    assert refined.explanation == "Root cause: a bad config value."
    assert refined.cause == "unknown"

    def broken(_prompt: str) -> str:
        raise RuntimeError("model unavailable")

    degraded = analyze(record, explainer=broken)
    assert degraded.explanation  # local verdict survives
    assert degraded.cause == "unknown"


def test_install_restores_previous_hooks(tmp_path):
    store = CrashStore(tmp_path / "crashes")
    previous_excepthook = sys.excepthook
    previous_threading_hook = threading.excepthook

    uninstall = install(store)
    try:
        assert sys.excepthook is not previous_excepthook
        assert threading.excepthook is not previous_threading_hook
    finally:
        uninstall()

    assert sys.excepthook is previous_excepthook
    assert threading.excepthook is previous_threading_hook


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_worker_thread_crash_is_recorded(tmp_path):
    store = CrashStore(tmp_path / "crashes")
    uninstall = install(store)

    def worker() -> None:
        raise RuntimeError("worker died")

    try:
        thread = threading.Thread(target=worker, name="probe-worker")
        thread.start()
        thread.join()
    finally:
        uninstall()

    latest = store.latest()
    assert latest is not None
    assert latest.source == "threading"
    assert latest.exception_type == "RuntimeError"
    assert latest.thread_name == "probe-worker"
    assert "worker died" in latest.message


def test_keyboard_interrupt_is_not_a_crash(tmp_path):
    store = CrashStore(tmp_path / "crashes")
    uninstall = install(store)
    try:
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    finally:
        uninstall()

    assert store.latest() is None


def test_render_report_carries_the_verdict_and_the_trace(tmp_path):
    store = CrashStore(tmp_path / "crashes")
    try:
        _boom()
    except RuntimeError as exc:
        record = record_exception(store, exc)

    report = render_report(record, analyze(record))

    assert record.fingerprint in report
    assert "cause:" in report
    assert "fix:" in report
    assert "kaboom" in report


def test_build_record_truncates_a_huge_traceback():
    class Deep(Exception):
        pass

    try:
        raise Deep("x" * 50_000)
    except Deep as exc:
        record = build_record(exc, source="manual")

    assert len(record.traceback) <= 8_000
    assert len(record.message) <= 800
