/**
 * Octopus OS · P2 前端去 fork 接缝:把 agent 工作台当一个桌面应用,开在窗口里。
 *
 * 现状:os 同源自带 agent 前端 → 默认指向同源工作台路由,窗口里直接加载。
 * 去 fork 方向:后端(或部署)注入 window.__OCTOPUS_AGENT_WORKSPACE_URL__
 * 指向独立运行的 agent 服务的工作台 UI;os 便不再 fork/打包 agent 前端,
 * 只在桌面窗口里把它当应用加载(消费而非 fork,顺手 dogfood 窗口系统)。
 *
 * 这是 P2 的接缝:换 URL 即从"自带"切到"外部消费",前端组件无须改动。
 */

declare global {
  interface Window {
    /** 部署/后端可注入,指向外部 agent 服务的工作台 UI(去 fork 时用)。 */
    __OCTOPUS_AGENT_WORKSPACE_URL__?: string;
  }
}

/** os 自带 agent 前端时的同源工作台入口(对话/编程/项目的实时工作台)。 */
const DEFAULT_WORKSPACE_PATH = "/workspace/realtime/new";

/** 解析 agent 工作台窗口要加载的 URL:优先外部注入,回退同源路由。 */
export function resolveAgentWorkspaceUrl(): string {
  if (typeof window !== "undefined" && window.__OCTOPUS_AGENT_WORKSPACE_URL__) {
    return window.__OCTOPUS_AGENT_WORKSPACE_URL__;
  }
  return DEFAULT_WORKSPACE_PATH;
}

/**
 * 向后端问一次"agent 工作台 UI 在哪"(P2 同机 webui 投喂)。后端 serve 了独立
 * agent webui 时回 /agent-ui/...,据此设全局 → 窗口加载外部 webui 而非同源工作台。
 * 后端未投喂(agent_workspace_url=null)或接口不可用 → 保持同源回退,不打扰。
 * 在桌面挂载时调用一次即可;失败静默。
 */
export async function loadAgentWorkspaceConfig(): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    const res = await fetch("/api/appliance/config");
    if (!res.ok) return;
    const cfg = (await res.json()) as { agent_workspace_url?: string | null };
    if (cfg.agent_workspace_url) {
      window.__OCTOPUS_AGENT_WORKSPACE_URL__ = cfg.agent_workspace_url;
    }
  } catch {
    // 接口不可用(母体/未开 appliance)→ 同源回退。
  }
}

/** 工作台桌面窗口的稳定 id(同一窗口不重复开)。 */
export const AGENT_WORKSPACE_WINDOW_ID = "agent-workspace";

/** Electron 寄生模式无窗口系统时的整页回退路由。 */
export const AGENT_WORKSPACE_FALLBACK_ROUTE = DEFAULT_WORKSPACE_PATH;
