"""Echo Mobile 端版本兼容矩阵.

确保 Runtime 知道 Echo Mobile 客户端的最低支持版本。
升级 Echo Mobile 时同步更新此文件。

详见 ``docs/mobile/protocol.md`` 第 8 节（版本演进）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class EchoMobileVersion:
    """Echo Mobile 版本声明."""

    min_supported: str  # echo-agent 最低支持的 Echo Mobile 版本
    recommended: str  # 推荐的 Echo Mobile 版本
    protocol_version: str  # 协议版本
    notes: str = ""


# 当前的兼容矩阵
ECHO_MOBILE_VERSION = EchoMobileVersion(
    min_supported="0.0.1",
    recommended="0.1.0",
    protocol_version="1.0",
    notes=(
        "Phase 0 概念验证：Echo Mobile 0.0.1（github.com/echo-agent/echo-mobile）"
        "可作为基础；Echo Mobile 在此基础上加 RPC 客户端。"
        "Echo Mobile 1.0 计划 2026-Q3 发布。"
    ),
)


def is_compatible(client_version: str) -> bool:
    """检查 Echo Mobile 客户端版本是否兼容."""
    # 简单字符串比较；生产环境建议用 packaging.version
    return _parse_version(client_version) >= _parse_version(ECHO_MOBILE_VERSION.min_supported)


def _parse_version(version: str) -> tuple[int, ...]:
    """解析 semver 字符串."""
    try:
        return tuple(int(p) for p in version.split("."))
    except (ValueError, AttributeError):
        return (0,)
