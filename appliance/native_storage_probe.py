"""Read-only native storage command evidence, without publishing host stderr.

The string wrapper preserves existing inventory consumers while ensuring that
an empty command result retains its cause. SMART's exit status is a bit mask:
bits 0-2 concern the read itself; bits 3-7 describe disk health/history.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any


class ReadOutput(str):
    state: str
    code: str | None
    exit_code: int | None
    partial_stdout: str

    def __new__(
        cls,
        value: str,
        *,
        state: str = "ok",
        code: str | None = None,
        exit_code: int | None = None,
        partial_stdout: str = "",
    ) -> ReadOutput:
        result = super().__new__(cls, value)
        result.state = state
        result.code = code
        result.exit_code = exit_code
        result.partial_stdout = partial_stdout
        return result


def run_readonly(args: tuple[str, ...], *, timeout: float, smart: bool = False) -> ReadOutput:
    try:
        result = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError:
        return ReadOutput("", state="unavailable", code="tool_missing")
    except PermissionError:
        return ReadOutput("", state="unavailable", code="permission_denied")
    except subprocess.TimeoutExpired:
        return ReadOutput("", state="error", code="timeout")
    except UnicodeError:
        return ReadOutput("", state="error", code="invalid_output")
    except (OSError, subprocess.SubprocessError):
        return ReadOutput("", state="error", code="read_failed")
    exit_code = result.returncode
    stdout = result.stdout or ""
    if not isinstance(stdout, str):
        return ReadOutput("", state="error", code="invalid_output", exit_code=exit_code)
    permission_denied = any(
        marker in str(result.stderr or "").casefold()
        for marker in ("permission denied", "operation not permitted", "access is denied")
    )
    if smart and 0 <= exit_code <= 255:
        if exit_code & 3:
            return ReadOutput(
                stdout,
                state="error",
                code="permission_denied" if permission_denied else "command_failed",
                exit_code=exit_code,
            )
        if exit_code & 4:
            return ReadOutput(
                stdout,
                state="partial",
                code="command_incomplete",
                exit_code=exit_code,
            )
        return ReadOutput(stdout, exit_code=exit_code)
    if exit_code != 0:
        return ReadOutput(
            "",
            state="error",
            code="permission_denied" if permission_denied else "command_failed",
            exit_code=exit_code,
            partial_stdout=stdout,
        )
    return ReadOutput(stdout, exit_code=exit_code)


@dataclass
class Probe:
    value: Any
    evidence: dict[str, Any]


def evidence(
    source: str,
    checked_at: str,
    output: str = "",
    *,
    target: str | None = None,
    required: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": source,
        "state": getattr(output, "state", "ok"),
        "required": required,
        "checkedAt": checked_at,
        "count": 0,
    }
    if target is not None:
        result["target"] = target
    code = getattr(output, "code", None)
    if code:
        result["code"] = code
    exit_code = getattr(output, "exit_code", None)
    if exit_code is not None:
        result["exitCode"] = exit_code
    return result


def parsed(probe: dict[str, Any], count: int, *, invalid: bool = False) -> None:
    """Record parsing without erasing a command failure or partial read."""
    probe["count"] = count
    if probe["state"] != "ok":
        return
    if invalid:
        probe.update(
            state="partial" if count else "error", code="partial_parse" if count else "parse_failed"
        )
    elif not count:
        probe.update(state="empty", code="empty_inventory")


def observation(probes: list[dict[str, Any]]) -> dict[str, Any]:
    required = [item for item in probes if item["required"]]
    usable = any(item["count"] > 0 for item in required)
    complete = bool(required) and all(item["state"] == "ok" for item in required)
    return {
        "coverage": "complete" if complete else "partial" if usable else "none",
        "available": usable,
        "probeEvidence": probes,
    }


def health_observation(
    probes: list[dict[str, Any]], alerts: list[dict[str, Any]], *, checked_at: str
) -> dict[str, Any]:
    """Separate observed disk faults from gaps in the current observation.

    A complete read may observe a critical disk, so success here means evidence
    was obtained, not that hardware passed. There is no persisted monitor history.
    """
    observed = observation(probes)
    critical = sum(item["severity"] == "critical" for item in alerts)
    warning = sum(item["severity"] == "warning" for item in alerts)
    state = (
        "critical"
        if critical
        else "warning"
        if warning
        else "healthy"
        if observed["coverage"] == "complete"
        else "degraded"
        if observed["available"]
        else "unknown"
    )
    return {
        **observed,
        "state": state,
        "stale": observed["coverage"] != "complete",
        "checkedAt": checked_at,
        "lastSuccessfulAt": checked_at if observed["coverage"] == "complete" else None,
        "intervalSeconds": 0,
        "persistenceHealthy": None,
        "persistence": "not-applicable",
        "monitoring": False,
        "activeAlerts": [
            {**alert, "firstSeenAt": checked_at, "lastSeenAt": checked_at, "occurrences": 1}
            for alert in alerts
        ],
        "events": [],
        "summary": {"critical": critical, "warning": warning, "total": len(alerts)},
    }
