"""Read-only health projection for mounted Btrfs filesystems."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from appliance.native_storage_probe import Probe, evidence, parsed

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ERROR_NAMES = frozenset(
    {
        "write_io_errs",
        "read_io_errs",
        "flush_io_errs",
        "corruption_errs",
        "generation_errs",
    }
)
Runner = Callable[..., str]


def _raw(output: str) -> str:
    return str(output or getattr(output, "partial_stdout", ""))


def _mark_partial(probe: dict[str, Any], output: str, code: str = "read_failed") -> None:
    if probe["state"] == "ok":
        probe.update(state="partial", code=getattr(output, "code", None) or code)


def _flatten_filesystems(value: Any) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    def walk(item: Any) -> None:
        if not isinstance(item, dict):
            raise ValueError("findmnt returned an invalid filesystem entry")
        flattened.append(item)
        children = item.get("children", [])
        if not isinstance(children, list):
            raise ValueError("findmnt returned invalid filesystem children")
        for child in children:
            walk(child)

    if not isinstance(value, list):
        raise ValueError("findmnt returned no filesystem list")
    for item in value:
        walk(item)
    return flattened


def _mounted_filesystems(output: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(_raw(output))
        raw_filesystems = payload.get("filesystems") if isinstance(payload, dict) else None
        filesystems = _flatten_filesystems(raw_filesystems)
    except (json.JSONDecodeError, ValueError) as exc:
        raise OSError("findmnt returned invalid Btrfs JSON") from exc
    by_uuid: dict[str, dict[str, Any]] = {}
    for item in filesystems:
        raw_uuid = item.get("uuid")
        filesystem_uuid = str(raw_uuid or "").lower()
        target = item.get("target")
        source = item.get("source")
        fstype = item.get("fstype")
        options = item.get("options")
        if (
            (filesystem_uuid and _UUID.fullmatch(filesystem_uuid) is None)
            or not isinstance(target, str)
            or not target.startswith("/")
            or len(target) > 4096
            or any(ord(character) < 32 for character in target)
            or not isinstance(source, str)
            or not source.startswith("/dev/")
            or fstype != "btrfs"
            or not isinstance(options, str)
        ):
            raise OSError("findmnt returned an unsafe Btrfs mount identity")
        option_tokens = {token.strip().casefold() for token in options.split(",") if token.strip()}
        read_only = "ro" in option_tokens and "rw" not in option_tokens
        identity = filesystem_uuid or f"source:{source}"
        current = by_uuid.get(identity)
        record = {
            "uuid": filesystem_uuid or None,
            "source": source,
            "mountpoint": target,
            "readOnly": read_only,
        }
        if current is None or len(target) < len(current["mountpoint"]):
            by_uuid[identity] = record
    return sorted(by_uuid.values(), key=lambda item: (item["uuid"] or "", item["mountpoint"]))


def _filesystem_show(output: str, *, expected_uuid: str) -> dict[str, Any]:
    raw = _raw(output)
    uuid_match = re.search(r"\buuid:\s*([0-9a-fA-F-]+)\s*$", raw, re.MULTILINE)
    total_match = re.search(r"^\s*Total devices\s+(\d+)\b", raw, re.MULTILINE)
    if uuid_match is None or uuid_match.group(1).lower() != expected_uuid or total_match is None:
        raise OSError("btrfs filesystem show returned an invalid identity")
    total = int(total_match.group(1))
    if not 1 <= total <= 256:
        raise OSError("btrfs filesystem show returned an invalid device count")
    device_lines = re.findall(r"^\s*devid\s+\d+\b.*\bpath\s+(\S+)\s*$", raw, re.MULTILINE)
    if len(device_lines) > total:
        raise OSError("btrfs filesystem show returned excess devices")
    missing = max(
        int("*** Some devices missing" in raw),
        sum(path.casefold() == "missing" for path in device_lines),
        total - len(device_lines),
    )
    missing = min(total, missing)
    return {"totalDevices": total, "activeDevices": total - missing, "missingDevices": missing}


def _profiles(output: str) -> dict[str, str]:
    profiles: dict[str, set[str]] = {"Data": set(), "Metadata": set()}
    for line in _raw(output).splitlines():
        match = re.match(r"^(Data|Metadata),\s*([^:]+):", line.strip())
        if match:
            profiles[match.group(1)].add(match.group(2).casefold())
    if not profiles["Data"] or not profiles["Metadata"]:
        raise OSError("btrfs filesystem df returned no data or metadata profile")
    return {
        "dataProfile": "+".join(sorted(profiles["Data"])),
        "metadataProfile": "+".join(sorted(profiles["Metadata"])),
    }


def _device_errors(output: str) -> dict[str, int]:
    totals = {name: 0 for name in sorted(_ERROR_NAMES)}
    seen = False
    for line in _raw(output).splitlines():
        match = re.fullmatch(r"\[[^\]\r\n]+\]\.([a-z_]+)\s+(\d+)", line.strip())
        if match is None or match.group(1) not in _ERROR_NAMES:
            if line.strip():
                raise OSError("btrfs device stats returned invalid output")
            continue
        seen = True
        value = int(match.group(2))
        if value > 2**63 - 1:
            raise OSError("btrfs device stats counter is out of range")
        totals[match.group(1)] += value
    if not seen:
        raise OSError("btrfs device stats returned no counters")
    return totals


def _inspect_mount(record: dict[str, Any], runner: Runner, probe: dict[str, Any]) -> dict[str, Any]:
    mountpoint = record["mountpoint"]
    show = runner("btrfs", "filesystem", "show", "--raw", mountpoint, timeout=15.0)
    if getattr(show, "state", "ok") != "ok":
        _mark_partial(probe, show)
    filesystem_uuid = record["uuid"]
    if filesystem_uuid is None:
        match = re.search(r"\buuid:\s*([0-9a-fA-F-]+)\s*$", _raw(show), re.MULTILINE)
        recovered_uuid = match.group(1).casefold() if match else ""
        if _UUID.fullmatch(recovered_uuid) is None:
            raise OSError("btrfs filesystem show returned an invalid identity")
        filesystem_uuid = recovered_uuid
    topology = _filesystem_show(show, expected_uuid=filesystem_uuid)
    profile_output = runner("btrfs", "filesystem", "df", "--raw", mountpoint, timeout=15.0)
    if getattr(profile_output, "state", "ok") != "ok":
        _mark_partial(probe, profile_output)
    profiles = _profiles(profile_output)
    stats = runner("btrfs", "device", "stats", "-c", mountpoint, timeout=15.0)
    exit_code = getattr(stats, "exit_code", 0)
    if exit_code not in {0, 64, 65}:
        _mark_partial(probe, stats)
    elif exit_code in {64, 65}:
        _mark_partial(probe, stats, "device_errors_observed")
        probe["code"] = "device_errors_observed"
    errors = _device_errors(stats)
    total_errors = sum(errors.values())
    status = (
        "degraded"
        if topology["missingDevices"]
        else "warning"
        if total_errors or record["readOnly"]
        else "healthy"
    )
    level = (
        f"btrfs-{profiles['dataProfile']}"
        if profiles["dataProfile"] == profiles["metadataProfile"]
        else "btrfs-mixed"
    )
    return {
        "devicefile": filesystem_uuid,
        "uuid": filesystem_uuid,
        "mountpoint": mountpoint,
        "level": level,
        "status": status,
        **topology,
        **profiles,
        "operation": None,
        "operationPercent": None,
        "deviceErrors": errors,
        "deviceErrorCount": total_errors,
        "readOnly": record["readOnly"],
        "kind": "btrfs",
    }


def probe_btrfs_filesystems(*, expected: bool, runner: Runner, checked_at: str) -> Probe:
    probe = evidence("btrfs", checked_at, required=expected)
    if not expected:
        probe.update(state="not-applicable", code="not_present")
        return Probe([], probe)
    inventory = runner(
        "findmnt",
        "--json",
        "--types",
        "btrfs",
        "--output",
        "UUID,SOURCE,TARGET,FSTYPE,OPTIONS",
        timeout=15.0,
    )
    probe = evidence("btrfs", checked_at, inventory, required=True)
    raw_inventory = _raw(inventory)
    if not raw_inventory:
        probe.update(state="error", code=getattr(inventory, "code", None) or "mount_missing")
        return Probe([], probe)
    try:
        mounts = _mounted_filesystems(inventory)
    except OSError:
        parsed(probe, 0, invalid=True)
        return Probe([], probe)
    filesystems: list[dict[str, Any]] = []
    invalid = False
    for mount in mounts:
        try:
            filesystems.append(_inspect_mount(mount, runner, probe))
        except OSError:
            invalid = True
    parsed(probe, len(filesystems), invalid=invalid)
    return Probe(filesystems, probe)


__all__ = ["probe_btrfs_filesystems"]
