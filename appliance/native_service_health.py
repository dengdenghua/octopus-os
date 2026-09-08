"""Bounded, read-only health probe for Echo OS systemd services.

Only a fixed list of appliance-owned or NAS-critical units is queried.  The
public result contains allow-listed systemd fields and synthesized alerts; raw
journal output, command stderr, environment, paths, credentials, and arbitrary
unit names never cross the API boundary.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1
MAX_OUTPUT_BYTES = 64 * 1024

MONITORED_UNITS = (
    "docker.service",
    "echo-agent.service",
    "echo-appliance.service",
    "echo-shell.service",
    "nginx.service",
    "smbd.service",
    "wsdd2.service",
)

_PROPERTIES = (
    "Id",
    "LoadState",
    "UnitFileState",
    "ActiveState",
    "SubState",
    "Result",
    "NRestarts",
)
_EXPECTED_UNIT_FILE_STATES = frozenset(
    {
        "enabled",
        "enabled-runtime",
        "linked",
        "linked-runtime",
    }
)
_ACTIVE_STATES = frozenset({"active", "reloading", "activating"})
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:@+-]{0,128}$")


def _run_systemctl_show() -> str:
    blocks: list[str] = []
    total_bytes = 0
    for unit in MONITORED_UNITS:
        try:
            completed = subprocess.run(
                [
                    "systemctl",
                    "show",
                    "--no-pager",
                    f"--property={','.join(_PROPERTIES)}",
                    unit,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=3.0,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise OSError("systemd service health query could not be completed") from exc
        output = (completed.stdout or "").strip()
        total_bytes += len(output.encode("utf-8"))
        if total_bytes > MAX_OUTPUT_BYTES:
            raise OSError("systemd service health response exceeded the safety limit")
        if completed.returncode == 4 and not output:
            # systemctl uses the LSB "unknown unit" status for units that are
            # intentionally absent on one of Echo's appliance layouts.
            continue
        if completed.returncode != 0 or not output:
            raise OSError("systemd service health query failed")
        blocks.append(output)
    return "\n\n".join(blocks)


def _safe_value(value: str) -> str:
    if _SAFE_VALUE.fullmatch(value) is None:
        raise ValueError("systemd service health response contained an unsafe value")
    return value


def _parse_show(output: str) -> list[dict[str, Any]]:
    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("systemd service health response exceeded the safety limit")
    raw_blocks = [block for block in re.split(r"\r?\n\s*\r?\n", output.strip()) if block]
    if not raw_blocks:
        raise ValueError("systemd service health response was empty")
    if len(raw_blocks) > len(MONITORED_UNITS):
        raise ValueError("systemd service health response contained too many units")

    units: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in raw_blocks:
        values: dict[str, str] = {}
        lines = block.splitlines()
        if len(lines) > len(_PROPERTIES):
            raise ValueError("systemd service health response contained too many fields")
        for line in lines:
            key, separator, value = line.partition("=")
            if not separator or key not in _PROPERTIES or key in values:
                raise ValueError("systemd service health response was malformed")
            values[key] = _safe_value(value)
        if set(values) != set(_PROPERTIES):
            raise ValueError("systemd service health response was incomplete")

        unit = values["Id"]
        if unit not in MONITORED_UNITS or unit in seen:
            raise ValueError("systemd service health response contained an unexpected unit")
        seen.add(unit)
        try:
            restarts = int(values["NRestarts"])
        except ValueError as exc:
            raise ValueError("systemd service restart count was malformed") from exc
        if not 0 <= restarts <= 1_000_000_000:
            raise ValueError("systemd service restart count was out of range")

        loaded = values["LoadState"] == "loaded"
        expected = loaded and values["UnitFileState"] in _EXPECTED_UNIT_FILE_STATES
        units.append(
            {
                "unit": unit,
                "loaded": loaded,
                "expected": expected,
                "activeState": values["ActiveState"],
                "subState": values["SubState"],
                "result": values["Result"] or None,
                "restarts": restarts,
            }
        )
    return sorted(units, key=lambda item: item["unit"])


def _alert_for(unit: dict[str, Any]) -> dict[str, str] | None:
    if not unit["expected"]:
        return None
    name = unit["unit"]
    result = unit["result"]
    active_state = unit["activeState"]
    if result == "start-limit-hit":
        code = "service.restart_storm"
        severity = "critical"
        message = f"系统服务 {name} 因短时间反复重启已触发保护"
    elif active_state == "failed":
        code = "service.failed"
        severity = "critical"
        message = f"系统服务 {name} 运行失败"
    elif active_state not in _ACTIVE_STATES:
        code = "service.inactive"
        severity = "warning"
        message = f"应持续运行的系统服务 {name} 当前未活动"
    else:
        return None
    return {
        "id": f"{name}:{code}",
        "code": code,
        "severity": severity,
        "resource": name,
        "message": message,
    }


def _unavailable(code: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": "systemd",
        "available": False,
        "state": "unknown",
        "code": code,
        "checkedAt": None,
        "monitored": 0,
        "expected": 0,
        "units": [],
        "activeAlerts": [],
    }


def service_health(
    *,
    command_finder: Callable[[str], str | None] = shutil.which,
    reader: Callable[[], str] = _run_systemctl_show,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Return one privacy-bounded snapshot of fixed systemd service health."""

    if command_finder("systemctl") is None:
        return _unavailable("toolMissing")
    try:
        units = _parse_show(reader())
    except (OSError, UnicodeError, ValueError):
        return _unavailable("probeFailed")

    alerts = [alert for unit in units if (alert := _alert_for(unit)) is not None]
    state = (
        "critical"
        if any(alert["severity"] == "critical" for alert in alerts)
        else "warning"
        if alerts
        else "healthy"
    )
    checked_at = now()
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": "systemd",
        "available": True,
        "state": state,
        "code": None,
        "checkedAt": checked_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "monitored": len(units),
        "expected": sum(1 for unit in units if unit["expected"]),
        "units": units,
        "activeAlerts": alerts,
    }


__all__ = [
    "MAX_OUTPUT_BYTES",
    "MONITORED_UNITS",
    "SCHEMA_VERSION",
    "service_health",
]
