#!/usr/bin/env python3
"""Validate Echo OS's bounded firewalld configuration and vendor zone."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CONFIG_PATH = Path("/etc/firewalld/firewalld.conf")
ZONE_PATH = Path("/usr/lib/firewalld/zones/echo-public.xml")
MAX_CONFIG_BYTES = 16 * 1024
MAX_ZONE_BYTES = 16 * 1024
ZONE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DEFAULT_ZONE = "echo-public"
ZONE_DESCRIPTION = (
    "Reject unsolicited inbound and forwarded traffic unless an administrator "
    "explicitly authorizes it."
)
CONFIG_INVARIANTS = {
    "CleanupOnExit": "no",
    "CleanupModulesOnExit": "no",
    "IPv6_rpfilter": "strict",
    "IndividualCalls": "no",
    "LogDenied": "off",
    "FirewallBackend": "nftables",
    "FlushAllOnReload": "yes",
    "ReloadPolicy": "INPUT:DROP,FORWARD:DROP,OUTPUT:DROP",
    "RFC3964_IPv4": "yes",
    "StrictForwardPorts": "yes",
    "NftablesFlowtable": "off",
    "NftablesCounters": "no",
    "NftablesTableOwner": "yes",
}


class FirewallPolicyError(ValueError):
    """Raised when host firewall policy no longer meets the signed baseline."""


def read_policy_file(path: Path, maximum: int, label: str, expected_uid: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_uid
            or stat.S_IMODE(before.st_mode) != 0o644
            or not 1 <= before.st_size <= maximum
        ):
            raise FirewallPolicyError(f"{label} must be an owned mode-0644 bounded regular file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(4096, remaining))
            if not chunk:
                raise FirewallPolicyError(f"{label} was truncated while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FirewallPolicyError(f"{label} grew while being read")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise FirewallPolicyError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def parse_config(raw: bytes, *, require_vendor_default: bool) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise FirewallPolicyError("firewalld configuration is not UTF-8") from error
    if "\x00" in text:
        raise FirewallPolicyError("firewalld configuration contains NUL")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if raw_line != line or line.count("=") != 1:
            raise FirewallPolicyError(f"invalid firewalld configuration line {line_number}")
        key, value = line.split("=", 1)
        if key in values:
            raise FirewallPolicyError(f"duplicate firewalld option: {key}")
        values[key] = value

    expected_keys = {"DefaultZone", *CONFIG_INVARIANTS}
    if set(values) != expected_keys:
        raise FirewallPolicyError("firewalld configuration has missing or unknown options")
    default_zone = values["DefaultZone"]
    if ZONE_NAME.fullmatch(default_zone) is None:
        raise FirewallPolicyError("firewalld default zone name is unsafe")
    if require_vendor_default and default_zone != DEFAULT_ZONE:
        raise FirewallPolicyError("fresh image must default to the Echo public zone")
    for key, expected in CONFIG_INVARIANTS.items():
        if values[key] != expected:
            raise FirewallPolicyError(f"firewalld security invariant changed: {key}")
    return default_zone


def verify_vendor_zone(raw: bytes) -> None:
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise FirewallPolicyError("firewalld zone must not contain a document type or entity")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise FirewallPolicyError("Echo public zone XML is malformed") from error
    if root.tag != "zone" or root.attrib != {"target": "default"}:
        raise FirewallPolicyError("Echo public zone root or target is invalid")
    children = list(root)
    if [child.tag for child in children] != ["short", "description", "service"]:
        raise FirewallPolicyError("Echo public zone has an unexpected capability")
    short, description, service = children
    if short.attrib or (short.text or "").strip() != "Echo Public" or list(short):
        raise FirewallPolicyError("Echo public zone short name is invalid")
    if (
        description.attrib
        or (description.text or "").strip() != ZONE_DESCRIPTION
        or list(description)
    ):
        raise FirewallPolicyError("Echo public zone description is invalid")
    if service.attrib != {"name": "dhcpv6-client"} or (service.text or "").strip() or list(service):
        raise FirewallPolicyError("Echo public zone must allow only the DHCPv6 client service")


def verify_policy(
    config: Path,
    zone: Path,
    *,
    expected_uid: int,
    require_vendor_default: bool,
) -> str:
    config_raw = read_policy_file(
        config,
        MAX_CONFIG_BYTES,
        "firewalld configuration",
        expected_uid,
    )
    zone_raw = read_policy_file(
        zone,
        MAX_ZONE_BYTES,
        "Echo public zone",
        expected_uid,
    )
    default_zone = parse_config(config_raw, require_vendor_default=require_vendor_default)
    verify_vendor_zone(zone_raw)
    return default_zone


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("verify-baseline", "verify-runtime"),
        help="baseline fixes the fresh-image zone; runtime permits an authorized default-zone change",
    )
    parser.add_argument("--machine", action="store_true")
    args = parser.parse_args()
    try:
        default_zone = verify_policy(
            CONFIG_PATH,
            ZONE_PATH,
            expected_uid=0,
            require_vendor_default=args.command == "verify-baseline",
        )
    except (FirewallPolicyError, OSError, UnicodeError) as error:
        print(f"Echo OS firewall policy rejected: {error}", file=sys.stderr)
        return 1
    if args.machine:
        print(default_zone)
    else:
        print(
            "ECHO_FIREWALL_POLICY_ACCEPTED",
            "backend=nftables",
            f"default-zone={default_zone}",
            "forward=explicit",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
