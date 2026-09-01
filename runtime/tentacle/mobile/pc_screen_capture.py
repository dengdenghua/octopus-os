"""PC 屏幕捕获 + 推流.

捕获电脑屏幕画面 → JPEG 编码 → 推送到 ScreenRelay → 手机端订阅显示.

两种捕获方式：
1. mss（推荐）：跨平台高速截屏，零依赖 PIL
2. PIL.ImageGrab：备选方案，需 Pillow

帧协议复用 screen_relay.py 的格式：
  byte 0-1: tentacle_id 长度 (uint16 BE)
  byte 2: 帧类型 (0x02=JPEG)
  byte 3: 标志位 (bit0=关键帧)
  byte 4+: tentacle_id (UTF-8) = "pc-host"
  byte 4+len+: 帧数据
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .screen_relay import ScreenRelay

logger = logging.getLogger(__name__)

# PC 屏幕源固定 ID
PC_HOST_ID = "pc-host"


class CaptureBackend(IntEnum):
    """截屏后端."""

    MSS = 0x01  # mss 库（推荐，最快）
    PILGRAB = 0x02  # PIL.ImageGrab（备选）


@dataclass
class PcScreenConfig:
    """PC 屏幕捕获配置."""

    # 目标 FPS
    fps: int = 10
    # JPEG 质量 (1-95)
    jpeg_quality: int = 65
    # 缩放比例（0.5 = 半分辨率，降低带宽）
    scale: float = 0.5
    # 捕获显示器编号（0=主屏）
    monitor_index: int = 0
    # 截屏后端
    backend: CaptureBackend = CaptureBackend.MSS
    # 最大帧大小（字节），超过则降低质量
    max_frame_bytes: int = 100_000  # 100KB


class PcScreenCapture:
    """PC 屏幕捕获 + 推流.

    用法::

        capture = PcScreenCapture(screen_relay, config=PcScreenConfig(fps=10))
        await capture.start()
        # ... 运行中 ...
        await capture.stop()
    """

    def __init__(
        self,
        screen_relay: ScreenRelay,
        *,
        config: PcScreenConfig | None = None,
    ) -> None:
        self._relay = screen_relay
        self._config = config or PcScreenConfig()
        self._task: asyncio.Task | None = None
        self._running = False
        # 统计
        self._frame_count = 0
        self._last_frame_ts: float = 0.0
        self._avg_frame_size: float = 0.0
        self._errors: int = 0
        # 捕获后端实例
        self._capture_fn = self._init_capture_backend()

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self) -> None:
        """启动屏幕捕获循环."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())
        logger.info(
            "PC screen capture started: fps=%d scale=%.1f backend=%s",
            self._config.fps,
            self._config.scale,
            self._config.backend.name,
        )

    async def stop(self) -> None:
        """停止屏幕捕获."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info(
            "PC screen capture stopped: frames=%d errors=%d", self._frame_count, self._errors
        )

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        """捕获统计."""
        return {
            "running": self._running,
            "frame_count": self._frame_count,
            "last_frame_ts": self._last_frame_ts,
            "avg_frame_size_kb": round(self._avg_frame_size / 1024, 1),
            "errors": self._errors,
            "config": {
                "fps": self._config.fps,
                "scale": self._config.scale,
                "jpeg_quality": self._config.jpeg_quality,
                "backend": self._config.backend.name,
            },
        }

    # ── 捕获循环 ──────────────────────────────────────────

    async def _capture_loop(self) -> None:
        """主捕获循环."""
        from .screen_relay import FrameFlags, FrameType, encode_frame_header

        interval = 1.0 / self._config.fps
        quality = self._config.jpeg_quality

        while self._running:
            try:
                # 截屏 + 编码
                jpeg_data = await asyncio.get_event_loop().run_in_executor(
                    None, self._capture_and_encode, quality
                )

                if jpeg_data is not None:
                    # 帧大小超限则降低质量
                    if len(jpeg_data) > self._config.max_frame_bytes:
                        quality = max(30, quality - 5)
                    elif len(jpeg_data) < self._config.max_frame_bytes // 2:
                        quality = min(90, quality + 2)

                    # 编码帧头
                    header = encode_frame_header(PC_HOST_ID, FrameType.JPEG, FrameFlags.KEYFRAME)
                    frame = header + jpeg_data

                    # 推送到 ScreenRelay
                    await self._relay.relay_frame(PC_HOST_ID, frame)

                    # 更新统计
                    self._frame_count += 1
                    self._last_frame_ts = time.time()
                    self._avg_frame_size = self._avg_frame_size * 0.9 + len(jpeg_data) * 0.1

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._errors += 1
                logger.warning("PC screen capture error: %s", e)
                await asyncio.sleep(1)

            await asyncio.sleep(interval)

    # ── 截屏后端 ──────────────────────────────────────────

    def _init_capture_backend(self) -> Any:
        """初始化截屏后端，返回捕获函数."""
        if self._config.backend == CaptureBackend.MSS:
            try:
                import mss  # noqa: F401

                return self._capture_mss
            except ImportError:
                logger.warning("mss not installed, falling back to PIL")

        try:
            from PIL import ImageGrab  # noqa: F401

            return self._capture_pil
        except ImportError:
            logger.error("Neither mss nor PIL available for screen capture")
            return self._capture_none

    def _capture_mss(self) -> bytes | None:
        """使用 mss 截屏."""
        import mss

        with mss.mss() as sct:
            monitors = sct.monitors
            if not monitors or len(monitors) <= self._config.monitor_index:
                return None
            monitor = monitors[self._config.monitor_index]
            screenshot = sct.grab(monitor)
            # mss 返回 BGRA，需要转 RGB
            from PIL import Image

            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            return self._encode_jpeg(img)

    def _capture_pil(self) -> bytes | None:
        """使用 PIL.ImageGrab 截屏."""
        from PIL import ImageGrab

        img = ImageGrab.grab()
        return self._encode_jpeg(img)

    def _capture_none(self) -> bytes | None:
        """无可用截屏后端."""
        return None

    def _capture_and_encode(self, quality: int) -> bytes | None:
        """截屏并编码为 JPEG."""
        self._config.jpeg_quality = quality
        raw = self._capture_fn()
        if raw is not None:
            return raw

        # 如果捕获失败，尝试用另一个后端
        if self._config.backend == CaptureBackend.MSS:
            try:
                from PIL import ImageGrab

                img = ImageGrab.grab()
                return self._encode_jpeg(img)
            except Exception:  # noqa: BLE001 — best-effort; fail-open
                pass
        return None

    def _encode_jpeg(self, img: Any) -> bytes:
        """将 PIL Image 编码为 JPEG（可选缩放）."""
        # 缩放
        if self._config.scale != 1.0 and self._config.scale > 0:
            new_w = int(img.width * self._config.scale)
            new_h = int(img.height * self._config.scale)
            img = img.resize((new_w, new_h))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self._config.jpeg_quality)
        return buf.getvalue()


# ── 远程输入控制 ──────────────────────────────────────────


class RemoteInputHandler:
    """处理来自手机端的远程输入事件，转发为本机鼠标/键盘操作.

    参考 ToDesk / 网易UU 远程桌面，支持完整的触控→鼠标映射：

    基础操作：
    - click(x, y) → 鼠标左键点击
    - double_click(x, y) → 鼠标双击
    - right_click(x, y) → 鼠标右键
    - middle_click(x, y) → 鼠标中键

    鼠标移动：
    - mouse_move(x, y) → 移动鼠标到绝对位置
    - mouse_move(dx, dy) → 相对移动（触控板模式）

    拖拽操作：
    - drag_start(x, y) → 按下鼠标左键
    - drag_move(x, y) → 移动鼠标（按住状态）
    - drag_end(x, y) → 释放鼠标左键

    滚动/缩放：
    - scroll(x, y, deltaY) → 鼠标滚轮
    - zoom(x, y, deltaY) → Ctrl+滚轮（缩放）

    键盘：
    - type_text(text) → 键盘输入文字
    - key_combo(key) → 组合键（alt+tab, win+d, ctrl+c 等）
    """

    # 组合键映射表：前端快捷键名 → pyautogui 按键序列
    KEY_COMBO_MAP: dict[str, list[str]] = {
        "alt+tab": ["alt", "tab"],
        "win+d": ["win", "d"],
        "win+e": ["win", "e"],
        "ctrl+c": ["ctrl", "c"],
        "ctrl+v": ["ctrl", "v"],
        "ctrl+x": ["ctrl", "x"],
        "ctrl+z": ["ctrl", "z"],
        "ctrl+a": ["ctrl", "a"],
        "ctrl+s": ["ctrl", "s"],
        "ctrl+w": ["ctrl", "w"],
        "ctrl+t": ["ctrl", "t"],
        "ctrl+n": ["ctrl", "n"],
        "ctrl+f": ["ctrl", "f"],
        "ctrl+shift+esc": ["ctrl", "shift", "esc"],
        "alt+f4": ["alt", "f4"],
        "f11": ["f11"],
        "esc": ["escape"],
        "enter": ["enter"],
        "backspace": ["backspace"],
        "delete": ["delete"],
        "tab": ["tab"],
        "home": ["home"],
        "end": ["end"],
        "pageup": ["pageup"],
        "pagedown": ["pagedown"],
        "printscreen": ["printscreen"],
    }

    def __init__(self, *, screen_width: int = 0, screen_height: int = 0) -> None:
        """初始化远程输入处理器.

        Args:
            screen_width: PC 屏幕实际宽度（0=自动检测）
            screen_height: PC 屏幕实际高度（0=自动检测）
        """
        self._screen_width = screen_width
        self._screen_height = screen_height
        self._pyautogui = None
        self._initialized = False
        # 拖拽状态
        self._dragging = False
        self._drag_button = "left"

    def _ensure_init(self) -> bool:
        """延迟初始化 pyautogui."""
        if self._initialized:
            return self._pyautogui is not None
        self._initialized = True
        try:
            import pyautogui

            self._pyautogui = pyautogui
            # 安全设置
            pyautogui.PAUSE = 0.0  # 零延迟，追求流畅
            pyautogui.FAILSAFE = True
            # 自动检测屏幕尺寸
            if self._screen_width == 0 or self._screen_height == 0:
                self._screen_width = pyautogui.size().width
                self._screen_height = pyautogui.size().height
            logger.info(
                "RemoteInputHandler initialized: screen=%dx%d",
                self._screen_width,
                self._screen_height,
            )
            return True
        except ImportError:
            logger.warning("pyautogui not installed, remote input disabled")
            return False

    def update_screen_size(self, width: int, height: int) -> None:
        """更新屏幕尺寸（坐标映射用）."""
        self._screen_width = width
        self._screen_height = height

    async def handle_input(self, event: dict[str, Any]) -> dict[str, Any]:
        """处理远程输入事件.

        Args:
            event: 输入事件字典，支持以下 action：
              - click / double_click / right_click / middle_click
              - mouse_move (绝对 x,y 或相对 dx,dy)
              - drag_start / drag_move / drag_end
              - scroll / zoom
              - type_text / key_combo

        Returns:
            {"success": bool, "error": str | None}
        """
        if not self._ensure_init():
            return {"success": False, "error": "pyautogui not available"}

        action = event.get("action", "")
        try:
            handler = {
                "click": self._click,
                "double_click": self._double_click,
                "right_click": self._right_click,
                "middle_click": self._middle_click,
                "mouse_move": self._mouse_move,
                "drag_start": self._drag_start,
                "drag_move": self._drag_move,
                "drag_end": self._drag_end,
                "scroll": self._scroll,
                "zoom": self._zoom,
                "type_text": self._type_text,
                "key_combo": self._key_combo,
                # 向后兼容旧接口
                "tap": self._click,
                "double_tap": self._double_click,
                "long_press": self._right_click,
                "swipe": self._swipe_compat,
                "key_press": self._key_combo,
            }.get(action)

            if handler is None:
                return {"success": False, "error": f"Unknown action: {action}"}

            return await handler(event)
        except Exception as e:
            logger.warning("Remote input error: action=%s error=%s", action, e)
            return {"success": False, "error": str(e)}

    # ── 坐标映射 ──────────────────────────────────────────

    def _map_coords(self, nx: float, ny: float) -> tuple[int, int]:
        """归一化坐标 [0,1] → 屏幕像素坐标."""
        x = int(nx * self._screen_width)
        y = int(ny * self._screen_height)
        return (
            max(0, min(x, self._screen_width - 1)),
            max(0, min(y, self._screen_height - 1)),
        )

    # ── 点击操作 ──────────────────────────────────────────

    async def _click(self, event: dict[str, Any]) -> dict[str, Any]:
        x, y = self._map_coords(event.get("x", 0), event.get("y", 0))
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.click(x, y, _pause=False)
        )
        return {"success": True, "error": None}

    async def _double_click(self, event: dict[str, Any]) -> dict[str, Any]:
        x, y = self._map_coords(event.get("x", 0), event.get("y", 0))
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.doubleClick(x, y, _pause=False)
        )
        return {"success": True, "error": None}

    async def _right_click(self, event: dict[str, Any]) -> dict[str, Any]:
        x, y = self._map_coords(event.get("x", 0), event.get("y", 0))
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.rightClick(x, y, _pause=False)
        )
        return {"success": True, "error": None}

    async def _middle_click(self, event: dict[str, Any]) -> dict[str, Any]:
        x, y = self._map_coords(event.get("x", 0), event.get("y", 0))
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.middleClick(x, y, _pause=False)
        )
        return {"success": True, "error": None}

    # ── 鼠标移动 ──────────────────────────────────────────

    async def _mouse_move(self, event: dict[str, Any]) -> dict[str, Any]:
        """移动鼠标.

        支持两种模式：
        1. 绝对坐标：x, y（归一化 [0,1]）→ 直接触控模式
        2. 相对坐标：dx, dy（像素偏移）→ 触控板模式
        """
        if "dx" in event and "dy" in event:
            # 触控板模式：相对移动
            dx = int(event["dx"] * self._screen_width)
            dy = int(event["dy"] * self._screen_height)
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._pyautogui.moveRel(dx, dy, _pause=False, duration=0)
            )
        else:
            # 直接触控模式：绝对坐标
            x, y = self._map_coords(event.get("x", 0), event.get("y", 0))
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._pyautogui.moveTo(x, y, _pause=False, duration=0)
            )
        return {"success": True, "error": None}

    # ── 拖拽操作 ──────────────────────────────────────────

    async def _drag_start(self, event: dict[str, Any]) -> dict[str, Any]:
        """按下鼠标左键（开始拖拽）."""
        x, y = self._map_coords(event.get("x", 0), event.get("y", 0))
        self._dragging = True
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.moveTo(x, y, _pause=False, duration=0)
        )
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.mouseDown(x, y, button="left", _pause=False)
        )
        return {"success": True, "error": None}

    async def _drag_move(self, event: dict[str, Any]) -> dict[str, Any]:
        """拖拽中移动鼠标."""
        if not self._dragging:
            return {"success": False, "error": "Not in drag state"}
        x, y = self._map_coords(event.get("x", 0), event.get("y", 0))
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.moveTo(x, y, _pause=False, duration=0)
        )
        return {"success": True, "error": None}

    async def _drag_end(self, event: dict[str, Any]) -> dict[str, Any]:
        """释放鼠标左键（结束拖拽）."""
        x, y = self._map_coords(event.get("x", 0), event.get("y", 0))
        self._dragging = False
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.mouseUp(x, y, button="left", _pause=False)
        )
        return {"success": True, "error": None}

    # ── 滚动/缩放 ──────────────────────────────────────────

    async def _scroll(self, event: dict[str, Any]) -> dict[str, Any]:
        """鼠标滚轮.

        deltaY > 0 → 向下滚动, deltaY < 0 → 向上滚动
        """
        x, y = self._map_coords(event.get("x", 0.5), event.get("y", 0.5))
        delta = event.get("deltaY", 0)
        # pyautogui scroll 的 clicks 参数：正数=上滚，负数=下滚
        clicks = -int(delta / 120) if delta else 0
        if clicks == 0:
            return {"success": True, "error": None}
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.scroll(clicks, x, y, _pause=False)
        )
        return {"success": True, "error": None}

    async def _zoom(self, event: dict[str, Any]) -> dict[str, Any]:
        """Ctrl+滚轮（缩放）."""
        x, y = self._map_coords(event.get("x", 0.5), event.get("y", 0.5))
        delta = event.get("deltaY", 0)
        clicks = -int(delta / 120) if delta else 0
        if clicks == 0:
            return {"success": True, "error": None}
        # Ctrl + 滚轮
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.keyDown("ctrl")
        )
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.scroll(clicks, x, y, _pause=False)
        )
        await asyncio.get_event_loop().run_in_executor(None, lambda: self._pyautogui.keyUp("ctrl"))
        return {"success": True, "error": None}

    # ── 键盘操作 ──────────────────────────────────────────

    async def _type_text(self, event: dict[str, Any]) -> dict[str, Any]:
        """键盘输入文字."""
        text = event.get("text", "")
        if not text:
            return {"success": False, "error": "Empty text"}
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.typewrite(text, interval=0.01, _pause=False)
        )
        return {"success": True, "error": None}

    async def _key_combo(self, event: dict[str, Any]) -> dict[str, Any]:
        """组合键.

        支持预定义快捷键名（如 "alt+tab"）或自定义组合.
        """
        key = event.get("key", "") or event.get("text", "")
        if not key:
            return {"success": False, "error": "Empty key"}

        # 查预定义映射
        keys = self.KEY_COMBO_MAP.get(key.lower())

        if keys is None:
            # 自定义组合：按 "+" 拆分
            keys = [k.strip() for k in key.split("+")]

        if len(keys) == 1:
            # 单键
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._pyautogui.press(keys[0], _pause=False)
            )
        else:
            # 组合键：按下所有修饰键 → 按最后一个键 → 释放修饰键
            keys[:-1]
            keys[-1]
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._pyautogui.hotkey(*keys, _pause=False)
            )
        return {"success": True, "error": None}

    # ── 向后兼容 ──────────────────────────────────────────

    async def _swipe_compat(self, event: dict[str, Any]) -> dict[str, Any]:
        """旧接口兼容：swipe → drag."""
        x1, y1 = self._map_coords(event.get("x", 0), event.get("y", 0))
        x2, y2 = self._map_coords(event.get("x2", 0), event.get("y2", 0))
        duration = event.get("duration_ms", 300) / 1000.0
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.moveTo(x1, y1, _pause=False, duration=0)
        )
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._pyautogui.drag(x2 - x1, y2 - y1, duration=duration, _pause=False)
        )
        return {"success": True, "error": None}
