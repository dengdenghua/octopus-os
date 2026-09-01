"""
自适应批处理性能基准测试

比较固定批处理 vs 自适应批处理在不同吞吐量场景下的性能。
"""

import asyncio
import time

from runtime.sensing.gateway.adaptive_delta_buffer import AdaptiveFlushPolicy


async def benchmark_fixed_batching(chunks: list[str], threshold: int = 64, interval_ms: int = 32):
    """模拟固定批处理策略"""
    buffer = []
    buffer_chars = 0
    flush_count = 0
    start_time = time.time()

    for chunk in chunks:
        buffer.append(chunk)
        buffer_chars += len(chunk)

        if buffer_chars >= threshold:
            # 刷新
            buffer.clear()
            buffer_chars = 0
            flush_count += 1
            await asyncio.sleep(0.001)  # 模拟网络延迟

    # 最后刷新
    if buffer:
        flush_count += 1

    elapsed = time.time() - start_time
    return flush_count, elapsed


async def benchmark_adaptive_batching(chunks: list[str]):
    """测试自适应批处理策略（策略只做决策，内容由调用方缓冲）"""
    policy = AdaptiveFlushPolicy()
    buffer: list[str] = []
    flush_count = 0
    start_time = time.time()

    for chunk in chunks:
        buffer.append(chunk)
        policy.record(len(chunk))

        if policy.should_flush():
            # 刷新
            buffer.clear()
            policy.mark_flushed()
            flush_count += 1
            await asyncio.sleep(0.001)  # 模拟网络延迟

    # 最后刷新
    if buffer:
        flush_count += 1

    elapsed = time.time() - start_time
    return flush_count, elapsed


def generate_chunks(pattern: str, count: int) -> list[str]:
    """生成测试数据"""
    if pattern == "low_throughput":
        # 每个 chunk 1-2 字符，模拟慢速生成
        return ["a" for _ in range(count)]
    if pattern == "high_throughput":
        # 每个 chunk 50-100 字符，模拟快速生成
        return ["x" * 80 for _ in range(count)]
    if pattern == "mixed":
        # 混合模式：开始慢，中间快，结束慢
        chunks = []
        chunks.extend(["a" for _ in range(count // 3)])
        chunks.extend(["x" * 80 for _ in range(count // 3)])
        chunks.extend(["b" for _ in range(count // 3)])
        return chunks
    raise ValueError(f"Unknown pattern: {pattern}")


async def run_benchmark():
    """运行完整基准测试"""
    print("=" * 60)
    print("自适应批处理性能基准测试")
    print("=" * 60)

    scenarios = [
        ("低吞吐 (1-2 chars/chunk)", "low_throughput", 500),
        ("高吞吐 (80 chars/chunk)", "high_throughput", 500),
        ("混合吞吐", "mixed", 600),
    ]

    for name, pattern, count in scenarios:
        print(f"\n场景: {name}")
        print("-" * 60)

        chunks = generate_chunks(pattern, count)
        total_chars = sum(len(c) for c in chunks)
        print(f"数据: {count} chunks, {total_chars} chars 总计")

        # 固定批处理
        fixed_flushes, fixed_time = await benchmark_fixed_batching(chunks)
        print("固定批处理 (64 chars, 32ms):")
        print(f"  刷新次数: {fixed_flushes}")
        print(f"  耗时: {fixed_time * 1000:.2f}ms")
        print(f"  平均每次刷新: {total_chars / fixed_flushes:.1f} chars")

        # 自适应批处理
        adaptive_flushes, adaptive_time = await benchmark_adaptive_batching(chunks)
        print("自适应批处理:")
        print(f"  刷新次数: {adaptive_flushes}")
        print(f"  耗时: {adaptive_time * 1000:.2f}ms")
        print(f"  平均每次刷新: {total_chars / adaptive_flushes:.1f} chars")

        # 对比
        flush_reduction = (1 - adaptive_flushes / fixed_flushes) * 100
        time_reduction = (1 - adaptive_time / fixed_time) * 100
        print("优化效果:")
        print(f"  刷新次数减少: {flush_reduction:+.1f}%")
        print(f"  耗时减少: {time_reduction:+.1f}%")

    print("\n" + "=" * 60)
    print("测试完成！")


if __name__ == "__main__":
    asyncio.run(run_benchmark())

