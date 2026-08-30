#!/usr/bin/env python3
"""Validate Echo OS' on-demand SANE AirScan privacy policy."""

from __future__ import annotations

import argparse
import configparser
import os
import stat
from pathlib import Path

SYSTEM_AIRSCAN_CONFIG = Path("/etc/sane.d/airscan.conf")
SOURCE_TEST_SENTINEL = "USE-SOURCE-RUNTIME"
MAX_CONFIG_BYTES = 64 * 1024


class PolicyError(RuntimeError):
    """Raised when the scanning policy is unsafe or incomplete."""


def _read_secure_file(path: Path, *, expected_uid: int, expected_gid: int) -> str:
    if not path.is_absolute():
        raise PolicyError(f"scanning policy path is not absolute: {path}")
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise PolicyError(f"scanning policy is missing: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PolicyError(f"scanning policy is not a regular non-symlink file: {path}")
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        raise PolicyError(f"scanning policy has the wrong owner: {path}")
    if metadata.st_mode & 0o022:
        raise PolicyError(f"scanning policy is group/world writable: {path}")
    if metadata.st_size <= 0 or metadata.st_size > MAX_CONFIG_BYTES:
        raise PolicyError(f"scanning policy has an unsafe size: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PolicyError(f"scanning policy cannot be read as UTF-8: {path}") from error


def verify_airscan_policy(text: str) -> None:
    if "\x00" in text:
        raise PolicyError("AirScan policy contains a NUL byte")
    parser = configparser.ConfigParser(
        interpolation=None,
        strict=True,
        inline_comment_prefixes=("#", ";"),
    )
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise PolicyError("AirScan policy is not valid INI") from error

    expected = {
        "devices": {},
        "options": {
            "discovery": "enable",
            "model": "network",
            "protocol": "auto",
            "ws-discovery": "fast",
            "pretend-local": "false",
        },
        "debug": {
            "enable": "false",
            "hexdump": "false",
        },
        "blacklist": {},
    }
    if parser.defaults():
        raise PolicyError("AirScan policy contains unexpected defaults")
    if set(parser.sections()) != set(expected):
        raise PolicyError("AirScan policy contains an unexpected or missing section")
    for section, expected_values in expected.items():
        actual_values = dict(parser.items(section, raw=True))
        if actual_values != expected_values:
            raise PolicyError(f"AirScan section {section} differs from the fixed policy")


def verify_file(path: Path, *, expected_uid: int, expected_gid: int = 0) -> None:
    verify_airscan_policy(
        _read_secure_file(path, expected_uid=expected_uid, expected_gid=expected_gid)
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--airscan-config", type=Path, default=SYSTEM_AIRSCAN_CONFIG)
    parser.add_argument("--expected-uid", type=int, default=0)
    parser.add_argument("--expected-gid", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    using_override = (
        arguments.airscan_config != SYSTEM_AIRSCAN_CONFIG
        or arguments.expected_uid != 0
        or arguments.expected_gid != 0
    )
    if using_override and os.environ.get("ECHO_SCANNING_SOURCE_TEST") != SOURCE_TEST_SENTINEL:
        raise PolicyError("scanning policy overrides require the source-test sentinel")
    verify_file(
        arguments.airscan_config,
        expected_uid=arguments.expected_uid,
        expected_gid=arguments.expected_gid,
    )
    print("ECHO_SCANNING_POLICY_READY discovery=on-demand protocol=escl,wsd sharing=off trace=off")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PolicyError as error:
        print(f"scanning policy error: {error}", file=__import__("sys").stderr)
        raise SystemExit(1) from error
