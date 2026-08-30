"""自适应流式刷新策略（纯决策，不存内容）

调用方（``_ReactBridgeState``）持有唯一的 delta 内容缓冲，本模块只
根据实时吞吐量决定"现在是否应该刷新"以及"兜底 deadline 用多久"：

- 高吞吐 (>1000 chars/s): 增大批次 256 chars / 64ms
- 中吞吐 (100-1000): 标准批次 64 chars / 32ms
- 低吞吐 (<100): 减小批次 32 chars / 16ms

使用方式::

    policy = AdaptiveFlushPolicy()

    for chunk in stream:
        buffer.append(chunk)
        policy.record(len(chunk))
        if policy.should_flush():
            await emit("".join(buffer))
            buffer.clear()
            policy.mark_flushed()
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class BufferMetrics:
    """批处理策略性能指标（快照，getter 不改状态）"""

    chars_accumulated: int = 0
    flushes: int = 0
    elapsed_s: float = 0.0
    avg_throughput_chars_per_s: float = 0.0


class AdaptiveFlushPolicy:
    """自适应刷新策略

    根据实时吞吐量动态调整批次大小和刷新频率。内容归调用方所有；
    这里只维护"自上次刷新以来的字符数 / 时间窗 / 吞吐量历史"。
    """

    # 阈值配置
    _MIN_INTERVAL_S = 0.016  # 16ms (60fps)
    _STD_INTERVAL_S = 0.032  # 32ms (30fps, 原始默认值)
    _MAX_INTERVAL_S = 0.064  # 64ms (15fps)

    _MIN_CHARS = 32
    _STD_CHARS = 64
    _MAX_CHARS = 256

    # 吞吐量样本的最小窗口时长：低于此值视为测量噪声
    # (时钟精度 + 调度抖动)，不记录，避免瞬时 flush 污染移动平均。
    _MIN_SAMPLE_INTERVAL_S = 0.001

    # 吞吐量分级
    _LOW_THROUGHPUT = 100  # chars/s
    _HIGH_THROUGHPUT = 1000  # chars/s

    def __init__(self, max_history: int = 10):
        """初始化策略

        Args:
            max_history: 保留的吞吐量历史记录数（用于计算移动平均）
        """
        self._pending_chars = 0
        self._window_start: float | None = None
        self._last_flush_time: float | None = None
        self._throughput_history: deque[float] = deque(maxlen=max_history)
        self._total_chars = 0
        self._flushes = 0

    def record(self, chars: int) -> None:
        """记录一次追加的字符数（自上次刷新累计）"""
        if chars <= 0:
            return
        if self._window_start is None:
            self._window_start = time.monotonic()
        self._pending_chars += chars
        self._total_chars += chars

    def should_flush(self) -> bool:
        """判断是否应该刷新（基于当前吞吐量与累计窗口）"""
        if self._pending_chars <= 0:
            return False

        # 首次刷新：立即发送（time-to-first-token）
        if self._last_flush_time is None:
            return True

        elapsed = time.monotonic() - self._last_flush_time
        avg_throughput = self._estimate_throughput()

        # 根据吞吐量选择阈值
        if avg_throughput > self._HIGH_THROUGHPUT:
            # 高吞吐：增大批次，降低频率
            return self._pending_chars >= self._MAX_CHARS or elapsed >= self._MAX_INTERVAL_S
        if avg_throughput < self._LOW_THROUGHPUT:
            # 低吞吐：减小批次，提高响应性
            return self._pending_chars >= self._MIN_CHARS or elapsed >= self._MIN_INTERVAL_S
        # 中等吞吐：保持原始策略
        return self._pending_chars >= self._STD_CHARS or elapsed >= self._STD_INTERVAL_S

    def mark_flushed(self) -> None:
        """调用方完成一次刷新后调用：采样吞吐量并重置窗口"""
        now = time.monotonic()
        window_start = self._window_start
        if (
            self._last_flush_time is not None
            and window_start is not None
            and now - window_start >= self._MIN_SAMPLE_INTERVAL_S
        ):
            self._throughput_history.append(self._pending_chars / (now - window_start))
        self._pending_chars = 0
        self._window_start = None
        self._last_flush_time = now
        self._flushes += 1

    def flush_interval_s(self) -> float:
        """当前吞吐档位对应的兜底刷新间隔（deadline flush 用）"""
        avg_throughput = self._estimate_throughput()
        if avg_throughput > self._HIGH_THROUGHPUT:
            return self._MAX_INTERVAL_S
        if self._throughput_history and avg_throughput < self._LOW_THROUGHPUT:
            return self._MIN_INTERVAL_S
        return self._STD_INTERVAL_S

    def _estimate_throughput(self) -> float:
        """估算平均吞吐量 (chars/s)；无历史时返回 0（视为低吞吐）"""
        if not self._throughput_history:
            return 0.0
        return sum(self._throughput_history) / len(self._throughput_history)

    def get_metrics(self) -> BufferMetrics:
        """性能指标快照（幂等，不修改内部状态）"""
        return BufferMetrics(
            chars_accumulated=self._total_chars,
            flushes=self._flushes,
            elapsed_s=(
                time.monotonic() - self._last_flush_time
                if self._last_flush_time is not None
                else 0.0
            ),
            avg_throughput_chars_per_s=self._estimate_throughput(),
        )

    @property
    def pending_chars(self) -> int:
        """自上次刷新以来累计的字符数"""
        return self._pending_chars
