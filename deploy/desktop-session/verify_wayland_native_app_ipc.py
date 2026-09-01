#!/usr/bin/env python3
"""Validate compositor evidence for the packaged Echo native-app IPC smoke.

The KWin bridge owns the authoritative Wayland window UUIDs. This helper keeps
the shell harness from interpreting bridge JSON and exposes only three bounded
operations: capture a baseline, find one new KCalc window, and prove that an
acknowledged close removed that exact UUID.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Optional

MAX_INPUT_BYTES = 1024 * 1024
MAX_WINDOWS = 4096
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


class WaylandIpcEvidenceError(RuntimeError):
    """KWin returned malformed or ambiguous native-app evidence."""


@dataclass(frozen=True)
class Window:
    window_id: str
    pid: int
    wm_class: str


def parse_state(text: str) -> tuple[Window, ...]:
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise WaylandIpcEvidenceError("KWin state exceeded its size limit")
    try:
        state = json.loads(text)
    except json.JSONDecodeError as error:
        raise WaylandIpcEvidenceError("KWin state is not valid JSON") from error
    windows = state.get("windows") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or state.get("ok") is not True
        or not isinstance(windows, list)
        or len(windows) > MAX_WINDOWS
    ):
        raise WaylandIpcEvidenceError("KWin state has an invalid window list")

    parsed: list[Window] = []
    seen: set[str] = set()
    for item in windows:
        if not isinstance(item, dict):
            raise WaylandIpcEvidenceError("KWin state contains an invalid window")
        window_id = item.get("id")
        pid = item.get("pid")
        wm_class = item.get("wmClass")
        if (
            not isinstance(window_id, str)
            or UUID_PATTERN.fullmatch(window_id) is None
            or window_id in seen
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid < 0
            or not isinstance(wm_class, str)
            or len(wm_class) > 512
        ):
            raise WaylandIpcEvidenceError("KWin window identity is invalid")
        seen.add(window_id)
        parsed.append(Window(window_id, pid, wm_class))
    return tuple(parsed)


def parse_baseline_ids(text: str) -> frozenset[str]:
    values = [line for line in text.splitlines() if line]
    if len(values) > MAX_WINDOWS or len(values) != len(set(values)):
        raise WaylandIpcEvidenceError("baseline window identities are invalid")
    if any(UUID_PATTERN.fullmatch(value) is None for value in values):
        raise WaylandIpcEvidenceError("baseline window identity is invalid")
    return frozenset(values)


def baseline_ids(windows: tuple[Window, ...]) -> str:
    return "\n".join(window.window_id for window in windows)


def is_kcalc(window: Window) -> bool:
    identity = window.wm_class.lower().removesuffix(".desktop")
    tokens = {token for token in re.split(r"[^a-z0-9]+", identity) if token}
    return identity == "org.kde.kcalc" or "kcalc" in tokens


def find_new_kcalc(
    windows: tuple[Window, ...], baseline: frozenset[str]
) -> Optional[str]:
    matches = [
        window.window_id
        for window in windows
        if window.window_id not in baseline and window.pid > 0 and is_kcalc(window)
    ]
    if len(matches) > 1:
        raise WaylandIpcEvidenceError("multiple new KCalc windows appeared")
    return matches[0] if matches else None


def read_standard_input() -> str:
    value = sys.stdin.read(MAX_INPUT_BYTES + 1)
    if len(value.encode("utf-8")) > MAX_INPUT_BYTES:
        raise WaylandIpcEvidenceError("KWin state exceeded its size limit")
    return value


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    subparsers = value.add_subparsers(dest="command", required=True)
    subparsers.add_parser("baseline")
    find = subparsers.add_parser("find")
    find.add_argument("--baseline-ids", required=True)
    absent = subparsers.add_parser("absent")
    absent.add_argument("--window-id", required=True)
    return value


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        windows = parse_state(read_standard_input())
        if arguments.command == "baseline":
            print(baseline_ids(windows))
            return 0
        if arguments.command == "find":
            match = find_new_kcalc(
                windows, parse_baseline_ids(arguments.baseline_ids)
            )
            if match is None:
                return 1
            print(match)
            return 0
        if UUID_PATTERN.fullmatch(arguments.window_id) is None:
            raise WaylandIpcEvidenceError("closed window identity is invalid")
        return 1 if any(
            window.window_id == arguments.window_id for window in windows
        ) else 0
    except WaylandIpcEvidenceError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
