"""Fail-closed local UPS shutdown guard for Echo OS appliances.

The timer-driven guard reads a root-owned policy and the already bounded local
NUT inventory.  It never accepts a host, device, or command from the network.
Automatic poweroff is disabled unless the policy explicitly enables it.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from appliance.native_ups import ups_status

POLICY_PATH = Path("/etc/echo-os/ups-shutdown.json")
STATE_PATH = Path("/run/echo-os/ups-shutdown-state.json")
SYSTEMCTL = Path("/usr/bin/systemctl")
SCHEMA_VERSION = 1
DEFAULT_REQUIRED_SAMPLES = 3
MAX_FILE_BYTES = 4096


class GuardError(RuntimeError):
    """The shutdown guard could not safely evaluate its local state."""


def _read_json(path: Path, *, required: bool, trusted_uid: int = 0) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if required:
            raise GuardError(f"required file is missing: {path.name}") from None
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise GuardError(f"unsafe file type: {path.name}")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise GuardError(f"unsafe file ownership or mode: {path.name}")
    if metadata.st_size > MAX_FILE_BYTES:
        raise GuardError(f"file exceeds safety limit: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"invalid JSON file: {path.name}") from exc
    if not isinstance(value, dict):
        raise GuardError(f"invalid JSON object: {path.name}")
    return value


def _policy(value: Mapping[str, Any] | None) -> tuple[bool, int]:
    if value is None:
        return False, DEFAULT_REQUIRED_SAMPLES
    if set(value) != {"schemaVersion", "enabled", "requiredConsecutiveSamples"}:
        raise GuardError("UPS shutdown policy has an unexpected schema")
    enabled = value["enabled"]
    samples = value["requiredConsecutiveSamples"]
    if value["schemaVersion"] != SCHEMA_VERSION or not isinstance(enabled, bool):
        raise GuardError("UPS shutdown policy has invalid values")
    if isinstance(samples, bool) or not isinstance(samples, int) or not 2 <= samples <= 12:
        raise GuardError("UPS shutdown sample count must be between 2 and 12")
    return enabled, samples


def _state(value: Mapping[str, Any] | None) -> tuple[str | None, int]:
    if value is None:
        return None, 0
    if set(value) != {"schemaVersion", "device", "consecutiveLowBatterySamples"}:
        return None, 0
    device = value["device"]
    count = value["consecutiveLowBatterySamples"]
    if (
        value["schemaVersion"] != SCHEMA_VERSION
        or (device is not None and (not isinstance(device, str) or len(device) > 64))
        or isinstance(count, bool)
        or not isinstance(count, int)
        or not 0 <= count <= 12
    ):
        return None, 0
    return device, count


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_state(path: Path, *, device: str | None, count: int) -> None:
    _atomic_json(
        path,
        {
            "schemaVersion": SCHEMA_VERSION,
            "device": device,
            "consecutiveLowBatterySamples": count,
        },
    )


def evaluate(
    *,
    policy_path: Path = POLICY_PATH,
    state_path: Path = STATE_PATH,
    status_reader: Callable[[], dict[str, Any]] = ups_status,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    systemctl: Path = SYSTEMCTL,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Evaluate one sample and power off only after the strict local threshold."""
    enabled, required_samples = _policy(
        _read_json(policy_path, required=False, trusted_uid=trusted_uid)
    )
    if not enabled:
        _write_state(state_path, device=None, count=0)
        return {"outcome": "disabled", "shutdownRequested": False}

    snapshot = status_reader()
    devices = snapshot.get("devices") if isinstance(snapshot, dict) else None
    if not snapshot.get("available") or not isinstance(devices, list):
        _write_state(state_path, device=None, count=0)
        return {"outcome": "unavailable", "shutdownRequested": False}

    fsd_device: str | None = None
    low_device: str | None = None
    for device in devices:
        if not isinstance(device, dict) or device.get("available") is not True:
            continue
        name = device.get("name")
        flags = device.get("statusFlags")
        if not isinstance(name, str) or len(name) > 64 or not isinstance(flags, list):
            continue
        if "FSD" in flags:
            fsd_device = name
            break
        if "OB" in flags and "LB" in flags and low_device is None:
            low_device = name

    previous_device, previous_count = _state(
        _read_json(state_path, required=False, trusted_uid=trusted_uid)
    )
    if fsd_device is not None:
        trigger_device, count, reason = fsd_device, required_samples, "forcedShutdown"
    elif low_device is not None:
        count = previous_count + 1 if previous_device == low_device else 1
        trigger_device, reason = low_device, "persistentLowBattery"
    else:
        _write_state(state_path, device=None, count=0)
        return {"outcome": "safe", "shutdownRequested": False}

    count = min(count, required_samples)
    _write_state(state_path, device=trigger_device, count=count)
    if count < required_samples:
        return {
            "outcome": "confirmingLowBattery",
            "shutdownRequested": False,
            "consecutiveSamples": count,
            "requiredSamples": required_samples,
        }

    if not systemctl.is_file() or systemctl.is_symlink():
        raise GuardError("trusted systemctl is unavailable")
    try:
        completed = command_runner(
            [str(systemctl), "poweroff", "--no-block"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardError("poweroff request could not be submitted") from exc
    if completed.returncode != 0:
        raise GuardError("poweroff request was rejected")
    return {
        "outcome": reason,
        "shutdownRequested": True,
        "consecutiveSamples": count,
        "requiredSamples": required_samples,
    }


def main() -> int:
    try:
        result = evaluate()
    except GuardError as exc:
        print(f"UPS shutdown guard refused to act: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
