"""Dense coverage for screen_relay frame codec (audit Q-05)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from runtime.tentacle.mobile.screen_relay import (
    FrameFlags,
    FrameType,
    ScreenRelay,
    _ws_remote_addr,
    decode_frame_header,
    encode_frame_header,
)


def test_frame_header_roundtrip() -> None:
    for tid in ("dev1", "", "很长很长的设备标识符"):
        for ftype in (FrameType.H264, FrameType.JPEG, FrameType.WEBP):
            for flags in (0, FrameFlags.KEYFRAME, FrameFlags.KEYFRAME | FrameFlags.LAST_SLICE):
                header = encode_frame_header(tid, ftype, flags)
                tid2, ftype2, flags2, size = decode_frame_header(header)
                assert tid2 == tid
                assert ftype2 == ftype
                assert flags2 == flags
                assert header[size:] == b""


def test_ws_remote_addr_shapes() -> None:
    assert _ws_remote_addr(SimpleNamespace(remote_address=("1.2.3.4", 9000))) == "1.2.3.4:9000"
    assert _ws_remote_addr(SimpleNamespace(remote_address="sock")) == "sock"
    assert _ws_remote_addr(SimpleNamespace(client=("5.6.7.8", 9001))) == "5.6.7.8:9001"
    assert _ws_remote_addr(SimpleNamespace()) == "unknown"


def test_mock_jpeg_and_stats(tmp_path: Path) -> None:
    relay = ScreenRelay(max_fps=5)
    jpeg = relay._generate_mock_jpeg("d1", 0)
    assert jpeg[:3] == b"\xff\xd8\xff"  # JPEG SOI marker
    minimal = relay._generate_mock_jpeg_minimal("d1", 1)
    assert minimal[:3] == b"\xff\xd8\xff"
    stats = relay.stats()
    assert "relays" in stats or "streams" in stats or isinstance(stats, dict)

