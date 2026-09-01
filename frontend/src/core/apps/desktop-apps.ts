import {
  AppWindowIcon,
  FileImageIcon,
  FileTextIcon,
  FilmIcon,
  FolderIcon,
  GlobeIcon,
  MonitorIcon,
  SettingsIcon,
  SparklesIcon,
  TerminalSquareIcon,
  type LucideIcon,
} from "lucide-react";

import {
  WORKBENCH_BUILTIN_APPS,
  type WorkbenchBuiltinApp,
} from "@/core/workbench/apps";
import type { Translations } from "@/core/i18n/locales";
import { BROWSER_WORKSPACE_ROUTE } from "@/core/workspace/sidebar-routing";

export type DesktopAppPlacement =
  | "primary"
  | "library"
  | "system"
  | "workbench";

export interface DesktopApp {
  id: string;
  name: string;
  subtitle: string;
  route: string;
  icon: LucideIcon;
  color: string;
  placement: DesktopAppPlacement;
}

const WORKBENCH_ICONS: Record<WorkbenchBuiltinApp["icon"], LucideIcon> = {
  projects: FolderIcon,
  trading: SparklesIcon,
  design: FileImageIcon,
  narrative: FileTextIcon,
  evolution: SparklesIcon,
  intelligence: GlobeIcon,
  community: AppWindowIcon,
};

const WORKBENCH_COLORS: Record<WorkbenchBuiltinApp["icon"], string> = {
  projects: "from-amber-500 to-orange-600",
  trading: "from-emerald-500 to-teal-600",
  design: "from-fuchsia-500 to-violet-600",
  narrative: "from-rose-500 to-orange-500",
  evolution: "from-violet-500 to-indigo-600",
  intelligence: "from-indigo-500 to-cyan-500",
  community: "from-sky-500 to-blue-600",
};

function workbenchDesktopApp(app: WorkbenchBuiltinApp): DesktopApp {
  return {
    id: `workbench:${app.id}`,
    name: app.name,
    subtitle: app.description,
    route: app.workspaceRoute,
    icon: WORKBENCH_ICONS[app.icon],
    color: WORKBENCH_COLORS[app.icon],
    placement: "workbench",
  };
}

/**
 * The single desktop-facing catalog. Pages keep their existing routes and
 * backend contracts; this layer only gives them a stable app identity and
 * launch metadata.
 */
export function buildDesktopApps(t: Translations): DesktopApp[] {
  const apps: DesktopApp[] = [
    {
      id: "workspace",
      name: t.desktop.apps.workspace.name,
      subtitle: t.desktop.apps.workspace.subtitle,
      route: "/workspace/realtime/new",
      icon: MonitorIcon,
      color: "from-sky-500 to-blue-600",
      placement: "primary",
    },
    {
      id: "ai-browser",
      name: t.desktop.apps.aiBrowser.name,
      subtitle: t.desktop.apps.aiBrowser.subtitle,
      route: BROWSER_WORKSPACE_ROUTE,
      icon: GlobeIcon,
      color: "from-indigo-500 to-cyan-500",
      placement: "primary",
    },
    {
      id: "local-files",
      name: t.desktop.apps.localFiles.name,
      subtitle: t.desktop.apps.localFiles.subtitle,
      route: "/workspace/knowledge",
      icon: FolderIcon,
      color: "from-warning to-orange-500",
      placement: "primary",
    },
    {
      id: "photos",
      name: t.storage.libraries.imagesLabel,
      subtitle: t.storage.libraries.imagesDetail,
      route: "/apps/photos",
      icon: FileImageIcon,
      color: "from-cyan-400 to-blue-500",
      placement: "library",
    },
    {
      id: "media",
      name: t.storage.libraries.videosLabel,
      subtitle: t.storage.libraries.videosDetail,
      route: "/apps/media",
      icon: FilmIcon,
      color: "from-violet-500 to-fuchsia-500",
      placement: "library",
    },
    {
      id: "local-apps",
      name: t.desktop.apps.localApps.name,
      subtitle: t.desktop.apps.localApps.subtitle,
      route: "/workspace/agents?surface=chat&tab=plugins",
      icon: AppWindowIcon,
      color: "from-violet-500 to-fuchsia-500",
      placement: "library",
    },
    {
      id: "terminal-logs",
      name: t.desktop.apps.terminalLogs.name,
      subtitle: t.desktop.apps.terminalLogs.subtitle,
      route: "/workspace/observability",
      icon: TerminalSquareIcon,
      color: "from-muted-foreground to-muted-foreground/70",
      placement: "system",
    },
    {
      id: "settings",
      name: t.desktop.apps.settings.name,
      subtitle: t.desktop.apps.settings.subtitle,
      route: "/workspace/settings",
      icon: SettingsIcon,
      color: "from-stone-500 to-neutral-700",
      placement: "system",
    },
  ];

  return [...apps, ...WORKBENCH_BUILTIN_APPS.map(workbenchDesktopApp)];
}

/** URL understood by the packaged Electron shell's auxiliary-window handler. */
export function desktopWindowURL(route: string): string {
  const normalized = route.startsWith("/") ? route : `/${route}`;
  const separator = normalized.includes("?") ? "&" : "?";
  return `echo-app://app/index.html#${normalized}${separator}embedded=app`;
}

export function shouldOpenDesktopWindow(): boolean {
  return (
    typeof window !== "undefined" &&
    Boolean(window.echo?.isElectron) &&
    window.location.protocol === "echo-app:"
  );
}
