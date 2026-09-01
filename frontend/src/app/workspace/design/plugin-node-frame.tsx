"use client";

import { useEffect, useRef, type IframeHTMLAttributes } from "react";

import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

type PluginStateRequest = {
  type: "echo.plugin-state.request";
  requestId: string;
  action: "get" | "set" | "delete";
  key?: string;
  value?: unknown;
  expectedRevision?: number;
};

type PluginNodeFrameProps = Omit<
  IframeHTMLAttributes<HTMLIFrameElement>,
  "sandbox"
> & {
  projectId: string | null;
  pluginId: string;
  nodeId: string;
};

function requestError(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "插件状态请求失败";
  const candidate = payload as { detail?: unknown };
  if (typeof candidate.detail === "string") return candidate.detail;
  return "插件状态请求失败";
}

/** A same-origin plugin frame with a capability-scoped state bridge.
 * The child never receives auth credentials and cannot select another
 * project, plugin or node namespace. */
export function PluginNodeFrame({
  projectId,
  pluginId,
  nodeId,
  src,
  ...iframeProps
}: PluginNodeFrameProps) {
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const frameOrigin = new URL(
      typeof src === "string" ? src : window.location.href,
      window.location.href,
    ).origin;
    const onMessage = async (event: MessageEvent<PluginStateRequest>) => {
      if (
        event.origin !== frameOrigin ||
        event.source !== frameRef.current?.contentWindow ||
        event.data?.type !== "echo.plugin-state.request"
      )
        return;
      const request = event.data;
      const reply = (payload: Record<string, unknown>) => {
        frameRef.current?.contentWindow?.postMessage(
          {
            type: "echo.plugin-state.response",
            requestId: request.requestId,
            ...payload,
          },
          frameOrigin,
        );
      };
      if (!projectId) {
        reply({ ok: false, error: "请先将画布绑定到项目" });
        return;
      }
      try {
        const base = `${getBackendBaseURL()}/api/design/projects/${encodeURIComponent(projectId)}/plugin-nodes/${encodeURIComponent(nodeId)}/state`;
        let response: Response;
        if (request.action === "get") {
          response = await fetch(
            `${base}?plugin_id=${encodeURIComponent(pluginId)}`,
            { headers: authHeaders() },
          );
        } else if (request.action === "set" && request.key) {
          response = await fetch(`${base}/${encodeURIComponent(request.key)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({
              plugin_id: pluginId,
              expected_revision: request.expectedRevision ?? 0,
              value: request.value,
            }),
          });
        } else if (request.action === "delete" && request.key) {
          const params = new URLSearchParams({
            plugin_id: pluginId,
            expected_revision: String(request.expectedRevision ?? 0),
          });
          response = await fetch(
            `${base}/${encodeURIComponent(request.key)}?${params}`,
            { method: "DELETE", headers: authHeaders() },
          );
        } else {
          reply({ ok: false, error: "无效的插件状态操作" });
          return;
        }
        const payload = (await response.json()) as unknown;
        if (!response.ok) throw new Error(requestError(payload));
        reply({ ok: true, payload });
      } catch (error) {
        reply({
          ok: false,
          error: error instanceof Error ? error.message : "插件状态请求失败",
        });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [nodeId, pluginId, projectId, src]);

  return (
    <iframe
      ref={frameRef}
      src={src}
      sandbox="allow-scripts allow-same-origin allow-downloads"
      {...iframeProps}
    />
  );
}
