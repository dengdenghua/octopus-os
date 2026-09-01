import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2Icon,
  ExternalLinkIcon,
  Loader2Icon,
  MonitorUpIcon,
} from "lucide-react";

import { fetchHubCatalog, type HubApp } from "@/appliance/hub";
import {
  APP_PRESENTATION_DESCRIPTIONS,
  APP_PRESENTATION_LABELS,
  requestOpenEchoHub,
} from "@/core/apps/app-presentation";

function appState(app: HubApp): string {
  if (app.installation.installed) {
    return app.updateAvailable ? "可更新" : "已安装";
  }
  return app.installable ? "可安装" : "检查安装条件";
}

export function StandaloneAppDirectory({
  searchQuery = "",
}: {
  searchQuery?: string;
}) {
  const [apps, setApps] = useState<HubApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [available, setAvailable] = useState(false);

  useEffect(() => {
    let active = true;
    fetchHubCatalog()
      .then((catalog) => {
        if (!active) return;
        setApps(catalog.apps);
        setAvailable(catalog.runtime.available);
      })
      .catch(() => {
        if (active) setApps([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const visibleApps = useMemo(() => {
    const needle = searchQuery.trim().toLocaleLowerCase();
    if (!needle) return apps;
    return apps.filter((app) =>
      `${app.nameZh} ${app.name} ${app.summary}`
        .toLocaleLowerCase()
        .includes(needle),
    );
  }, [apps, searchQuery]);

  if (!loading && apps.length === 0) return null;

  return (
    <section aria-labelledby="standalone-app-directory-title" className="mb-6">
      <div className="mb-3 flex items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h3
              id="standalone-app-directory-title"
              className="text-sm font-semibold"
            >
              {APP_PRESENTATION_LABELS.standalone}应用
            </h3>
            <span className="rounded-full bg-sky-50 px-2 py-0.5 text-micro font-medium text-sky-700">
              {apps.length}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {APP_PRESENTATION_DESCRIPTIONS.standalone} 安装状态与 Echo Hub
            完全一致。
          </p>
        </div>
        {!loading && !available ? (
          <span className="shrink-0 text-micro text-amber-600">
            当前预览未连接设备服务
          </span>
        ) : null}
      </div>

      {loading ? (
        <div className="flex h-20 items-center justify-center text-xs text-muted-foreground">
          <Loader2Icon className="mr-2 size-4 animate-spin" />
          正在读取应用目录…
        </div>
      ) : (
        <div className="grid gap-x-8 sm:grid-cols-2">
          {visibleApps.map((app) => (
            <div
              key={app.id}
              className="group flex min-h-16 items-center gap-3 border-b border-border-subtle px-2 py-2 transition-colors hover:bg-muted/25"
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100">
                <MonitorUpIcon className="size-[18px] stroke-[1.8]" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-foreground">
                    {app.nameZh}
                  </span>
                  <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-micro text-muted-foreground">
                    {appState(app)}
                  </span>
                </span>
                <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                  {app.summary}
                </span>
              </span>
              <button
                type="button"
                onClick={() => requestOpenEchoHub(app.id)}
                aria-label={`在 Echo Hub 中管理${app.nameZh}`}
                className="flex h-7 shrink-0 items-center gap-1 px-2 text-micro font-medium text-primary transition-colors hover:text-primary/75"
              >
                {app.installation.installed ? (
                  <CheckCircle2Icon className="size-3.5" />
                ) : (
                  <ExternalLinkIcon className="size-3.5" />
                )}
                {app.installation.installed ? "管理" : "安装"}
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
