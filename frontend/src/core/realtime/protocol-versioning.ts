/**
 * 流式事件协议版本管理
 *
 * 支持前后端版本不匹配时的兼容性：
 * - 语义化版本（major.minor.patch）
 * - 适配器模式（每个版本一个适配器）
 * - 向后兼容（旧客户端可读新协议）
 * - 能力协商（可选特性的声明）
 *
 * @example
 * // 服务端
 * const event: RealtimeEventV2 = {
 *   type: "tool_start",
 *   protocol_version: { major: 2, minor: 1, patch: 0 },
 *   tool_call_id: "call_123",
 *   input: { query: "test" },
 *   // V2 新字段
 *   supports_cancellation: true,
 * };
 *
 * // 客户端
 * const adapted = registry.adapt(event);
 * // 自动适配为内部格式
 */

import type { LiveToolEvent } from "@/components/workspace/live-tool-timeline";

// ============================================================================
// 协议版本定义
// ============================================================================

export interface ProtocolVersion {
  major: number; // 破坏性变更
  minor: number; // 向后兼容的新功能
  patch: number; // Bug 修复
}

export const PROTOCOL_VERSION_V1: ProtocolVersion = { major: 1, minor: 0, patch: 0 };
export const PROTOCOL_VERSION_V2: ProtocolVersion = { major: 2, minor: 0, patch: 0 };
export const CURRENT_PROTOCOL_VERSION = PROTOCOL_VERSION_V2;

/**
 * 比较协议版本
 */
export function compareVersions(a: ProtocolVersion, b: ProtocolVersion): number {
  if (a.major !== b.major) return a.major - b.major;
  if (a.minor !== b.minor) return a.minor - b.minor;
  return a.patch - b.patch;
}

/**
 * 检查版本兼容性
 */
export function isCompatible(
  clientVersion: ProtocolVersion,
  serverVersion: ProtocolVersion,
): boolean {
  // Major 版本必须匹配
  if (clientVersion.major !== serverVersion.major) return false;

  // 服务端 minor 版本可以更高（向后兼容）
  return clientVersion.minor <= serverVersion.minor;
}

// ============================================================================
// 原始事件类型（从服务端接收）
// ============================================================================

export interface BaseRealtimeEvent {
  type: string;
  protocol_version?: ProtocolVersion;
  timestamp?: number;

  // 扩展元数据（V2+）
  extended_metadata?: Record<string, unknown>;
}

export interface ToolStartEventRaw extends BaseRealtimeEvent {
  type: "tool_start";
  tool_call_id?: string;
  tool_name?: string;
  name?: string;
  input?: Record<string, unknown>;
  input_preview?: Record<string, unknown>;

  // V2 新字段
  supports_cancellation?: boolean;
  input_stream_id?: string;
  parent_tool_use_id?: string;
}

export interface ToolEndEventRaw extends BaseRealtimeEvent {
  type: "tool_end";
  tool_call_id?: string;
  tool_name?: string;
  name?: string;
  output?: unknown;
  output_preview?: unknown;
  status?: string | boolean;
  is_error?: boolean;
  duration_ms?: number;

  // V2 新字段
  performance_metrics?: {
    cpu_time_ms?: number;
    memory_peak_mb?: number;
  };
}

// ============================================================================
// 事件适配器接口
// ============================================================================

export interface EventAdapter {
  /**
   * 检查是否可处理该版本
   */
  canHandle(version: ProtocolVersion): boolean;

  /**
   * 将原始事件适配为内部格式
   */
  adapt(rawEvent: BaseRealtimeEvent): LiveToolEvent | null;

  /**
   * 协议版本
   */
  readonly version: ProtocolVersion;
}

// ============================================================================
// V1 适配器
// ============================================================================

export class EventAdapterV1 implements EventAdapter {
  readonly version = PROTOCOL_VERSION_V1;

  canHandle(version: ProtocolVersion): boolean {
    return version.major === 1;
  }

  adapt(rawEvent: BaseRealtimeEvent): LiveToolEvent | null {
    const type = rawEvent.type;

    if (type === "tool_start" || type === "sub_tool_start") {
      return this._adaptToolStart(rawEvent as ToolStartEventRaw);
    }

    if (type === "tool_end" || type === "sub_tool_end") {
      return this._adaptToolEnd(rawEvent as ToolEndEventRaw);
    }

    // 未知类型
    return null;
  }

  private _adaptToolStart(raw: ToolStartEventRaw): LiveToolEvent | null {
    const name = this._stringValue(raw.tool_name ?? raw.name);
    const id = this._stringValue(raw.tool_call_id);

    if (!name || !id) return null;

    return {
      id,
      name,
      status: "running",
      startedAt: raw.timestamp ?? Date.now(),
      finishedAt: undefined,
      durationMs: undefined,
      iteration: 0,
      input: this._recordValue(raw.input_preview ?? raw.input),
      output: undefined,
      parentToolUseId: this._stringValue(raw.parent_tool_use_id),
    };
  }

  private _adaptToolEnd(raw: ToolEndEventRaw): LiveToolEvent | null {
    const name = this._stringValue(raw.tool_name ?? raw.name);
    const id = this._stringValue(raw.tool_call_id);

    if (!name || !id) return null;

    const status = this._terminalStatus(raw.status ?? raw.is_error);

    return {
      id,
      name,
      status,
      startedAt: raw.timestamp ?? Date.now(),
      finishedAt: raw.timestamp ?? Date.now(),
      durationMs: this._numberValue(raw.duration_ms),
      iteration: 0,
      output: raw.output_preview ?? raw.output,
    };
  }

  private _stringValue(value: unknown): string | undefined {
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
  }

  private _numberValue(value: unknown): number | undefined {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() && !Number.isNaN(Number(value))) {
      return Number(value);
    }
    return undefined;
  }

  private _recordValue(value: unknown): Record<string, unknown> | undefined {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
    return undefined;
  }

  private _terminalStatus(value: unknown): LiveToolEvent["status"] {
    if (value === true) return "error";
    const normalized = typeof value === "string" ? value.toLowerCase() : "";
    if (["error", "failed", "failure", "rejected", "cancelled", "timeout"].includes(normalized)) {
      return "error";
    }
    return "done";
  }
}

// ============================================================================
// V2 适配器（增强版）
// ============================================================================

export class EventAdapterV2 implements EventAdapter {
  readonly version = PROTOCOL_VERSION_V2;

  canHandle(version: ProtocolVersion): boolean {
    return version.major === 2;
  }

  adapt(rawEvent: BaseRealtimeEvent): LiveToolEvent | null {
    // V2 完全兼容 V1，先尝试 V1 适配
    const v1Adapter = new EventAdapterV1();
    const adapted = v1Adapter.adapt(rawEvent);

    if (!adapted) return null;

    // 增强 V2 特性
    if (rawEvent.type === "tool_start") {
      const raw = rawEvent as ToolStartEventRaw;
      return {
        ...adapted,
        // V2 新字段映射到扩展元数据
        metadata: {
          supportsCancellation: raw.supports_cancellation,
          inputStreamId: raw.input_stream_id,
        },
      } as LiveToolEvent;
    }

    if (rawEvent.type === "tool_end") {
      const raw = rawEvent as ToolEndEventRaw;
      return {
        ...adapted,
        metadata: {
          performanceMetrics: raw.performance_metrics,
        },
      } as LiveToolEvent;
    }

    return adapted;
  }
}

// ============================================================================
// 适配器注册表
// ============================================================================

export class EventAdapterRegistry {
  private adapters: EventAdapter[] = [];

  constructor() {
    // 注册适配器，最新版本在前
    this.register(new EventAdapterV2());
    this.register(new EventAdapterV1());
  }

  /**
   * 注册适配器
   */
  register(adapter: EventAdapter): void {
    // 按版本降序插入
    const index = this.adapters.findIndex(
      (a) => compareVersions(a.version, adapter.version) < 0,
    );

    if (index === -1) {
      this.adapters.push(adapter);
    } else {
      this.adapters.splice(index, 0, adapter);
    }
  }

  /**
   * 适配原始事件
   */
  adapt(rawEvent: BaseRealtimeEvent): LiveToolEvent | null {
    const version = rawEvent.protocol_version ?? PROTOCOL_VERSION_V1;

    // 查找匹配的适配器
    for (const adapter of this.adapters) {
      if (adapter.canHandle(version)) {
        try {
          return adapter.adapt(rawEvent);
        } catch (error) {
          console.error(
            `[EventAdapter] Failed to adapt event with ${adapter.version.major}.${adapter.version.minor}`,
            error,
          );
          continue;
        }
      }
    }

    // 降级：尝试最老的适配器
    console.warn(
      `[EventAdapter] No adapter found for version ${version.major}.${version.minor}, falling back to V1`,
    );

    const fallback = this.adapters[this.adapters.length - 1];
    if (fallback) {
      try {
        return fallback.adapt(rawEvent);
      } catch (error) {
        console.error("[EventAdapter] Fallback adaptation failed", error);
      }
    }

    return null;
  }

  /**
   * 批量适配
   */
  adaptMany(rawEvents: BaseRealtimeEvent[]): LiveToolEvent[] {
    return rawEvents
      .map((raw) => this.adapt(raw))
      .filter((event): event is LiveToolEvent => event !== null);
  }
}

/**
 * 全局适配器注册表实例
 */
export const globalEventAdapterRegistry = new EventAdapterRegistry();
