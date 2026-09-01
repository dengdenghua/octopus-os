import { useSyncExternalStore } from "react";

import type { AppPresentation } from "@/core/apps/app-presentation";

export type WorkbenchBuiltinIcon =
  | "projects"
  | "trading"
  | "design"
  | "narrative"
  | "evolution"
  | "intelligence"
  | "community";

export interface WorkbenchBuiltinApp {
  id: string;
  moduleId: string;
  name: string;
  description: string;
  workspaceRoute: string;
  launchUrl: string;
  icon: WorkbenchBuiltinIcon;
  /** Core surfaces ship with the shell; remote surfaces are installed on demand. */
  delivery: "core" | "remote";
  /** Cloud catalog id and extracted package directory for remote surfaces. */
  cloudId?: string;
  packageId?: string;
  /** Runtime ModulePlugin name when this surface has a live plugin lifecycle. */
  runtimePlugin?: string;
  /** How the installed app is presented to the user. */
  presentation: AppPresentation;
}

/** Native EchoAI pages that can also live in the browser desktop and Dock. */
export const WORKBENCH_BUILTIN_APPS: readonly WorkbenchBuiltinApp[] = [
  {
    id: "projects",
    moduleId: "projects",
    name: "项目管理",
    description: "里程碑、风险与项目协作",
    workspaceRoute: "/workspace/projects",
    launchUrl: "echo://workspace/projects",
    icon: "projects",
    delivery: "core",
    presentation: "workbench",
  },
  {
    id: "paper-trading",
    moduleId: "paper.trading",
    name: "模拟炒股",
    description: "策略验证与模拟交易",
    workspaceRoute: "/workspace/paper-trading",
    launchUrl: "echo://workspace/paper-trading",
    icon: "trading",
    delivery: "remote",
    cloudId: "workbench_paper-trading",
    packageId: "paper-trading",
    runtimePlugin: "paper_trading",
    presentation: "workbench",
  },
  {
    id: "design",
    moduleId: "design",
    name: "设计画布",
    description: "视觉创作、素材编排与设计工作流",
    workspaceRoute: "/workspace/design",
    launchUrl: "echo://workspace/design",
    icon: "design",
    delivery: "remote",
    cloudId: "workbench_design",
    packageId: "design",
    presentation: "workbench",
  },
  {
    id: "narrative",
    moduleId: "narrative",
    name: "叙事工坊",
    description: "角色、世界观、剧情分支与正典协作",
    workspaceRoute: "/workspace/narrative",
    launchUrl: "echo://workspace/narrative",
    icon: "narrative",
    delivery: "remote",
    cloudId: "workbench_narrative",
    packageId: "narrative_studio",
    runtimePlugin: "narrative_studio",
    presentation: "workbench",
  },
  {
    id: "evolution",
    moduleId: "evolution",
    name: "自进化",
    description: "双螺旋、候选基因、治理与审计",
    workspaceRoute: "/workspace/evolution",
    launchUrl: "echo://workspace/evolution",
    icon: "evolution",
    delivery: "remote",
    cloudId: "workbench_self-evolution",
    packageId: "self_evolution",
    presentation: "workbench",
  },
  {
    id: "intelligence",
    moduleId: "intelligence",
    name: "订阅",
    description: "持续跟踪主题与情报",
    workspaceRoute: "/workspace/intelligence?surface=chat",
    launchUrl: "echo://workspace/intelligence",
    icon: "intelligence",
    delivery: "remote",
    cloudId: "workbench_intelligence",
    packageId: "intelligence",
    presentation: "workbench",
  },
  {
    id: "community",
    moduleId: "community",
    name: "发现社区",
    description: "发现并复用社区工作流",
    workspaceRoute: "/workspace/community",
    launchUrl: "echo://workspace/community",
    icon: "community",
    delivery: "remote",
    cloudId: "workbench_community",
    packageId: "community",
    presentation: "workbench",
  },
];

export interface WorkspaceWebShortcut {
  id: string;
  name: string;
  url: string;
  logoUrl?: string;
}

export function workspaceWebAppRoute(shortcut: {
  url: string;
  name?: string;
}): string {
  const params = new URLSearchParams({ url: shortcut.url });
  if (shortcut.name) params.set("title", shortcut.name);
  return `/workspace/web-app?${params.toString()}`;
}

const STORAGE_KEY = "echo:workbench:workspace-web-shortcuts.v1";
const CHANGE_EVENT = "echo:workbench-web-shortcuts-changed";
const EMPTY_SHORTCUTS: readonly WorkspaceWebShortcut[] = [];
let cache: readonly WorkspaceWebShortcut[] | null = null;

function isWebUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function shortcutId(url: string): string {
  return `web:${url}`;
}

function sanitizeShortcut(value: unknown): WorkspaceWebShortcut | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<WorkspaceWebShortcut>;
  const name = typeof candidate.name === "string" ? candidate.name.trim() : "";
  const url = typeof candidate.url === "string" ? candidate.url.trim() : "";
  if (!name || !isWebUrl(url)) return null;
  const logoUrl =
    typeof candidate.logoUrl === "string" && isWebUrl(candidate.logoUrl)
      ? candidate.logoUrl
      : undefined;
  return {
    id: shortcutId(url),
    name: name.slice(0, 80),
    url,
    ...(logoUrl ? { logoUrl } : {}),
  };
}

function readShortcuts(): readonly WorkspaceWebShortcut[] {
  if (cache) return cache;
  if (typeof window === "undefined") return EMPTY_SHORTCUTS;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
    const items = Array.isArray(parsed)
      ? parsed
          .map(sanitizeShortcut)
          .filter((item): item is WorkspaceWebShortcut => Boolean(item))
      : [];
    cache = Array.from(new Map(items.map((item) => [item.url, item])).values());
  } catch {
    cache = [];
  }
  return cache;
}

function emitChange(): void {
  cache = null;
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

function writeShortcuts(items: readonly WorkspaceWebShortcut[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    emitChange();
  } catch {
    // Private browsing or a full storage quota should not break navigation.
  }
}

export function setWorkspaceWebShortcut(
  shortcut: Omit<WorkspaceWebShortcut, "id">,
  pinned: boolean,
): void {
  if (typeof window === "undefined") return;
  const clean = sanitizeShortcut(shortcut);
  if (!clean) return;
  const current = readShortcuts();
  const next = pinned
    ? [...current.filter((item) => item.url !== clean.url), clean]
    : current.filter((item) => item.url !== clean.url);
  writeShortcuts(next);
}

function subscribe(listener: () => void): () => void {
  const onChange = () => {
    cache = null;
    listener();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) onChange();
  };
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function useWorkspaceWebShortcuts(): readonly WorkspaceWebShortcut[] {
  return useSyncExternalStore(subscribe, readShortcuts, () => EMPTY_SHORTCUTS);
}

export function resetWorkspaceWebShortcutCache(): void {
  cache = null;
}
