"""Pixel assertions for browser/computer replay evidence.

Replay evidence needs more than "a screenshot file exists". These helpers make
small, deterministic assertions against PNG screenshots without external image
dependencies: nonblank checks and before/after changed-pixel ratios.
"""

from __future__ import annotations

import struct
import zlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def assert_screenshot_pixels(
    source: bytes | str | Path,
    *,
    min_unique_colors: int = 2,
    min_non_background_ratio: float = 0.01,
) -> dict[str, Any]:
    pixels, width, height = _png_pixels(_read_source(source))
    total = max(1, len(pixels))
    counts = Counter(pixels)
    background_count = counts.most_common(1)[0][1] if counts else 0
    non_background_ratio = round((total - background_count) / total, 4)
    ok = len(counts) >= min_unique_colors and non_background_ratio >= min_non_background_ratio
    return {
        "schema": "echo.browser_pixel_assertion.v1",
        "ok": ok,
        "width": width,
        "height": height,
        "unique_colors": len(counts),
        "non_background_ratio": non_background_ratio,
        "thresholds": {
            "min_unique_colors": min_unique_colors,
            "min_non_background_ratio": min_non_background_ratio,
        },
        "reason": "passed" if ok else "screenshot appears blank or unchanged",
    }


def compare_screenshot_pixels(
    before: bytes | str | Path,
    after: bytes | str | Path,
    *,
    min_changed_ratio: float = 0.01,
) -> dict[str, Any]:
    before_pixels, before_width, before_height = _png_pixels(_read_source(before))
    after_pixels, after_width, after_height = _png_pixels(_read_source(after))
    if (before_width, before_height) != (after_width, after_height):
        return {
            "schema": "echo.browser_pixel_comparison.v1",
            "ok": False,
            "changed_ratio": 0.0,
            "reason": "screenshot dimensions differ",
            "before": {"width": before_width, "height": before_height},
            "after": {"width": after_width, "height": after_height},
            "thresholds": {"min_changed_ratio": min_changed_ratio},
        }
    total = max(1, len(before_pixels))
    changed = sum(1 for lhs, rhs in zip(before_pixels, after_pixels, strict=True) if lhs != rhs)
    changed_ratio = round(changed / total, 4)
    ok = changed_ratio >= min_changed_ratio
    return {
        "schema": "echo.browser_pixel_comparison.v1",
        "ok": ok,
        "changed_ratio": changed_ratio,
        "reason": "passed" if ok else "not enough pixels changed",
        "before": {"width": before_width, "height": before_height},
        "after": {"width": after_width, "height": after_height},
        "thresholds": {"min_changed_ratio": min_changed_ratio},
    }


def browser_pixel_replay_gate_case(
    *,
    artifact: dict[str, Any],
    assertion: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    task_id: str = "",
    agent_id: str = "",
) -> dict[str, Any] | None:
    """Return a replay-gate case when browser pixel evidence fails.

    Browser/computer-use regressions are visual by nature. A failed pixel
    assertion or before/after comparison should become a durable replay
    artifact instead of a transient screenshot warning, so promotion gates can
    block or explain UI regressions with concrete evidence.
    """
    failed: list[dict[str, Any]] = []
    for item in (assertion, comparison):
        if isinstance(item, dict) and item.get("ok") is False:
            failed.append(item)
    if not failed:
        return None

    filename = str(artifact.get("filename") or "")
    artifact_url = str(artifact.get("url") or "")
    case_id_source = filename or artifact_url or datetime.now(UTC).isoformat()
    return {
        "schema": "echo.browser_pixel_replay_gate_case.v1",
        "case_id": f"browser-pixel::{case_id_source}",
        "task_id": str(task_id or artifact.get("task_id") or ""),
        "agent_id": str(agent_id or artifact.get("agent_id") or ""),
        "status": "failed",
        "kind": "browser_pixel_evidence",
        "artifact": {
            "filename": filename,
            "url": artifact_url,
            "local_path": str(artifact.get("local_path") or ""),
            "width": artifact.get("width"),
            "height": artifact.get("height"),
        },
        "failures": [
            {
                "schema": item.get("schema"),
                "reason": item.get("reason") or "browser pixel evidence failed",
                "thresholds": item.get("thresholds") or {},
                "metrics": _pixel_failure_metrics(item),
            }
            for item in failed
        ],
        "replay_gate": {
            "passed": False,
            "reason": "browser_pixel_evidence_failed",
            "min_cases": 1,
        },
        "next_actions": [
            "Replay the browser step and compare screenshots before promotion.",
            "Attach this case to the task-run replay gate before accepting the change.",
        ],
    }


def _pixel_failure_metrics(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "unique_colors",
        "non_background_ratio",
        "changed_ratio",
        "width",
        "height",
    ):
        if key in item:
            out[key] = item[key]
    return out


def _read_source(source: bytes | str | Path) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def _png_pixels(data: bytes) -> tuple[list[tuple[int, ...]], int, int]:
    if not data.startswith(_PNG_SIG):
        raise ValueError("not a PNG screenshot")
    offset = len(_PNG_SIG)
    width = 0
    height = 0
    channels = 0
    idat = bytearray()
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(
                ">IIBB",
                chunk_data[:10],
            )
            if bit_depth != 8 or color_type not in {2, 6}:
                raise ValueError("only 8-bit RGB/RGBA PNG screenshots are supported")
            channels = 3 if color_type == 2 else 4
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if width <= 0 or height <= 0 or channels <= 0:
        raise ValueError("PNG is missing a supported IHDR")
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows: list[bytes] = []
    pos = 0
    prev = bytes(stride)
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        scanline = bytearray(raw[pos : pos + stride])
        pos += stride
        _unfilter(scanline, prev, filter_type, channels)
        rows.append(bytes(scanline))
        prev = rows[-1]
    pixels: list[tuple[int, ...]] = []
    for row in rows:
        for idx in range(0, len(row), channels):
            pixels.append(tuple(row[idx : idx + channels]))
    return pixels, width, height


def _unfilter(
    scanline: bytearray,
    prev: bytes,
    filter_type: int,
    bpp: int,
) -> None:
    for idx, value in enumerate(scanline):
        left = scanline[idx - bpp] if idx >= bpp else 0
        up = prev[idx] if idx < len(prev) else 0
        up_left = prev[idx - bpp] if idx >= bpp and idx - bpp < len(prev) else 0
        if filter_type == 0:
            continue
        if filter_type == 1:
            scanline[idx] = (value + left) & 0xFF
        elif filter_type == 2:
            scanline[idx] = (value + up) & 0xFF
        elif filter_type == 3:
            scanline[idx] = (value + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            scanline[idx] = (value + _paeth(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"unsupported PNG filter type: {filter_type}")


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    pa = abs(estimate - left)
    pb = abs(estimate - up)
    pc = abs(estimate - up_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return up_left


__all__ = [
    "assert_screenshot_pixels",
    "browser_pixel_replay_gate_case",
    "compare_screenshot_pixels",
]
