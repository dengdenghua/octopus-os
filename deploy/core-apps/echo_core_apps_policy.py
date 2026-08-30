#!/usr/bin/env python3
"""Validate Echo OS' signed-root XDG default-application policy."""

from __future__ import annotations

import argparse
import configparser
import os
import stat
from pathlib import Path

SYSTEM_MIMEAPPS = Path("/etc/xdg/mimeapps.list")
SOURCE_TEST_SENTINEL = "USE-SOURCE-RUNTIME"
MAX_CONFIG_BYTES = 64 * 1024

EXPECTED_DEFAULTS = {
    "inode/directory": "org.kde.dolphin.desktop;",
    "text/plain": "org.kde.kate.desktop;",
    "text/markdown": "org.kde.kate.desktop;",
    "text/csv": "org.kde.kate.desktop;",
    "application/json": "org.kde.kate.desktop;",
    "application/xml": "org.kde.kate.desktop;",
    "text/html": "firefox-esr.desktop;",
    "application/xhtml+xml": "firefox-esr.desktop;",
    "x-scheme-handler/http": "firefox-esr.desktop;",
    "x-scheme-handler/https": "firefox-esr.desktop;",
    "application/pdf": "org.kde.okular.desktop;",
    "application/postscript": "org.kde.okular.desktop;",
    "image/jpeg": "org.kde.gwenview.desktop;",
    "image/png": "org.kde.gwenview.desktop;",
    "image/gif": "org.kde.gwenview.desktop;",
    "image/webp": "org.kde.gwenview.desktop;",
    "image/svg+xml": "org.kde.gwenview.desktop;",
    "image/tiff": "org.kde.gwenview.desktop;",
    "application/zip": "org.kde.ark.desktop;",
    "application/x-tar": "org.kde.ark.desktop;",
    "application/x-7z-compressed": "org.kde.ark.desktop;",
    "application/vnd.rar": "org.kde.ark.desktop;",
    "application/gzip": "org.kde.ark.desktop;",
    "application/x-bzip2": "org.kde.ark.desktop;",
    "application/x-xz": "org.kde.ark.desktop;",
    "application/zstd": "org.kde.ark.desktop;",
    "video/mp4": "org.kde.haruna.desktop;",
    "video/x-matroska": "org.kde.haruna.desktop;",
    "video/webm": "org.kde.haruna.desktop;",
    "audio/mpeg": "org.kde.haruna.desktop;",
    "audio/ogg": "org.kde.haruna.desktop;",
    "audio/flac": "org.kde.haruna.desktop;",
    "audio/x-wav": "org.kde.haruna.desktop;",
    "audio/vnd.wave": "org.kde.haruna.desktop;",
}


class PolicyError(RuntimeError):
    """Raised when the default-application policy is unsafe or incomplete."""


def _read_secure_file(path: Path, *, expected_uid: int, expected_gid: int) -> str:
    if not path.is_absolute():
        raise PolicyError(f"core-app policy path is not absolute: {path}")
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise PolicyError(f"core-app policy is missing: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PolicyError(f"core-app policy is not a regular non-symlink file: {path}")
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        raise PolicyError(f"core-app policy has the wrong owner: {path}")
    if metadata.st_mode & 0o022:
        raise PolicyError(f"core-app policy is group/world writable: {path}")
    if metadata.st_size <= 0 or metadata.st_size > MAX_CONFIG_BYTES:
        raise PolicyError(f"core-app policy has an unsafe size: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PolicyError(f"core-app policy cannot be read as UTF-8: {path}") from error


def verify_mimeapps_policy(text: str) -> None:
    if "\x00" in text:
        raise PolicyError("core-app policy contains a NUL byte")
    parser = configparser.ConfigParser(
        interpolation=None,
        strict=True,
        delimiters=("=",),
        comment_prefixes=("#",),
        inline_comment_prefixes=None,
    )
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise PolicyError("core-app policy is not valid INI") from error
    if parser.defaults():
        raise PolicyError("core-app policy contains unexpected defaults")
    if parser.sections() != ["Default Applications"]:
        raise PolicyError("core-app policy must contain only Default Applications")
    actual = dict(parser.items("Default Applications", raw=True))
    if actual != EXPECTED_DEFAULTS:
        raise PolicyError("core-app default handlers differ from the fixed policy")


def verify_file(path: Path, *, expected_uid: int, expected_gid: int = 0) -> None:
    verify_mimeapps_policy(
        _read_secure_file(path, expected_uid=expected_uid, expected_gid=expected_gid)
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mimeapps", type=Path, default=SYSTEM_MIMEAPPS)
    parser.add_argument("--expected-uid", type=int, default=0)
    parser.add_argument("--expected-gid", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    using_override = (
        arguments.mimeapps != SYSTEM_MIMEAPPS
        or arguments.expected_uid != 0
        or arguments.expected_gid != 0
    )
    if using_override and os.environ.get("ECHO_CORE_APPS_SOURCE_TEST") != SOURCE_TEST_SENTINEL:
        raise PolicyError("core-app policy overrides require the source-test sentinel")
    verify_file(
        arguments.mimeapps,
        expected_uid=arguments.expected_uid,
        expected_gid=arguments.expected_gid,
    )
    print(
        "ECHO_CORE_APPS_POLICY_READY browser=firefox files=dolphin text=kate "
        "documents=okular images=gwenview archives=ark media=haruna"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PolicyError as error:
        print(f"core-app policy error: {error}", file=__import__("sys").stderr)
        raise SystemExit(1) from error
