"""``runtime doctor`` must surface a recent crash instead of hiding it.

On an appliance a pre-dawn crash + auto-restart leaves the process healthy
again, so the crash is invisible everywhere except the dump directory. Doctor
is the one command an operator reaches for when "something felt off", so it
has to report unhandled failures.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from runtime.platform.observability.crash_reporter import (
    default_store,
    record_exception,
)
from runtime.platform.observability.doctor import CheckResult, Doctor, DoctorReport


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _crash_result(report: DoctorReport) -> CheckResult | None:
    return next((r for r in report.results if r.name == "Crashes"), None)


def test_doctor_reports_no_crashes(isolated_cwd: Path) -> None:
    report = DoctorReport()
    Doctor()._check_crashes(report)
    result = _crash_result(report)
    assert result is not None
    assert result.status == "ok"
    assert "none" in result.message


def test_doctor_warns_on_a_one_off_crash(isolated_cwd: Path) -> None:
    record_exception(default_store(), RuntimeError("boom"), source="manual")
    report = DoctorReport()
    Doctor()._check_crashes(report)
    result = _crash_result(report)
    assert result is not None
    assert result.status == "warn"
    assert "RuntimeError" in result.message
    assert "triage" in result.fix_hint


def test_doctor_fails_on_a_recurring_crash(isolated_cwd: Path) -> None:
    for _ in range(3):
        # Same type + same frame → collapses to one record, count bumps.
        record_exception(default_store(), ValueError("loop"), source="threading")
    report = DoctorReport()
    Doctor()._check_crashes(report)
    result = _crash_result(report)
    assert result is not None
    assert result.status == "fail"
    assert "3" in result.message
