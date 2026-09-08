import { lazy, Suspense } from "react";
import {
  MemoryRouter,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { shouldRouteAnchorClick } from "@/core/navigation/open-target";
import { DatabaseIcon, PanelLeftIcon } from "lucide-react";
import {
  setModuleEnabledGlobally,
  useEnabledModuleIds,
} from "@/core/modules/enabled-modules";
import { WorkbenchSurfaceProvider } from "@/core/workbench/workbench-surface";
import { LOCAL_DATABASE_APP_ID } from "@/core/workbench/apps";
import { DetachedRouterContext } from "./detached-router-context";
import { sourceArtifactRoute } from "@/core/storage/file-location";
import {
  preserveWorkbenchPresentation,
  workbenchRoute,
} from "@/core/router/desktop-workspace-route";

const LocalDatabaseContent = lazy(() => import("./local-database-content"));
const LIBRARIES = [
  ["overview", "概览"],
  ["apps", "应用"],
  ["docs", "文档"],
  ["images", "图片"],
  ["videos", "视频"],
  ["computer", "本机"],
  ["sources", "授权目录"],
] as const;

export function LocalDatabaseSurface({
  onOpenWorkbench,
}: {
  onOpenWorkbench?: (route: string) => void;
}) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { search } = useLocation();
  // The system database is one app across all personas. Its sidebar pin is
  // account-wide so toggling it from a detached window remains visible in the
  // workbench after switching agents.
  const enabled = useEnabledModuleIds().includes(LOCAL_DATABASE_APP_ID);
  const openWorkbench = (route: string) => {
    const target = preserveWorkbenchPresentation(route, search);
    if (onOpenWorkbench) {
      onOpenWorkbench(target);
      return;
    }
    navigate(workbenchRoute(target));
  };
  return (
    <div
      data-testid="local-database-app"
      className="flex size-full min-h-0 flex-col bg-background text-foreground"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <h1 className="flex items-center gap-2 text-sm font-semibold">
          <DatabaseIcon className="size-4" />
          本地数据库
        </h1>
        {params.get("sourceThread") && params.get("sourceArtifact") && (
          <a
            className="rounded-md border px-3 py-1.5 text-xs hover:bg-muted"
            href={`#${preserveWorkbenchPresentation(
              sourceArtifactRoute(
                params.get("sourceThread")!,
                params.get("sourceArtifact")!,
              ),
              search,
            )}`}
          >
            返回原产物
          </a>
        )}
        {params.get("sourceThread") && (
          <a
            className="rounded-md border px-3 py-1.5 text-xs hover:bg-muted"
            href={`#${preserveWorkbenchPresentation(
              `/workspace/realtime/${encodeURIComponent(params.get("sourceThread")!)}`,
              search,
            )}`}
            onClick={(event) => {
              if (!shouldRouteAnchorClick(event)) return;
              event.preventDefault();
              (onOpenWorkbench ?? navigate)(
                preserveWorkbenchPresentation(
                  `/workspace/realtime/${encodeURIComponent(params.get("sourceThread")!)}`,
                  search,
                ),
              );
            }}
          >
            返回原任务
          </a>
        )}
        <button
          type="button"
          className="rounded-md border px-3 py-1.5 text-xs hover:bg-muted"
          onClick={() =>
            openWorkbench(`/workspace/storage?${params.toString()}`)
          }
        >
          在工作台中打开
        </button>
        <button
          type="button"
          aria-pressed={enabled}
          onClick={() =>
            setModuleEnabledGlobally(LOCAL_DATABASE_APP_ID, !enabled)
          }
          className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs hover:bg-muted"
        >
          <PanelLeftIcon className="size-3.5" />
          {enabled ? "从工作台侧边栏移除" : "添加到工作台侧边栏"}
        </button>
      </header>
      <nav
        aria-label="本地数据库分类"
        className="flex shrink-0 gap-1 overflow-x-auto border-b p-2"
      >
        {LIBRARIES.map(([key, label]) => (
          <button
            type="button"
            key={key}
            aria-current={
              (params.get("library") || "overview") === key ? "page" : undefined
            }
            onClick={() =>
              setParams((previous) => {
                const next = new URLSearchParams(previous);
                next.set("library", key);
                return next;
              })
            }
            className="shrink-0 rounded-md px-3 py-1.5 text-xs hover:bg-muted aria-[current=page]:bg-muted aria-[current=page]:font-semibold"
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1">
        <WorkbenchSurfaceProvider surface="browser">
          <Suspense
            fallback={
              <div role="status" className="p-6 text-sm">
                正在打开本地数据库…
              </div>
            }
          >
            <LocalDatabaseContent />
          </Suspense>
        </WorkbenchSurfaceProvider>
      </div>
    </div>
  );
}

export function LocalDatabaseApp({
  initialRoute = "/workspace/storage?surface=company&library=overview",
  onOpenWorkbench,
}: {
  initialRoute?: string;
  onOpenWorkbench?: (route: string) => void;
}) {
  return (
    <DetachedRouterContext>
      <MemoryRouter initialEntries={[initialRoute]}>
        <LocalDatabaseSurface onOpenWorkbench={onOpenWorkbench} />
      </MemoryRouter>
    </DetachedRouterContext>
  );
}
