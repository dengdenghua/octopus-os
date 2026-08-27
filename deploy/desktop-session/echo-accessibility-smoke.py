#!/usr/bin/env python3
"""Prove that one fixed Echo marker is present in a live AT-SPI tree.

The probe deliberately never prints accessible names or tree contents: a real
desktop accessibility tree may contain private document, notification, or Agent
text.  Callers provide a fixed product marker and the PID of the application
they started; success requires the matching object to belong to that process or
one of its descendants.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
from typing import Iterator


MAX_ACCESSIBLE_NODES = 10_000
MAX_ACCESSIBLE_DEPTH = 64


def _positive_pid(value: str) -> int:
    try:
        pid = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("PID must be an integer") from error
    if pid <= 0:
        raise argparse.ArgumentTypeError("PID must be positive")
    return pid


def _read_parent_pid(pid: int, proc_root: Path = Path("/proc")) -> int | None:
    try:
        status = (proc_root / str(pid) / "status").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    for line in status.splitlines():
        if line.startswith("PPid:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def process_belongs_to(
    candidate_pid: int,
    root_pid: int,
    proc_root: Path = Path("/proc"),
) -> bool:
    """Return whether candidate is root_pid or a live descendant of it."""

    current = candidate_pid
    seen: set[int] = set()
    while current > 0 and current not in seen:
        if current == root_pid:
            return True
        seen.add(current)
        parent = _read_parent_pid(current, proc_root)
        if parent is None or parent == current:
            return False
        current = parent
    return False


def _safe_name(accessible: object) -> str:
    try:
        return str(getattr(accessible, "name", "") or "")
    except Exception:
        return ""


def _safe_children(accessible: object) -> Iterator[object]:
    try:
        count = int(getattr(accessible, "childCount"))
    except Exception:
        return
    for index in range(max(0, min(count, MAX_ACCESSIBLE_NODES))):
        try:
            child = accessible.getChildAtIndex(index)
        except Exception:
            continue
        if child is not None:
            yield child


def walk_accessibles(root: object) -> Iterator[object]:
    """Walk a bounded AT-SPI tree without serializing any node payload."""

    stack: list[tuple[object, int]] = [(root, 0)]
    visited = 0
    while stack and visited < MAX_ACCESSIBLE_NODES:
        accessible, depth = stack.pop()
        visited += 1
        yield accessible
        if depth >= MAX_ACCESSIBLE_DEPTH:
            continue
        children = list(_safe_children(accessible))
        stack.extend((child, depth + 1) for child in reversed(children))


def _application_pid(accessible: object) -> int | None:
    try:
        application = accessible.getApplication()
        process_id = int(application.get_process_id())
    except Exception:
        return None
    return process_id if process_id > 0 else None


def marker_belongs_to_process(
    desktop: object,
    required_name: str,
    root_pid: int,
    proc_root: Path = Path("/proc"),
) -> bool:
    for accessible in walk_accessibles(desktop):
        if _safe_name(accessible) != required_name:
            continue
        process_id = _application_pid(accessible)
        if process_id is not None and process_belongs_to(
            process_id, root_pid, proc_root
        ):
            return True
    return False


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-pid", required=True, type=_positive_pid)
    parser.add_argument("--required-name", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--poll-interval", type=float, default=0.2)
    args = parser.parse_args(argv)
    if not args.required_name or len(args.required_name) > 256:
        parser.error("required name must contain 1-256 characters")
    if not 0 < args.timeout_seconds <= 60:
        parser.error("timeout must be between 0 and 60 seconds")
    if not 0 < args.poll_interval <= 5:
        parser.error("poll interval must be between 0 and 5 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        print("AT-SPI probe requires a session D-Bus", file=sys.stderr)
        return 1
    try:
        import pyatspi
    except ImportError:
        print("AT-SPI Python bindings are unavailable", file=sys.stderr)
        return 1

    deadline = time.monotonic() + args.timeout_seconds
    while time.monotonic() < deadline:
        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception:
            desktop = None
        if desktop is not None and marker_belongs_to_process(
            desktop, args.required_name, args.root_pid
        ):
            print("ECHO_ACCESSIBILITY_TREE_READY provider=at-spi2 application=echo")
            return 0
        time.sleep(args.poll_interval)

    print("Echo application did not expose its fixed AT-SPI marker", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
