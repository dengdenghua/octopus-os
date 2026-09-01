/**
 * 性能追踪工具
 *
 * 用于测量和记录前端关键操作的性能指标。
 * 支持标记-测量模式和火焰图生成。
 *
 * 使用示例:
 * ```typescript
 * const tracker = new PerformanceTracker();
 *
 * tracker.mark('snapshot:start');
 * // ... 执行操作
 * tracker.mark('snapshot:normalize');
 * tracker.measure('normalize_events', 'snapshot:normalize', { event_count: 100 });
 *
 * tracker.measure('total_time', 'snapshot:start');
 * console.log(tracker.getReport());
 * ```
 *
 * 优化目标:
 * - 识别性能瓶颈 (>100ms 的操作自动上报)
 * - 量化优化效果
 * - 生产环境性能基线
 */


declare global {
  interface Window {
    __reportMetric?: (name: string, payload: Record<string, unknown>) => void;
  }
}

export interface PerformanceMeasure {
  name: string;
  duration: number;
  startTime: number;
  metadata?: Record<string, unknown>;
}

export interface FlameGraphNode {
  name: string;
  value: number;
  children: FlameGraphNode[];
}

export class PerformanceTracker {
  private marks = new Map<string, number>();
  private measures: PerformanceMeasure[] = [];
  private enabled: boolean;

  constructor(enabled = true) {
    this.enabled = enabled;
  }

  /**
   * 创建性能标记
   */
  mark(name: string): void {
    if (!this.enabled) return;
    this.marks.set(name, performance.now());
  }

  /**
   * 测量两个标记之间的时间
   */
  measure(
    name: string,
    startMark: string,
    metadata?: Record<string, unknown>,
  ): number | null {
    if (!this.enabled) return null;

    const startTime = this.marks.get(startMark);
    if (startTime === undefined) {
      console.warn(`[PerformanceTracker] Start mark "${startMark}" not found`);
      return null;
    }

    const duration = performance.now() - startTime;
    this.measures.push({ name, duration, startTime, metadata });

    // 自动上报慢操作 (>100ms)
    if (duration > 100) {
      this.reportSlowOperation(name, duration, metadata);
    }

    return duration;
  }

  /**
   * 测量函数执行时间
   */
  async measureAsync<T>(
    name: string,
    fn: () => Promise<T>,
    metadata?: Record<string, unknown>,
  ): Promise<T> {
    const markName = `async:${name}:${Date.now()}`;
    this.mark(markName);
    try {
      return await fn();
    } finally {
      this.measure(name, markName, metadata);
    }
  }

  /**
   * 测量同步函数执行时间
   */
  measureSync<T>(
    name: string,
    fn: () => T,
    metadata?: Record<string, unknown>,
  ): T {
    const markName = `sync:${name}:${Date.now()}`;
    this.mark(markName);
    try {
      return fn();
    } finally {
      this.measure(name, markName, metadata);
    }
  }

  /**
   * 获取性能报告
   */
  getReport(): {
    measures: PerformanceMeasure[];
    summary: {
      total: number;
      slowest: PerformanceMeasure | null;
      fastest: PerformanceMeasure | null;
      average: number;
    };
  } {
    const total = this.measures.length;
    const slowest =
      total > 0
        ? this.measures.reduce((prev, curr) =>
            prev.duration > curr.duration ? prev : curr,
          )
        : null;
    const fastest =
      total > 0
        ? this.measures.reduce((prev, curr) =>
            prev.duration < curr.duration ? prev : curr,
          )
        : null;
    const average =
      total > 0
        ? this.measures.reduce((sum, m) => sum + m.duration, 0) / total
        : 0;

    return {
      measures: [...this.measures],
      summary: { total, slowest, fastest, average },
    };
  }

  /**
   * 生成火焰图数据
   */
  getFlameGraph(): FlameGraphNode {
    const root: FlameGraphNode = { name: "root", value: 0, children: [] };

    // 按开始时间排序
    const sorted = [...this.measures].sort((a, b) => a.startTime - b.startTime);

    // 简单的层级结构：按时间重叠关系构建
    const stack: FlameGraphNode[] = [root];

    for (const measure of sorted) {
      const node: FlameGraphNode = {
        name: measure.name,
        value: measure.duration,
        children: [],
      };

      // 找到合适的父节点（时间范围包含当前节点）
      let parent = stack[stack.length - 1];
      while (stack.length > 1) {
        const candidate = stack[stack.length - 1];
        if (!candidate) break;
        const candidateMeasure = this.measures.find(
          (m) => m.name === candidate.name,
        );
        if (
          candidateMeasure &&
          measure.startTime >= candidateMeasure.startTime &&
          measure.startTime + measure.duration <=
            candidateMeasure.startTime + candidateMeasure.duration
        ) {
          parent = candidate;
          break;
        }
        stack.pop();
      }

      if (parent) {
        parent.children.push(node);
      }
      stack.push(node);
    }

    return root;
  }

  /**
   * 清空所有记录
   */
  clear(): void {
    this.marks.clear();
    this.measures = [];
  }

  /**
   * 导出为 JSON
   */
  export(): string {
    return JSON.stringify(
      {
        marks: Array.from(this.marks.entries()),
        measures: this.measures,
        timestamp: Date.now(),
      },
      null,
      2,
    );
  }

  /**
   * 上报慢操作到监控系统
   */
  private reportSlowOperation(
    name: string,
    duration: number,
    metadata?: Record<string, unknown>,
  ): void {
    // 仅在开发环境打印警告
    if (import.meta.env.DEV) {
      console.warn(
        `[PerformanceTracker] Slow operation detected: ${name} took ${duration.toFixed(2)}ms`,
        metadata,
      );
    }

    // 生产环境：上报到监控系统（例如 Sentry、DataDog）
    if (typeof window !== "undefined" && window.__reportMetric) {
      window.__reportMetric("slow_operation", {
        operation: name,
        duration_ms: duration,
        ...metadata,
      });
    }
  }
}

/**
 * 全局单例实例
 */
export const globalPerformanceTracker = new PerformanceTracker(
  import.meta.env.DEV ||
  (typeof window !== "undefined" &&
   window.localStorage.getItem("echo:perf:tracking") === "1"),
);

/**
 * React Hook: 测量组件渲染性能
 */
export function usePerformanceTracker(componentName: string) {
  const tracker = globalPerformanceTracker;

  return {
    trackRender: () => {
      const markName = `render:${componentName}:${Date.now()}`;
      tracker.mark(markName);
      return () => {
        tracker.measure(`render:${componentName}`, markName);
      };
    },
    trackEffect: (effectName: string) => {
      const markName = `effect:${componentName}:${effectName}:${Date.now()}`;
      tracker.mark(markName);
      return () => {
        tracker.measure(`effect:${componentName}:${effectName}`, markName);
      };
    },
  };
}
