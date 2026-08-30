"""工具调用桥接 —— JSON-RPC 2.0 envelope 的运行时侧定义.

本文件定义 Runtime 侧发送 / 接收的 JSON-RPC 2.0 envelope。
Echo Mobile 端用 Kotlin 重写（``../echo-mobile/app/src/main/java/com/apk/claw/android/echo_mobile/Protocol.kt``）。

完整协议见 ``docs/mobile/protocol.md``。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorCode(int, Enum):
    """JSON-RPC 2.0 + Echo Mobile 扩展错误码."""

    # 标准 JSON-RPC 2.0
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Echo Mobile 扩展
    COORDINATE_OUT_OF_BOUNDS = -32001
    TOOL_TIMEOUT = -32002
    TOOL_NOT_FOUND = -32003
    APP_NOT_FOUND = -32004
    PERMISSION_DENIED = -32005
    DEVICE_LOCKED = -32010
    DEVICE_OFFLINE = -32011
    SKILL_INSTALL_FAILED = -32020
    CONFIG_CONFLICT = -32030


@dataclass(slots=True)
class Envelope:
    """JSON-RPC 2.0 envelope (Echo Mobile 扩展).

    完整字段：method / id / params / result / error 三选一.
    """

    method: str | None = None
    id: str | None = None
    params: dict[str, Any] | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    @staticmethod
    def request(
        method: str, params: dict[str, Any] | None = None, id: str | None = None
    ) -> Envelope:
        return Envelope(method=method, params=params, id=id or str(uuid.uuid4()))

    @staticmethod
    def reply_ok(call_id: str, result: Any) -> Envelope:
        return Envelope(id=call_id, result=result)

    @staticmethod
    def reply_err(call_id: str, code: int, message: str, data: Any = None) -> Envelope:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return Envelope(id=call_id, error=err)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.method is not None:
            out["method"] = self.method
            out["params"] = self.params or {}
        if self.id is not None:
            out["id"] = self.id
        if self.error is not None:
            out["error"] = self.error
        elif "method" not in out:
            out["result"] = self.result
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Envelope:
        return cls(
            method=d.get("method"),
            id=d.get("id"),
            params=d.get("params"),
            result=d.get("result"),
            error=d.get("error"),
        )

    def is_request(self) -> bool:
        return self.method is not None

    def is_reply(self) -> bool:
        return self.id is not None and (self.result is not None or self.error is not None)


# 便捷构造器


def hello(
    tentacle_id: str,
    protocol_version: str = "1.0",
    client_type: str = "android_tentacle",
    client_version: str = "0.1.0",
    device_meta: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
) -> Envelope:
    """device/hello —— 协议握手."""
    return Envelope.request(
        "device/hello",
        {
            "protocol_version": protocol_version,
            "client_type": client_type,
            "client_version": client_version,
            "tentacle_id": tentacle_id,
            "device_meta": device_meta or {},
            "capabilities": capabilities or [],
        },
    )


def heartbeat(tentacle_id: str, **extra: Any) -> Envelope:
    """device/heartbeat —— 心跳."""
    params = {"tentacle_id": tentacle_id, "ts": int(time.time() * 1000), **extra}
    return Envelope.request("device/heartbeat", params)


def tool_execute(
    tentacle_id: str,
    tool: str,
    args: dict[str, Any] | None = None,
    timeout_ms: int = 15_000,
    trace_id: str | None = None,
    call_id: str | None = None,
) -> Envelope:
    """tool/execute —— 工具执行请求."""
    return Envelope.request(
        "tool/execute",
        {
            "tentacle_id": tentacle_id,
            "tool": tool,
            "args": args or {},
            "timeout_ms": timeout_ms,
            "trace_id": trace_id,
        },
        id=call_id,
    )


def screen_changed(
    tentacle_id: str, current_app: str, screen_hash: str, tree_delta: dict | None = None
) -> Envelope:
    """device/screen_changed —— 屏幕状态变化."""
    return Envelope.request(
        "device/screen_changed",
        {
            "tentacle_id": tentacle_id,
            "ts": int(time.time() * 1000),
            "current_app": current_app,
            "screen_tree_hash": screen_hash,
            "tree_delta": tree_delta or {},
        },
    )
