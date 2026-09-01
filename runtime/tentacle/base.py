"""Tentacle 基类与共享类型.

定义所有物理触手的核心接口（Protocol）+ 枚举 + 数据类。
本文件不包含任何具体实现 —— 那是 mobile.py / desktop.py 的事。

设计原则（见 docs/architecture/organs/tentacle.md）：

1. **设备无关**：不暴露任何设备特定 API
2. **离线友好**：断网时降级工作
3. **状态可观测**：所有状态通过 Nerves 总线广播
4. **能力可扩展**：新 Tentacle 类型只需实现 6 个方法
5. **失败可恢复**：网络抖动 / 设备锁死 / 进程崩溃均可重连
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class TentacleType(StrEnum):
    """触手类型枚举.

    工程名（按 ADR-001）—— 不在枚举名里用生物词。
    """

    MOBILE = "mobile"
    DESKTOP = "desktop"
    TV = "tv"
    IOT = "iot"
    AR_VR = "ar_vr"
    CAR = "car"
    UNKNOWN = "unknown"


class TentacleStatus(StrEnum):
    """触手在线状态机."""

    OFFLINE = "offline"  # 未连接
    CONNECTING = "connecting"  # 重连中
    ONLINE = "online"  # 在线，空闲
    BUSY = "busy"  # 在线，被占用
    ERROR = "error"  # 错误状态
    LOCKED = "locked"  # 被其他 Arm 持锁


@dataclass(slots=True)
class Heartbeat:
    """设备心跳（每 30s 一次）."""

    tentacle_id: str
    ts: int
    online: bool
    current_app: str | None = None
    screen_on: bool = True
    battery: int | None = None
    is_charging: bool = False
    last_screen_tree_hash: str | None = None
    running_tasks: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tentacle_id": self.tentacle_id,
            "ts": self.ts,
            "online": self.online,
            "current_app": self.current_app,
            "screen_on": self.screen_on,
            "battery": self.battery,
            "is_charging": self.is_charging,
            "last_screen_tree_hash": self.last_screen_tree_hash,
            "running_tasks": self.running_tasks,
            **self.extra,
        }


@dataclass(slots=True)
class ToolCall:
    """工具调用请求（Runtime → Tentacle）."""

    call_id: str  # 全局唯一 ID
    tentacle_id: str
    tool: str  # 如 "android.tap"
    args: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 15_000
    trace_id: str | None = None  # 关联到 trace_store 的 trace

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "tentacle_id": self.tentacle_id,
            "tool": self.tool,
            "args": self.args,
            "timeout_ms": self.timeout_ms,
            "trace_id": self.trace_id,
        }


@dataclass(slots=True)
class ToolResult:
    """工具调用结果（Tentacle → Runtime）."""

    call_id: str
    success: bool
    data: Any = None
    error_code: int | None = None
    error_message: str | None = None
    duration_ms: int = 0
    screenshot_after: str | None = None  # 引用，不内联
    screen_hash_after: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        call_id: str,
        data: Any = None,
        duration_ms: int = 0,
        **extra: Any,
    ) -> ToolResult:
        return cls(
            call_id=call_id,
            success=True,
            data=data,
            duration_ms=duration_ms,
            extra=extra,
        )

    @classmethod
    def fail(
        cls,
        call_id: str,
        code: int,
        message: str,
        duration_ms: int = 0,
        **extra: Any,
    ) -> ToolResult:
        return cls(
            call_id=call_id,
            success=False,
            error_code=code,
            error_message=message,
            duration_ms=duration_ms,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": self.success,
            "duration_ms": self.duration_ms,
        }
        if self.data is not None:
            result["data"] = self.data
        if not self.success:
            result["error"] = {
                "code": self.error_code,
                "message": self.error_message,
            }
        if self.screenshot_after:
            result["screenshot_after"] = self.screenshot_after
        if self.screen_hash_after:
            result["screen_hash_after"] = self.screen_hash_after
        if self.extra:
            result.update(self.extra)
        return result


@runtime_checkable
class Tentacle(Protocol):
    """物理触手接口（章鱼伸出去的"真实肢体"）.

    所有 Tentacle 实现必须满足这个协议。Tents（mobile/desktop/iot...）
    可以独立实现，也可以包装其他 Arm。

    生命周期方法都是 async，因为 Tentacle 通常跨网络（WebSocket）。
    """

    # ── 身份（必填） ──────────────────────────────────────────
    tentacle_id: str
    tentacle_type: TentacleType
    platform: str  # android / macos / windows / linux
    meta: dict[str, Any]

    # ── 能力（动态上报） ──────────────────────────────────────
    capabilities: list[str]

    # ── 生命周期 ──────────────────────────────────────────────
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    # ── 状态 ──────────────────────────────────────────────────
    async def heartbeat(self) -> Heartbeat: ...
    async def execute(self, call: ToolCall) -> ToolResult: ...

    # ── 状态机 ────────────────────────────────────────────────
    @property
    def status(self) -> TentacleStatus: ...
    @property
    def is_online(self) -> bool: ...
    @property
    def is_busy(self) -> bool: ...
    @property
    def last_used_at(self) -> float: ...


def now_ms() -> int:
    """当前时间戳（毫秒）."""
    return int(time.time() * 1000)
