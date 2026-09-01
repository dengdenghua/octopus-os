/**
 * EventBus - 类型安全的事件总线系统
 *
 * 替代 window.dispatchEvent / CustomEvent 反模式
 * 提供统一的订阅/发布机制，支持 React hooks 集成
 */

import { swallow } from "@/core/utils/log";
import {
  DEFAULT_PRIMARY_AGENT_ID,
  isPrimaryPersonaAgentId,
} from "@/core/agents/persona-policy";
import { useEffect, useCallback, useRef } from "react";

// 事件类型定义
export interface EventMap {
  // Agent 相关
  "agent:changed": { name: string; source?: "user" | "thread" | "system" };

  // 设置相关
  "settings:changed": void;

  // UI 相关
  "ui:toggle-sidebar": void;
  "ui:command-palette": void;
  "ui:file-search": void;
  "ui:go-to-line": void;
  "ui:toggle-panel": void;
  "ui:open-settings": { tab?: string };

  // 项目相关
  "projects:changed": void;
  "projects:sort": void;
  "project:new": void;

  // 聊天相关
  "chats:sort": void;
  "thread:run-status": {
    href?: string;
    state: "running" | "waiting" | "pending" | "error" | "done" | null;
    threadId: string;
  };
  // A new realtime turn receives its server thread id before the workspace
  // page can safely remount onto that route. The sidebar uses this transient
  // route while the page keeps its live socket mounted.
  "thread:route-sync": { href: string; threadId: string };

  // 团队相关
  "team:select": { id: string; name: string };
  "team:updated": { id: string; name: string };
  "teams:refresh": void;
  "teams:changed": void;
  "team:create": void;
  "team:removed": { teamId: string };
  "team:thread-update": { threadId: string; teamId: string };
  "team:room-updated": { roomId: string };

  // 任务相关
  "task:new":
    | {
        agentId?: string;
        workspacePath?: string;
      }
    | undefined;
  "task:changed": { type: string; task_id?: string; threadId?: string };

  // ReAct 相关
  "react:step": {
    taskId?: string;
    threadId?: string;
    currentPhase?: string | null;
    workingSet?: Array<{
      path: string;
      last_read_at: number;
      last_modified_at: number;
      tokens_estimated: number;
      relevance: string;
    }> | null;
    progressSummary?: string | null;
    feedbackSummary?: string | null;
    thinkingPlan?: unknown;
  };
  "thinking:signal": {
    threadId?: string;
    type: string;
    iteration?: number | null;
    active?: boolean;
  };

  // 工作区相关
  "workspace:changed": void;

  // 工具相关
  // 编辑器相关
  "editor:go-to-line": { line: number; column?: number };
  "editor:auto-fix": { text: string; threadId?: string | null };

  // 消息相关
  "message:regenerate": { messageId: string };
  "message:edit": { messageId: string; newContent: string };
  "message:deep-research": { messageId: string };
}

export type EventName = keyof EventMap;
export type EventPayload<T extends EventName> = EventMap[T];

// 监听器类型
type Listener<T extends EventName> = (payload: EventPayload<T>) => void;

// 事件总线类
class EventBus {
  private listeners: Map<EventName, Set<Listener<EventName>>> = new Map();

  // 订阅事件
  on<T extends EventName>(event: T, listener: Listener<T>): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(listener as Listener<EventName>);

    // 返回取消订阅函数
    return () => {
      this.listeners.get(event)?.delete(listener as Listener<EventName>);
    };
  }

  // 订阅事件（只触发一次）
  once<T extends EventName>(event: T, listener: Listener<T>): () => void {
    const unsubscribe = this.on(event, ((payload: EventPayload<T>) => {
      listener(payload);
      unsubscribe();
    }) as Listener<T>);
    return unsubscribe;
  }

  // 发布事件
  emit<T extends EventName>(event: T, payload?: EventPayload<T>): void {
    const eventListeners = this.listeners.get(event);
    if (!eventListeners) return;

    // 使用 Array.from 避免在迭代过程中修改集合
    Array.from(eventListeners).forEach((listener) => {
      try {
        (listener as Listener<T>)(payload as EventPayload<T>);
      } catch (error) {
        console.error(`EventBus: Error in listener for ${event}:`, error);
      }
    });
  }

  // 移除所有监听器（用于测试或重置）
  clear(): void {
    this.listeners.clear();
  }

  // 获取当前监听数量（用于调试）
  listenerCount(event?: EventName): number {
    if (event) {
      return this.listeners.get(event)?.size ?? 0;
    }
    let total = 0;
    this.listeners.forEach((set) => {
      total += set.size;
    });
    return total;
  }
}

// 单例实例
export const eventBus = new EventBus();

// React Hook: 订阅事件
export function useEvent<T extends EventName>(
  event: T,
  listener: Listener<T>,
  deps: React.DependencyList = [],
): void {
  const listenerRef = useRef(listener);
  listenerRef.current = listener;

  useEffect(() => {
    return eventBus.on(event, (payload) => listenerRef.current(payload));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event, ...deps]);
}

// React Hook: 订阅事件（带记忆化回调）
export function useEventCallback<T extends EventName>(
  event: T,
  listener: Listener<T>,
): void {
  const callback = useCallback(listener, [listener]);
  useEffect(() => {
    return eventBus.on(event, callback);
  }, [event, callback]);
}

// 便捷函数：触发 agent 变更事件
export function emitAgentChanged(
  name: string,
  source: "user" | "thread" | "system" = "user",
): void {
  eventBus.emit("agent:changed", { name, source });
  // Only fixed White Ghost identities own personal conversation lanes.
  // Historical expert-owned threads may still announce their owner, but that
  // must not replace the persisted lead for the next task.
  try {
    if (isPrimaryPersonaAgentId(name)) {
      window.localStorage.setItem("echo.active-agent", name);
    } else if (source !== "thread") {
      window.localStorage.setItem(
        "echo.active-agent",
        DEFAULT_PRIMARY_AGENT_ID,
      );
    }
  } catch (e) {
    swallow(e, "storage");
  }
}

// 便捷函数：触发设置变更事件
export function emitSettingsChanged(): void {
  eventBus.emit("settings:changed");
}

// 便捷函数：触发项目变更事件
export function emitProjectsChanged(): void {
  eventBus.emit("projects:changed");
}

// 便捷函数：触发团队选择事件
export function emitTeamSelect(team: { id: string; name: string }): void {
  eventBus.emit("team:select", team);
}

// 便捷函数：触发打开设置事件
export function emitOpenSettings(tab?: string): void {
  eventBus.emit("ui:open-settings", { tab });
}

// 便捷函数：触发侧边栏切换
export function emitToggleSidebar(): void {
  eventBus.emit("ui:toggle-sidebar");
}

// 便捷函数：触发命令面板
export function emitCommandPalette(): void {
  eventBus.emit("ui:command-palette");
}

// 便捷函数：触发文件搜索
export function emitFileSearch(): void {
  eventBus.emit("ui:file-search");
}

// 便捷函数：触发跳转到行
export function emitGoToLine(line: number, column?: number): void {
  eventBus.emit("editor:go-to-line", { line, column });
}

// 便捷函数：触发面板切换
export function emitTogglePanel(): void {
  eventBus.emit("ui:toggle-panel");
}
