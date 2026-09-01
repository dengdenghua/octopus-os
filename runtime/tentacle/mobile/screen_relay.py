"""屏幕流中继服务.

设备推送 H.264/JPEG 帧 → ScreenRelay → 订阅的浏览器客户端.

两种模式：
1. 二进制帧模式：设备发送 H.264 NAL 单元（WebSocket binary frame）
   → 直接转发给订阅者（低延迟，需 WebCodecs 解码）
2. JPEG 快照模式：设备发送 JPEG 图片（WebSocket binary frame）
   → 直接转发给订阅者（兼容性好，延迟较高）

协议格式（二进制帧前4字节为header）：
- byte 0-1: tentacle_id 长度 (uint16 BE)
- byte 2: 帧类型 (0x01=H.264, 0x02=JPEG, 0x03=WebP)
- byte 3: 标志位 (bit0=关键帧, bit1=最后一个分片)
- byte 4+: tentacle_id (UTF-8)
- byte 4+len+: 帧数据
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


# ── 帧类型常量 ──────────────────────────────────────────────


class FrameType(IntEnum):
    """二进制帧类型."""

    H264 = 0x01  # H.264 NAL 单元
    JPEG = 0x02  # JPEG 图片
    WEBP = 0x03  # WebP 图片


class FrameFlags(IntEnum):
    """帧标志位."""

    KEYFRAME = 0x01  # 关键帧
    LAST_SLICE = 0x02  # 最后一个分片


# ── 帧头编解码 ──────────────────────────────────────────────

# 帧头格式: uint16 (tentacle_id 长度) + uint8 (帧类型) + uint8 (标志位)
FRAME_HEADER_FORMAT = "!HBB"
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FORMAT)  # 4 字节


def encode_frame_header(
    tentacle_id: str,
    frame_type: FrameType,
    flags: int = 0,
) -> bytes:
    """编码帧头.

    Args:
        tentacle_id: 设备 ID
        frame_type: 帧类型（H264/JPEG/WebP）
        flags: 标志位（bit0=关键帧, bit1=最后分片）

    Returns:
        帧头字节（4 + len(tentacle_id) 字节）
    """
    tid_bytes = tentacle_id.encode("utf-8")
    header = struct.pack(FRAME_HEADER_FORMAT, len(tid_bytes), frame_type, flags)
    return header + tid_bytes


def decode_frame_header(data: bytes) -> tuple[str, FrameType, int, int]:
    """解码帧头.

    Args:
        data: 原始二进制数据

    Returns:
        (tentacle_id, frame_type, flags, header_size) 元组
    """
    tid_len, ftype, flags = struct.unpack(FRAME_HEADER_FORMAT, data[:FRAME_HEADER_SIZE])
    tid_start = FRAME_HEADER_SIZE
    tid_end = tid_start + tid_len
    tentacle_id = data[tid_start:tid_end].decode("utf-8")
    return tentacle_id, FrameType(ftype), flags, tid_end


def _ws_remote_addr(ws: Any) -> str:
    """获取 WebSocket 连接的远程地址（兼容 websockets 和 FastAPI）.

    Args:
        ws: WebSocket 连接对象

    Returns:
        远程地址字符串
    """
    # websockets 库: ws.remote_address = (host, port)
    if hasattr(ws, "remote_address"):
        addr = ws.remote_address
        if isinstance(addr, tuple):
            return f"{addr[0]}:{addr[1]}"
        return str(addr)
    # FastAPI/Starlette: ws.client = (host, port)
    if hasattr(ws, "client"):
        client = ws.client
        if isinstance(client, tuple):
            return f"{client[0]}:{client[1]}"
        return str(client)
    return "unknown"


# ── 屏幕流中继 ──────────────────────────────────────────────


@dataclass
class DeviceStreamState:
    """设备屏幕流状态."""

    tentacle_id: str
    # 最新 JPEG 截图（用于 REST 端点获取截图）
    last_jpeg: bytes | None = None
    last_jpeg_ts: float = 0.0
    # 帧计数
    frame_count: int = 0
    # 最后帧时间
    last_frame_ts: float = 0.0


class ScreenRelay:
    """屏幕流中继服务.

    设备推送视频帧 → ScreenRelay → 订阅的浏览器客户端.

    支持：
    - 多个客户端同时订阅同一设备
    - 帧率控制（默认 15fps）
    - 最新截图缓存（供 REST 端点获取）
    - Mock 模式（无设备时生成测试图案）

    WebSocket 类型兼容：
    - websockets 库的 WebSocketServerProtocol（设备连接）
    - FastAPI/Starlette 的 WebSocket（浏览器客户端连接）
    两者都有 send() 方法，通过 Any 类型统一接受。
    """

    def __init__(self, *, max_fps: int = 15) -> None:
        self._max_fps = max_fps
        # tentacle_id → 订阅者 WebSocket 列表
        self._subscribers: dict[str, list[Any]] = {}
        # ws → 订阅的 tentacle_id 集合（用于快速移除）
        self._ws_subscriptions: dict[Any, set[str]] = {}
        # tentacle_id → 设备流状态
        self._stream_states: dict[str, DeviceStreamState] = {}
        # 帧率控制：tentacle_id → 上次转发时间
        self._last_relay_ts: dict[str, float] = {}
        # 锁
        self._lock = asyncio.Lock()
        # Mock 模式任务
        self._mock_tasks: dict[str, asyncio.Task] = {}

    # ── 订阅管理 ────────────────────────────────────────────

    async def add_subscriber(self, tentacle_id: str, ws_client: Any) -> None:
        """添加订阅者.

        Args:
            tentacle_id: 要订阅的设备 ID
            ws_client: 浏览器客户端 WebSocket 连接
                       （兼容 websockets 和 FastAPI 的 WebSocket）
        """
        async with self._lock:
            if tentacle_id not in self._subscribers:
                self._subscribers[tentacle_id] = []
            self._subscribers[tentacle_id].append(ws_client)

            if ws_client not in self._ws_subscriptions:
                self._ws_subscriptions[ws_client] = set()
            self._ws_subscriptions[ws_client].add(tentacle_id)

            # 初始化设备流状态
            if tentacle_id not in self._stream_states:
                self._stream_states[tentacle_id] = DeviceStreamState(tentacle_id=tentacle_id)

        logger.info(
            "screen subscriber added: ws=%s device=%s total_subscribers=%d",
            _ws_remote_addr(ws_client),
            tentacle_id,
            len(self._subscribers.get(tentacle_id, [])),
        )

    async def remove_subscriber(self, ws_client: Any) -> None:
        """移除订阅者（断开连接时调用）.

        Args:
            ws_client: 要移除的浏览器客户端 WebSocket
        """
        async with self._lock:
            subscribed_ids = self._ws_subscriptions.pop(ws_client, set())
            for tid in subscribed_ids:
                subs = self._subscribers.get(tid, [])
                if ws_client in subs:
                    subs.remove(ws_client)
                # 如果该设备没有订阅者了，清理状态
                if not subs:
                    self._subscribers.pop(tid, None)
                    self._last_relay_ts.pop(tid, None)

        if subscribed_ids:
            logger.info(
                "screen subscriber removed: ws=%s devices=%s",
                _ws_remote_addr(ws_client),
                subscribed_ids,
            )

    async def unsubscribe(self, tentacle_id: str, ws_client: Any) -> None:
        """取消对指定设备的订阅.

        Args:
            tentacle_id: 设备 ID
            ws_client: 浏览器客户端 WebSocket
        """
        async with self._lock:
            subs = self._subscribers.get(tentacle_id, [])
            if ws_client in subs:
                subs.remove(ws_client)
            if not subs:
                self._subscribers.pop(tentacle_id, None)
                self._last_relay_ts.pop(tentacle_id, None)

            ws_subs = self._ws_subscriptions.get(ws_client, set())
            ws_subs.discard(tentacle_id)
            if not ws_subs:
                self._ws_subscriptions.pop(ws_client, None)

        logger.info(
            "screen unsubscribed: ws=%s device=%s",
            _ws_remote_addr(ws_client),
            tentacle_id,
        )

    def get_subscribers(self, tentacle_id: str) -> list[Any]:
        """获取某设备的订阅者列表."""
        return list(self._subscribers.get(tentacle_id, []))

    @property
    def subscriber_count(self) -> int:
        """当前总订阅者数."""
        return len(self._ws_subscriptions)

    # ── 帧中继 ──────────────────────────────────────────────

    async def relay_frame(self, tentacle_id: str, binary_data: bytes) -> None:
        """中继帧数据到所有订阅者.

        设备发送的二进制帧格式：
        - byte 0-1: tentacle_id 长度 (uint16 BE)
        - byte 2: 帧类型 (0x01=H.264, 0x02=JPEG, 0x03=WebP)
        - byte 3: 标志位 (bit0=关键帧, bit1=最后一个分片)
        - byte 4+: tentacle_id (UTF-8)
        - byte 4+len+: 帧数据

        Args:
            tentacle_id: 设备 ID
            binary_data: 原始二进制帧数据（含帧头）
        """
        # 解析帧头获取帧类型和标志
        try:
            parsed_tid, frame_type, flags, header_size = decode_frame_header(binary_data)
        except (struct.error, ValueError, IndexError) as e:
            logger.warning("invalid frame header from %s: %s", tentacle_id, e)
            return

        # 更新设备流状态
        state = self._stream_states.get(tentacle_id)
        if state is None:
            state = DeviceStreamState(tentacle_id=tentacle_id)
            self._stream_states[tentacle_id] = state

        state.frame_count += 1
        state.last_frame_ts = time.time()

        # 缓存最新 JPEG 截图
        if frame_type == FrameType.JPEG:
            frame_data = binary_data[header_size:]
            state.last_jpeg = frame_data
            state.last_jpeg_ts = time.time()

        # 帧率控制
        now = time.time()
        min_interval = 1.0 / self._max_fps if self._max_fps > 0 else 0
        last_ts = self._last_relay_ts.get(tentacle_id, 0)
        if now - last_ts < min_interval:
            return  # 跳过此帧（帧率过高）
        self._last_relay_ts[tentacle_id] = now

        # 获取订阅者并转发
        subscribers = self._subscribers.get(tentacle_id, [])
        if not subscribers:
            return

        # 并发转发给所有订阅者
        tasks = []
        for ws in subscribers:
            tasks.append(self._send_frame(ws, binary_data))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.debug(
                    "send frame to subscriber failed: ws=%s error=%s",
                    _ws_remote_addr(subscribers[i]),
                    result,
                )

    async def _send_frame(self, ws: Any, data: bytes) -> None:
        """发送帧数据到单个订阅者.

        Args:
            ws: 订阅者 WebSocket 连接
            data: 二进制帧数据
        """
        try:
            await ws.send(data)
        except Exception as e:
            logger.debug("send frame error: %s", e)
            raise

    # ── 截图获取 ──────────────────────────────────────────────

    def get_screenshot(self, tentacle_id: str) -> bytes | None:
        """获取设备最新截图（JPEG）.

        Args:
            tentacle_id: 设备 ID

        Returns:
            JPEG 字节数据，无截图时返回 None
        """
        state = self._stream_states.get(tentacle_id)
        if state is None or state.last_jpeg is None:
            return None
        return state.last_jpeg

    def get_stream_info(self, tentacle_id: str) -> dict[str, Any] | None:
        """获取设备屏幕流信息.

        Args:
            tentacle_id: 设备 ID

        Returns:
            流信息字典，设备不存在时返回 None
        """
        state = self._stream_states.get(tentacle_id)
        if state is None:
            return None
        return {
            "tentacle_id": state.tentacle_id,
            "frame_count": state.frame_count,
            "last_frame_ts": state.last_frame_ts,
            "has_screenshot": state.last_jpeg is not None,
            "last_jpeg_ts": state.last_jpeg_ts,
            "subscribers": len(self._subscribers.get(tentacle_id, [])),
        }

    # ── 设备断开清理 ──────────────────────────────────────────

    async def on_device_disconnect(self, tentacle_id: str) -> None:
        """设备断开时清理.

        Args:
            tentacle_id: 断开的设备 ID
        """
        async with self._lock:
            # 停止 mock 任务
            mock_task = self._mock_tasks.pop(tentacle_id, None)
            if mock_task is not None:
                mock_task.cancel()

            # 清理流状态
            self._stream_states.pop(tentacle_id, None)
            self._last_relay_ts.pop(tentacle_id, None)

            # 通知订阅者设备已断开
            subscribers = self._subscribers.pop(tentacle_id, [])
            for ws in subscribers:
                ws_subs = self._ws_subscriptions.get(ws, set())
                ws_subs.discard(tentacle_id)
                if not ws_subs:
                    self._ws_subscriptions.pop(ws, None)

        # 发送断开通知给订阅者
        for ws in subscribers:
            try:
                import json as _json

                await ws.send(
                    _json.dumps(
                        {
                            "type": "device_disconnected",
                            "tentacle_id": tentacle_id,
                        }
                    )
                )
            except Exception:  # noqa: BLE001 — best-effort; fail-open
                pass

        logger.info(
            "screen relay cleaned up for device=%s subscribers_notified=%d",
            tentacle_id,
            len(subscribers),
        )

    # ── Mock 模式 ──────────────────────────────────────────────

    async def start_mock_stream(self, tentacle_id: str) -> None:
        """启动 mock 屏幕流（生成测试图案）.

        当无真实设备连接时，可用于测试前端显示。

        Args:
            tentacle_id: 设备 ID
        """
        if tentacle_id in self._mock_tasks:
            return  # 已在运行

        async def _mock_loop() -> None:
            """生成 mock JPEG 帧的循环."""
            try:
                from PIL import Image, ImageDraw, ImageFont  # noqa: F401

                has_pil = True
            except ImportError:
                has_pil = False

            frame_interval = 1.0 / self._max_fps
            frame_idx = 0

            while True:
                try:
                    if has_pil:
                        jpeg_data = self._generate_mock_jpeg(tentacle_id, frame_idx)
                    else:
                        jpeg_data = self._generate_mock_jpeg_minimal(tentacle_id, frame_idx)

                    # 编码帧
                    header = encode_frame_header(tentacle_id, FrameType.JPEG, FrameFlags.KEYFRAME)
                    frame = header + jpeg_data

                    # 直接更新状态并转发
                    state = self._stream_states.get(tentacle_id)
                    if state is None:
                        state = DeviceStreamState(tentacle_id=tentacle_id)
                        self._stream_states[tentacle_id] = state
                    state.frame_count += 1
                    state.last_frame_ts = time.time()
                    state.last_jpeg = jpeg_data
                    state.last_jpeg_ts = time.time()

                    # 转发给订阅者
                    subscribers = self._subscribers.get(tentacle_id, [])
                    for ws in subscribers:
                        with contextlib.suppress(Exception):  # best-effort; fail-open
                            await ws.send(frame)

                    frame_idx += 1
                    await asyncio.sleep(frame_interval)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("mock stream error: %s", e)
                    await asyncio.sleep(1)

        task = asyncio.create_task(_mock_loop())
        self._mock_tasks[tentacle_id] = task
        logger.info("mock screen stream started for device=%s", tentacle_id)

    def stop_mock_stream(self, tentacle_id: str) -> None:
        """停止 mock 屏幕流."""
        task = self._mock_tasks.pop(tentacle_id, None)
        if task is not None:
            task.cancel()
            logger.info("mock screen stream stopped for device=%s", tentacle_id)

    @staticmethod
    def _generate_mock_jpeg(tentacle_id: str, frame_idx: int) -> bytes:
        """使用 PIL 生成 mock JPEG 测试图案."""
        import io

        from PIL import Image, ImageDraw

        img = Image.new("RGB", (320, 240), color=(30, 30, 40))
        draw = ImageDraw.Draw(img)

        # 绘制网格
        for x in range(0, 320, 40):
            draw.line([(x, 0), (x, 240)], fill=(60, 60, 80))
        for y in range(0, 240, 40):
            draw.line([(0, y), (320, y)], fill=(60, 60, 80))

        # 绘制时间戳
        ts = time.strftime("%H:%M:%S")
        draw.text((10, 10), f"MOCK: {tentacle_id}", fill=(100, 200, 255))
        draw.text((10, 40), f"Frame: {frame_idx}", fill=(200, 200, 100))
        draw.text((10, 70), f"Time: {ts}", fill=(150, 255, 150))

        # 绘制动态矩形
        x_pos = (frame_idx * 5) % 280
        draw.rectangle([x_pos, 120, x_pos + 40, 160], fill=(255, 100, 100))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        return buf.getvalue()

    @staticmethod
    def _generate_mock_jpeg_minimal(tentacle_id: str, frame_idx: int) -> bytes:
        """生成最小 mock JPEG（无 PIL 依赖时使用）.

        返回一个 1x1 像素的最小有效 JPEG.
        """
        # 最小有效 JPEG：1x1 灰色像素
        return bytes(
            [
                0xFF,
                0xD8,
                0xFF,
                0xE0,
                0x00,
                0x10,
                0x4A,
                0x46,
                0x49,
                0x46,
                0x00,
                0x01,
                0x01,
                0x00,
                0x00,
                0x01,
                0x00,
                0x01,
                0x00,
                0x00,
                0xFF,
                0xDB,
                0x00,
                0x43,
                0x00,
                0x08,
                0x06,
                0x06,
                0x07,
                0x06,
                0x05,
                0x08,
                0x07,
                0x07,
                0x07,
                0x09,
                0x09,
                0x08,
                0x0A,
                0x0C,
                0x14,
                0x0D,
                0x0C,
                0x0B,
                0x0B,
                0x0C,
                0x19,
                0x12,
                0x13,
                0x0F,
                0x14,
                0x1D,
                0x1A,
                0x1F,
                0x1E,
                0x1D,
                0x1A,
                0x1C,
                0x1C,
                0x20,
                0x24,
                0x2E,
                0x27,
                0x20,
                0x22,
                0x2C,
                0x23,
                0x1C,
                0x1C,
                0x28,
                0x37,
                0x29,
                0x2C,
                0x30,
                0x31,
                0x34,
                0x34,
                0x34,
                0x1F,
                0x27,
                0x39,
                0x3D,
                0x38,
                0x32,
                0x3C,
                0x2E,
                0x33,
                0x34,
                0x32,
                0xFF,
                0xC0,
                0x00,
                0x0B,
                0x08,
                0x00,
                0x01,
                0x00,
                0x01,
                0x01,
                0x01,
                0x11,
                0x00,
                0xFF,
                0xC4,
                0x00,
                0x1F,
                0x00,
                0x00,
                0x01,
                0x05,
                0x01,
                0x01,
                0x01,
                0x01,
                0x01,
                0x01,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x01,
                0x02,
                0x03,
                0x04,
                0x05,
                0x06,
                0x07,
                0x08,
                0x09,
                0x0A,
                0x0B,
                0xFF,
                0xC4,
                0x00,
                0xB5,
                0x10,
                0x00,
                0x02,
                0x01,
                0x03,
                0x03,
                0x02,
                0x04,
                0x03,
                0x05,
                0x05,
                0x04,
                0x04,
                0x00,
                0x00,
                0x01,
                0x7D,
                0x01,
                0x02,
                0x03,
                0x00,
                0x04,
                0x11,
                0x05,
                0x12,
                0x21,
                0x31,
                0x41,
                0x06,
                0x13,
                0x51,
                0x61,
                0x07,
                0x22,
                0x71,
                0x14,
                0x32,
                0x81,
                0x91,
                0xA1,
                0x08,
                0x23,
                0x42,
                0xB1,
                0xC1,
                0x15,
                0x52,
                0xD1,
                0xF0,
                0x24,
                0x33,
                0x62,
                0x72,
                0x82,
                0x09,
                0x0A,
                0x16,
                0x17,
                0x18,
                0x19,
                0x1A,
                0x25,
                0x26,
                0x27,
                0x28,
                0x29,
                0x2A,
                0x34,
                0x35,
                0x36,
                0x37,
                0x38,
                0x39,
                0x3A,
                0x43,
                0x44,
                0x45,
                0x46,
                0x47,
                0x48,
                0x49,
                0x4A,
                0x53,
                0x54,
                0x55,
                0x56,
                0x57,
                0x58,
                0x59,
                0x5A,
                0x63,
                0x64,
                0x65,
                0x66,
                0x67,
                0x68,
                0x69,
                0x6A,
                0x73,
                0x74,
                0x75,
                0x76,
                0x77,
                0x78,
                0x79,
                0x7A,
                0x83,
                0x84,
                0x85,
                0x86,
                0x87,
                0x88,
                0x89,
                0x8A,
                0x92,
                0x93,
                0x94,
                0x95,
                0x96,
                0x97,
                0x98,
                0x99,
                0x9A,
                0xA2,
                0xA3,
                0xA4,
                0xA5,
                0xA6,
                0xA7,
                0xA8,
                0xA9,
                0xAA,
                0xB2,
                0xB3,
                0xB4,
                0xB5,
                0xB6,
                0xB7,
                0xB8,
                0xB9,
                0xBA,
                0xC2,
                0xC3,
                0xC4,
                0xC5,
                0xC6,
                0xC7,
                0xC8,
                0xC9,
                0xCA,
                0xD2,
                0xD3,
                0xD4,
                0xD5,
                0xD6,
                0xD7,
                0xD8,
                0xD9,
                0xDA,
                0xE1,
                0xE2,
                0xE3,
                0xE4,
                0xE5,
                0xE6,
                0xE7,
                0xE8,
                0xE9,
                0xEA,
                0xF1,
                0xF2,
                0xF3,
                0xF4,
                0xF5,
                0xF6,
                0xF7,
                0xF8,
                0xF9,
                0xFA,
                0xFF,
                0xDA,
                0x00,
                0x08,
                0x01,
                0x01,
                0x00,
                0x00,
                0x3F,
                0x00,
                0x7B,
                0x94,
                0xFF,
                0xD9,
            ]
        )

    # ── 统计 ──────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """获取屏幕流中继统计信息."""
        return {
            "max_fps": self._max_fps,
            "total_subscribers": len(self._ws_subscriptions),
            "device_streams": {
                tid: {
                    "subscribers": len(subs),
                    "frame_count": self._stream_states.get(tid, DeviceStreamState(tid)).frame_count,
                    "has_screenshot": self._stream_states.get(tid, DeviceStreamState(tid)).last_jpeg
                    is not None,
                }
                for tid, subs in self._subscribers.items()
            },
            "mock_streams": list(self._mock_tasks.keys()),
        }
