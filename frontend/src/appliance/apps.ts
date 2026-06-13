/**
 * Octopus OS appliance layer:启动器的应用数据源。
 *
 * 对接后端 /api/appliance/apps(Docker 容器注册器,OCTOPUS_APPLIANCE=1 时挂载)。
 * 后端只返回端口号——完整地址在这里用浏览器可见的主机名拼出来,
 * 因为只有浏览器知道用户是通过哪个 IP/域名访问这台 NAS 的。
 */

import { useCallback, useEffect, useState } from "react";

import { authHeader } from "@/appliance/auth";

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

type AppsResponse = {
  available: boolean;
  apps: ApplianceApp[];
  error: string | null;
};

export function appOpenUrl(app: ApplianceApp): string | null {
  if (app.web_url) return app.web_url;
  if (app.web_port == null) return null;
  const protocol = app.web_port === 443 ? "https:" : "http:";
  return `${protocol}//${window.location.hostname}:${app.web_port}`;
}

export async function fetchApplianceApps(): Promise<AppsResponse> {
  const response = await fetch("/api/appliance/apps", {
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`apps fetch failed: ${response.status}`);
  return (await response.json()) as AppsResponse;
}

export async function startApplianceApp(id: string): Promise<void> {
  const response = await fetch(`/api/appliance/apps/${id}/start`, {
    method: "POST",
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`start failed: ${response.status}`);
}

const POLL_MS = 30_000;

/** 拉取应用列表;30s 轮询保持状态新鲜。API 不可用(母体模式 /
 * 未开 appliance profile / 无 Docker)时静默返回空列表,
 * 桌面回退到占位图标,不打扰用户。 */
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
