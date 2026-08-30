"""自适应刷新策略（AdaptiveFlushPolicy）的单元测试

策略是纯决策：不存内容，只记录窗口字符数并按吞吐量分档决策。
"""

import time

from runtime.sensing.gateway.adaptive_delta_buffer import AdaptiveFlushPolicy


def test_first_flush_immediate():
    """首次刷新应立即触发 (time-to-first-token)"""
    policy = AdaptiveFlushPolicy()
    policy.record(5)
    assert policy.should_flush()


def test_no_pending_chars_no_flush():
    """没有累计字符时不应刷新"""
    policy = AdaptiveFlushPolicy()
    assert not policy.should_flush()


def test_low_throughput_small_batch():
    """低吞吐量应使用小批次策略 (32 chars)"""
    policy = AdaptiveFlushPolicy()

    # 建立低吞吐历史 (~67 chars/s)
    for _ in range(5):
        policy.record(10)
        policy._window_start = time.monotonic() - 0.15
        policy._last_flush_time = time.monotonic() - 0.16
        policy.mark_flushed()

    policy.record(20)
    policy._last_flush_time = time.monotonic() - 0.001  # 1ms
    assert not policy.should_flush()  # 未达到 32 chars

    policy.record(15)  # 总共 35 chars
    assert policy.should_flush()  # 超过 32 chars


def test_high_throughput_large_batch():
    """高吞吐量应使用大批次策略 (256 chars)"""
    policy = AdaptiveFlushPolicy()

    # 建立高吞吐历史 (2000 chars/s)
    for _ in range(5):
        policy.record(200)
        policy._window_start = time.monotonic() - 0.1
        policy._last_flush_time = time.monotonic() - 0.11
        policy.mark_flushed()

    policy.record(100)
    policy._last_flush_time = time.monotonic() - 0.01  # 10ms
    assert not policy.should_flush()  # 未达到 256 chars 也未达到 64ms

    policy.record(160)  # 总共 260 chars
    assert policy.should_flush()  # 超过 256 chars


def test_time_based_flush():
    """超时应触发刷新"""
    policy = AdaptiveFlushPolicy()
    policy.record(1)
    policy.mark_flushed()

    policy.record(4)
    policy._last_flush_time = time.monotonic() - 0.100  # 100ms 前
    assert policy.should_flush()  # 超过最大间隔 64ms


def test_flush_interval_follows_throughput_tier():
    """兜底刷新间隔应跟随吞吐档位"""
    policy = AdaptiveFlushPolicy()
    # 无历史 → 标准档 32ms
    assert policy.flush_interval_s() == 0.032

    # 高吞吐历史 → 64ms
    for _ in range(5):
        policy.record(200)
        policy._window_start = time.monotonic() - 0.1
        policy._last_flush_time = time.monotonic() - 0.11
        policy.mark_flushed()
    assert policy.flush_interval_s() == 0.064

    # 低吞吐历史 → 16ms
    policy_low = AdaptiveFlushPolicy()
    for _ in range(5):
        policy_low.record(10)
        policy_low._window_start = time.monotonic() - 0.15
        policy_low._last_flush_time = time.monotonic() - 0.16
        policy_low.mark_flushed()
    assert policy_low.flush_interval_s() == 0.016


def test_mark_flushed_resets_window_keeps_history():
    """mark_flushed 应重置窗口但保留吞吐量历史"""
    policy = AdaptiveFlushPolicy()
    policy.record(100)
    policy._window_start = time.monotonic() - 0.05
    policy._last_flush_time = time.monotonic() - 0.06
    policy.mark_flushed()

    assert policy.pending_chars == 0
    assert not policy.should_flush()
    assert len(policy._throughput_history) > 0  # 历史应保留


def test_metrics_snapshot_is_idempotent():
    """get_metrics 是纯快照，连续调用结果一致且不改状态"""
    policy = AdaptiveFlushPolicy()
    policy.record(50)
    policy._window_start = time.monotonic() - 0.05
    policy._last_flush_time = time.monotonic() - 0.06
    policy.mark_flushed()

    policy.record(100)
    policy._window_start = time.monotonic() - 0.05
    policy.mark_flushed()

    first = policy.get_metrics()
    second = policy.get_metrics()

    assert first.chars_accumulated == 150
    assert first.flushes == 2
    assert first.avg_throughput_chars_per_s > 0
    # 幂等：两次快照的计数字段完全一致
    assert second.chars_accumulated == first.chars_accumulated
    assert second.flushes == first.flushes
    # 不影响后续决策
    assert policy.pending_chars == 0

