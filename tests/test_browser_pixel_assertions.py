from __future__ import annotations

import struct
import zlib

from runtime.safety.replay.browser_pixel_assertions import (
    assert_screenshot_pixels,
    browser_pixel_replay_gate_case,
    compare_screenshot_pixels,
)


def test_blank_screenshot_fails_pixel_assertion() -> None:
    image = _png(4, 4, [(255, 255, 255)] * 16)

    result = assert_screenshot_pixels(image)

    assert result["ok"] is False
    assert result["unique_colors"] == 1


def test_nonblank_screenshot_passes_pixel_assertion() -> None:
    pixels = [(255, 255, 255)] * 16
    pixels[0] = (0, 0, 0)
    image = _png(4, 4, pixels)

    result = assert_screenshot_pixels(
        image,
        min_non_background_ratio=0.05,
    )

    assert result["ok"] is True
    assert result["unique_colors"] == 2
    assert result["non_background_ratio"] == 0.0625


def test_screenshot_pixel_comparison_detects_change() -> None:
    before = _png(4, 4, [(255, 255, 255)] * 16)
    after_pixels = [(255, 255, 255)] * 16
    after_pixels[0] = (0, 0, 0)
    after_pixels[1] = (0, 0, 0)
    after = _png(4, 4, after_pixels)

    result = compare_screenshot_pixels(before, after, min_changed_ratio=0.1)

    assert result["ok"] is True
    assert result["changed_ratio"] == 0.125


def test_browser_pixel_failure_becomes_replay_gate_case() -> None:
    assertion = {
        "schema": "echo.browser_pixel_assertion.v1",
        "ok": False,
        "reason": "screenshot appears blank or unchanged",
        "unique_colors": 1,
        "non_background_ratio": 0.0,
        "thresholds": {"min_unique_colors": 2},
    }

    case = browser_pixel_replay_gate_case(
        artifact={
            "filename": "screenshot-1.png",
            "url": "/api/browser-artifacts/screenshot-1.png",
            "width": 4,
            "height": 4,
        },
        assertion=assertion,
        task_id="turn-1",
        agent_id="browser-agent",
    )

    assert case is not None
    assert case["schema"] == "echo.browser_pixel_replay_gate_case.v1"
    assert case["case_id"] == "browser-pixel::screenshot-1.png"
    assert case["replay_gate"]["passed"] is False
    assert case["replay_gate"]["reason"] == "browser_pixel_evidence_failed"
    assert case["failures"][0]["metrics"]["unique_colors"] == 1


def test_browser_pixel_success_does_not_create_replay_gate_case() -> None:
    case = browser_pixel_replay_gate_case(
        artifact={"filename": "screenshot-ok.png"},
        assertion={"schema": "echo.browser_pixel_assertion.v1", "ok": True},
        comparison={"schema": "echo.browser_pixel_comparison.v1", "ok": True},
    )

    assert case is None


def _png(width: int, height: int, pixels: list[tuple[int, int, int]]) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for pixel in pixels[y * width : (y + 1) * width]:
            row.extend(pixel)
        rows.append(bytes(row))
    payload = b"".join(rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(payload))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    import zlib as _zlib

    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", _zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


