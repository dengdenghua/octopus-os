#!/usr/bin/env python3
"""Validate Echo OS' local-only CUPS and driverless USB policy."""

from __future__ import annotations

import argparse
import configparser
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

SYSTEM_CUPS_CONFIG = Path("/etc/cups/cupsd.conf")
SYSTEM_IPP_USB_CONFIG = Path("/etc/ipp-usb/ipp-usb.conf")
SOURCE_TEST_SENTINEL = "USE-SOURCE-RUNTIME"
MAX_CONFIG_BYTES = 128 * 1024


class PolicyError(RuntimeError):
    """Raised when printing policy is unsafe or incomplete."""


@dataclass(frozen=True)
class Directive:
    name: str
    value: str
    context: tuple[tuple[str, str], ...]


def _read_secure_file(path: Path, *, expected_uid: int, expected_gid: int) -> str:
    if not path.is_absolute():
        raise PolicyError(f"printing policy path is not absolute: {path}")
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise PolicyError(f"printing policy is missing: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PolicyError(f"printing policy is not a regular non-symlink file: {path}")
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        raise PolicyError(f"printing policy is not owned by the expected root identity: {path}")
    if metadata.st_mode & 0o022:
        raise PolicyError(f"printing policy is group/world writable: {path}")
    if metadata.st_size <= 0 or metadata.st_size > MAX_CONFIG_BYTES:
        raise PolicyError(f"printing policy has an unsafe size: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PolicyError(f"printing policy cannot be read as UTF-8: {path}") from error


def _parse_cups(text: str) -> list[Directive]:
    directives: list[Directive] = []
    stack: list[tuple[str, str]] = []
    open_tag = re.compile(r"^<([A-Za-z][A-Za-z0-9-]*)(?:\s+([^<>]+))?>$")
    close_tag = re.compile(r"^</([A-Za-z][A-Za-z0-9-]*)>$")

    for line_number, source_line in enumerate(text.splitlines(), start=1):
        if "\x00" in source_line or source_line.rstrip().endswith("\\"):
            raise PolicyError(f"unsupported CUPS syntax on line {line_number}")
        line = source_line.split("#", 1)[0].strip()
        if not line:
            continue
        closing = close_tag.fullmatch(line)
        if closing:
            if not stack or stack[-1][0] != closing.group(1).lower():
                raise PolicyError(f"mismatched CUPS block on line {line_number}")
            stack.pop()
            continue
        opening = open_tag.fullmatch(line)
        if opening:
            stack.append(
                (
                    opening.group(1).lower(),
                    (opening.group(2) or "").strip(),
                )
            )
            continue
        name, separator, value = line.partition(" ")
        directives.append(
            Directive(
                name=name.lower(),
                value=value.strip() if separator else "",
                context=tuple(stack),
            )
        )
    if stack:
        raise PolicyError("CUPS policy contains an unterminated block")
    return directives


def _top_values(directives: list[Directive], name: str) -> list[str]:
    return [item.value for item in directives if not item.context and item.name == name]


def _require_single(directives: list[Directive], name: str, value: str) -> None:
    values = _top_values(directives, name)
    if [item.casefold() for item in values] != [value.casefold()]:
        raise PolicyError(f"CUPS directive {name} must occur once as {value!r}: {values!r}")


def _location_directives(directives: list[Directive], location: str) -> list[Directive]:
    context = (("location", location),)
    return [item for item in directives if item.context == context]


def verify_cups_policy(text: str) -> None:
    directives = _parse_cups(text)
    forbidden = {
        "allow",
        "browseallow",
        "browsepoll",
        "browseremoteprotocols",
        "include",
        "includeoptional",
        "port",
        "serveralias",
        "sslport",
        "ssllisten",
    }
    for directive in directives:
        if directive.name in forbidden:
            raise PolicyError(f"CUPS policy contains forbidden directive: {directive.name}")

    listeners = _top_values(directives, "listen")
    if listeners != ["localhost:631", "/run/cups/cups.sock"]:
        raise PolicyError(f"CUPS listeners are not the fixed local-only pair: {listeners!r}")

    for name, value in (
        ("browsing", "No"),
        ("browselocalprotocols", "none"),
        ("defaultshared", "No"),
        ("webinterface", "No"),
        ("preservejobfiles", "No"),
        ("preservejobhistory", "No"),
        ("maxjobs", "50"),
        ("maxlogsize", "1m"),
        ("defaultauthtype", "Basic"),
    ):
        _require_single(directives, name, value)
    _require_single(directives, "pagelogformat", "")

    for location in ("/admin", "/admin/conf", "/admin/log"):
        scoped = _location_directives(directives, location)
        values = {(item.name, item.value.casefold()) for item in scoped}
        if ("authtype", "default") not in values or (
            "require",
            "user @system",
        ) not in values:
            raise PolicyError(f"CUPS administrator location is not protected: {location}")

    root_values = {
        (item.name, item.value.casefold()) for item in _location_directives(directives, "/")
    }
    if ("order", "allow,deny") not in root_values:
        raise PolicyError("CUPS root location does not use the local-only default order")


def verify_ipp_usb_policy(text: str) -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise PolicyError("ipp-usb policy is not valid INI") from error
    expected = {
        "network": {
            "http-min-port": "60000",
            "http-max-port": "65535",
            "dns-sd": "enable",
            "interface": "loopback",
            "ipv6": "enable",
        },
        "logging": {
            "device-log": "error",
            "main-log": "error",
            "console-log": "error",
            "max-file-size": "256K",
            "max-backup-files": "2",
            "console-color": "disable",
        },
    }
    if set(parser.sections()) != set(expected):
        raise PolicyError("ipp-usb policy contains an unexpected or missing section")
    for section, values in expected.items():
        actual = dict(parser.items(section))
        if actual != values:
            raise PolicyError(f"ipp-usb section {section} differs from the fixed policy")


def verify_files(
    cups_config: Path,
    ipp_usb_config: Path,
    *,
    expected_uid: int,
    expected_gid: int = 0,
) -> None:
    verify_cups_policy(
        _read_secure_file(
            cups_config,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    )
    verify_ipp_usb_policy(
        _read_secure_file(
            ipp_usb_config,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cups-config", type=Path, default=SYSTEM_CUPS_CONFIG)
    parser.add_argument("--ipp-usb-config", type=Path, default=SYSTEM_IPP_USB_CONFIG)
    parser.add_argument("--expected-uid", type=int, default=0)
    parser.add_argument("--expected-gid", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    using_override = (
        arguments.cups_config != SYSTEM_CUPS_CONFIG
        or arguments.ipp_usb_config != SYSTEM_IPP_USB_CONFIG
        or arguments.expected_uid != 0
        or arguments.expected_gid != 0
    )
    if using_override and os.environ.get("ECHO_PRINTING_SOURCE_TEST") != SOURCE_TEST_SENTINEL:
        raise PolicyError("printing policy overrides require the source-test sentinel")
    verify_files(
        arguments.cups_config,
        arguments.ipp_usb_config,
        expected_uid=arguments.expected_uid,
        expected_gid=arguments.expected_gid,
    )
    print(
        "ECHO_PRINTING_POLICY_READY listener=local-only sharing=off "
        "web=off retention=off usb=ipp-loopback"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PolicyError as error:
        print(f"printing policy error: {error}", file=__import__("sys").stderr)
        raise SystemExit(1) from error
