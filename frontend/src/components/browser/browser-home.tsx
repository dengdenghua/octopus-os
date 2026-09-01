import {
  useEffect,
  useState,
  useCallback,
  useRef,
  useMemo,
  type CSSProperties,
  type DragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  SearchIcon,
  BotIcon,
  HomeIcon,
  PaletteIcon,
  PanelLeftIcon,
  ImageIcon,
  Gamepad2Icon,
  CirclePlusIcon,
  SettingsIcon,
  CalendarDaysIcon,
  CheckIcon,
  ChevronDownIcon,
  FileTextIcon,
  Clock3Icon,
  Loader2Icon,
  RefreshCwIcon,
  ServerIcon,
  LayoutGridIcon,
  SparklesIcon,
  BookOpenIcon,
  MessageCircleIcon,
  BrainCircuitIcon,
  GraduationCapIcon,
  Edit3,
  EllipsisIcon,
  Folder,
  FolderOpen,
  Trash2,
  X,
  Plus,
  Minimize2,
  Maximize2,
  CompassIcon,
  SquareKanbanIcon,
  CandlestickChartIcon,
  DnaIcon,
  RssIcon,
  type LucideIcon,
} from "lucide-react";

import { swallow } from "@/core/utils/log";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { usePromptDialog } from "@/components/ui/prompt-dialog";
import {
  detectLocalServices,
  type DetectedLocalService,
} from "@/core/browser/local-services";
import {
  WORKBENCH_BUILTIN_APPS,
  setWorkspaceWebShortcut,
  useWorkspaceWebShortcuts,
  type WorkbenchBuiltinIcon,
} from "@/core/workbench/apps";
import { useActiveAgentId } from "@/core/agents/active";
import { useEnabledModuleIds } from "@/core/modules/enabled-modules";
import { useWorkbenchAvailabilitySync } from "@/core/workbench/availability";

import {
  BROWSER_EDIT_HOME_EVENT,
  useBrowserStore,
  type BrowserTab,
} from "./browser-store";

type DesktopPanelId =
  | "home"
  | "theme"
  | "widgets"
  | "wallpaper"
  | "games"
  | "add"
  | "settings";
type DesktopAppCategory = "workspace" | "ai" | "video" | "dev" | "knowledge";

interface BrowserDesktopApp {
  name: string;
  url: string;
  icon: LucideIcon;
  logoUrl?: string;
  color: string;
  description: string;
  category: DesktopAppCategory;
  moduleId?: string;
  workspaceRoute?: string;
}

interface QuickLink {
  id: string;
  name: string;
  url: string;
  icon: string;
  category: string;
  color?: string;
  folderId?: string;
}

interface UserFolder {
  id: string;
  name: string;
  linkIds: string[];
}

interface Widget {
  id: string;
  type: "weather" | "calendar" | "notes" | "system" | "ai-tools" | "bookmarks";
  size: "small" | "medium" | "large";
  title: string;
}

type DesktopBackdropId =
  | "palette"
  | "theme-mist"
  | "theme-focus"
  | "theme-clear"
  | "wallpaper-sky"
  | "wallpaper-clear-sky"
  | "wallpaper-forest"
  | "wallpaper-ember";

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  targetId: string | null;
  targetType: "icon" | "widget" | "folder" | "background";
}

interface DragState {
  draggedId: string | null;
  draggedType: "widget" | "icon" | null;
  dropTargetId: string | null;
  dropPosition: "before" | "after" | null;
}

interface EditWidgetState {
  visible: boolean;
  widgetId: string | null;
  title: string;
  type: Widget["type"];
  size: Widget["size"];
}

const BUILTIN_ICON_MAP: Record<WorkbenchBuiltinIcon, LucideIcon> = {
  projects: SquareKanbanIcon,
  trading: CandlestickChartIcon,
  design: PaletteIcon,
  narrative: BookOpenIcon,
  evolution: DnaIcon,
  intelligence: RssIcon,
  community: CompassIcon,
};

/** Keep native app tiles distinct without letting the theme's saturated
 * primary color overpower translucent desktop and Dock surfaces. */
const BUILTIN_ICON_TONE: Record<WorkbenchBuiltinIcon, string> = {
  projects: "from-slate-600/75 to-sky-500/65",
  trading: "from-teal-600/75 to-emerald-500/65",
  design: "from-violet-600/70 to-indigo-400/60",
  narrative: "from-fuchsia-600/70 to-violet-500/60",
  evolution: "from-violet-600/75 to-cyan-500/60",
  intelligence: "from-cyan-600/75 to-blue-500/65",
  community: "from-indigo-600/70 to-violet-500/60",
};

const WORKSPACE_DESKTOP_APPS: BrowserDesktopApp[] = WORKBENCH_BUILTIN_APPS.map(
  (app) => ({
    name: app.name,
    url: app.launchUrl,
    icon: BUILTIN_ICON_MAP[app.icon],
    color: BUILTIN_ICON_TONE[app.icon],
    description: app.description,
    category: "workspace",
    workspaceRoute: app.workspaceRoute,
    moduleId: app.moduleId,
  }),
);

const AI_DESKTOP_APPS: BrowserDesktopApp[] = [
  ...WORKSPACE_DESKTOP_APPS,
  {
    name: "Gemini",
    url: "https://gemini.google.com/app",
    icon: SparklesIcon,
    logoUrl: "https://cdn.simpleicons.org/googlegemini",
    color: "from-blue-500 to-cyan-400",
    description: "Comprehensive search, multi-turn analysis",
    category: "ai",
  },
  {
    name: "NotebookLM",
    url: "https://notebooklm.google.com/",
    icon: BookOpenIcon,
    logoUrl: "https://cdn.simpleicons.org/notebooklm",
    color: "from-warning to-orange-400",
    description: "Library, citations, document research",
    category: "ai",
  },
  {
    name: "Doubao",
    url: "https://www.doubao.com/chat/",
    icon: MessageCircleIcon,
    logoUrl: "https://www.google.com/s2/favicons?domain=www.doubao.com&sz=128",
    color: "from-success to-teal-400",
    description: "Chinese research, Chinese rewriting",
    category: "ai",
  },
  {
    name: "DeepSeek",
    url: "https://chat.deepseek.com/",
    icon: BrainCircuitIcon,
    logoUrl: "https://cdn.simpleicons.org/deepseek",
    color: "from-blue-700 to-indigo-500",
    description: "Reasoning, coding, Chinese Q&A",
    category: "ai",
  },
  {
    name: "Tongyi Qianwen",
    url: "https://chat.qwen.ai/",
    icon: SparklesIcon,
    logoUrl: "https://cdn.simpleicons.org/qwen",
    color: "from-blue-600 to-cyan-500",
    description: "Tongyi models, multimodal chat",
    category: "ai",
  },
  {
    name: "Wenxin Yiyan",
    url: "https://yiyan.baidu.com/",
    icon: MessageCircleIcon,
    logoUrl: "https://cdn.simpleicons.org/baidu",
    color: "from-indigo-600 to-blue-500",
    description: "Baidu agents, Chinese creation",
    category: "ai",
  },
  {
    name: "Tencent Yuanbao",
    url: "https://yuanbao.tencent.com/",
    icon: BotIcon,
    logoUrl:
      "https://www.google.com/s2/favicons?domain=yuanbao.tencent.com&sz=128",
    color: "from-cyan-600 to-blue-500",
    description: "Chinese search, material summary",
    category: "ai",
  },
  {
    name: "Perplexity",
    url: "https://www.perplexity.ai/",
    icon: SearchIcon,
    logoUrl: "https://cdn.simpleicons.org/perplexity",
    color: "from-sky-500 to-indigo-500",
    description: "Web search, source leads",
    category: "ai",
  },
  {
    name: "ChatGPT",
    url: "https://chatgpt.com/",
    icon: BotIcon,
    logoUrl: "https://chatgpt.com/favicon.ico",
    color: "from-zinc-700 to-zinc-500",
    description: "General chat, coding assistance",
    category: "ai",
  },
  {
    name: "Claude",
    url: "https://claude.ai/",
    icon: BrainCircuitIcon,
    logoUrl: "https://cdn.simpleicons.org/claude",
    color: "from-stone-600 to-destructive",
    description: "Long-text analysis, writing organization",
    category: "ai",
  },
  {
    name: "Kimi",
    url: "https://www.kimi.com/",
    icon: GraduationCapIcon,
    logoUrl: "https://www.google.com/s2/favicons?domain=www.kimi.com&sz=128",
    color: "from-violet-500 to-fuchsia-500",
    description: "Long context, Chinese materials",
    category: "ai",
  },
  {
    name: "Agnes AI",
    url: "https://app.agnes-ai.com/",
    icon: ImageIcon,
    logoUrl:
      "https://www.google.com/s2/favicons?domain=app.agnes-ai.com&sz=128",
    color: "from-pink-500 to-destructive",
    description: "AI gateway, image/video generation",
    category: "ai",
  },
  {
    name: "YouTube",
    url: "https://www.youtube.com/",
    icon: ImageIcon,
    logoUrl: "https://cdn.simpleicons.org/youtube",
    color: "from-destructive to-destructive",
    description: "Videos, channels, live streams",
    category: "video",
  },
  {
    name: "Bilibili",
    url: "https://www.bilibili.com/",
    icon: ImageIcon,
    logoUrl: "https://cdn.simpleicons.org/bilibili",
    color: "from-sky-500 to-cyan-400",
    description: "Videos, anime, knowledge zone",
    category: "video",
  },
  {
    name: "GitHub",
    url: "https://github.com/",
    icon: BotIcon,
    logoUrl: "https://github.githubassets.com/favicons/favicon.svg",
    color: "from-zinc-900 to-zinc-700",
    description: "Code repos, project collaboration",
    category: "dev",
  },
  {
    name: "Stack Overflow",
    url: "https://stackoverflow.com/",
    icon: BrainCircuitIcon,
    logoUrl: "https://cdn.simpleicons.org/stackoverflow",
    color: "from-orange-500 to-warning",
    description: "Programming Q&A, troubleshooting",
    category: "dev",
  },
  {
    name: "MDN",
    url: "https://developer.mozilla.org/",
    icon: BookOpenIcon,
    logoUrl: "https://cdn.simpleicons.org/mdnwebdocs",
    color: "from-foreground to-blue-600",
    description: "Web docs, API reference",
    category: "dev",
  },
  {
    name: "Zhihu",
    url: "https://www.zhihu.com/",
    icon: SearchIcon,
    logoUrl: "https://cdn.simpleicons.org/zhihu",
    color: "from-blue-600 to-sky-500",
    description: "Q&A, columns, Chinese materials",
    category: "knowledge",
  },
  {
    name: "Wikipedia",
    url: "https://www.wikipedia.org/",
    icon: GraduationCapIcon,
    logoUrl: "https://cdn.simpleicons.org/wikipedia",
    color: "from-muted-foreground to-muted-foreground/70",
    description: "Encyclopedia, background materials",
    category: "knowledge",
  },
];

const DESKTOP_APP_GROUPS: Array<{
  id: DesktopAppCategory;
  appUrls: string[];
}> = [
  {
    id: "workspace",
    appUrls: WORKBENCH_BUILTIN_APPS.map((app) => app.launchUrl),
  },
  {
    id: "ai",
    appUrls: [
      "https://gemini.google.com/app",
      "https://notebooklm.google.com/",
      "https://chatgpt.com/",
      "https://www.doubao.com/chat/",
      "https://chat.deepseek.com/",
      "https://chat.qwen.ai/",
      "https://yiyan.baidu.com/",
      "https://yuanbao.tencent.com/",
    ],
  },
  {
    id: "video",
    appUrls: ["https://www.youtube.com/", "https://www.bilibili.com/"],
  },
  {
    id: "dev",
    appUrls: [
      "https://github.com/",
      "https://stackoverflow.com/",
      "https://developer.mozilla.org/",
    ],
  },
  {
    id: "knowledge",
    appUrls: ["https://www.zhihu.com/", "https://www.wikipedia.org/"],
  },
];

const DESKTOP_SIDE_NAV: Array<{
  id: DesktopPanelId;
  icon: LucideIcon;
}> = [
  { id: "theme", icon: PaletteIcon },
  { id: "widgets", icon: PanelLeftIcon },
  { id: "wallpaper", icon: ImageIcon },
  { id: "games", icon: Gamepad2Icon },
];

const SEARCH_ENGINES = [
  {
    name: "Baidu",
    url: "https://www.baidu.com/s?wd=",
    icon: "Bai",
    logoUrl: "https://www.baidu.com/favicon.ico",
    accent: "bg-[#2f6bff] text-white",
  },
  {
    name: "Google",
    url: "https://www.google.com/search?q=",
    icon: "G",
    logoUrl: "https://www.google.com/favicon.ico",
    accent: "bg-white text-[#4285f4]",
  },
  {
    name: "Bing",
    url: "https://www.bing.com/search?q=",
    icon: "B",
    logoUrl: "https://www.bing.com/favicon.ico",
    accent: "bg-[#008373] text-white",
  },
  {
    name: "GitHub",
    url: "https://github.com/search?q=",
    icon: "GH",
    logoUrl: "https://github.githubassets.com/favicons/favicon.svg",
    accent: "bg-[#24292f] text-white",
  },
];

type SearchEngine = (typeof SEARCH_ENGINES)[number];

function DesktopAppLogo({
  app,
  className,
  iconClassName,
}: {
  app: BrowserDesktopApp;
  className?: string;
  iconClassName?: string;
}) {
  const [failed, setFailed] = useState(false);
  const FallbackIcon = app.icon;

  return (
    <span
      className={cn(
        "grid place-items-center overflow-hidden rounded-md text-foreground transition",
        className,
        (failed || !app.logoUrl) && "bg-gradient-to-br text-white",
        (failed || !app.logoUrl) && app.color,
      )}
    >
      {failed || !app.logoUrl ? (
        <FallbackIcon className={cn("size-1/2", iconClassName)} />
      ) : (
        <img
          src={app.logoUrl}
          alt=""
          className={cn("size-[68%] object-contain", iconClassName)}
          onError={() => setFailed(true)}
        />
      )}
    </span>
  );
}

function SearchEngineLogo({
  engine,
  className,
}: {
  engine: SearchEngine;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);

  return (
    <span
      className={cn(
        "grid place-items-center overflow-hidden rounded-md",
        className,
      )}
    >
      {failed ? (
        <span
          className={cn(
            "grid size-full place-items-center text-micro font-black leading-none rounded-md",
            engine.accent,
          )}
        >
          {engine.icon}
        </span>
      ) : (
        <img
          src={engine.logoUrl}
          alt=""
          className="size-full object-contain"
          onError={() => setFailed(true)}
        />
      )}
    </span>
  );
}

const DESKTOP_APP_ORDER_KEY = "echo:browser-desktop-app-order";
const DOCK_APP_URLS_KEY = "echo:browser:dock-app-urls";
const QUICK_LINKS_KEY = "echo:browser:quick-links";
const FOLDERS_KEY = "echo:browser:folders";
const WIDGETS_KEY = "echo:browser:widgets";
const DESKTOP_BACKDROP_KEY = "echo:browser:desktop-backdrop";
const DEFAULT_DOCK_APP_URLS = [
  "echo://workspace/projects",
  "echo://workspace/paper-trading",
  "echo://workspace/intelligence",
  "echo://workspace/community",
  "https://gemini.google.com/app",
  "https://chat.deepseek.com/",
  "https://chat.qwen.ai/",
  "https://www.doubao.com/chat/",
  "https://chatgpt.com/",
  "https://github.com/",
];
const INTERNAL_DOCK_APP_URLS = WORKBENCH_BUILTIN_APPS.map(
  (app) => app.launchUrl,
);
const LEGACY_DOCK_REPLACEMENTS = new Set([
  "https://notebooklm.google.com/",
  "https://www.youtube.com/",
  "https://www.bilibili.com/",
  "https://www.perplexity.ai/",
]);
const DEFAULT_DESKTOP_BACKDROP: DesktopBackdropId = "palette";
interface DesktopBackdropConfig {
  className: string;
  swatchClassName: string;
  imageUrl?: string;
}

const DESKTOP_BACKDROPS: Record<DesktopBackdropId, DesktopBackdropConfig> = {
  palette: {
    className: "browser-backdrop-palette",
    swatchClassName: "browser-backdrop-palette-swatch",
  },
  "theme-mist": {
    imageUrl:
      "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=1920&q=80",
    className:
      "bg-[radial-gradient(circle_at_16%_10%,rgba(236,253,245,0.72),transparent_27%),radial-gradient(circle_at_78%_6%,rgba(125,211,252,0.42),transparent_25%),radial-gradient(circle_at_88%_70%,rgba(251,207,232,0.38),transparent_31%),radial-gradient(circle_at_34%_88%,rgba(253,230,138,0.24),transparent_30%),linear-gradient(145deg,#5f8c91_0%,#8ea7bd_34%,#bba8b8_68%,#d1ad8f_100%)]",
    swatchClassName:
      "bg-gradient-to-br from-muted-foreground via-zinc-300 to-destructive",
  },
  "theme-focus": {
    imageUrl:
      "https://images.unsplash.com/photo-1444703686981-a3abbc4d4fe3?auto=format&fit=crop&w=1920&q=80",
    className:
      "bg-[radial-gradient(circle_at_18%_10%,rgba(59,130,246,0.34),transparent_28%),radial-gradient(circle_at_82%_14%,rgba(168,85,247,0.24),transparent_30%),radial-gradient(circle_at_76%_82%,rgba(14,165,233,0.18),transparent_32%),linear-gradient(145deg,#0f172a_0%,#1e293b_42%,#111827_100%)]",
    swatchClassName:
      "bg-gradient-to-br from-background via-muted-foreground/30 to-primary",
  },
  "theme-clear": {
    imageUrl:
      "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1920&q=80",
    className:
      "bg-[radial-gradient(circle_at_18%_12%,rgba(191,219,254,0.86),transparent_28%),radial-gradient(circle_at_78%_18%,rgba(125,211,252,0.48),transparent_28%),radial-gradient(circle_at_82%_82%,rgba(224,242,254,0.82),transparent_34%),linear-gradient(145deg,#eff6ff_0%,#dbeafe_36%,#f8fafc_72%,#e0f2fe_100%)]",
    swatchClassName: "bg-gradient-to-br from-sky-200 via-white to-blue-300",
  },
  "wallpaper-sky": {
    imageUrl:
      "https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=1920&q=80",
    className:
      "bg-[radial-gradient(circle_at_22%_14%,rgba(219,234,254,0.92),transparent_26%),radial-gradient(circle_at_72%_16%,rgba(103,232,249,0.44),transparent_28%),linear-gradient(145deg,#60a5fa_0%,#93c5fd_45%,#f8fafc_100%)]",
    swatchClassName: "bg-gradient-to-br from-sky-300 via-blue-300 to-white",
  },
  "wallpaper-clear-sky": {
    imageUrl:
      "https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?auto=format&fit=crop&w=1920&q=80",
    className:
      "bg-[radial-gradient(circle_at_24%_16%,rgba(219,234,254,0.72),transparent_30%),radial-gradient(circle_at_76%_28%,rgba(226,232,240,0.52),transparent_32%),linear-gradient(145deg,#dbeafe_0%,#f8fafc_52%,#e0f2fe_100%)]",
    swatchClassName: "bg-gradient-to-br from-blue-100 via-white to-sky-200",
  },
  "wallpaper-forest": {
    imageUrl:
      "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?auto=format&fit=crop&w=1920&q=80",
    className:
      "bg-[radial-gradient(circle_at_18%_12%,rgba(187,247,208,0.56),transparent_30%),radial-gradient(circle_at_78%_70%,rgba(45,212,191,0.28),transparent_34%),linear-gradient(145deg,#134e4a_0%,#3f6212_46%,#a7f3d0_100%)]",
    swatchClassName: "bg-gradient-to-br from-success via-teal-500 to-lime-700",
  },
  "wallpaper-ember": {
    imageUrl:
      "https://images.unsplash.com/photo-1472214103451-9374bd1c798e?auto=format&fit=crop&w=1920&q=80",
    className:
      "bg-[radial-gradient(circle_at_20%_16%,rgba(254,215,170,0.66),transparent_30%),radial-gradient(circle_at_84%_76%,rgba(251,113,133,0.36),transparent_32%),linear-gradient(145deg,#44403c_0%,#a16207_50%,#fed7aa_100%)]",
    swatchClassName:
      "bg-gradient-to-br from-stone-600 via-warning to-orange-200",
  },
};

function getBackdropImageStyle(
  backdrop: DesktopBackdropConfig,
  overlay = "linear-gradient(180deg,rgba(15,23,42,0.18),rgba(15,23,42,0.12))",
): CSSProperties | undefined {
  if (!backdrop.imageUrl) return undefined;
  return {
    backgroundImage: `${overlay},url(${backdrop.imageUrl})`,
    backgroundPosition: "center",
    backgroundRepeat: "no-repeat",
    backgroundSize: "cover",
  };
}
const DESKTOP_THEME_BACKDROPS: DesktopBackdropId[] = [
  "palette",
  "theme-mist",
  "theme-focus",
  "theme-clear",
];
const DESKTOP_WALLPAPER_BACKDROPS: DesktopBackdropId[] = [
  "wallpaper-sky",
  "wallpaper-clear-sky",
  "wallpaper-forest",
  "wallpaper-ember",
];
const WIDGET_PANEL_TYPES: Widget["type"][] = [
  "calendar",
  "notes",
  "weather",
  "system",
];
const GAME_PANEL_URLS = [
  "https://www.bilibili.com/",
  "https://poki.com/",
  "https://music.youtube.com/",
  "https://www.youtube.com/",
];

function loadDesktopAppOrder(): string[] {
  if (typeof window === "undefined")
    return AI_DESKTOP_APPS.map((app) => app.url);
  try {
    const parsed = JSON.parse(
      localStorage.getItem(DESKTOP_APP_ORDER_KEY) || "[]",
    );
    if (!Array.isArray(parsed)) return AI_DESKTOP_APPS.map((app) => app.url);
    const known = new Set(AI_DESKTOP_APPS.map((app) => app.url));
    const saved = parsed.filter(
      (item): item is string => typeof item === "string" && known.has(item),
    );
    const missing = AI_DESKTOP_APPS.map((app) => app.url).filter(
      (url) => !saved.includes(url),
    );
    return [...saved, ...missing];
  } catch (e) {
    swallow(e);
    return AI_DESKTOP_APPS.map((app) => app.url);
  }
}

function loadDockAppUrls(): string[] {
  if (typeof window === "undefined") return DEFAULT_DOCK_APP_URLS;
  try {
    const known = new Set(AI_DESKTOP_APPS.map((app) => app.url));
    const parsed = JSON.parse(localStorage.getItem(DOCK_APP_URLS_KEY) || "[]");
    if (!Array.isArray(parsed) || parsed.length === 0)
      return DEFAULT_DOCK_APP_URLS;
    const saved = parsed.filter(
      (item): item is string => typeof item === "string" && known.has(item),
    );
    if (saved.some((url) => INTERNAL_DOCK_APP_URLS.includes(url))) return saved;
    return [
      ...INTERNAL_DOCK_APP_URLS,
      ...saved.filter((url) => !LEGACY_DOCK_REPLACEMENTS.has(url)),
    ].slice(0, DEFAULT_DOCK_APP_URLS.length);
  } catch (e) {
    swallow(e);
    return DEFAULT_DOCK_APP_URLS;
  }
}

function loadDesktopBackdrop(): DesktopBackdropId {
  if (typeof window === "undefined") return DEFAULT_DESKTOP_BACKDROP;
  const saved = localStorage.getItem(DESKTOP_BACKDROP_KEY);
  return saved && saved in DESKTOP_BACKDROPS
    ? (saved as DesktopBackdropId)
    : DEFAULT_DESKTOP_BACKDROP;
}

function faviconForUrl(url: string): string {
  try {
    return `https://www.google.com/s2/favicons?domain=${
      new URL(url).hostname
    }&sz=64`;
  } catch (e) {
    swallow(e);
    return "";
  }
}

function moveDesktopApp(
  order: string[],
  fromUrl: string,
  toUrl: string,
): string[] {
  if (fromUrl === toUrl) return order;
  const next = order.filter((url) => url !== fromUrl);
  const targetIndex = next.indexOf(toUrl);
  if (targetIndex < 0) return order;
  next.splice(targetIndex, 0, fromUrl);
  return next;
}

function MenuItem({
  icon: Icon,
  label,
  active,
  danger,
  shortcut,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  active?: boolean;
  danger?: boolean;
  shortcut?: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3 px-4 py-2 text-sm transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
        danger && "text-destructive hover:bg-destructive/10",
      )}
    >
      <Icon className="w-4 h-4 flex-shrink-0" />
      <span className="flex-1 text-left">{label}</span>
      {shortcut && (
        <span className="text-xs text-muted-foreground/70">{shortcut}</span>
      )}
    </button>
  );
}

function ContextMenu({
  state,
  onClose,
  onEdit,
  onDelete,
  onResize,
  onEditHome,
  onAddWidget,
  onAddIcon,
  onOpenSettings,
  currentSize,
}: {
  state: ContextMenuState;
  onClose: () => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
  onResize: (id: string, size: string) => void;
  onEditHome: () => void;
  onAddWidget: () => void;
  onAddIcon: () => void;
  onOpenSettings: () => void;
  currentSize?: string;
}) {
  const { t } = useI18n();
  const wt = t.browser.webviewTab;

  if (!state.visible) return null;

  const isIcon = state.targetType === "icon";
  const isWidget = state.targetType === "widget";
  const isFolder = state.targetType === "folder";

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        className="fixed z-50 bg-popover text-popover-foreground border border-border-subtle rounded-lg py-2 min-w-[160px] shadow-[var(--shadow-md)]"
        style={{ left: state.x, top: state.y }}
      >
        {isIcon && (
          <>
            <MenuItem
              icon={Edit3}
              label={wt.ctxEditIcon}
              onClick={() => {
                onEdit(state.targetId!);
                onClose();
              }}
            />
            <MenuItem
              icon={Trash2}
              label={wt.ctxDelete}
              danger
              onClick={() => {
                onDelete(state.targetId!);
                onClose();
              }}
            />
          </>
        )}
        {isWidget && (
          <>
            <MenuItem
              icon={Edit3}
              label={wt.ctxEditWidget}
              onClick={() => {
                onEdit(state.targetId!);
                onClose();
              }}
            />
            <MenuItem
              icon={Trash2}
              label={wt.ctxDelete}
              danger
              onClick={() => {
                onDelete(state.targetId!);
                onClose();
              }}
            />
            <div className="h-px bg-muted my-2" />
            <MenuItem
              icon={Minimize2}
              label={wt.ctxSizeSmall}
              active={currentSize === "small"}
              onClick={() => {
                onResize(state.targetId!, "small");
                onClose();
              }}
            />
            <MenuItem
              icon={LayoutGridIcon}
              label={wt.ctxSizeMedium}
              active={currentSize === "medium"}
              onClick={() => {
                onResize(state.targetId!, "medium");
                onClose();
              }}
            />
            <MenuItem
              icon={Maximize2}
              label={wt.ctxSizeLarge}
              active={currentSize === "large"}
              onClick={() => {
                onResize(state.targetId!, "large");
                onClose();
              }}
            />
          </>
        )}
        {isFolder && (
          <>
            <MenuItem
              icon={Edit3}
              label={wt.ctxEditFolder}
              onClick={() => {
                onEdit(state.targetId!);
                onClose();
              }}
            />
            <MenuItem
              icon={Trash2}
              label={wt.ctxDelete}
              danger
              onClick={() => {
                onDelete(state.targetId!);
                onClose();
              }}
            />
          </>
        )}
        {state.targetType === "background" && (
          <>
            <MenuItem
              icon={LayoutGridIcon}
              label={wt.ctxEditHome}
              onClick={() => {
                onEditHome();
                onClose();
              }}
            />
            <MenuItem
              icon={Plus}
              label={wt.ctxAddWidget}
              onClick={() => {
                onAddWidget();
                onClose();
              }}
            />
            <MenuItem
              icon={Plus}
              label={wt.ctxAddIcon}
              onClick={() => {
                onAddIcon();
                onClose();
              }}
            />
            <div className="h-px bg-muted my-2" />
            <MenuItem
              icon={SettingsIcon}
              label={wt.ctxSettings}
              onClick={() => {
                onOpenSettings();
                onClose();
              }}
            />
          </>
        )}
      </div>
    </>
  );
}

export function BrowserHome({
  active,
  device,
  onOpen,
}: {
  active: boolean;
  device: BrowserTab["device"];
  onOpen: (url: string) => void;
}) {
  useWorkbenchAvailabilitySync();
  const activeAgentId = useActiveAgentId() ?? "general";
  const enabledModuleIds = useEnabledModuleIds(activeAgentId);
  const enabledModuleIdSet = useMemo(
    () => new Set(enabledModuleIds),
    [enabledModuleIds],
  );
  const { t } = useI18n();
  const wt = t.browser.webviewTab;
  const bt = t.browserHome;
  const bp = t.browserPreviewPanel;
  const { history } = useBrowserStore();
  const workspaceWebShortcuts = useWorkspaceWebShortcuts();
  const [localServices, setLocalServices] = useState<DetectedLocalService[]>(
    [],
  );
  const [scanningLocalServices, setScanningLocalServices] = useState(false);

  const scanLocalServices = useCallback(() => {
    const ownPort = Number(window.location.port);
    const excludePorts =
      Number.isFinite(ownPort) && ownPort > 0 ? [ownPort] : [];
    setScanningLocalServices(true);
    void detectLocalServices({ excludePorts })
      .then(setLocalServices)
      .catch(() => setLocalServices([]))
      .finally(() => setScanningLocalServices(false));
  }, []);

  useEffect(() => {
    if (!active) return;
    scanLocalServices();
  }, [active, scanLocalServices]);

  const appNameMap = useMemo<Record<string, string>>(
    () => ({
      Doubao: bt.appNameDoubao,
      "Tongyi Qianwen": bt.appNameTongyiQianwen,
      "Wenxin Yiyan": bt.appNameWenxinYiyan,
      "Tencent Yuanbao": bt.appNameTencentYuanbao,
      Zhihu: bt.appNameZhihu,
    }),
    [bt],
  );

  const appDescMap = useMemo<Record<string, string>>(
    () => ({
      "Comprehensive search, multi-turn analysis": bt.appDescGemini,
      "Library, citations, document research": bt.appDescNotebookLM,
      "Chinese research, Chinese rewriting": bt.appDescDoubao,
      "Reasoning, coding, Chinese Q&A": bt.appDescDeepSeek,
      "Tongyi models, multimodal chat": bt.appDescTongyiQianwen,
      "Baidu agents, Chinese creation": bt.appDescWenxinYiyan,
      "Chinese search, material summary": bt.appDescTencentYuanbao,
      "Web search, source leads": bt.appDescPerplexity,
      "General chat, coding assistance": bt.appDescChatGPT,
      "Long-text analysis, writing organization": bt.appDescClaude,
      "Long context, Chinese materials": bt.appDescKimi,
      "AI gateway, image/video generation": bt.appDescAgnesAi,
      "Videos, channels, live streams": bt.appDescYouTube,
      "Videos, anime, knowledge zone": bt.appDescBilibili,
      "Code repos, project collaboration": bt.appDescGitHub,
      "Programming Q&A, troubleshooting": bt.appDescStackOverflow,
      "Web docs, API reference": bt.appDescMdn,
      "Q&A, columns, Chinese materials": bt.appDescZhihu,
      "Encyclopedia, background materials": bt.appDescWikipedia,
    }),
    [bt],
  );

  const sideNavLabelMap = useMemo<Record<string, string>>(
    () => ({
      home: wt.navHome,
      theme: wt.navTheme,
      widgets: wt.navWidgets,
      wallpaper: wt.navWallpaper,
      games: wt.navGames,
    }),
    [wt],
  );

  const [query, setQuery] = useState("");
  const [selectedEngine, setSelectedEngine] = useState(0);
  const [enginePickerOpen, setEnginePickerOpen] = useState(false);
  const [openAppGroupId, setOpenAppGroupId] =
    useState<DesktopAppCategory | null>(null);
  const [activePanel, setActivePanel] = useState<DesktopPanelId>("home");
  const [editMode, setEditMode] = useState(false);
  useEffect(() => {
    const enterEditMode = () => setEditMode(true);
    window.addEventListener(BROWSER_EDIT_HOME_EVENT, enterEditMode);
    return () =>
      window.removeEventListener(BROWSER_EDIT_HOME_EVENT, enterEditMode);
  }, []);
  const [appOrder, setAppOrder] = useState<string[]>(() =>
    loadDesktopAppOrder(),
  );
  const [dockAppUrls, setDockAppUrls] = useState<string[]>(() =>
    loadDockAppUrls(),
  );
  const [desktopBackdrop, setDesktopBackdrop] = useState<DesktopBackdropId>(
    () => loadDesktopBackdrop(),
  );
  const [draggingUrl, setDraggingUrl] = useState<string | null>(null);
  const [quickLinks, setQuickLinks] = useState<QuickLink[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      return JSON.parse(localStorage.getItem(QUICK_LINKS_KEY) || "[]");
    } catch (e) {
      swallow(e);
      return [];
    }
  });
  const [folders, setFolders] = useState<UserFolder[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      return JSON.parse(localStorage.getItem(FOLDERS_KEY) || "[]");
    } catch (e) {
      swallow(e);
      return [];
    }
  });
  const [widgets, setWidgets] = useState<Widget[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      return JSON.parse(localStorage.getItem(WIDGETS_KEY) || "[]");
    } catch (e) {
      swallow(e);
      return [];
    }
  });
  const [folderOpenStates, setFolderOpenStates] = useState<
    Record<string, boolean>
  >({});
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    visible: false,
    x: 0,
    y: 0,
    targetId: null,
    targetType: "background",
  });
  const [dragState, setDragState] = useState<DragState>({
    draggedId: null,
    draggedType: null,
    dropTargetId: null,
    dropPosition: null,
  });
  const [editWidgetState, setEditWidgetState] = useState<EditWidgetState>({
    visible: false,
    widgetId: null,
    title: "",
    type: "notes",
    size: "medium",
  });

  const visibleDesktopApps = useMemo(
    () =>
      AI_DESKTOP_APPS.filter(
        (app) => !app.moduleId || enabledModuleIdSet.has(app.moduleId),
      ),
    [enabledModuleIdSet],
  );
  const appByUrl = useMemo(
    () => new Map(visibleDesktopApps.map((app) => [app.url, app])),
    [visibleDesktopApps],
  );
  const dockApps = useMemo(
    () =>
      dockAppUrls
        .map((url) => appByUrl.get(url))
        .filter((app): app is BrowserDesktopApp => Boolean(app)),
    [appByUrl, dockAppUrls],
  );
  const getAppName = useCallback(
    (name: string) => appNameMap[name] ?? name,
    [appNameMap],
  );
  const getAppDesc = useCallback(
    (desc: string) => appDescMap[desc] ?? desc,
    [appDescMap],
  );
  const openDesktopApp = useCallback(
    (url: string) => {
      onOpen(url);
    },
    [onOpen],
  );
  const workspaceShortcutUrls = useMemo(
    () => workspaceWebShortcuts.map((shortcut) => shortcut.url),
    [workspaceWebShortcuts],
  );
  const toggleWorkspaceShortcut = useCallback(
    (url: string) => {
      const app = appByUrl.get(url);
      if (!app || app.workspaceRoute) return;
      setWorkspaceWebShortcut(
        {
          name: getAppName(app.name),
          url: app.url,
          logoUrl: app.logoUrl,
        },
        !workspaceShortcutUrls.includes(url),
      );
    },
    [appByUrl, getAppName, workspaceShortcutUrls],
  );
  const recentPanelItems = useMemo(() => {
    return history
      .filter(
        (item) =>
          item.url &&
          !item.url.startsWith("about:") &&
          !item.url.startsWith("echo:"),
      )
      .map((item) => ({
        url: item.url,
        title: item.title || item.url,
        favicon: item.favicon,
        meta: bt.metaRecent,
      }))
      .slice(0, 4);
  }, [bt.metaRecent, history]);
  const renderCompactLink = useCallback(
    (item: { url: string; title: string; favicon?: string; meta: string }) => {
      let fallbackIcon = "";
      try {
        fallbackIcon = `https://www.google.com/s2/favicons?domain=${
          new URL(item.url).hostname
        }&sz=64`;
      } catch (e) {
        swallow(e);
      }
      return (
        <button
          key={item.url}
          type="button"
          onClick={() => onOpen(item.url)}
          className="group flex min-w-0 items-center gap-2 rounded-xl p-1.5 text-left transition hover:bg-white/10"
          title={item.title}
        >
          <span className="grid size-8 shrink-0 place-items-center overflow-hidden rounded-lg liquid-glass-icon">
            {item.favicon || fallbackIcon ? (
              <img
                src={item.favicon || fallbackIcon}
                alt=""
                className="size-[65%] object-contain"
                onError={(event) => {
                  event.currentTarget.style.display = "none";
                }}
              />
            ) : (
              <BookOpenIcon className="size-3.5 text-muted-foreground" />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-mini font-semibold text-foreground">
              {item.title}
            </span>
            <span className="block truncate text-micro text-muted-foreground/80">
              {item.meta}
            </span>
          </span>
        </button>
      );
    },
    [onOpen],
  );
  const desktopAppGroups = useMemo(() => {
    const titles: Record<
      DesktopAppCategory,
      { title: string; subtitle: string }
    > = {
      workspace: { title: "EchoAI 工作台", subtitle: "原生能力，一键直达" },
      ai: { title: bt.groupAiTools, subtitle: bt.groupAiToolsSubtitle },
      video: { title: bt.groupVideo, subtitle: bt.groupVideoSubtitle },
      dev: { title: bt.groupDev, subtitle: bt.groupDevSubtitle },
      knowledge: {
        title: bt.groupKnowledge,
        subtitle: bt.groupKnowledgeSubtitle,
      },
    };
    return DESKTOP_APP_GROUPS.map((group) => ({
      ...titles[group.id],
      id: group.id,
      apps: group.appUrls
        .map((url) => appByUrl.get(url))
        .filter((app): app is BrowserDesktopApp => Boolean(app)),
    }));
  }, [appByUrl, bt]);
  const compactDesktop = device !== "desktop";
  const tabletDesktop = device === "tablet";
  const mobileDesktop = device === "mobile";
  const visibleDockApps = mobileDesktop ? dockApps.slice(0, 5) : dockApps;
  const today = new Date();
  const day = today.getDate().toString().padStart(2, "0");
  const month = wt.monthFormat(today.getFullYear(), today.getMonth() + 1);
  const week = wt.weekdays[today.getDay()];
  const searchInputRef = useRef<HTMLInputElement>(null);
  const enginePickerRef = useRef<HTMLDivElement>(null);
  const searchEngineNameMap = useMemo<Record<string, string>>(
    () => ({
      Baidu: bt.searchEngineBaidu,
    }),
    [bt],
  );
  const searchEngineIconMap = useMemo<Record<string, string>>(
    () => ({
      Bai: bt.searchEngineBaiduIcon,
    }),
    [bt],
  );
  const engines = useMemo(
    () =>
      SEARCH_ENGINES.map((engine) => ({
        ...engine,
        name: searchEngineNameMap[engine.name] ?? engine.name,
        icon: searchEngineIconMap[engine.icon] ?? engine.icon,
      })),
    [searchEngineNameMap, searchEngineIconMap],
  );
  const selectedSearchEngine = engines[selectedEngine] ?? engines[0]!;
  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(DESKTOP_APP_ORDER_KEY, JSON.stringify(appOrder));
  }, [appOrder]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(DOCK_APP_URLS_KEY, JSON.stringify(dockAppUrls));
  }, [dockAppUrls]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(DESKTOP_BACKDROP_KEY, desktopBackdrop);
  }, [desktopBackdrop]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(QUICK_LINKS_KEY, JSON.stringify(quickLinks));
  }, [quickLinks]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(FOLDERS_KEY, JSON.stringify(folders));
  }, [folders]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem(WIDGETS_KEY, JSON.stringify(widgets));
  }, [widgets]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
      if (e.key === "/" && document.activeElement !== searchInputRef.current) {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
      if (e.key === "Escape") {
        setContextMenu((prev) => ({ ...prev, visible: false }));
        setEnginePickerOpen(false);
        setEditMode(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    if (!enginePickerOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (
        enginePickerRef.current &&
        !enginePickerRef.current.contains(event.target as Node)
      ) {
        setEnginePickerOpen(false);
      }
    };
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [enginePickerOpen]);

  const submitSearch = () => {
    const trimmed = query.trim();
    if (!trimmed) return;
    const engine = selectedSearchEngine;
    if (!engine) return;
    const target = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(trimmed)
      ? trimmed
      : /\s/.test(trimmed) || !trimmed.includes(".")
        ? engine.url + encodeURIComponent(trimmed)
        : `https://${trimmed}`;
    setEnginePickerOpen(false);
    onOpen(target);
  };

  const onSearchKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") submitSearch();
  };

  const startAppDrag = (event: DragEvent<HTMLElement>, url: string) => {
    if (!editMode) return;
    setDraggingUrl(url);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", url);
  };

  const dropDockApp = (event: DragEvent<HTMLElement>, targetUrl: string) => {
    if (!editMode) return;
    event.preventDefault();
    const sourceUrl = draggingUrl || event.dataTransfer.getData("text/plain");
    if (!sourceUrl) return;
    setDockAppUrls((prev) => moveDesktopApp(prev, sourceUrl, targetUrl));
    setDraggingUrl(null);
  };

  const addAppToDock = useCallback((url: string) => {
    setDockAppUrls((prev) =>
      prev.includes(url) ? prev : [...prev, url].slice(0, 12),
    );
  }, []);

  const removeAppFromDock = useCallback((url: string) => {
    setDockAppUrls((prev) => prev.filter((item) => item !== url));
  }, []);

  const { confirm, confirmDialog } = useConfirmDialog();
  const { prompt, promptDialog } = usePromptDialog();

  const resetDesktopLayout = async () => {
    if (
      !(await confirm({
        title: wt.resetLayoutConfirmTitle,
        description: wt.resetLayoutConfirmDescription,
        confirmLabel: wt.resetLayout,
        destructive: false,
      }))
    )
      return;
    setAppOrder(AI_DESKTOP_APPS.map((app) => app.url));
    setDockAppUrls(DEFAULT_DOCK_APP_URLS);
    setDesktopBackdrop(DEFAULT_DESKTOP_BACKDROP);
    setDraggingUrl(null);
  };

  const focusSearchFromPanel = useCallback(() => {
    setActivePanel("home");
    setEnginePickerOpen(false);
    searchInputRef.current?.focus();
  }, []);

  const handleContext = useCallback(
    (
      e: React.MouseEvent,
      id: string | null,
      type: ContextMenuState["targetType"],
    ) => {
      e.preventDefault();
      e.stopPropagation();
      setContextMenu({
        visible: true,
        x: Math.min(e.clientX, window.innerWidth - 180),
        y: Math.min(e.clientY, window.innerHeight - 300),
        targetId: id,
        targetType: type,
      });
    },
    [],
  );

  const handleBackgroundContext = useCallback(
    (e: React.MouseEvent) => {
      handleContext(e, null, "background");
    },
    [handleContext],
  );

  const handleEdit = useCallback(
    async (id: string) => {
      const widget = widgets.find((w) => w.id === id);
      if (widget) {
        setEditWidgetState({
          visible: true,
          widgetId: id,
          title: widget.title,
          type: widget.type,
          size: widget.size,
        });
        return;
      }
      const link = quickLinks.find((item) => item.id === id);
      if (link) {
        const name = await prompt({
          title: wt.promptSiteName,
          defaultValue: link.name,
        });
        if (!name) return;
        const url = await prompt({
          title: wt.promptSiteUrl,
          defaultValue: link.url,
        });
        if (!url) return;
        setQuickLinks((prev) =>
          prev.map((item) =>
            item.id === id
              ? {
                  ...item,
                  name,
                  url: url.startsWith("http") ? url : `https://${url}`,
                }
              : item,
          ),
        );
        return;
      }
      const folder = folders.find((item) => item.id === id);
      if (folder) {
        const name = await prompt({
          title: wt.promptFolderName,
          defaultValue: folder.name,
        });
        if (!name) return;
        setFolders((prev) =>
          prev.map((item) => (item.id === id ? { ...item, name } : item)),
        );
      }
    },
    [
      folders,
      prompt,
      quickLinks,
      widgets,
      wt.promptFolderName,
      wt.promptSiteName,
      wt.promptSiteUrl,
    ],
  );

  const handleSaveWidget = useCallback(() => {
    const { widgetId, title, type, size } = editWidgetState;
    if (!widgetId) return;
    setWidgets((prev) =>
      prev.map((w) => (w.id === widgetId ? { ...w, title, type, size } : w)),
    );
    setEditWidgetState({
      visible: false,
      widgetId: null,
      title: "",
      type: "notes",
      size: "medium",
    });
  }, [editWidgetState]);

  const handleDelete = useCallback(
    async (id: string) => {
      if (
        !(await confirm({
          title: wt.deleteConfirmTitle,
          description: wt.deleteConfirmDescription,
          confirmLabel: wt.ctxDelete,
          destructive: true,
        }))
      )
        return;
      setQuickLinks((prev) => prev.filter((l) => l.id !== id));
      setWidgets((prev) => prev.filter((w) => w.id !== id));
      setFolders((prev) =>
        prev.map((f) => ({
          ...f,
          linkIds: f.linkIds.filter((linkId) => linkId !== id),
        })),
      );
    },
    [confirm, wt.ctxDelete, wt.deleteConfirmDescription, wt.deleteConfirmTitle],
  );

  const handleResize = useCallback((id: string, size: string) => {
    setWidgets((prev) =>
      prev.map((widget) =>
        widget.id === id && ["small", "medium", "large"].includes(size)
          ? { ...widget, size: size as Widget["size"] }
          : widget,
      ),
    );
  }, []);

  const handleAddWidget = useCallback(
    (type: Widget["type"] = "notes", title = wt.newWidget) => {
      const newWidget: Widget = {
        id: `w-${Date.now()}`,
        type,
        size: "medium",
        title,
      };
      setWidgets((prev) => [...prev, newWidget]);
    },
    [wt.newWidget],
  );

  const handleAddIcon = useCallback(async () => {
    const name = await prompt({ title: wt.promptSiteName });
    if (!name) return;
    const url = await prompt({ title: wt.promptSiteUrl });
    if (!url) return;
    const newLink: QuickLink = {
      id: Date.now().toString(),
      name,
      url: url.startsWith("http") ? url : `https://${url}`,
      icon: "Globe",
      category: "tool",
    };
    setQuickLinks((prev) => [...prev, newLink]);
  }, [prompt, wt.promptSiteName, wt.promptSiteUrl]);

  const handleCreateFolder = useCallback(async () => {
    const name = await prompt({ title: wt.promptFolderName });
    if (!name) return;
    const newFolder: UserFolder = {
      id: `folder-${Date.now()}`,
      name,
      linkIds: [],
    };
    setFolders((prev) => [...prev, newFolder]);
  }, [prompt, wt.promptFolderName]);

  const handleWidgetDragStart = useCallback((id: string) => {
    setDragState({
      draggedId: id,
      draggedType: "widget",
      dropTargetId: null,
      dropPosition: null,
    });
  }, []);

  const handleWidgetDragOver = useCallback((e: React.DragEvent, id: string) => {
    e.preventDefault();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    const position = e.clientY < midY ? "before" : "after";
    setDragState((prev) => ({
      ...prev,
      dropTargetId: id,
      dropPosition: position,
    }));
  }, []);

  const handleWidgetDrop = useCallback(
    (targetId: string) => {
      const { draggedId, draggedType, dropPosition } = dragState;
      if (!draggedId || draggedType !== "widget" || draggedId === targetId)
        return;
      setWidgets((prev) => {
        const draggedIndex = prev.findIndex((w) => w.id === draggedId);
        const targetIndex = prev.findIndex((w) => w.id === targetId);
        if (draggedIndex === -1 || targetIndex === -1) return prev;
        const newWidgets = [...prev];
        const [draggedWidget] = newWidgets.splice(draggedIndex, 1);
        const newTargetIndex = newWidgets.findIndex((w) => w.id === targetId);
        const insertIndex =
          dropPosition === "after" ? newTargetIndex + 1 : newTargetIndex;
        if (draggedWidget) newWidgets.splice(insertIndex, 0, draggedWidget);
        return newWidgets;
      });
      setDragState({
        draggedId: null,
        draggedType: null,
        dropTargetId: null,
        dropPosition: null,
      });
    },
    [dragState],
  );

  const handleIconDragStart = useCallback((id: string) => {
    setDragState({
      draggedId: id,
      draggedType: "icon",
      dropTargetId: null,
      dropPosition: null,
    });
  }, []);

  const handleIconDragOver = useCallback((e: React.DragEvent, id: string) => {
    e.preventDefault();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const midX = rect.left + rect.width / 2;
    const position = e.clientX < midX ? "before" : "after";
    setDragState((prev) => ({
      ...prev,
      dropTargetId: id,
      dropPosition: position,
    }));
  }, []);

  const handleIconDrop = useCallback(
    (targetId: string) => {
      const { draggedId, draggedType, dropPosition } = dragState;
      if (!draggedId || draggedType !== "icon" || draggedId === targetId)
        return;
      setQuickLinks((prev) => {
        const standaloneLinks = prev.filter((l) => !l.folderId);
        const draggedIndex = standaloneLinks.findIndex(
          (l) => l.id === draggedId,
        );
        const targetIndex = standaloneLinks.findIndex((l) => l.id === targetId);
        if (draggedIndex === -1 || targetIndex === -1) return prev;
        const newLinks = [...prev];
        const draggedLink = newLinks.find((l) => l.id === draggedId);
        if (!draggedLink) return prev;
        const newStandaloneLinks = newLinks.filter((l) => !l.folderId);
        const [removedDragged] = newStandaloneLinks.splice(draggedIndex, 1);
        const newTargetIndex = newStandaloneLinks.findIndex(
          (l) => l.id === targetId,
        );
        const insertIndex =
          dropPosition === "after" ? newTargetIndex + 1 : newTargetIndex;
        if (removedDragged)
          newStandaloneLinks.splice(insertIndex, 0, removedDragged);
        const folderLinks = newLinks.filter((l) => l.folderId);
        return [...newStandaloneLinks, ...folderLinks];
      });
      setDragState({
        draggedId: null,
        draggedType: null,
        dropTargetId: null,
        dropPosition: null,
      });
    },
    [dragState],
  );

  const handleDropOnFolder = useCallback(
    (folderId: string) => {
      const { draggedId, draggedType } = dragState;
      if (!draggedId || draggedType !== "icon") return;
      setFolders((prev) =>
        prev.map((f) =>
          f.id === folderId
            ? {
                ...f,
                linkIds: f.linkIds.includes(draggedId)
                  ? f.linkIds
                  : [...f.linkIds, draggedId],
              }
            : f,
        ),
      );
      setQuickLinks((prev) =>
        prev.map((l) => (l.id === draggedId ? { ...l, folderId } : l)),
      );
      setDragState({
        draggedId: null,
        draggedType: null,
        dropTargetId: null,
        dropPosition: null,
      });
    },
    [dragState],
  );

  const toggleFolder = useCallback((folderId: string) => {
    setFolderOpenStates((prev) => ({ ...prev, [folderId]: !prev[folderId] }));
  }, []);

  const handleDragEnd = useCallback(() => {
    setDragState({
      draggedId: null,
      draggedType: null,
      dropTargetId: null,
      dropPosition: null,
    });
  }, []);

  const standaloneLinks = quickLinks.filter((l) => !l.folderId);
  const folderLinks = folders.map((folder) => ({
    folder,
    links: quickLinks.filter((l) => folder.linkIds.includes(l.id)),
  }));
  const activeBackdrop = DESKTOP_BACKDROPS[desktopBackdrop];
  const activeBackdropStyle = getBackdropImageStyle(activeBackdrop);

  return (
    <div
      style={
        active
          ? {
              display: "flex",
              width: "100%",
              height: "100%",
              ...activeBackdropStyle,
            }
          : {
              display: "flex",
              position: "absolute",
              width: 0,
              height: 0,
              visibility: "hidden",
              pointerEvents: "none",
            }
      }
      className={cn(
        "relative min-h-0 flex-col overflow-hidden",
        activeBackdrop.className,
      )}
      onContextMenu={handleBackgroundContext}
    >
      <div
        data-testid="desktop-side-rail"
        className={cn(
          "absolute left-3 top-5 z-10 flex h-[calc(100%-2.5rem)] w-10 flex-col items-center rounded-2xl py-2.5 liquid-glass transition-[transform,opacity] duration-200 ease-out",
          !compactDesktop &&
            activePanel === "home" &&
            !editMode &&
            "-translate-x-[42px] opacity-65 after:pointer-events-none after:absolute after:right-1 after:top-1/2 after:h-10 after:w-1 after:-translate-y-1/2 after:rounded-full after:bg-foreground/30 after:content-[''] hover:translate-x-0 hover:opacity-100 hover:after:opacity-0 focus-within:translate-x-0 focus-within:opacity-100 focus-within:after:opacity-0",
          compactDesktop && "left-2 top-4 h-[calc(100%-2rem)]",
          mobileDesktop && "left-1.5 w-9",
        )}
      >
        <button
          type="button"
          onClick={focusSearchFromPanel}
          className={cn(
            "grid size-8 place-items-center rounded-xl text-foreground/80 transition-colors hover:bg-white/12 hover:text-foreground",
            activePanel === "home" && "bg-white/15 text-primary",
          )}
          title={wt.navHome}
        >
          <BotIcon className="size-[18px]" />
        </button>
        <button
          type="button"
          onClick={() => openDesktopApp("echo://workspace/community")}
          className="mt-1 grid size-8 place-items-center rounded-xl text-foreground/60 transition-colors hover:bg-white/12 hover:text-foreground"
          title="发现社区"
        >
          <CompassIcon className="size-[18px]" />
        </button>
        <div className="mt-4 flex flex-col gap-1">
          {DESKTOP_SIDE_NAV.map((item) => {
            const Icon = item.icon;
            const selected = activePanel === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  setActivePanel(item.id);
                }}
                className={cn(
                  "grid size-8 place-items-center rounded-xl text-foreground/60 transition-colors hover:bg-white/12 hover:text-foreground",
                  selected && "bg-white/15 text-primary",
                )}
                title={sideNavLabelMap[item.id]}
              >
                <Icon className="size-[18px]" />
              </button>
            );
          })}
        </div>
        <div className="mt-auto flex flex-col gap-1">
          <button
            type="button"
            onClick={() => setActivePanel("add")}
            className={cn(
              "grid size-8 place-items-center rounded-xl text-foreground/60 transition-colors hover:bg-white/12 hover:text-foreground",
              activePanel === "add" && "bg-white/15 text-primary",
            )}
            title={wt.addTitle}
          >
            <CirclePlusIcon className="size-[18px]" />
          </button>
          <button
            type="button"
            onClick={() => setActivePanel("settings")}
            className={cn(
              "grid size-8 place-items-center rounded-xl text-foreground/60 transition-colors hover:bg-white/12 hover:text-foreground",
              activePanel === "settings" && "bg-white/15 text-primary",
            )}
            title={wt.settingsTitle}
          >
            <SettingsIcon className="size-[18px]" />
          </button>
        </div>
      </div>

      <div
        className={cn(
          "flex h-full min-h-0 flex-col pl-16 pr-10 pt-7",
          compactDesktop && "pl-14 pr-5 pt-5 pb-28",
          tabletDesktop && "pl-16 pr-8",
          mobileDesktop && "pl-12 pr-4 pt-4 pb-24",
        )}
      >
        <div
          ref={enginePickerRef}
          className={cn(
            "relative mx-auto w-full max-w-[720px]",
            compactDesktop && "max-w-none",
            tabletDesktop && "max-w-[760px]",
          )}
        >
          <div
            className={cn(
              "flex h-12 items-center gap-2 rounded-2xl px-3 text-muted-foreground liquid-glass-subtle",
              mobileDesktop && "h-11 rounded-xl px-2.5",
            )}
          >
            <button
              type="button"
              onClick={() => setEnginePickerOpen((value) => !value)}
              aria-haspopup="menu"
              aria-expanded={enginePickerOpen}
              title={bt.switchSearchEngine}
              className={cn(
                "group flex h-8 shrink-0 items-center gap-1 rounded-lg px-1.5 transition-colors hover:bg-foreground/5",
                mobileDesktop && "h-7 px-1",
              )}
            >
              <SearchEngineLogo
                engine={selectedSearchEngine}
                className="size-5"
              />
              <ChevronDownIcon
                className={cn(
                  "size-3 text-muted-foreground/60 transition-transform group-hover:text-muted-foreground",
                  enginePickerOpen && "rotate-180 text-foreground",
                )}
              />
            </button>
            <div className="h-4 w-px bg-white/20" />
            <SearchIcon className="size-4 shrink-0 text-muted-foreground/80" />
            <input
              ref={searchInputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={onSearchKey}
              placeholder={wt.searchPlaceholderFormat(
                selectedSearchEngine?.name ?? wt.searchEngineFallback,
              )}
              className="min-w-0 flex-1 bg-transparent text-[15px] font-medium text-foreground outline-none placeholder:text-muted-foreground/60"
            />
          </div>

          {enginePickerOpen && (
            <div
              role="menu"
              className={cn(
                "absolute left-1 top-[calc(100%+8px)] z-30 w-52 rounded-2xl p-1.5 text-foreground liquid-glass",
              )}
            >
              {engines.map((engine, index) => {
                const active = selectedEngine === index;
                return (
                  <button
                    key={engine.name}
                    type="button"
                    role="menuitemradio"
                    aria-checked={active}
                    onClick={() => {
                      setSelectedEngine(index);
                      setEnginePickerOpen(false);
                      searchInputRef.current?.focus();
                    }}
                    className={cn(
                      "flex h-10 w-full items-center gap-2.5 rounded-xl px-2.5 text-left text-sm font-medium transition-colors",
                      active ? "bg-white/15 text-primary" : "hover:bg-white/10",
                    )}
                  >
                    <SearchEngineLogo engine={engine} className="size-5" />
                    <span className="min-w-0 flex-1 truncate">
                      {engine.name}
                    </span>
                    {active && <CheckIcon className="size-4" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div
          className={cn(
            "absolute right-10 top-7 flex items-center gap-1",
            compactDesktop && "right-5 top-6",
            tabletDesktop && "right-8",
            mobileDesktop && "right-4 top-5",
          )}
        >
          {editMode && (
            <>
              <button
                type="button"
                onClick={resetDesktopLayout}
                className="rounded-lg px-2 py-1 text-mini font-medium text-muted-foreground transition hover:bg-white/10 hover:text-foreground liquid-glass-subtle"
              >
                {wt.resetLayout}
              </button>
              <button
                type="button"
                onClick={handleCreateFolder}
                className="rounded-lg px-2 py-1 text-mini font-medium text-muted-foreground transition hover:bg-white/10 hover:text-foreground liquid-glass-subtle"
              >
                <Folder className="w-3 h-3 inline mr-0.5" />
                {wt.newFolder}
              </button>
              <button
                type="button"
                onClick={handleAddIcon}
                className="rounded-lg px-2 py-1 text-mini font-medium text-muted-foreground transition hover:bg-white/10 hover:text-foreground liquid-glass-subtle"
              >
                <Plus className="w-3 h-3 inline mr-0.5" />
                {wt.addIconBtn}
              </button>
              <button
                type="button"
                onClick={() => handleAddWidget()}
                className="rounded-lg px-2 py-1 text-mini font-medium text-muted-foreground transition hover:bg-white/10 hover:text-foreground liquid-glass-subtle"
              >
                <PanelLeftIcon className="w-3 h-3 inline mr-0.5" />
                {wt.addWidgetBtn}
              </button>
            </>
          )}
          {editMode && (
            <button
              type="button"
              onClick={() => setEditMode(false)}
              className="rounded-lg bg-primary/90 px-2 py-1 text-mini font-medium text-primary-foreground transition hover:bg-primary liquid-glass-subtle"
            >
              {wt.finishEditing}
            </button>
          )}
        </div>

        {editMode && (
          <div
            className={cn(
              "absolute left-16 top-7 rounded-xl px-2 py-1 text-mini font-medium text-foreground/80 liquid-glass-subtle",
              compactDesktop && "left-14 top-[88px]",
              tabletDesktop && "left-16",
              mobileDesktop && "left-12 right-4 text-center",
            )}
          >
            {wt.dragHint}
          </div>
        )}

        <div
          className={cn(
            "grid min-h-0 flex-1 grid-cols-[320px_minmax(380px,1fr)] gap-8 py-10",
            compactDesktop &&
              "grid-cols-1 gap-5 overflow-y-auto pb-8 pt-5 pr-1",
            tabletDesktop &&
              "grid-cols-[260px_minmax(320px,1fr)] gap-5 pb-28 pr-2",
            mobileDesktop && "gap-4 pb-6 pt-4",
          )}
        >
          <div
            className={cn(
              "flex flex-col items-center gap-7",
              compactDesktop && "gap-5",
              tabletDesktop && "justify-start",
            )}
          >
            <div className="text-center">
              <div
                className={cn(
                  "flex overflow-hidden rounded-2xl text-foreground liquid-glass-subtle",
                )}
              >
                <div
                  className={cn(
                    "grid w-[72px] place-items-center border-r border-white/20 px-2.5 py-2 text-center",
                    mobileDesktop && "w-16 px-2 py-2",
                  )}
                >
                  <div
                    className={cn(
                      "text-[28px] font-semibold text-foreground leading-none",
                      mobileDesktop && "text-2xl",
                    )}
                  >
                    {day}
                  </div>
                  <div className="mt-1 text-mini font-medium text-muted-foreground">
                    {week}
                  </div>
                </div>
                <div
                  className={cn(
                    "flex min-w-32 flex-col justify-center px-3.5 text-left",
                    mobileDesktop && "min-w-28 px-3",
                  )}
                >
                  <div
                    className={cn(
                      "text-[14px] font-semibold leading-tight",
                      mobileDesktop && "text-sm",
                    )}
                  >
                    {month}
                  </div>
                  <div className="mt-0.5 text-mini text-muted-foreground">
                    {wt.aiBrowserDesktop}
                  </div>
                </div>
              </div>
            </div>

            <div className="relative">
              <div
                className={cn(
                  "grid grid-cols-2 gap-2.5 p-0.5",
                  mobileDesktop && "gap-2",
                )}
              >
                {desktopAppGroups.map((group) => (
                  <button
                    key={group.id}
                    type="button"
                    onClick={() =>
                      setOpenAppGroupId((current) =>
                        current === group.id ? null : group.id,
                      )
                    }
                    className="group flex min-w-0 flex-col items-center gap-1.5 rounded-2xl p-2 text-center transition hover:bg-white/5 focus:outline-none focus:ring-2 focus:ring-primary/30"
                    title={`${group.title} · ${group.subtitle}`}
                  >
                    <span
                      className={cn(
                        "grid size-[68px] grid-cols-2 gap-1 rounded-3xl p-1.5 transition liquid-glass-icon",
                        mobileDesktop && "size-16",
                      )}
                    >
                      {group.apps.slice(0, 4).map((app) => (
                        <DesktopAppLogo
                          key={app.url}
                          app={app}
                          className="size-full rounded-lg"
                          iconClassName="size-[80%]"
                        />
                      ))}
                    </span>
                    <span className="w-24 truncate text-[12px] font-semibold text-foreground">
                      {group.title}
                    </span>
                  </button>
                ))}
              </div>
              <div className="mt-1.5 text-center text-mini font-medium text-muted-foreground/70">
                {bt.commonCategories}
              </div>

              {desktopAppGroups.map((group) =>
                openAppGroupId === group.id ? (
                  <div
                    key={group.id}
                    className={cn(
                      "absolute left-1/2 top-[calc(100%+8px)] z-20 w-72 -translate-x-1/2 rounded-2xl p-2.5 text-foreground liquid-glass",
                    )}
                  >
                    <div className="mb-1.5 flex items-center justify-between px-1">
                      <div>
                        <div className="text-sm font-semibold">
                          {group.title}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {group.subtitle}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => setOpenAppGroupId(null)}
                        className="grid size-7 place-items-center rounded-lg text-muted-foreground/70 transition hover:bg-foreground/5 hover:text-foreground"
                      >
                        <X className="size-4" />
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-1.5">
                      {group.apps.map((app) => (
                        <button
                          key={app.url}
                          type="button"
                          onClick={() => {
                            setOpenAppGroupId(null);
                            openDesktopApp(app.url);
                          }}
                          className="group flex items-center gap-2 rounded-lg p-2 text-left transition hover:bg-foreground/5"
                          title={`${getAppName(app.name)} · ${getAppDesc(app.description)}`}
                        >
                          <DesktopAppLogo
                            app={app}
                            className="size-8 rounded-lg"
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-semibold">
                              {getAppName(app.name)}
                            </span>
                            <span className="block truncate text-mini text-muted-foreground">
                              {getAppDesc(app.description)}
                            </span>
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null,
              )}
            </div>
          </div>

          <div className={cn("flex flex-col gap-7", compactDesktop && "gap-5")}>
            <section
              className={cn(
                "w-[340px] max-w-[calc(100vw-1rem)] self-end rounded-2xl p-3.5 text-foreground liquid-glass",
                compactDesktop && "w-full self-stretch",
                mobileDesktop && "p-3",
              )}
            >
              <div className="mb-2.5 flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <div className="grid size-7 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                    <ServerIcon className="size-[15px]" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-[13px] font-semibold text-foreground">
                      {bp.localServices}
                    </div>
                    <div className="text-mini text-muted-foreground">
                      localhost
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={scanLocalServices}
                  disabled={scanningLocalServices}
                  className="grid size-7 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground disabled:opacity-50"
                  title={bp.scanLocalServices}
                  aria-label={bp.scanLocalServices}
                >
                  {scanningLocalServices ? (
                    <Loader2Icon className="size-[15px] animate-spin" />
                  ) : (
                    <RefreshCwIcon className="size-[15px]" />
                  )}
                </button>
              </div>
              {localServices.length > 0 ? (
                <div className="grid grid-cols-2 gap-1.5">
                  {localServices.slice(0, 6).map((service) => (
                    <button
                      key={service.port}
                      type="button"
                      onClick={() => onOpen(service.url)}
                      className="group flex min-w-0 items-center gap-2 rounded-lg border border-border-subtle/60 px-2 py-2 text-left transition-colors hover:border-primary/25 hover:bg-primary/5"
                      title={`${service.name} · ${service.url}`}
                    >
                      <span
                        className={cn(
                          "size-2 shrink-0 rounded-full",
                          service.type === "frontend"
                            ? "bg-primary"
                            : service.type === "backend"
                              ? "bg-success"
                              : "bg-muted-foreground",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium text-foreground">
                          {service.name}
                        </span>
                        <span className="block truncate text-micro text-muted-foreground">
                          localhost:{service.port}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-border-subtle/60 px-3 py-4 text-center text-mini text-muted-foreground/70">
                  {scanningLocalServices
                    ? bp.scanLocalServices
                    : bp.noLocalServices}
                </div>
              )}
            </section>

            <section
              className={cn(
                "w-[340px] max-w-[calc(100vw-1rem)] self-end rounded-2xl p-3.5 text-foreground liquid-glass",
                compactDesktop && "w-full self-stretch",
                mobileDesktop && "p-3",
              )}
            >
              <div className="mb-2.5 flex items-center justify-between">
                <div>
                  <div className="text-[13px] font-semibold text-foreground">
                    {bt.recentVisits}
                  </div>
                  <div className="text-mini text-muted-foreground">
                    {recentPanelItems.length > 0
                      ? bt.recentVisitCount(recentPanelItems.length)
                      : bt.noRecentVisits}
                  </div>
                </div>
                <div
                  className={cn(
                    "grid size-7 place-items-center rounded-lg text-muted-foreground/70",
                  )}
                  title={bt.historyOnly}
                >
                  <Clock3Icon className="size-[15px]" />
                </div>
              </div>
              {recentPanelItems.length > 0 && (
                <div className="grid grid-cols-2 gap-1.5">
                  {recentPanelItems.map(renderCompactLink)}
                </div>
              )}
              {recentPanelItems.length === 0 && (
                <div className="rounded-lg border border-border-subtle/60 px-3 py-4 text-center text-mini text-muted-foreground/70">
                  {bt.noRecentVisits}
                </div>
              )}
            </section>

            {widgets.length > 0 && (
              <div className="grid grid-cols-2 gap-3">
                {widgets.map((widget) => {
                  const isDragging = dragState.draggedId === widget.id;
                  const isDropTarget = dragState.dropTargetId === widget.id;
                  return (
                    <div
                      key={widget.id}
                      className={cn(
                        "group/widget rounded-2xl p-4 transition-all liquid-glass-subtle",
                        widget.size === "large" && "col-span-2",
                        widget.size === "small" && "p-3",
                        isDragging && "opacity-40 scale-95",
                        isDropTarget && "ring-2 ring-primary",
                      )}
                      draggable
                      onDragStart={() => handleWidgetDragStart(widget.id)}
                      onDragOver={(e) => handleWidgetDragOver(e, widget.id)}
                      onDrop={() => handleWidgetDrop(widget.id)}
                      onDragEnd={handleDragEnd}
                      onContextMenu={(e) =>
                        handleContext(e, widget.id, "widget")
                      }
                    >
                      <div className="flex items-center justify-between mb-2.5">
                        <span className="text-[15px] font-semibold text-foreground">
                          {widget.title}
                        </span>
                        <div className="flex items-center gap-1">
                          {editMode && (
                            <button
                              type="button"
                              onClick={() => handleEdit(widget.id)}
                              className="grid size-7 place-items-center rounded-lg text-muted-foreground/60 transition hover:bg-foreground/5 hover:text-muted-foreground"
                              title={wt.ctxEditWidget}
                              aria-label={wt.ctxEditWidget}
                            >
                              <Edit3 className="w-3.5 h-3.5" />
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={(event) =>
                              handleContext(event, widget.id, "widget")
                            }
                            className="grid size-7 place-items-center rounded-lg text-muted-foreground/55 opacity-60 transition hover:bg-foreground/5 hover:text-foreground focus-visible:opacity-100 group-hover/widget:opacity-100"
                            title={`${wt.ctxEditWidget} / ${wt.ctxDelete}`}
                            aria-label={`${wt.ctxEditWidget} / ${wt.ctxDelete}`}
                          >
                            <EllipsisIcon className="size-4" />
                          </button>
                        </div>
                      </div>
                      <div className="text-sm text-muted-foreground">
                        {widget.type === "notes" && (
                          <div className="space-y-1.5">
                            <div className="rounded-lg border border-border-subtle/60 p-2">
                              <p className="text-mini text-muted-foreground/80">
                                {bt.todoPlaceholder}
                              </p>
                            </div>
                          </div>
                        )}
                        {widget.type === "weather" && (
                          <div className="flex items-center gap-2">
                            <span className="text-xl">☀️</span>
                            <span className="text-lg text-muted-foreground">
                              26°C
                            </span>
                          </div>
                        )}
                        {widget.type === "system" && (
                          <div className="space-y-1.5">
                            <div className="h-1.5 rounded-full bg-muted-foreground/12">
                              <div className="h-full bg-primary rounded-full w-[23%]" />
                            </div>
                            <div className="h-1.5 rounded-full bg-muted-foreground/12">
                              <div className="h-full bg-accent-foreground/60 rounded-full w-[45%]" />
                            </div>
                          </div>
                        )}
                        {widget.type === "ai-tools" && (
                          <div className="grid grid-cols-3 gap-1.5">
                            {["ChatGPT", "Claude", "Gemini"].map((name) => (
                              <div
                                key={name}
                                className="rounded-lg border border-border-subtle/60 p-1.5 text-center text-mini text-muted-foreground/80"
                              >
                                {name}
                              </div>
                            ))}
                          </div>
                        )}
                        {widget.type === "calendar" && (
                          <div className="text-center">
                            <div className="text-2xl font-semibold text-foreground">
                              {day}
                            </div>
                            <div className="text-[12px] text-muted-foreground">
                              {month}
                            </div>
                          </div>
                        )}
                        {widget.type === "bookmarks" && (
                          <div className="space-y-1">
                            {["GitHub", "StackOverflow", "MDN"].map((name) => (
                              <div
                                key={name}
                                className="rounded-lg border border-border-subtle/60 p-1 text-mini text-muted-foreground/80"
                              >
                                {name}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            <div>
              <div
                className={cn(
                  "grid grid-cols-4 gap-5",
                  compactDesktop && "grid-cols-3 gap-4",
                  mobileDesktop && "grid-cols-2 gap-3",
                )}
              >
                {standaloneLinks.map((link) => {
                  const isDragging = dragState.draggedId === link.id;
                  const isDropTarget = dragState.dropTargetId === link.id;
                  return (
                    <div
                      key={link.id}
                      className={cn(
                        "group relative",
                        isDragging && "opacity-40 scale-95",
                      )}
                    >
                      <button
                        draggable
                        onClick={() => onOpen(link.url)}
                        className={cn(
                          "w-full flex flex-col items-center gap-1.5 p-2 rounded-xl transition-all",
                          editMode
                            ? "hover:bg-card/70 cursor-move"
                            : "hover:bg-card/70",
                          isDropTarget && "ring-2 ring-primary",
                        )}
                        onDragStart={() => handleIconDragStart(link.id)}
                        onDragOver={(e) => handleIconDragOver(e, link.id)}
                        onDrop={() => handleIconDrop(link.id)}
                        onDragEnd={handleDragEnd}
                        onContextMenu={(e) => handleContext(e, link.id, "icon")}
                      >
                        <div className="relative w-12 h-12 rounded-xl liquid-glass-icon flex items-center justify-center text-muted-foreground overflow-hidden">
                          <span className="text-lg font-bold">
                            {link.name[0]}
                          </span>
                          {faviconForUrl(link.url) && (
                            <img
                              src={faviconForUrl(link.url)}
                              alt=""
                              className="absolute inset-0 m-auto size-[60%] object-contain"
                              onError={(event) => {
                                event.currentTarget.style.display = "none";
                              }}
                            />
                          )}
                        </div>
                        <span className="text-mini text-muted-foreground/80 truncate w-full text-center">
                          {link.name}
                        </span>
                      </button>
                      {editMode && (
                        <button
                          onClick={() => handleDelete(link.id)}
                          className="absolute -top-1 -right-1 size-[18px] bg-destructive rounded-full flex items-center justify-center shadow-[var(--shadow-md)]"
                        >
                          <X className="size-2.5 text-white" />
                        </button>
                      )}
                    </div>
                  );
                })}

                {folderLinks.map(({ folder, links }) => {
                  const isOpen = folderOpenStates[folder.id];
                  const isDropTarget = dragState.dropTargetId === folder.id;
                  return (
                    <div key={folder.id} className="group relative">
                      <button
                        className={cn(
                          "w-full flex flex-col items-center gap-1.5 p-2 rounded-2xl transition-all hover:bg-white/5",
                          isDropTarget && "ring-2 ring-primary",
                        )}
                        onContextMenu={(e) =>
                          handleContext(e, folder.id, "folder")
                        }
                        onDragOver={(e) => {
                          e.preventDefault();
                          setDragState((prev) => ({
                            ...prev,
                            dropTargetId: folder.id,
                            dropPosition: null,
                          }));
                        }}
                        onDrop={() => handleDropOnFolder(folder.id)}
                        onDragLeave={() => {
                          setDragState((prev) =>
                            prev.dropTargetId === folder.id
                              ? { ...prev, dropTargetId: null }
                              : prev,
                          );
                        }}
                        onClick={() => toggleFolder(folder.id)}
                      >
                        <div className="w-12 h-12 rounded-xl liquid-glass-icon flex items-center justify-center relative">
                          {isOpen ? (
                            <FolderOpen className="w-5 h-5 text-foreground" />
                          ) : (
                            <Folder className="w-5 h-5 text-muted-foreground" />
                          )}
                          <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 bg-primary rounded-full flex items-center justify-center text-[9px] text-primary-foreground font-bold">
                            {links.length}
                          </span>
                        </div>
                        <span className="text-mini text-muted-foreground/80 truncate w-full text-center">
                          {folder.name}
                        </span>
                      </button>
                      {isOpen && (
                        <div className="absolute z-10 mt-1.5 p-2.5 text-foreground rounded-2xl liquid-glass min-w-[190px]">
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[13px] font-medium text-foreground">
                              {folder.name}
                            </span>
                            <button
                              type="button"
                              aria-label={t.browser.closeFolderAria(
                                folder.name,
                              )}
                              onClick={() => toggleFolder(folder.id)}
                            >
                              <X className="w-3.5 h-3.5 text-muted-foreground/60 hover:text-muted-foreground" />
                            </button>
                          </div>
                          <div className="grid grid-cols-3 gap-1.5">
                            {links.map((link) => (
                              <button
                                key={link.id}
                                onClick={() => onOpen(link.url)}
                                className="flex flex-col items-center gap-1 p-1.5 rounded-lg hover:bg-white/10 transition-colors"
                              >
                                <span className="relative grid size-6 place-items-center">
                                  <span className="text-base font-bold text-muted-foreground">
                                    {link.name[0]}
                                  </span>
                                  {faviconForUrl(link.url) && (
                                    <img
                                      src={faviconForUrl(link.url)}
                                      alt=""
                                      className="absolute inset-0 m-auto size-[18px] object-contain"
                                      onError={(event) => {
                                        event.currentTarget.style.display =
                                          "none";
                                      }}
                                    />
                                  )}
                                </span>
                                <span className="text-micro text-muted-foreground/80 truncate w-full text-center">
                                  {link.name}
                                </span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        <div
          className={cn(
            "mx-auto mb-6 flex h-16 max-w-[760px] items-center gap-3 overflow-hidden rounded-2xl px-3 py-1.5 liquid-glass-dock",
            compactDesktop &&
              "absolute bottom-5 left-14 right-5 z-10 mx-0 mb-0 h-14 justify-start gap-2.5 overflow-x-auto rounded-2xl px-3 py-2",
            tabletDesktop &&
              "left-16 right-8 justify-center gap-3 rounded-2xl px-4",
            mobileDesktop && "bottom-4 left-12 right-4 gap-2 h-12 px-2.5",
          )}
        >
          {visibleDockApps.map((app) => (
            <div key={app.url} className="group/dock relative shrink-0">
              <button
                key={app.url}
                type="button"
                draggable={editMode}
                onClick={() => {
                  if (!editMode) openDesktopApp(app.url);
                }}
                onDragStart={(event) => startAppDrag(event, app.url)}
                onDragOver={(event) => {
                  if (editMode) event.preventDefault();
                }}
                onDrop={(event) => dropDockApp(event, app.url)}
                onDragEnd={() => setDraggingUrl(null)}
                className={cn(
                  "group grid size-12 place-items-center rounded-xl transition liquid-glass-icon hover:bg-white/10",
                  compactDesktop && "size-11 shrink-0 rounded-xl",
                  tabletDesktop && "size-11",
                  mobileDesktop && "size-10 rounded-xl",
                  editMode && "cursor-move ring-2 ring-primary/50",
                  draggingUrl === app.url && "scale-95 opacity-45",
                )}
                title={`${getAppName(app.name)} · ${getAppDesc(app.description)}`}
              >
                <DesktopAppLogo
                  app={app}
                  className="size-full rounded-xl"
                  iconClassName={cn(
                    "size-[58%]",
                    compactDesktop && "size-[56%]",
                    mobileDesktop && "size-[54%]",
                  )}
                />
              </button>
              {editMode && (
                <button
                  type="button"
                  onClick={() => removeAppFromDock(app.url)}
                  className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full bg-destructive text-white shadow-[var(--shadow-md)] transition hover:bg-destructive"
                  title={bt.removeFromDock}
                >
                  <X className="size-3" />
                </button>
              )}
            </div>
          ))}
          {editMode && (
            <button
              type="button"
              onClick={() => setActivePanel("add")}
              className={cn(
                "grid size-12 shrink-0 place-items-center rounded-lg border-dashed border border-border-subtle/70 text-muted-foreground/70 transition",
                "bg-card/50 hover:bg-card/70 hover:text-muted-foreground",
                compactDesktop && "size-11 rounded-lg",
                tabletDesktop && "size-11",
                mobileDesktop && "size-10 rounded-lg",
              )}
              title={bt.addToDock}
            >
              <Plus className="size-5" />
            </button>
          )}
        </div>
      </div>

      <ContextMenu
        state={contextMenu}
        onClose={() => setContextMenu((prev) => ({ ...prev, visible: false }))}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onResize={handleResize}
        onEditHome={() => setEditMode(true)}
        onAddWidget={() => handleAddWidget()}
        onAddIcon={handleAddIcon}
        onOpenSettings={() => setActivePanel("settings")}
        currentSize={
          contextMenu.targetType === "widget"
            ? widgets.find((widget) => widget.id === contextMenu.targetId)?.size
            : undefined
        }
      />

      {confirmDialog}
      {promptDialog}

      {editWidgetState.visible && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="rounded-2xl p-6 w-full max-w-sm liquid-glass">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-lg font-medium text-foreground">
                {wt.editWidgetDialogTitle}
              </h3>
              <button
                onClick={() =>
                  setEditWidgetState({
                    visible: false,
                    widgetId: null,
                    title: "",
                    type: "notes",
                    size: "medium",
                  })
                }
              >
                <X className="w-5 h-5 text-muted-foreground/70 hover:text-muted-foreground" />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm text-muted-foreground mb-2">
                  {wt.editWidgetTitleLabel}
                </label>
                <input
                  type="text"
                  value={editWidgetState.title}
                  onChange={(e) =>
                    setEditWidgetState((prev) => ({
                      ...prev,
                      title: e.target.value,
                    }))
                  }
                  className="w-full bg-input border border-border-subtle rounded-md px-4 py-2 text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  placeholder={wt.editWidgetTitlePlaceholder}
                />
              </div>
              <div>
                <label className="block text-sm text-muted-foreground mb-2">
                  {wt.editWidgetTypeLabel}
                </label>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    {
                      type: "weather" as const,
                      label: wt.editWidgetTypeWeather,
                    },
                    {
                      type: "calendar" as const,
                      label: wt.editWidgetTypeCalendar,
                    },
                    { type: "notes" as const, label: wt.editWidgetTypeNotes },
                    { type: "system" as const, label: wt.editWidgetTypeSystem },
                    {
                      type: "ai-tools" as const,
                      label: wt.editWidgetTypeAiTools,
                    },
                    {
                      type: "bookmarks" as const,
                      label: wt.editWidgetTypeBookmarks,
                    },
                  ].map(({ type, label }) => (
                    <button
                      key={type}
                      onClick={() =>
                        setEditWidgetState((prev) => ({ ...prev, type }))
                      }
                      className={cn(
                        "flex flex-col items-center gap-1 p-2 rounded-md border transition-all",
                        editWidgetState.type === type
                          ? "bg-accent border-primary text-foreground"
                          : "bg-card/60 border-border-subtle text-muted-foreground hover:bg-card",
                      )}
                    >
                      <span className="text-xs">{label}</span>
                    </button>
                  ))}
                </div>
              </div>
              <button
                onClick={handleSaveWidget}
                className="w-full py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors font-medium"
              >
                {wt.editWidgetSave}
              </button>
            </div>
          </div>
        </div>
      )}

      {activePanel !== "home" && (
        <DesktopControlPanel
          apps={visibleDesktopApps}
          panel={activePanel}
          onClose={() => setActivePanel("home")}
          onOpen={openDesktopApp}
          dockAppUrls={dockAppUrls}
          onAddToDock={addAppToDock}
          workspaceShortcutUrls={workspaceShortcutUrls}
          onToggleWorkspaceShortcut={toggleWorkspaceShortcut}
          selectedBackdrop={desktopBackdrop}
          onSelectBackdrop={setDesktopBackdrop}
          widgets={widgets}
          onAddWidget={handleAddWidget}
          onFocusSearch={focusSearchFromPanel}
          onResetLayout={resetDesktopLayout}
          onToggleEditMode={() => {
            setEditMode((value) => !value);
            setActivePanel("home");
          }}
        />
      )}
    </div>
  );
}

function DesktopControlPanel({
  apps,
  panel,
  onClose,
  onOpen,
  dockAppUrls,
  onAddToDock,
  workspaceShortcutUrls,
  onToggleWorkspaceShortcut,
  selectedBackdrop,
  onSelectBackdrop,
  widgets,
  onAddWidget,
  onFocusSearch,
  onResetLayout,
  onToggleEditMode,
}: {
  apps: readonly BrowserDesktopApp[];
  panel: DesktopPanelId;
  onClose: () => void;
  onOpen: (url: string) => void;
  dockAppUrls: string[];
  onAddToDock: (url: string) => void;
  workspaceShortcutUrls: readonly string[];
  onToggleWorkspaceShortcut: (url: string) => void;
  selectedBackdrop: DesktopBackdropId;
  onSelectBackdrop: (id: DesktopBackdropId) => void;
  widgets: Widget[];
  onAddWidget: (type?: Widget["type"], title?: string) => void;
  onFocusSearch: () => void;
  onResetLayout: () => void;
  onToggleEditMode: () => void;
}) {
  const { t } = useI18n();
  const wt = t.browser.webviewTab;
  const bt = t.browserHome;

  const title =
    panel === "theme"
      ? wt.panelTitleTheme
      : panel === "widgets"
        ? wt.panelTitleWidgets
        : panel === "wallpaper"
          ? wt.panelTitleWallpaper
          : panel === "games"
            ? wt.panelTitleGames
            : panel === "add"
              ? wt.panelTitleAddApp
              : wt.panelTitleDesktopSettings;

  const appNameMap = useMemo<Record<string, string>>(
    () => ({
      Doubao: bt.appNameDoubao,
      "Tongyi Qianwen": bt.appNameTongyiQianwen,
      "Wenxin Yiyan": bt.appNameWenxinYiyan,
      "Tencent Yuanbao": bt.appNameTencentYuanbao,
      Zhihu: bt.appNameZhihu,
    }),
    [bt],
  );

  const getAppName = useCallback(
    (name: string) => appNameMap[name] ?? name,
    [appNameMap],
  );
  const countWidgetsByType = useCallback(
    (type: Widget["type"]) =>
      widgets.filter((widget) => widget.type === type).length,
    [widgets],
  );
  const runSettingAction = useCallback(
    (index: number) => {
      if (index === 0) {
        onFocusSearch();
        return;
      }
      if (index === 1) {
        onToggleEditMode();
        return;
      }
      if (index === 2) {
        onClose();
        onOpen("echo://home");
        return;
      }
      onResetLayout();
    },
    [onClose, onFocusSearch, onOpen, onResetLayout, onToggleEditMode],
  );

  return (
    <div
      className={cn(
        "absolute bottom-7 left-16 top-8 z-20 w-[360px] max-w-[calc(100vw-1rem)] overflow-hidden rounded-2xl text-foreground liquid-glass",
      )}
    >
      <div className="flex items-center justify-between border-b border-white/15 px-5 py-4">
        <div>
          <div className="text-base font-semibold">{title}</div>
          <div className="text-xs text-muted-foreground">
            {wt.panelSubtitle}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-3 py-1 text-xs font-medium text-muted-foreground transition hover:bg-white/10 hover:text-foreground liquid-glass-subtle"
        >
          {wt.panelClose}
        </button>
      </div>
      <div className="max-h-[calc(100%-72px)] overflow-y-auto p-5">
        {panel === "theme" && (
          <div className="space-y-4">
            {DESKTOP_THEME_BACKDROPS.map((backdropId, index) => {
              const active = selectedBackdrop === backdropId;
              const backdrop = DESKTOP_BACKDROPS[backdropId];
              return (
                <button
                  key={backdropId}
                  type="button"
                  onClick={() => onSelectBackdrop(backdropId)}
                  aria-pressed={active}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-xl p-3 text-left transition hover:bg-white/8",
                    active ? "bg-white/12 ring-1 ring-primary/40" : "",
                  )}
                >
                  <span
                    style={getBackdropImageStyle(
                      backdrop,
                      "linear-gradient(180deg,rgba(15,23,42,0.04),rgba(15,23,42,0.1))",
                    )}
                    className={cn(
                      "size-10 rounded-lg shadow-inner",
                      backdrop.swatchClassName,
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold">
                      {wt.themeNames[index]}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {wt.themeDescs[index]}
                    </span>
                  </span>
                  {active && <CheckIcon className="size-4 text-primary" />}
                </button>
              );
            })}
          </div>
        )}
        {panel === "widgets" && (
          <div className="space-y-3">
            {[CalendarDaysIcon, FileTextIcon, Clock3Icon, LayoutGridIcon].map(
              (Icon, index) => {
                const widgetType = WIDGET_PANEL_TYPES[index] ?? "notes";
                const count = countWidgetsByType(widgetType);
                return (
                  <button
                    key={wt.widgetPanelNames[index]}
                    type="button"
                    onClick={() =>
                      onAddWidget(widgetType, wt.widgetPanelNames[index])
                    }
                    className="flex items-center gap-3 rounded-xl p-3 transition hover:bg-white/8"
                  >
                    <span className="grid size-10 place-items-center rounded-xl bg-primary/90 text-primary-foreground">
                      <Icon className="size-5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-semibold">
                        {wt.widgetPanelNames[index]}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        {wt.widgetPanelDescs[index]}
                      </div>
                    </div>
                    <span className="rounded-lg bg-primary/15 px-2 py-1 text-micro font-medium text-primary">
                      {count > 0 ? `${wt.widgetEnabled} ${count}` : bt.add}
                    </span>
                  </button>
                );
              },
            )}
          </div>
        )}
        {panel === "wallpaper" && (
          <div className="grid grid-cols-2 gap-3">
            {DESKTOP_WALLPAPER_BACKDROPS.map((backdropId, index) => {
              const backdrop = DESKTOP_BACKDROPS[backdropId];
              return (
                <button
                  key={backdropId}
                  type="button"
                  onClick={() => onSelectBackdrop(backdropId)}
                  aria-pressed={selectedBackdrop === backdropId}
                  style={getBackdropImageStyle(
                    backdrop,
                    "linear-gradient(180deg,rgba(15,23,42,0.02),rgba(15,23,42,0.12))",
                  )}
                  className={cn(
                    "relative h-24 rounded-xl border border-white/20 transition overflow-hidden",
                    backdrop.swatchClassName,
                    selectedBackdrop === backdropId &&
                      "ring-2 ring-primary ring-offset-2 ring-offset-transparent",
                  )}
                  title={wt.wallpaperTitle(index + 1)}
                >
                  {selectedBackdrop === backdropId && (
                    <span className="absolute right-3 top-3 grid size-6 place-items-center rounded-full bg-white/90 text-primary shadow-lg">
                      <CheckIcon className="size-3.5" />
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}
        {panel === "games" && (
          <div className="space-y-3">
            {wt.gameNames.map((name, index) => {
              const Icon = index === 0 ? Gamepad2Icon : SparklesIcon;
              const url = GAME_PANEL_URLS[index] ?? GAME_PANEL_URLS[0]!;
              return (
                <button
                  key={name}
                  type="button"
                  className="flex w-full items-center gap-3 rounded-xl p-3 text-left transition hover:bg-white/8"
                  onClick={() => onOpen(url)}
                >
                  <span className="grid size-10 place-items-center rounded-xl bg-primary/90 text-primary-foreground">
                    <Icon className="size-5" />
                  </span>
                  <span className="text-sm font-semibold">{name}</span>
                </button>
              );
            })}
          </div>
        )}
        {panel === "add" && (
          <div className="space-y-3">
            {apps.map((app) => {
              const inDock = dockAppUrls.includes(app.url);
              const inWorkspaceSidebar = workspaceShortcutUrls.includes(
                app.url,
              );
              return (
                <div
                  key={app.url}
                  className="flex w-full items-center gap-3 rounded-xl p-3 text-left transition hover:bg-white/8"
                >
                  <button
                    type="button"
                    onClick={() => onOpen(app.url)}
                    className="flex min-w-0 flex-1 items-center gap-3 text-left"
                  >
                    <DesktopAppLogo app={app} className="size-10 rounded-xl" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold">
                        {getAppName(app.name)}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {app.url}
                      </span>
                    </span>
                  </button>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {!app.workspaceRoute && (
                      <button
                        type="button"
                        onClick={() => onToggleWorkspaceShortcut(app.url)}
                        className={cn(
                          "rounded-xl px-3 py-1.5 text-xs font-semibold transition liquid-glass-subtle",
                          inWorkspaceSidebar &&
                            "bg-primary/12 text-primary ring-1 ring-primary/20",
                        )}
                      >
                        {inWorkspaceSidebar ? "移出侧栏" : "加入侧栏"}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={inDock}
                      onClick={() => onAddToDock(app.url)}
                      className={cn(
                        "rounded-xl px-3 py-1.5 text-xs font-semibold transition liquid-glass-subtle",
                        inDock
                          ? "opacity-50"
                          : "bg-primary/90 text-primary-foreground hover:bg-primary",
                      )}
                    >
                      {inDock ? bt.alreadyInDock : bt.add}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {panel === "settings" && (
          <div className="space-y-3">
            {wt.settingNames.map((name, index) => (
              <button
                key={name}
                type="button"
                onClick={() => runSettingAction(index)}
                className="w-full rounded-xl p-3 text-left transition hover:bg-white/8"
              >
                <div className="flex items-start gap-3 text-left">
                  <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/90 text-primary-foreground">
                    {index === 0 ? (
                      <SearchIcon className="size-4" />
                    ) : index === 1 ? (
                      <LayoutGridIcon className="size-4" />
                    ) : index === 2 ? (
                      <HomeIcon className="size-4" />
                    ) : (
                      <CheckIcon className="size-4" />
                    )}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold">{name}</span>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {wt.settingDescs[index]}
                    </span>
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
