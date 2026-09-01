/**
 * Echo OS appliance layer:启动器的应用数据源。
 *
 * 对接后端 /api/appliance/apps(Docker 容器注册器,ECHO_APPLIANCE=1 时挂载)。
 * 后端返回发布端口以及经过协议/凭据检查的可选 Web UI label。前端仍不信任
 * label 的主机名，而是统一换成浏览器当前可见的 NAS 主机名，避免容器元数据
 * 把用户导航到外部站点。
 */

import { useCallback, useEffect, useState } from "react";

import { authHeader } from "@/appliance/auth";
import { approvalHeader } from "@/appliance/approval";

export type ApplianceApp = {
  id: string;
  name: string;
  description: string;
  icon: string;
  state: string; // running / exited / paused / …
  status: string; // e.g. "Up 3 hours"
  image: string;
  web_port: number | null;
  web_url: string | null;
  ports: number[];
};

export const MAX_DOCK_APPLIANCE_APPS = 6;

type AppsResponse = {
  available: boolean;
  apps: ApplianceApp[];
  error: string | null;
};

function isLocalAppHostname(hostname: string): boolean {
  const value = hostname
    .trim()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "")
    .toLowerCase();
  const current = window.location.hostname
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "")
    .toLowerCase();
  if (value === current) return true;
  if (
    value === "localhost" ||
    value.endsWith(".localhost") ||
    value.endsWith(".local") ||
    value.endsWith(".lan") ||
    value.endsWith(".home.arpa") ||
    !value.includes(".")
  )
    return true;
  const octets = value.split(".").map(Number);
  if (
    octets.length === 4 &&
    octets.every(
      (octet) => Number.isInteger(octet) && octet >= 0 && octet <= 255,
    )
  ) {
    const [first = -1, second = -1] = octets;
    return (
      first === 10 ||
      first === 127 ||
      (first === 169 && second === 254) ||
      (first === 172 && second >= 16 && second <= 31) ||
      (first === 192 && second === 168)
    );
  }
  return (
    value.includes(":") &&
    (value === "::1" ||
      value.startsWith("fc") ||
      value.startsWith("fd") ||
      value.startsWith("fe80:"))
  );
}

function labelOpenUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      parsed.username ||
      parsed.password ||
      !isLocalAppHostname(parsed.hostname)
    )
      return null;
    const currentHostname = window.location.hostname.replace(/^\[|\]$/g, "");
    const displayHost = currentHostname.includes(":")
      ? `[${currentHostname}]`
      : currentHostname;
    const port = parsed.port ? `:${parsed.port}` : "";
    return `${parsed.protocol}//${displayHost}${port}${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return null;
  }
}

export function appOpenUrl(app: ApplianceApp): string | null {
  const labelUrl = app.web_url ? labelOpenUrl(app.web_url) : null;
  if (labelUrl) return labelUrl;
  if (app.web_port == null) return null;
  const protocol = app.web_port === 443 ? "https:" : "http:";
  const hostname = window.location.hostname.replace(/^\[|\]$/g, "");
  const displayHost = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${protocol}//${displayHost}:${app.web_port}`;
}

export function applianceAppsForLibrary(apps: ApplianceApp[]): ApplianceApp[] {
  return apps.filter((app) => appOpenUrl(app) !== null);
}

export function applianceAppsForDock(
  apps: ApplianceApp[],
  maximum: number = MAX_DOCK_APPLIANCE_APPS,
): ApplianceApp[] {
  const limit = Number.isInteger(maximum) ? Math.max(0, maximum) : 0;
  return applianceAppsForLibrary(apps).slice(0, limit);
}

export async function fetchApplianceApps(): Promise<AppsResponse> {
  const response = await fetch("/api/appliance/apps", {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`apps fetch failed: ${response.status}`);
  return (await response.json()) as AppsResponse;
}

async function controlApplianceApp(
  id: string,
  action: "start" | "stop",
  approvalToken: string,
): Promise<void> {
  const response = await fetch(`/api/appliance/apps/${id}/${action}`, {
    method: "POST",
    headers: { ...authHeader(), ...approvalHeader(approvalToken) },
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    throw new Error(detail || `${action} failed: ${response.status}`);
  }
}

export function startApplianceApp(
  id: string,
  approvalToken: string,
): Promise<void> {
  return controlApplianceApp(id, "start", approvalToken);
}

export function stopApplianceApp(
  id: string,
  approvalToken: string,
): Promise<void> {
  return controlApplianceApp(id, "stop", approvalToken);
}

const POLL_MS = 30_000;

/** 拉取应用列表;30s 轮询保持状态新鲜。API 不可用(母体模式 /
 * 未开 appliance profile / 无 Docker)时静默返回空列表。 */
export function useApplianceApps(): {
  apps: ApplianceApp[];
  available: boolean;
  refresh: () => void;
} {
  const [apps, setApps] = useState<ApplianceApp[]>([]);
  const [available, setAvailable] = useState(false);

  const refresh = useCallback(() => {
    fetchApplianceApps()
      .then((body) => {
        setAvailable(body.available);
        setApps(body.available ? body.apps : []);
      })
      .catch(() => {
        setAvailable(false);
        setApps([]);
      });
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { apps, available, refresh };
}
