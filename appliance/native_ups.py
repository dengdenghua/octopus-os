"""Bounded read-only Network UPS Tools inventory.

Only the local ``upsc`` client is invoked.  Device names come from the local
NUT daemon and are validated before reuse; callers cannot select a remote
host or inject command arguments.  The public payload is an allow-list of
operational fields and never includes serial numbers, raw NUT output, config
files, credentials, or daemon endpoints.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any

SCHEMA_VERSION = 1
_MAX_UPS_DEVICES = 16
_MAX_OUTPUT_BYTES = 256 * 1024
_UPS_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_PROPERTY_NAME = re.compile(r"[a-z][a-z0-9._-]{0,127}")
_KNOWN_STATUS_FLAGS = frozenset(
    {
        "BOOST",
        "BYPASS",
        "CAL",
        "CHRG",
        "DISCHRG",
        "FSD",
        "LB",
        "OB",
        "OFF",
        "OL",
        "OVER",
        "RB",
        "TRIM",
    }
)


def _run_upsc(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["upsc", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise OSError("local NUT query could not be completed") from exc
    output = completed.stdout or ""
    if len(output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise OSError("local NUT response exceeded the safety limit")
    if completed.returncode != 0:
        raise OSError("local NUT service did not answer")
    return output


def _device_names(output: str) -> list[str]:
    names = [line.strip() for line in output.splitlines() if line.strip()]
    if len(names) > _MAX_UPS_DEVICES:
        raise OSError("local NUT inventory exceeded the device limit")
    if any(_UPS_NAME.fullmatch(name) is None for name in names):
        raise OSError("local NUT inventory contained an unsafe device name")
    if len(names) != len(set(names)):
        raise OSError("local NUT inventory contained duplicate device names")
    return sorted(names)


def _properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    lines = output.splitlines()
    if len(lines) > 512:
        raise OSError("local NUT device response exceeded the property limit")
    for raw_line in lines:
        if not raw_line.strip():
            continue
        key, separator, value = raw_line.partition(":")
        key = key.strip()
        value = value.strip()
        if (
            not separator
            or _PROPERTY_NAME.fullmatch(key) is None
            or key in properties
            or len(value) > 2048
            or any(ord(character) < 0x20 for character in value)
        ):
            raise OSError("local NUT device response was malformed")
        properties[key] = value
    return properties


def _number(
    properties: dict[str, str],
    key: str,
    *,
    minimum: float,
    maximum: float,
) -> float | None:
    raw = properties.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not minimum <= value <= maximum:
        return None
    return round(value, 2)


def _bounded_label(properties: dict[str, str], key: str) -> str | None:
    value = properties.get(key)
    if value is None or not value or len(value) > 128:
        return None
    return value


def _status(properties: dict[str, str]) -> tuple[list[str], str]:
    raw_flags = properties.get("ups.status", "").upper().split()
    flags = sorted({flag for flag in raw_flags if flag in _KNOWN_STATUS_FLAGS})
    has_unknown_flag = any(flag not in _KNOWN_STATUS_FLAGS for flag in raw_flags)
    if "FSD" in flags:
        state = "shutdownPending"
    elif "OB" in flags and "LB" in flags:
        state = "lowBattery"
    elif "RB" in flags:
        state = "replaceBattery"
    elif has_unknown_flag:
        state = "unknown"
    elif "OB" in flags:
        state = "onBattery"
    elif "OFF" in flags:
        state = "offline"
    elif "OL" in flags:
        state = "online"
    else:
        state = "unknown"
    return flags, state


def _device_snapshot(name: str, output: str) -> dict[str, Any]:
    properties = _properties(output)
    flags, state = _status(properties)
    return {
        "name": name,
        "available": True,
        "state": state,
        "statusFlags": flags,
        "chargePercent": _number(properties, "battery.charge", minimum=0, maximum=100),
        "runtimeSeconds": _number(
            properties,
            "battery.runtime",
            minimum=0,
            maximum=366 * 24 * 60 * 60,
        ),
        "loadPercent": _number(properties, "ups.load", minimum=0, maximum=100),
        "inputVoltage": _number(properties, "input.voltage", minimum=0, maximum=1000),
        "outputVoltage": _number(properties, "output.voltage", minimum=0, maximum=1000),
        "batteryVoltage": _number(properties, "battery.voltage", minimum=0, maximum=1000),
        "temperatureC": _number(properties, "ups.temperature", minimum=-100, maximum=250),
        "manufacturer": _bounded_label(properties, "ups.mfr"),
        "model": _bounded_label(properties, "ups.model"),
    }


def ups_status() -> dict[str, Any]:
    """Return local UPS health without treating an absent NUT stack as success."""
    base = {
        "schemaVersion": SCHEMA_VERSION,
        "source": "nut",
        "readOnly": True,
        "devices": [],
    }
    if shutil.which("upsc") is None:
        return {
            **base,
            "configured": False,
            "available": False,
            "state": "unavailable",
            "code": "toolMissing",
        }
    try:
        names = _device_names(_run_upsc("-l"))
    except OSError:
        return {
            **base,
            "configured": False,
            "available": False,
            "state": "unavailable",
            "code": "serviceUnavailable",
        }
    if not names:
        return {
            **base,
            "configured": False,
            "available": True,
            "state": "notConfigured",
            "code": "emptyInventory",
        }

    devices: list[dict[str, Any]] = []
    readable = 0
    for name in names:
        try:
            snapshot = _device_snapshot(name, _run_upsc(name))
        except OSError:
            snapshot = {
                "name": name,
                "available": False,
                "state": "unknown",
                "statusFlags": [],
                "chargePercent": None,
                "runtimeSeconds": None,
                "loadPercent": None,
                "inputVoltage": None,
                "outputVoltage": None,
                "batteryVoltage": None,
                "temperatureC": None,
                "manufacturer": None,
                "model": None,
            }
        else:
            readable += 1
        devices.append(snapshot)
    return {
        **base,
        "configured": True,
        "available": readable > 0,
        "state": "ready" if readable == len(devices) else "degraded" if readable else "unavailable",
        "code": None
        if readable == len(devices)
        else "partialRead"
        if readable
        else "deviceUnavailable",
        "devices": devices,
    }


__all__ = ["ups_status"]
