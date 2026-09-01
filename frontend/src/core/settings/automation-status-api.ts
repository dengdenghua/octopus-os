import { authHeaders } from "@/core/auth/api";
import { openAuthenticatedWebSocket } from "@/core/auth/websocket";
import { getBackendBaseURL, getBackendWebSocketBaseURL } from "@/core/config";

export interface BrowserRelayStatus {
  connected: boolean;
  connection_state?: "online" | "reconnecting" | "offline";
  extension_version: string;
  push_connected: boolean;
  last_seen: number;
  manifest_exists: boolean;
  extension_path: string;
  active_tab?: {
    id?: number | string;
    url?: string;
    title?: string;
  } | null;
  recent_human_activity?: Array<{
    kind?: string;
    at?: number;
    url?: string;
    title?: string;
    tabId?: number | string;
    target?: Record<string, unknown>;
    data?: Record<string, unknown>;
  }>;
}

export interface DesktopAutomationPermissions {
  supported: boolean;
  platform: string;
  screenRecording: "granted" | "denied" | "restricted" | "unknown";
  accessibility: "granted" | "denied" | "unknown";
}

export async function getBrowserRelayStatus(): Promise<BrowserRelayStatus> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/browser/relay/status`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    throw new Error(`relay status unavailable: ${response.status}`);
  }
  return (await response.json()) as BrowserRelayStatus;
}

export function subscribeBrowserRelayStatus(
  onStatus: (status: BrowserRelayStatus) => void,
): () => void {
  let disposed = false;
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectDelay = 500;

  const connect = () => {
    if (disposed || typeof WebSocket === "undefined") return;
    const base = getBackendWebSocketBaseURL().replace(/\/+$/, "");
    const url = `${base}/api/browser/relay/status/ws`;
    const authorization = authHeaders().Authorization;
    const token = authorization?.replace(/^Bearer\s+/i, "") ?? "";
    socket = openAuthenticatedWebSocket(url, token);
    socket.onopen = () => {
      reconnectDelay = 500;
    };
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as {
          type?: string;
          status?: BrowserRelayStatus;
        };
        if (payload.type === "browser_relay_status" && payload.status) {
          onStatus(payload.status);
        }
      } catch {
        // Ignore malformed watcher messages; HTTP polling remains the fallback.
      }
    };
    socket.onclose = () => {
      socket = null;
      if (disposed) return;
      reconnectTimer = setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(10_000, reconnectDelay * 2);
    };
    socket.onerror = () => socket?.close();
  };

  connect();
  return () => {
    disposed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    socket?.close();
    socket = null;
  };
}

export async function getDesktopAutomationPermissions(): Promise<DesktopAutomationPermissions> {
  if (!window.echo?.desktop?.getAutomationPermissions) {
    return {
      supported: false,
      platform: "web",
      screenRecording: "unknown",
      accessibility: "unknown",
    };
  }
  return window.echo.desktop.getAutomationPermissions();
}

export async function openDesktopAutomationPermission(
  permission: "screen-recording" | "accessibility",
): Promise<boolean> {
  const result =
    await window.echo?.desktop?.openAutomationPermission?.(permission);
  return result?.ok === true;
}
