/**
 * Echo OS 内建 Agent 工作台路由。
 *
 * Agent 与桌面共用同一套 React 路由和构建产物；桌面窗口拿到的
 * 始终是本地应用路由。
 */

declare global {
  interface Window {
    /** Echo Storage 服务地址，由 appliance 配置注入。 */
    __ECHO_STORAGE_URL__?: string;
  }
}

const DEFAULT_WORKSPACE_PATH = "/workspace/realtime/new";

function localRoute(route: string): string {
  const trimmed = route.trim();
  if (!trimmed) return DEFAULT_WORKSPACE_PATH;
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

/** Agent 应用始终解析为当前 Echo OS 前端中的本地路由。 */
export function resolveAgentAppUrl(route: string): string {
  return localRoute(route);
}

/** 当前内建工作台入口。 */
export function resolveAgentWorkspaceUrl(): string {
  return DEFAULT_WORKSPACE_PATH;
}

/** 配置端点提供存储服务地址。 */
export async function loadAgentWorkspaceConfig(): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    const response = await fetch("/api/appliance/config");
    if (!response.ok) return;
    const config = (await response.json()) as { storage_url?: string | null };
    if (config.storage_url) {
      window.__ECHO_STORAGE_URL__ = config.storage_url;
    }
  } catch {
    // 本地工作台不依赖这个端点，存储服务离线时由对应页面展示状态。
  }
}

export const AGENT_WORKSPACE_WINDOW_ID = "agent-workspace";
