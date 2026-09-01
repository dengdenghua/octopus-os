"""Tentacle 设备池 —— 多触手协调.

TentaclePool 是 Cerebrum 与多个物理设备交互的统一入口。

特性：
- 异步注册 / 注销
- 按 affinity 智能选择（LEAST_USED 策略）
- 离线自动剔除
- 锁支持（多 Arm 互斥）
- 订阅支持（Cerebrum 监听屏幕变化）

详见 ``docs/mobile/architecture.md`` 第 2.3 节。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from .base import Tentacle, TentacleType

logger = logging.getLogger(__name__)


# 设备锁的 owner 标识
DeviceLockOwner = str  # arm_id or task_id


class DeviceLock:
    """设备锁 —— 多 Arm 互斥访问同一台设备."""

    __slots__ = ("tentacle_id", "owner", "task_id", "acquired_at", "timeout_s")

    def __init__(
        self,
        tentacle_id: str,
        owner: str,
        task_id: str,
        timeout_s: int = 300,
    ) -> None:
        self.tentacle_id = tentacle_id
        self.owner = owner
        self.task_id = task_id
        self.acquired_at = asyncio.get_event_loop().time()
        self.timeout_s = timeout_s

    @property
    def is_expired(self) -> bool:
        return asyncio.get_event_loop().time() - self.acquired_at > self.timeout_s


class TentaclePool:
    """所有已连接触手的管理器.

    用法::

        pool = TentaclePool()
        await pool.register(mobile_device_1)
        await pool.register(mobile_device_2)

        # 选择最合适的手机
        device = pool.select_for_affinity(["android", "ecommerce"])
        if device:
            result = await device.execute(tool_call)

        # 订阅屏幕变化
        async def on_change(event):
            print("screen changed", event)
        await pool.subscribe_screen_changes(on_change)
    """

    def __init__(self) -> None:
        self._tentacles: dict[str, Tentacle] = {}
        self._affinity_index: dict[str, set[str]] = defaultdict(set)
        self._type_index: dict[TentacleType, set[str]] = defaultdict(set)
        self._locks: dict[str, DeviceLock] = {}
        self._subscribers: list[Callable[[dict], Awaitable[None]]] = []
        self._lock = asyncio.Lock()

    # ── 注册 / 注销 ─────────────────────────────────────────

    async def register(self, tentacle: Tentacle) -> None:
        """注册一个新触手."""
        async with self._lock:
            self._tentacles[tentacle.tentacle_id] = tentacle
            for cap in tentacle.capabilities:
                self._affinity_index[cap].add(tentacle.tentacle_id)
            self._type_index[tentacle.tentacle_type].add(tentacle.tentacle_id)
            # 同时把 tentacle_type.value 也加到 affinity 索引（便于按类型查找）
            self._affinity_index[tentacle.tentacle_type.value].add(tentacle.tentacle_id)
            # 把 platform 也加进去
            self._affinity_index[tentacle.platform].add(tentacle.tentacle_id)
        await self._emit(
            "tentacle.registered",
            {
                "tentacle_id": tentacle.tentacle_id,
                "tentacle_type": tentacle.tentacle_type.value,
                "platform": tentacle.platform,
                "capabilities": tentacle.capabilities,
            },
        )
        logger.info(
            "tentacle registered id=%s type=%s caps=%d",
            tentacle.tentacle_id,
            tentacle.tentacle_type.value,
            len(tentacle.capabilities),
        )

    async def unregister(self, tentacle_id: str) -> None:
        """注销一个触手（设备断开时）."""
        async with self._lock:
            t = self._tentacles.pop(tentacle_id, None)
            if t is None:
                return
            for cap in t.capabilities:
                self._affinity_index[cap].discard(tentacle_id)
            self._type_index[t.tentacle_type].discard(tentacle_id)
            self._affinity_index[t.tentacle_type.value].discard(tentacle_id)
            self._affinity_index[t.platform].discard(tentacle_id)
            self._locks.pop(tentacle_id, None)
        await self._emit("tentacle.unregistered", {"tentacle_id": tentacle_id})
        logger.info("tentacle unregistered id=%s", tentacle_id)

    # ── 查询 ────────────────────────────────────────────────

    def all(self) -> list[Tentacle]:
        return list(self._tentacles.values())

    def all_online(self) -> list[Tentacle]:
        return [t for t in self._tentacles.values() if t.is_online]

    def get(self, tentacle_id: str) -> Tentacle | None:
        return self._tentacles.get(tentacle_id)

    def select_for_affinity(
        self,
        affinity: list[str],
        *,
        prefer_typed: TentacleType | None = None,
    ) -> Tentacle | None:
        """按 affinity 智能选择最佳触手.

        策略：
        1. 候选 = 在线 ∩ (capabilities ∩ affinity ≠ ∅)
        2. 如果 prefer_typed 给出，优先选该类型
        3. 在候选中选"最久未用"的（负载均衡）
        """
        candidate_ids: set[str] = set()
        for aff in affinity:
            candidate_ids |= self._affinity_index.get(aff, set())
        candidates = [
            self._tentacles[tid] for tid in candidate_ids if self._tentacles[tid].is_online
        ]
        if prefer_typed is not None:
            typed = [c for c in candidates if c.tentacle_type == prefer_typed]
            if typed:
                candidates = typed
        if not candidates:
            return None
        return min(candidates, key=lambda t: (t.is_busy, t.last_used_at))

    def select_by_id(self, tentacle_id: str) -> Tentacle | None:
        t = self._tentacles.get(tentacle_id)
        return t if (t and t.is_online) else None

    # ── 设备锁（多 Arm 互斥） ────────────────────────────────

    async def acquire_lock(
        self,
        tentacle_id: str,
        owner: str,
        task_id: str,
        timeout_s: int = 300,
    ) -> bool:
        """尝试获取设备锁.

        Returns True 成功，False 失败（被其他 Arm 持有）.
        """
        async with self._lock:
            existing = self._locks.get(tentacle_id)
            if existing is not None and not existing.is_expired and existing.owner != owner:
                return False
            self._locks[tentacle_id] = DeviceLock(tentacle_id, owner, task_id, timeout_s)
            return True

    async def release_lock(self, tentacle_id: str, owner: str) -> bool:
        """释放设备锁（只有 owner 能释放）."""
        async with self._lock:
            lock = self._locks.get(tentacle_id)
            if lock is None or lock.owner != owner:
                return False
            del self._locks[tentacle_id]
            return True

    def lock_holder(self, tentacle_id: str) -> DeviceLock | None:
        return self._locks.get(tentacle_id)

    # ── 屏幕状态订阅 ────────────────────────────────────────

    def subscribe_screen_changes(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """订阅屏幕变化事件（异步回调）."""
        self._subscribers.append(callback)

    async def push_screen_change(self, event: dict) -> None:
        """设备推送屏幕变化时调用 (audit P-10: the unbounded dead queue was
        removed — delivery goes through the subscriber fan-out in _emit)."""
        await self._emit("device.screen_changed", event)

    # ── 内部 ────────────────────────────────────────────────

    async def _emit(self, event_type: str, data: dict) -> None:
        """广播事件到所有订阅者."""
        event = {"event": event_type, "ts": __import__("time").time(), **data}
        for sub in self._subscribers:
            try:
                await sub(event)
            except Exception as e:
                logger.warning("subscriber failed: %s", e)

    def stats(self) -> dict[str, Any]:
        """池的统计信息（用于调试和监控）."""
        online = self.all_online()
        busy = [t for t in self._tentacles.values() if t.is_busy]
        return {
            "total": len(self._tentacles),
            "online": len(online),
            "offline": len(self._tentacles) - len(online),
            "busy": len(busy),
            "by_type": {tt.value: len(tids) for tt, tids in self._type_index.items()},
            "locked": len(self._locks),
            "subscribers": len(self._subscribers),
        }


# ── 顶层工具函数 ──────────────────────────────────────────────


def select_for_affinity(
    pool: TentaclePool,
    affinity: list[str],
    *,
    prefer_typed: TentacleType | None = None,
) -> Tentacle | None:
    """便捷函数：选择最佳触手.

    等价于 ``pool.select_for_affinity(affinity, prefer_typed=...)``.
    """
    return pool.select_for_affinity(affinity, prefer_typed=prefer_typed)
