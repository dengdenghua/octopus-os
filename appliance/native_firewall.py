"""Fail-closed firewalld rules for Echo-managed NAS and Hub services.

The immutable ``echo-public`` zone stays closed by default.  This module owns
only a deterministic set of rich rules derived from approved SMB/Time Machine,
NFS, and catalog-owned Hub container state. It never accepts arbitrary ports,
services, zones, or rule fragments from a browser-facing API caller.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

STATE_SCHEMA = "echo.native-firewall.v3"
LEGACY_STATE_SCHEMA_V1 = "echo.native-firewall.v1"
LEGACY_STATE_SCHEMA_V2 = "echo.native-firewall.v2"
ZONE = "echo-public"
STATE_PATH = Path("/var/lib/echo-os/native-firewall.json")
FIREWALL_CMD = "/usr/bin/firewall-cmd"

_SMB_NETWORKS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)
_NFS_SERVICES = ("mountd", "nfs", "rpc-bind")
_WSD_PORTS = (("3702", "udp"), ("5357", "tcp"))
_DLNA_PORTS = (("1900", "udp"), ("8200", "tcp"))
_HUB_APP_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


def _default_state() -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "zone": ZONE,
        "smb": False,
        "nfsClients": [],
        "dlna": False,
        "hubForwards": [],
    }


def _canonical_private_network(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise OSError("managed firewall NFS client is invalid")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise OSError("managed firewall NFS client is not canonical CIDR") from exc
    allowed = (
        network.subnet_of(ipaddress.ip_network("10.0.0.0/8"))
        or network.subnet_of(ipaddress.ip_network("172.16.0.0/12"))
        or network.subnet_of(ipaddress.ip_network("192.168.0.0/16"))
        if network.version == 4
        else network.subnet_of(ipaddress.ip_network("fc00::/7"))
    )
    if not allowed or value != network.with_prefixlen:
        raise OSError("managed firewall NFS client must be canonical private CIDR")
    return network.with_prefixlen


def _canonical_hub_forward(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "appId",
        "hostPort",
        "containerPort",
        "protocol",
        "mode",
        "address",
    }:
        raise OSError("managed firewall Hub forward has unexpected fields")
    app_id = value.get("appId")
    host_port = value.get("hostPort")
    container_port = value.get("containerPort")
    protocol = value.get("protocol")
    mode = value.get("mode")
    address = value.get("address")
    if (
        not isinstance(app_id, str)
        or len(app_id) > 64
        or _HUB_APP_ID.fullmatch(app_id) is None
        or not isinstance(host_port, int)
        or isinstance(host_port, bool)
        or not 1024 <= host_port <= 65535
        or not isinstance(container_port, int)
        or isinstance(container_port, bool)
        or not 1 <= container_port <= 65535
        or protocol not in {"tcp", "udp"}
        or mode not in {"bridge", "host"}
    ):
        raise OSError("managed firewall Hub forward is invalid")
    if mode == "host":
        if address is not None or host_port != container_port:
            raise OSError("managed firewall host-network Hub forward is invalid")
    else:
        if not isinstance(address, str):
            raise OSError("managed firewall bridged Hub address is invalid")
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise OSError("managed firewall bridged Hub address is invalid") from exc
        private = any(parsed in ipaddress.ip_network(network) for network in _SMB_NETWORKS[:3])
        if parsed.version != 4 or not private or str(parsed) != address:
            raise OSError("managed firewall bridged Hub address must be private IPv4")
    return {
        "appId": app_id,
        "hostPort": host_port,
        "containerPort": container_port,
        "protocol": protocol,
        "mode": mode,
        "address": address,
    }


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OSError("managed firewall state has unexpected fields")
    if value.get("schema") == LEGACY_STATE_SCHEMA_V1 and set(value) == {
        "schema",
        "zone",
        "smb",
        "nfsClients",
    }:
        value = {**value, "schema": STATE_SCHEMA, "dlna": False, "hubForwards": []}
    if value.get("schema") == LEGACY_STATE_SCHEMA_V2 and set(value) == {
        "schema",
        "zone",
        "smb",
        "nfsClients",
        "hubForwards",
    }:
        value = {**value, "schema": STATE_SCHEMA, "dlna": False}
    if set(value) != {
        "schema",
        "zone",
        "smb",
        "nfsClients",
        "dlna",
        "hubForwards",
    }:
        raise OSError("managed firewall state has unexpected fields")
    if value.get("schema") != STATE_SCHEMA or value.get("zone") != ZONE:
        raise OSError("managed firewall state has an unsupported identity")
    if not isinstance(value.get("smb"), bool):
        raise OSError("managed firewall SMB state is invalid")
    if not isinstance(value.get("dlna"), bool):
        raise OSError("managed firewall DLNA state is invalid")
    clients = value.get("nfsClients")
    if not isinstance(clients, list):
        raise OSError("managed firewall NFS state is invalid")
    canonical = sorted({_canonical_private_network(item) for item in clients})
    if clients != canonical:
        raise OSError("managed firewall NFS clients are not canonical and unique")
    forwards = value.get("hubForwards")
    if not isinstance(forwards, list) or len(forwards) > 128:
        raise OSError("managed firewall Hub forwards are invalid")
    canonical_forwards = sorted(
        (_canonical_hub_forward(item) for item in forwards),
        key=lambda item: (
            item["hostPort"],
            item["protocol"],
            item["appId"],
            item["containerPort"],
            item["mode"],
            item["address"] or "",
        ),
    )
    identities = {(item["hostPort"], item["protocol"]) for item in canonical_forwards}
    if forwards != canonical_forwards or len(identities) != len(canonical_forwards):
        raise OSError("managed firewall Hub forwards are not canonical and unique")
    return {
        "schema": STATE_SCHEMA,
        "zone": ZONE,
        "smb": value["smb"],
        "nfsClients": canonical,
        "dlna": value["dlna"],
        "hubForwards": canonical_forwards,
    }


def desired_state(
    *,
    smb: bool,
    nfs_clients: Sequence[str],
    dlna: bool = False,
    hub_forwards: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    if not isinstance(smb, bool) or not isinstance(dlna, bool):
        raise ValueError("managed firewall protocol state must be boolean")
    clients = sorted({_canonical_private_network(item) for item in nfs_clients})
    forwards = sorted(
        (_canonical_hub_forward(item) for item in hub_forwards),
        key=lambda item: (
            item["hostPort"],
            item["protocol"],
            item["appId"],
            item["containerPort"],
            item["mode"],
            item["address"] or "",
        ),
    )
    return _validate_state(
        {
            "schema": STATE_SCHEMA,
            "zone": ZONE,
            "smb": smb,
            "nfsClients": clients,
            "dlna": dlna,
            "hubForwards": forwards,
        }
    )


def managed_rules(state: dict[str, Any]) -> tuple[str, ...]:
    state = _validate_state(dict(state))
    rules: list[str] = []
    if state["smb"]:
        for network in _SMB_NETWORKS:
            family = "ipv4" if ipaddress.ip_network(network).version == 4 else "ipv6"
            rules.append(
                f'rule family="{family}" source address="{network}" service name="samba" accept'
            )
            for port, protocol in _WSD_PORTS:
                rules.append(
                    f'rule family="{family}" source address="{network}" '
                    f'port port="{port}" protocol="{protocol}" accept'
                )
    for network in state["nfsClients"]:
        family = "ipv4" if ipaddress.ip_network(network).version == 4 else "ipv6"
        for service in _NFS_SERVICES:
            rules.append(
                f'rule family="{family}" source address="{network}" service name="{service}" accept'
            )
    if state["dlna"]:
        for network in _SMB_NETWORKS[:3]:
            for port, protocol in _DLNA_PORTS:
                rules.append(
                    f'rule family="ipv4" source address="{network}" '
                    f'port port="{port}" protocol="{protocol}" accept'
                )
    for forward in state["hubForwards"]:
        for network in _SMB_NETWORKS[:3]:
            prefix = f'rule family="ipv4" source address="{network}" '
            if forward["mode"] == "host":
                rules.append(
                    prefix
                    + f'port port="{forward["hostPort"]}" '
                    + f'protocol="{forward["protocol"]}" accept'
                )
            else:
                rules.append(
                    prefix
                    + f'forward-port port="{forward["hostPort"]}" '
                    + f'protocol="{forward["protocol"]}" '
                    + f'to-port="{forward["containerPort"]}" '
                    + f'to-addr="{forward["address"]}"'
                )
    return tuple(sorted(rules))


def _state_path() -> Path:
    override = os.environ.get("ECHO_NATIVE_FIREWALL_STATE")
    if override is None:
        return STATE_PATH
    if os.environ.get("ECHO_NATIVE_FIREWALL_SOURCE_TEST") != "USE-SOURCE-RUNTIME":
        raise OSError("managed firewall state override requires the source-test sentinel")
    path = Path(override)
    if not path.is_absolute():
        raise OSError("managed firewall state override must be absolute")
    return path


def _source_test_enabled() -> bool:
    return os.environ.get("ECHO_NATIVE_FIREWALL_SOURCE_TEST") == "USE-SOURCE-RUNTIME"


def _firewall_cmd() -> str:
    override = os.environ.get("ECHO_NATIVE_FIREWALL_CMD")
    if override is None:
        return FIREWALL_CMD
    if os.environ.get("ECHO_NATIVE_FIREWALL_SOURCE_TEST") != "USE-SOURCE-RUNTIME":
        raise OSError("managed firewall command override requires the source-test sentinel")
    if not os.path.isabs(override):
        raise OSError("managed firewall command override must be absolute")
    return override


def _read_state() -> dict[str, Any]:
    path = _state_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _default_state()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise OSError("managed firewall state is not a regular file")
    expected_uid = os.getuid() if _source_test_enabled() else 0
    if os.name == "posix" and (info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) != 0o600):
        raise OSError("managed firewall state must be root-owned mode 0600")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("managed firewall state is unreadable") from exc
    return _validate_state(payload)


def _write_state(state: dict[str, Any]) -> None:
    state = _validate_state(dict(state))
    path = _state_path()
    if state == _default_state():
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode):
        raise OSError("managed firewall state directory is unsafe")
    expected_uid = os.getuid() if _source_test_enabled() else 0
    if os.name == "posix" and parent.st_uid != expected_uid:
        raise OSError("managed firewall state directory must be root-owned")
    text = json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows source tests exercise this branch
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _run(*args: str, timeout: float = 30.0) -> str:
    command = [_firewall_cmd(), *args]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("firewall-cmd failed to start") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise OSError(f"firewall-cmd failed: {detail}")
    return completed.stdout or ""


def _listed_rules(*, permanent: bool) -> tuple[str, ...]:
    args = (["--permanent"] if permanent else []) + [f"--zone={ZONE}", "--list-rich-rules"]
    return tuple(sorted(line.strip() for line in _run(*args).splitlines() if line.strip()))


def _verify_rules(expected: tuple[str, ...]) -> None:
    if _run("--get-default-zone").strip() != ZONE:
        raise OSError("Echo NAS protocol firewall requires echo-public as the default zone")
    if _listed_rules(permanent=False) != expected:
        raise OSError("runtime rich rules differ from managed firewall state")
    if _listed_rules(permanent=True) != expected:
        raise OSError("persistent rich rules differ from managed firewall state")


def verify() -> dict[str, Any]:
    state = _read_state()
    rules = managed_rules(state)
    _verify_rules(rules)
    return {"state": state, "rules": list(rules)}


def _reconcile_permanent(current: tuple[str, ...], wanted: tuple[str, ...]) -> None:
    for rule in sorted(set(current) - set(wanted)):
        _run("--permanent", f"--zone={ZONE}", f"--remove-rich-rule={rule}")
    for rule in sorted(set(wanted) - set(current)):
        _run("--permanent", f"--zone={ZONE}", f"--add-rich-rule={rule}")
    _run("--reload", timeout=60.0)


def _sync_state(wanted: dict[str, Any]) -> dict[str, Any]:
    """Apply one exact protocol-rule set and roll back both disk and runtime on failure."""
    wanted = _validate_state(dict(wanted))
    before = _read_state()
    before_rules = managed_rules(before)
    _verify_rules(before_rules)
    wanted_rules = managed_rules(wanted)
    if wanted == before:
        return {"changed": False, "state": wanted, "rules": list(wanted_rules)}
    try:
        _reconcile_permanent(before_rules, wanted_rules)
        _write_state(wanted)
        _verify_rules(wanted_rules)
    except Exception as exc:
        try:
            current = _listed_rules(permanent=True)
            _reconcile_permanent(current, before_rules)
            _write_state(before)
            _verify_rules(before_rules)
        except Exception as rollback_exc:
            raise OSError(
                "managed firewall update failed and rollback also failed; inspect firewalld"
            ) from rollback_exc
        if isinstance(exc, (OSError, ValueError)):
            raise
        raise OSError("managed firewall update failed") from exc
    return {"changed": True, "state": wanted, "rules": list(wanted_rules)}


def sync(*, smb: bool, nfs_clients: Sequence[str]) -> dict[str, Any]:
    """Synchronize NAS rules while preserving catalog-derived Hub forwarding."""

    current = _read_state()
    return _sync_state(
        desired_state(
            smb=smb,
            nfs_clients=nfs_clients,
            dlna=current["dlna"],
            hub_forwards=current["hubForwards"],
        )
    )


def sync_hub(hub_forwards: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Synchronize catalog-derived Hub rules while preserving NAS protocols."""

    current = _read_state()
    return _sync_state(
        desired_state(
            smb=current["smb"],
            nfs_clients=current["nfsClients"],
            dlna=current["dlna"],
            hub_forwards=hub_forwards,
        )
    )


def sync_dlna(*, enabled: bool) -> dict[str, Any]:
    """Synchronize the fixed ReadyMedia ports while preserving other owners."""

    current = _read_state()
    return _sync_state(
        desired_state(
            smb=current["smb"],
            nfs_clients=current["nfsClients"],
            dlna=enabled,
            hub_forwards=current["hubForwards"],
        )
    )


def verify_dlna(*, enabled: bool) -> dict[str, Any]:
    """Verify all managed rules and the exact DLNA enablement bit."""

    result = verify()
    if result["state"]["dlna"] is not enabled:
        raise OSError("managed firewall DLNA state differs from the active DLNA shares")
    return result


def verify_protocols(*, smb: bool, nfs_clients: Sequence[str]) -> dict[str, Any]:
    """Verify NAS protocol ownership without clobbering DLNA or Hub state."""

    result = verify()
    expected_clients = sorted({_canonical_private_network(item) for item in nfs_clients})
    if result["state"]["smb"] is not smb or result["state"]["nfsClients"] != expected_clients:
        raise OSError("managed firewall state differs from the active NAS shares")
    return result


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Echo-managed NAS firewall rules")
    parser.add_argument("command", choices=("verify-health",))
    args = parser.parse_args(argv)
    if args.command == "verify-health":
        try:
            result = verify()
        except (OSError, ValueError) as exc:
            print(f"managed NAS firewall verification failed: {exc}", file=sys.stderr)
            return 1
        print(f"ECHO_NATIVE_FIREWALL_READY rules={len(result['rules'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the shell health gate
    raise SystemExit(_main())


__all__ = [
    "STATE_PATH",
    "desired_state",
    "managed_rules",
    "sync",
    "sync_dlna",
    "sync_hub",
    "verify",
    "verify_dlna",
    "verify_protocols",
]
