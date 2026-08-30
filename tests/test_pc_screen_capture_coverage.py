"""Dense coverage for pc_screen_capture config + coords (audit Q-05)."""

from __future__ import annotations

from runtime.tentacle.mobile.pc_screen_capture import (
    CaptureBackend,
    PcScreenCapture,
    PcScreenConfig,
    RemoteInputHandler,
)
from runtime.tentacle.mobile.screen_relay import ScreenRelay


def test_config_defaults_and_overrides() -> None:
    cfg = PcScreenConfig()
    assert cfg.fps == 10
    assert cfg.jpeg_quality == 65
    assert cfg.scale == 0.5
    assert cfg.backend is CaptureBackend.MSS
    custom = PcScreenConfig(fps=30, jpeg_quality=80, scale=1.0, monitor_index=1)
    assert custom.fps == 30 and custom.jpeg_quality == 80 and custom.scale == 1.0


def test_remote_coord_mapping() -> None:
    handler = RemoteInputHandler(screen_width=1920, screen_height=1080)
    assert handler._map_coords(0.5, 0.5) == (960, 540)
    assert handler._map_coords(0.0, 0.0) == (0, 0)
    assert handler._map_coords(1.0, 1.0) == (1919, 1079)  # clamped
    assert handler._map_coords(2.0, -1.0) == (1919, 0)
    handler.update_screen_size(800, 600)
    assert handler._map_coords(0.5, 0.5) == (400, 300)


def test_capture_stats_and_is_running() -> None:
    cap = PcScreenCapture(ScreenRelay())
    assert cap.is_running is False
    stats = cap.stats
    assert stats["running"] is False
    assert "config" in stats

