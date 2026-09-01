#!/usr/bin/env python3
"""Send the fixed Super+Alt+S chord to a disposable Echo OS QEMU VM."""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO

QMP_TIMEOUT_SECONDS = 5.0
CAPABILITIES_ID = "echo-capabilities"
SEND_KEY_ID = "echo-screen-reader-key"
SCREEN_READER_KEYS = (
    {"type": "qcode", "data": "meta_l"},
    {"type": "qcode", "data": "alt"},
    {"type": "qcode", "data": "s"},
)


def validate_socket(socket_path: Path) -> None:
    if not socket_path.is_absolute():
        raise ValueError("QMP socket path must be absolute")
    try:
        metadata = socket_path.lstat()
    except OSError as error:
        raise ValueError("QMP socket is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISSOCK(metadata.st_mode):
        raise ValueError("QMP endpoint must be a real Unix socket")
    if metadata.st_uid != os.getuid():
        raise ValueError("QMP socket belongs to a different user")
    if metadata.st_mode & 0o077:
        raise ValueError("QMP socket must be private to the VM test owner")


def read_message(stream: BinaryIO) -> dict[str, Any]:
    line = stream.readline()
    if not line:
        raise RuntimeError("QMP closed before replying")
    try:
        message = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("QMP returned an invalid JSON message") from error
    if not isinstance(message, dict):
        raise RuntimeError("QMP returned a non-object message")
    return message


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\r\n"
    stream.write(payload)
    stream.flush()


def expect_response(stream: BinaryIO, request_id: str) -> None:
    for _attempt in range(64):
        message = read_message(stream)
        if message.get("id") != request_id:
            continue
        if "error" in message:
            raise RuntimeError(f"QMP rejected {request_id}")
        if "return" not in message:
            raise RuntimeError(f"QMP response for {request_id} is incomplete")
        return
    raise RuntimeError(f"QMP did not answer {request_id}")


def send_screen_reader_key(socket_path: Path) -> None:
    validate_socket(socket_path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(QMP_TIMEOUT_SECONDS)
        connection.connect(str(socket_path))
        with connection.makefile("rwb", buffering=0) as stream:
            greeting = read_message(stream)
            if not isinstance(greeting.get("QMP"), dict):
                raise RuntimeError("QMP greeting is missing")
            write_message(
                stream,
                {"execute": "qmp_capabilities", "id": CAPABILITIES_ID},
            )
            expect_response(stream, CAPABILITIES_ID)
            write_message(
                stream,
                {
                    "execute": "send-key",
                    "arguments": {
                        "keys": list(SCREEN_READER_KEYS),
                        "hold-time": 150,
                    },
                    "id": SEND_KEY_ID,
                },
            )
            expect_response(stream, SEND_KEY_ID)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("socket", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        send_screen_reader_key(args.socket)
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("ECHO_QMP_KEY_SENT chord=super-alt-s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
