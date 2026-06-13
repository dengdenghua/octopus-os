import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  AppWindowIcon,
  ArchiveIcon,
  BatteryFullIcon,
  BellIcon,
  BotIcon,
  CpuIcon,
  ExternalLinkIcon,
  FileTextIcon,
  FolderIcon,
  FolderInputIcon,
  GlobeIcon,
  HardDriveIcon,
  ImageIcon,
  Loader2Icon,
  MonitorIcon,
  RotateCcwIcon,
  SearchIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  StoreIcon,
  TerminalSquareIcon,
  Trash2Icon,
  UserCircleIcon,
  WifiIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { useDebounce } from "@/hooks";
import type { NativeDesktopItem } from "@/types/electron";
import {
  appOpenUrl,
  startApplianceApp,
  useApplianceApps,
  type ApplianceApp,
} from "@/appliance/apps";
import { Dock, DockItem } from "@/appliance/dock";
import { fetchApplianceAuthStatus } from "@/appliance/auth";
import { ApplianceLogin } from "@/appliance/login";
import { FileManager } from "@/appliance/file-manager";
import { AppWindow, type DesktopWindow } from "@/appliance/app-window";
import {
  AGENT_WORKSPACE_FALLBACK_ROUTE,
  AGENT_WORKSPACE_WINDOW_ID,
  resolveAgentWorkspaceUrl,
} from "@/appliance/agent-workspace";

type DesktopApp = {
  name: string;
  subtitle: string;
  route: string;
  icon: typeof SparklesIcon;
  color: string;
};

type DesktopCategory = {
  key: "all" | "folder" | "app" | "image" | "document" | "package" | "other";
  label: string;
};

const DESKTOP_APPS: DesktopApp[] = [
  {
    name: "工作台",
    subtitle: "对话、编程、项目",
    route: "/workspace/realtime/new",
    icon: MonitorIcon,
    color: "from-sky-500 to-blue-600",
  },
  {
    name: "AI 浏览器",
    subtitle: "浏览、调研、自动化",
    route: "/browser",
    icon: GlobeIcon,
    color: "from-indigo-500 to-cyan-500",
  },
  {
    name: "本地文件",
    subtitle: "工作区与资料",
    route: "/workspace/knowledge",
    icon: FolderIcon,
    color: "from-amber-400 to-orange-500",
  },
  {
    name: "本地应用",
    subtitle: "应用快捷入口",
    route: "/workspace/store",
    icon: AppWindowIcon,
    color: "from-violet-500 to-fuchsia-500",
  },
  {
    name: "终端日志",
    subtitle: "运行状态",
    route: "/workspace/observability",
    icon: TerminalSquareIcon,
    color: "from-slate-700 to-slate-500",
  },
  {
    name: "设置",
    subtitle: "账号、模型、权限",
    route: "/workspace",
    icon: SettingsIcon,
    color: "from-stone-500 to-neutral-700",
  },
];

const DOCK_APPS = DESKTOP_APPS.slice(0, 3);
const LOCAL_APP_PLACEHOLDERS: DesktopApp[] = [
  {
    name: "浏览器",
    subtitle: "本地应用占位",
    route: "/workspace/store",
    icon: GlobeIcon,
    color: "from-white to-slate-100 text-slate-700",
  },
  {
    name: "沟通",
    subtitle: "本地应用占位",
    route: "/workspace/store",
    icon: AppWindowIcon,
    color: "from-white to-slate-100 text-slate-700",
  },
  {
    name: "笔记",
    subtitle: "本地应用占位",
    route: "/workspace/store",
    icon: FileTextIcon,
    color: "from-white to-slate-100 text-slate-700",
  },
];
const DESKTOP_CATEGORIES: DesktopCategory[] = [
  { key: "all", label: "全部" },
  { key: "folder", label: "文件夹" },
  { key: "app", label: "应用" },
  { key: "image", label: "图片" },
  { key: "document", label: "文档" },
  { key: "package", label: "安装包" },
  { key: "other", label: "其他" },
];

const IMAGE_EXTENSIONS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "bmp",
  "svg",
  "ico",
]);
const DOCUMENT_EXTENSIONS = new Set([
  "txt",
  "md",
  "pdf",
  "doc",
  "docx",
  "xls",
  "xlsx",
  "ppt",
  "pptx",
  "csv",
]);
const PACKAGE_EXTENSIONS = new Set([
  "zip",
  "rar",
  "7z",
  "tar",
  "gz",
  "exe",
  "msi",
  "dmg",
  "pkg",
]);
const ARCHIVE_FOLDER_MAP: Record<string, string> = {
  image: "图片",
  document: "文档",
  package: "安装包",
  other: "其他",
};
const DESKTOP_ORGANIZER_ENABLED_KEY = "octopus:desktop-organizer-enabled";

// Octopus OS 原生路线:桌面即系统主页 —— 默认进入、不透明、自带壁纸+启动器。
// 母体 octopus-agent 走寄生路线(透明叠加真实桌面的整理工具,类比腾讯/360
// 桌面助手),那里此常量为 false:保留 opt-in 门、desktop-overlay 透明类与
// 鼠标穿透。把差异收敛到这一个常量,从母体合并更新时冲突面最小。
const IS_NATIVE_DESKTOP = true;

function getDesktopItemCategory(
  item: NativeDesktopItem,
): DesktopCategory["key"] {
  if (item.kind === "folder") return "folder";
  if (item.kind === "app") return "app";
  if (IMAGE_EXTENSIONS.has(item.extension)) return "image";
  if (DOCUMENT_EXTENSIONS.has(item.extension)) return "document";
  if (PACKAGE_EXTENSIONS.has(item.extension)) return "package";
  return "other";
}

function groupDesktopItems(items: NativeDesktopItem[]) {
  return DESKTOP_CATEGORIES.filter((category) => category.key !== "all")
    .map((category) => ({
      key: category.key,
      title: category.label,
      items: items.filter(
        (item) => getDesktopItemCategory(item) === category.key,
      ),
    }))
    .filter((group) => group.items.length > 0);
}

export default function DesktopShellPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [nativeDesktopItems, setNativeDesktopItems] = useState<
    NativeDesktopItem[]
  >([]);
  const [desktopDrawerOpen, setDesktopDrawerOpen] = useState(false);
  const [desktopCategory, setDesktopCategory] =
    useState<DesktopCategory["key"]>("all");
  const [desktopSearch, setDesktopSearch] = useState("");
  const [organizerEnabled, setOrganizerEnabled] = useState(
    () =>
      IS_NATIVE_DESKTOP ||
      (typeof window !== "undefined"
        ? localStorage.getItem(DESKTOP_ORGANIZER_ENABLED_KEY) === "true"
        : false),
  );
  const [archiving, setArchiving] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [archiveResult, setArchiveResult] = useState<{
    moved: number;
    skipped: number;
  } | null>(null);
  const [showWidget, setShowWidget] = useState(false);
  const [systemInfo, setSystemInfo] = useState<{
    cpu: { model: string; cores: number; usage: number };
    memory: { total: number; used: number; percent: number };
    uptime: number;
  } | null>(null);
  const [dragOverCategory, setDragOverCategory] = useState<string | null>(null);
  const [loadingItems, setLoadingItems] = useState(false);
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    item: NativeDesktopItem;
  } | null>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const mousePassthroughRef = useRef(false);
  const today = useMemo(() => new Date(), []);
  const weekday = [
    "星期日",
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
  ][today.getDay()];

  const debouncedSearch = useDebounce(desktopSearch, 200);

  // Electron 外壳:桌面透明穿透模式(显示真实系统桌面);
  // 非 Electron(浏览器 / NAS)则铺自有极光壁纸。
  const isElectronShell =
    typeof window !== "undefined" && !!window.octopus?.isElectron;

  // Appliance 单用户认证门:null=检测中,true=放行(无需认证或已登录),
  // false=需登录。仅 NAS appliance 形态会要求认证(后端 OCTOPUS_APPLIANCE=1)。
  const [applianceAuthed, setApplianceAuthed] = useState<boolean | null>(null);
  // NAS 文件管理器(原生路线;Electron 寄生模式仍用透明桌面整理抽屉)。
  const [fileManagerOpen, setFileManagerOpen] = useState(false);
  const openFiles = () => {
    if (isElectronShell) setDesktopDrawerOpen(true);
    else setFileManagerOpen(true);
  };

  // 桌面窗口:第三方应用以 iframe 开成桌面内窗口(桌面即窗口系统)。
  const [windows, setWindows] = useState<DesktopWindow[]>([]);
  const [minimized, setMinimized] = useState<Set<string>>(new Set());
  const [focusedWin, setFocusedWin] = useState<string | null>(null);
  const openWindow = (win: DesktopWindow) => {
    setWindows((prev) =>
      prev.some((w) => w.id === win.id) ? prev : [...prev, win],
    );
    setMinimized((prev) => {
      const next = new Set(prev);
      next.delete(win.id);
      return next;
    });
    setFocusedWin(win.id);
  };
  const closeWindow = (id: string) => {
    setWindows((prev) => prev.filter((w) => w.id !== id));
    setMinimized((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };
  const minimizeWindow = (id: string) =>
    setMinimized((prev) => new Set(prev).add(id));
  useEffect(() => {
    let alive = true;
    fetchApplianceAuthStatus()
      .then((s) => {
        if (alive) setApplianceAuthed(!s.authRequired || s.authenticated);
      })
      .catch(() => {
        // 状态接口不可用(母体模式 / 未开 appliance)→ 不拦截。
        if (alive) setApplianceAuthed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const openApp = (app: DesktopApp) => navigate(app.route);

  // Octopus OS:Dock 的"本地应用"段接真实数据(Docker 应用注册器)。
  // API 不可用时 dockApplianceApps 为空,渲染处回退到占位图标。
  const { apps: applianceApps, refresh: refreshApplianceApps } =
    useApplianceApps();
  const dockApplianceApps = useMemo(
    () => applianceApps.filter((app) => appOpenUrl(app) !== null).slice(0, 6),
    [applianceApps],
  );
  const openApplianceApp = (app: ApplianceApp) => {
    if (app.state !== "running") {
      void startApplianceApp(app.id)
        .then(refreshApplianceApps)
        .catch(() => {});
      return;
    }
    const url = appOpenUrl(app);
    // 原生路线:开成桌面内窗口;Electron 寄生模式仍走新标签(无窗口系统)。
    if (!url) return;
    if (isElectronShell) window.open(url, "_blank", "noopener");
    else openWindow({ id: app.id, title: app.name, url });
  };

  // P2 前端去 fork:把 agent 工作台当桌面应用开在窗口里(dogfood 窗口系统)。
  // URL 由 resolveAgentWorkspaceUrl 决定:默认同源工作台,可注入指向外部 agent
  // 服务 → os 不再 fork agent 前端。Electron 寄生模式无窗口系统,整页打开。
  const openAgentWorkspace = () => {
    if (isElectronShell) {
      navigate(AGENT_WORKSPACE_FALLBACK_ROUTE);
      return;
    }
    openWindow({
      id: AGENT_WORKSPACE_WINDOW_ID,
      title: "Agent 工作台",
      url: resolveAgentWorkspaceUrl(),
    });
  };

  useEffect(() => {
    if (!organizerEnabled) return;
    const off = window.octopus?.on?.("desktop:organize-now", () => {
      setDesktopCategory("all");
      setDesktopSearch("");
      setDesktopDrawerOpen(true);
    });
    return () => off?.();
  }, [organizerEnabled]);

  useEffect(() => {
    // 寄生模式专属:透明叠加真实桌面。原生 OS 桌面不透明,跳过。
    if (IS_NATIVE_DESKTOP || !organizerEnabled) return;
    document.documentElement.classList.add("desktop-overlay");
    return () => {
      document.documentElement.classList.remove("desktop-overlay");
      void window.octopus?.window?.setMousePassthrough?.(false);
    };
  }, [organizerEnabled]);

  useEffect(() => {
    // 寄生模式专属:空白处鼠标穿透到真实桌面。原生 OS 桌面不需要。
    if (IS_NATIVE_DESKTOP || !organizerEnabled) return;
    if (!window.octopus?.window?.setMousePassthrough) return;

    const setPassthrough = (enabled: boolean) => {
      if (mousePassthroughRef.current === enabled) return;
      mousePassthroughRef.current = enabled;
      void window.octopus?.window?.setMousePassthrough?.(enabled).catch(() => {
        mousePassthroughRef.current = !enabled;
      });
    };

    if (desktopDrawerOpen) {
      setPassthrough(false);
      return;
    }

    const onPointerMove = (event: PointerEvent) => {
      const target = event.target;
      const overInteractive =
        target instanceof Element &&
        !!target.closest("[data-desktop-interactive]");
      setPassthrough(!overInteractive);
    };

    const onPointerLeave = () => setPassthrough(true);

    document.addEventListener("pointermove", onPointerMove, true);
    document.addEventListener("pointerleave", onPointerLeave, true);
    setPassthrough(true);

    return () => {
      document.removeEventListener("pointermove", onPointerMove, true);
      document.removeEventListener("pointerleave", onPointerLeave, true);
      setPassthrough(false);
    };
  }, [desktopDrawerOpen, organizerEnabled]);

  useEffect(() => {
    if (!organizerEnabled) return;
    let alive = true;
    if (!window.octopus?.desktop) return;
    setLoadingItems(true);
    setItemsError(null);
    window.octopus.desktop
      .listItems()
      .then((result) => {
        if (!alive) return;
        setLoadingItems(false);
        if (!result.ok) {
          setItemsError(result.error || "读取桌面文件失败");
          setNativeDesktopItems([]);
          return;
        }
        setNativeDesktopItems(result.items);
      })
      .catch((e) => {
        if (!alive) return;
        setLoadingItems(false);
        setItemsError(e instanceof Error ? e.message : "读取桌面文件失败");
        setNativeDesktopItems([]);
      });
    return () => {
      alive = false;
    };
  }, [organizerEnabled]);

  useEffect(() => {
    if (!showWidget) return;
    let alive = true;
    const poll = async () => {
      if (!window.octopus?.desktop?.getSystemInfo) return;
      try {
        const result = await window.octopus.desktop.getSystemInfo();
        if (alive && result.ok) {
          setSystemInfo({
            cpu: result.cpu!,
            memory: result.memory!,
            uptime: result.uptime!,
          });
        }
      } catch {}
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [showWidget]);

  useEffect(() => {
    if (!desktopDrawerOpen) return;
    const timer = setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 100);
    return () => clearTimeout(timer);
  }, [desktopDrawerOpen]);

  useEffect(() => {
    if (!desktopDrawerOpen) return;
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        setDesktopDrawerOpen(false);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [desktopDrawerOpen]);

  useEffect(() => {
    const onClick = () => setContextMenu(null);
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  const filteredDesktopItems = useMemo(() => {
    const search = debouncedSearch.trim().toLowerCase();
    return nativeDesktopItems.filter((item) => {
      const matchesCategory =
        desktopCategory === "all" ||
        getDesktopItemCategory(item) === desktopCategory;
      if (!matchesCategory) return false;
      if (!search) return true;
      return `${item.name} ${item.subtitle} ${item.extension}`
        .toLowerCase()
        .includes(search);
    });
  }, [desktopCategory, debouncedSearch, nativeDesktopItems]);

  const groupedDesktopItems = useMemo(
    () => groupDesktopItems(filteredDesktopItems),
    [filteredDesktopItems],
  );

  const openDesktopFile = (item: NativeDesktopItem) => {
    void window.octopus?.desktop?.openItem(item.path);
  };

  const refreshDesktopItems = () => {
    if (!window.octopus?.desktop) return;
    setLoadingItems(true);
    setItemsError(null);
    window.octopus.desktop
      .listItems()
      .then((result) => {
        setLoadingItems(false);
        if (result.ok) {
          setNativeDesktopItems(result.items);
        } else {
          setItemsError(result.error || "刷新失败");
        }
      })
      .catch((e) => {
        setLoadingItems(false);
        setItemsError(e instanceof Error ? e.message : "刷新失败");
      });
  };

  const handleAutoArchive = async () => {
    if (!window.octopus?.desktop?.moveItemsBatch) return;
    setArchiving(true);
    setArchiveResult(null);
    try {
      const fileItems = nativeDesktopItems.filter(
        (item) =>
          item.kind === "file" && getDesktopItemCategory(item) !== "app",
      );
      if (fileItems.length === 0) {
        toast.info("桌面上没有可整理的文件");
        setArchiveResult({ moved: 0, skipped: 0 });
        return;
      }
      const batch = fileItems.map((item) => ({
        srcPath: item.path,
        category: getDesktopItemCategory(item),
      }));
      const result = await window.octopus.desktop.moveItemsBatch(batch);
      if (result.ok) {
        setArchiveResult({ moved: result.moved, skipped: result.skipped });
        refreshDesktopItems();
        toast.success(`已整理 ${result.moved} 个文件到分类文件夹`);
      } else {
        toast.error(result.error || "整理失败");
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "整理失败");
      setArchiveResult({ moved: 0, skipped: 0 });
    } finally {
      setArchiving(false);
    }
  };

  const handleUndo = async () => {
    if (!window.octopus?.desktop?.undoMoves) return;
    setUndoing(true);
    try {
      const result = await window.octopus.desktop.undoMoves();
      if (result.ok && result.undone > 0) {
        refreshDesktopItems();
        setArchiveResult(null);
        toast.success(`已撤销 ${result.undone} 个文件的移动`);
      } else if (result.ok && result.undone === 0) {
        toast.info("没有可撤销的操作");
      } else {
        toast.error(result.error || "撤销失败");
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "撤销失败");
    } finally {
      setUndoing(false);
    }
  };

  const handleContextMenuAction = async (
    action: "open" | "archive" | "delete",
    item: NativeDesktopItem,
  ) => {
    setContextMenu(null);
    if (action === "open") {
      openDesktopFile(item);
    } else if (action === "archive") {
      if (!window.octopus?.desktop?.moveItem) return;
      const category = getDesktopItemCategory(item);
      if (category === "app" || category === "folder") {
        toast.info("仅支持归档文件");
        return;
      }
      try {
        const listResult = await window.octopus.desktop.listItems();
        const desktopPath = listResult.ok ? listResult.desktopPath || "" : "";
        const folderName = ARCHIVE_FOLDER_MAP[category];
        if (!folderName || !desktopPath) return;
        const destDir = desktopPath + "\\" + folderName;
        const result = await window.octopus.desktop.moveItem(
          item.path,
          destDir,
        );
        if (result.ok) {
          refreshDesktopItems();
          toast.success(`已将 "${item.name}" 归档到 ${folderName}`);
        } else {
          toast.error(result.error || "归档失败");
        }
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "归档失败");
      }
    } else if (action === "delete") {
      toast.info("删除功能暂未实现");
    }
  };

  const submit = () => {
    const value = query.trim().toLowerCase();
    if (!value) return;
    const app = DESKTOP_APPS.find((item) => {
      const haystack = `${item.name} ${item.subtitle}`.toLowerCase();
      return (
        haystack.includes(value) || value.includes(item.name.toLowerCase())
      );
    });
    if (app) {
      openApp(app);
      return;
    }
    navigate(`/browser?q=${encodeURIComponent(query.trim())}`);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") submit();
  };

  const enableOrganizer = () => {
    localStorage.setItem(DESKTOP_ORGANIZER_ENABLED_KEY, "true");
    setOrganizerEnabled(true);
  };

  // Appliance 认证门:需登录且未登录时显示原生登录屏。检测中(null)先不渲染
  // 桌面,避免未登录态一闪而过。
  if (applianceAuthed === false) {
    return <ApplianceLogin onSuccess={() => setApplianceAuthed(true)} />;
  }

  // 寄生路线的 opt-in 门:原生 OS 桌面默认进入,不显示此门(IS_NATIVE_DESKTOP)。
  if (!IS_NATIVE_DESKTOP && !organizerEnabled) {
    return (
      <main className="flex h-screen items-center justify-center bg-background p-6 text-foreground">
        <section className="w-full max-w-xl rounded-3xl border border-border bg-card p-6 shadow-sm">
          <div className="mb-5 flex items-center gap-3">
            <div className="flex size-11 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <FolderIcon className="size-5" />
            </div>
            <div>
              <h1 className="text-xl font-semibold">桌面助手未开启</h1>
              <p className="text-sm text-muted-foreground">
                Octopus
                默认进入欢迎、登录与工作区。需要处理系统桌面文件时，可以单独开启透明桌面助手。
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={enableOrganizer}
              className="inline-flex h-10 items-center justify-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm transition hover:bg-primary/90"
            >
              开启桌面助手
            </button>
            <button
              type="button"
              onClick={() => navigate("/workspace/desktop-organizer")}
              className="inline-flex h-10 items-center justify-center rounded-lg border border-border bg-background px-4 text-sm font-medium transition hover:bg-muted"
            >
              打开插件设置
            </button>
            <button
              type="button"
              onClick={() => navigate("/workspace/realtime/new")}
              className="inline-flex h-10 items-center justify-center rounded-lg border border-border bg-background px-4 text-sm font-medium transition hover:bg-muted"
            >
              回到工作区
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="relative h-screen overflow-hidden bg-transparent text-white">
      {/* 原创极光壁纸(深色景深,衬托毛玻璃面板)。原生 OS 桌面始终铺壁纸
          (含将来的 Electron kiosk);母体寄生模式仅浏览器铺,Electron 叠加
          时不渲染以让真实系统桌面透过来。 */}
      {(IS_NATIVE_DESKTOP || !isElectronShell) && (
        <div aria-hidden className="desktop-wallpaper absolute inset-0 z-0" />
      )}
      <section className="relative z-10 flex h-full min-h-0 flex-col">
        <header className="hidden">
          <div className="flex min-w-0 items-center gap-2.5">
            <button
              type="button"
              onClick={() => navigate("/workspace/realtime/new")}
              className="flex shrink-0 items-center gap-2 rounded-lg px-1 py-0.5 transition hover:bg-white/32"
              title="打开工作台"
            >
              <span className="grid size-6 place-items-center rounded-md bg-white/50 text-slate-700 shadow-sm ring-1 ring-white/35">
                <BotIcon className="size-4" />
              </span>
              <span className="font-semibold">Octopus</span>
            </button>
            <button
              type="button"
              onClick={() => navigate("/workspace")}
              className="hidden items-center gap-1.5 rounded-md px-1.5 py-0.5 text-slate-700 transition hover:bg-white/34 md:flex"
              title="账号与模型"
            >
              <UserCircleIcon className="size-3.5" />
              <span>官方模型</span>
            </button>
            <button
              type="button"
              onClick={() => setDesktopDrawerOpen(true)}
              className="hidden items-center gap-1.5 rounded-md px-1.5 py-0.5 text-slate-700 transition hover:bg-white/34 sm:flex"
              title="桌面助手"
            >
              <FolderIcon className="size-3.5" />
              <span>桌面 {nativeDesktopItems.length || "--"}</span>
            </button>
            <button
              type="button"
              onClick={() => navigate("/workspace/store")}
              className="hidden items-center gap-1.5 rounded-md px-1.5 py-0.5 text-slate-700 transition hover:bg-white/34 md:flex"
              title="市场"
            >
              <StoreIcon className="size-3.5" />
              <span>市场</span>
            </button>
          </div>

          <div className="flex shrink-0 items-center gap-2 text-slate-700">
            <button
              type="button"
              onClick={() => navigate("/workspace/observability")}
              className="hidden items-center gap-1.5 rounded-md px-1.5 py-0.5 transition hover:bg-white/34 md:flex"
              title="运行状态"
            >
              <SparklesIcon className="size-3.5 text-blue-600" />
              <span>AI 就绪</span>
            </button>
            <span className="hidden items-center gap-1.5 rounded-md px-1.5 py-0.5 sm:flex">
              <WifiIcon className="size-3.5" />
              <span>Wi-Fi</span>
            </span>
            <span className="hidden items-center gap-1.5 rounded-md px-1.5 py-0.5 sm:flex">
              <BatteryFullIcon className="size-3.5" />
              <span>100%</span>
            </span>
            <button
              type="button"
              onClick={() => navigate("/workspace")}
              className="grid size-7 place-items-center rounded-md transition hover:bg-white/34"
              title="通知"
            >
              <BellIcon className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => navigate("/workspace")}
              className="grid size-7 place-items-center rounded-md transition hover:bg-white/34"
              title="快捷设置"
            >
              <SlidersHorizontalIcon className="size-4" />
            </button>
            <span className="min-w-[108px] text-right">
              {today.getMonth() + 1}月{today.getDate()}日 · {weekday}
            </span>
          </div>
        </header>

        <div className="relative min-h-0 flex-1 px-8 pb-28 pt-7">
          <div className="pointer-events-none absolute right-8 top-8 rounded-[22px] bg-black/20 px-5 py-4 text-right shadow-2xl shadow-black/14 backdrop-blur-2xl">
            <div className="text-xs font-semibold text-white/78">今日</div>
            <div className="mt-1 text-4xl font-semibold leading-none">
              {today.getDate()}
            </div>
            <div className="mt-1 text-xs text-white/72">
              {today.getFullYear()}年{today.getMonth() + 1}月 · {weekday}
            </div>
          </div>

          <div className="mx-auto flex h-full max-w-[760px] flex-col items-center justify-center pb-12">
            <div
              data-desktop-interactive
              className="flex h-12 w-full max-w-[620px] items-center gap-3 rounded-2xl border border-white/36 bg-white/68 px-4 text-slate-700 shadow-2xl shadow-black/12 backdrop-blur-2xl"
            >
              <SearchIcon className="size-5 shrink-0 text-blue-600" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder="搜索任务、文件、应用，或打开工作台"
                className="min-w-0 flex-1 bg-transparent text-sm font-medium outline-none placeholder:text-slate-400"
              />
              <button
                type="button"
                onClick={submit}
                className="rounded-full bg-slate-900 px-3 py-1.5 text-[11px] font-semibold text-white transition hover:bg-slate-700"
              >
                打开
              </button>
            </div>
          </div>
        </div>

        <div
          className={cn(
            "absolute inset-0 z-30 transition-all duration-300 ease-out",
            desktopDrawerOpen
              ? "pointer-events-auto opacity-100"
              : "pointer-events-none opacity-0",
          )}
        >
          <div
            data-desktop-interactive
            className={cn(
              "absolute inset-0 bg-black/18 px-8 pb-28 pt-14 backdrop-blur-sm transition-opacity duration-300",
              desktopDrawerOpen ? "opacity-100" : "opacity-0",
            )}
            onClick={() => setDesktopDrawerOpen(false)}
          />
          <section
            ref={drawerRef}
            className={cn(
              "absolute inset-x-8 bottom-28 top-14 mx-auto max-w-[760px] rounded-[28px] border border-white/34 bg-white/72 p-5 text-slate-800 shadow-2xl shadow-black/24 backdrop-blur-2xl transition-all duration-300 ease-out",
              desktopDrawerOpen
                ? "translate-y-0 opacity-100"
                : "translate-y-4 opacity-0",
            )}
          >
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-semibold">桌面助手</h2>
                <p className="mt-0.5 text-xs text-slate-500">
                  {loadingItems
                    ? "正在读取桌面文件..."
                    : itemsError
                      ? itemsError
                      : window.octopus?.desktop
                        ? `${nativeDesktopItems.length} 个桌面项目已收纳`
                        : "Electron 模式下显示真实系统桌面"}
                </p>
              </div>
              <div className="flex items-center gap-1.5">
                {archiveResult && !itemsError && (
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                    已整理 {archiveResult.moved} 项
                  </span>
                )}
                {window.octopus?.desktop?.moveItemsBatch && !itemsError && (
                  <button
                    type="button"
                    onClick={handleAutoArchive}
                    disabled={archiving || loadingItems}
                    className="inline-flex h-7 items-center gap-1 rounded-lg bg-blue-600 px-2.5 text-[11px] font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
                    title="一键整理桌面文件"
                  >
                    <ArchiveIcon className="size-3" />
                    {archiving ? "整理中..." : "一键整理"}
                  </button>
                )}
                {window.octopus?.desktop?.undoMoves && !itemsError && (
                  <button
                    type="button"
                    onClick={handleUndo}
                    disabled={undoing || loadingItems}
                    className="inline-flex h-7 items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 text-[11px] font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
                    title="撤销上一次整理"
                  >
                    <RotateCcwIcon className="size-3" />
                    {undoing ? "撤销中..." : "撤销"}
                  </button>
                )}
                <button
                  ref={closeButtonRef}
                  type="button"
                  onClick={() => setDesktopDrawerOpen(false)}
                  className="grid size-8 place-items-center rounded-full bg-slate-900/8 text-lg leading-none text-slate-600 transition hover:bg-slate-900/14"
                  aria-label="关闭桌面文件"
                >
                  ×
                </button>
              </div>
            </div>

            {itemsError && (
              <div className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
                <div className="flex items-center gap-2">
                  <span className="font-medium">读取失败</span>
                  <button
                    type="button"
                    onClick={refreshDesktopItems}
                    className="ml-auto rounded-md bg-red-100 px-2 py-0.5 text-[10px] font-medium text-red-700 transition hover:bg-red-200"
                  >
                    重试
                  </button>
                </div>
                <p className="mt-1 opacity-80">{itemsError}</p>
              </div>
            )}

            {!itemsError && (
              <>
                <div className="mt-4 flex items-center gap-2 rounded-2xl border border-white/45 bg-white/56 px-3 py-2">
                  <SearchIcon className="size-4 shrink-0 text-slate-400" />
                  <input
                    value={desktopSearch}
                    onChange={(event) => setDesktopSearch(event.target.value)}
                    placeholder="搜索桌面文件、应用、图片"
                    className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400"
                  />
                  {desktopSearch && (
                    <button
                      type="button"
                      onClick={() => setDesktopSearch("")}
                      className="grid size-4 place-items-center rounded-full text-slate-400 transition hover:text-slate-600"
                    >
                      <XIcon className="size-3" />
                    </button>
                  )}
                </div>

                <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
                  {DESKTOP_CATEGORIES.map((category) => {
                    const count =
                      category.key === "all"
                        ? nativeDesktopItems.length
                        : nativeDesktopItems.filter(
                            (item) =>
                              getDesktopItemCategory(item) === category.key,
                          ).length;
                    return (
                      <button
                        key={category.key}
                        type="button"
                        onClick={() => setDesktopCategory(category.key)}
                        onDragOver={(e) => {
                          if (category.key === "all") return;
                          e.preventDefault();
                          e.dataTransfer.dropEffect = "move";
                          setDragOverCategory(category.key);
                        }}
                        onDragLeave={() => setDragOverCategory(null)}
                        onDrop={async (e) => {
                          e.preventDefault();
                          setDragOverCategory(null);
                          const srcPath = e.dataTransfer.getData("text/plain");
                          if (!srcPath || !window.octopus?.desktop?.moveItem)
                            return;
                          const desktopPath = await window.octopus.desktop
                            .listItems()
                            .then((r) => r.desktopPath || "");
                          const folderName = ARCHIVE_FOLDER_MAP[category.key];
                          if (!folderName) return;
                          const destDir = desktopPath + "\\" + folderName;
                          const result = await window.octopus.desktop.moveItem(
                            srcPath,
                            destDir,
                          );
                          if (result.ok) {
                            refreshDesktopItems();
                            toast.success("文件已移动");
                          } else {
                            toast.error(result.error || "移动失败");
                          }
                        }}
                        className={cn(
                          "shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition",
                          desktopCategory === category.key
                            ? "bg-slate-900 text-white"
                            : dragOverCategory === category.key
                              ? "bg-blue-500 text-white ring-2 ring-blue-300"
                              : "bg-white/56 text-slate-600 hover:bg-white/80",
                        )}
                      >
                        {category.label}
                        <span className="ml-1 opacity-65">{count}</span>
                      </button>
                    );
                  })}
                </div>

                <div className="mt-4 max-h-[calc(100%-132px)] overflow-auto pr-1">
                  {loadingItems ? (
                    <div className="grid h-56 place-items-center">
                      <div className="text-center">
                        <Loader2Icon className="mx-auto size-8 animate-spin text-slate-400" />
                        <p className="mt-3 text-sm font-medium text-slate-500">
                          正在读取桌面文件...
                        </p>
                      </div>
                    </div>
                  ) : filteredDesktopItems.length > 0 ? (
                    <div className="space-y-5">
                      {(desktopCategory === "all"
                        ? groupedDesktopItems
                        : [
                            {
                              key: desktopCategory,
                              title:
                                DESKTOP_CATEGORIES.find(
                                  (item) => item.key === desktopCategory,
                                )?.label || "文件",
                              items: filteredDesktopItems,
                            },
                          ]
                      ).map((group) => (
                        <div key={group.key}>
                          <div className="mb-2 text-xs font-semibold text-slate-500">
                            {group.title}
                          </div>
                          <div className="grid grid-cols-5 gap-3">
                            {group.items.map((item) => {
                              const category = getDesktopItemCategory(item);
                              const Icon =
                                category === "folder"
                                  ? FolderIcon
                                  : category === "app"
                                    ? AppWindowIcon
                                    : category === "image"
                                      ? ImageIcon
                                      : FileTextIcon;
                              return (
                                <button
                                  key={item.path}
                                  type="button"
                                  draggable={item.kind === "file"}
                                  onDragStart={(e) => {
                                    e.dataTransfer.setData(
                                      "text/plain",
                                      item.path,
                                    );
                                    e.dataTransfer.effectAllowed = "move";
                                  }}
                                  onClick={() => openDesktopFile(item)}
                                  onContextMenu={(e) => {
                                    e.preventDefault();
                                    setContextMenu({
                                      x: e.clientX,
                                      y: e.clientY,
                                      item,
                                    });
                                  }}
                                  title={item.path}
                                  className="group flex min-h-[92px] flex-col items-center justify-center gap-1.5 rounded-2xl p-2 text-center transition hover:bg-white/62 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2"
                                >
                                  <span
                                    className={cn(
                                      "grid size-12 place-items-center rounded-[14px] text-white shadow-lg shadow-black/12 ring-1 ring-white/35 transition-transform duration-150 group-hover:scale-105",
                                      category === "folder"
                                        ? "bg-gradient-to-br from-amber-400 to-orange-500"
                                        : category === "app"
                                          ? "bg-gradient-to-br from-violet-500 to-fuchsia-500"
                                          : category === "image"
                                            ? "bg-gradient-to-br from-cyan-400 to-blue-500"
                                            : category === "document"
                                              ? "bg-gradient-to-br from-blue-500 to-indigo-500"
                                              : category === "package"
                                                ? "bg-gradient-to-br from-rose-500 to-orange-500"
                                                : "bg-gradient-to-br from-slate-600 to-slate-500",
                                    )}
                                  >
                                    <Icon className="size-6" />
                                  </span>
                                  <span className="line-clamp-2 max-w-24 text-[11px] font-medium leading-tight text-slate-700">
                                    {item.name}
                                  </span>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="grid h-56 place-items-center rounded-3xl border border-dashed border-slate-300/80 bg-white/32 text-center">
                      <div>
                        <SearchIcon className="mx-auto size-8 text-slate-400" />
                        <p className="mt-3 text-sm font-medium text-slate-600">
                          {desktopSearch.trim()
                            ? "未找到匹配的文件"
                            : nativeDesktopItems.length === 0
                              ? "桌面暂无文件"
                              : "该分类下暂无文件"}
                        </p>
                        <p className="mt-1 text-xs text-slate-400">
                          {desktopSearch.trim()
                            ? "尝试更换搜索关键词"
                            : "将文件放到桌面即可在此管理"}
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </section>
        </div>

        {contextMenu && (
          <div
            data-desktop-interactive
            className="fixed z-50 w-40 rounded-lg border border-slate-200 bg-white py-1 shadow-xl"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            <button
              type="button"
              onClick={() => handleContextMenuAction("open", contextMenu.item)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 transition hover:bg-slate-100"
            >
              <ExternalLinkIcon className="size-3.5" />
              打开
            </button>
            {contextMenu.item.kind === "file" && (
              <button
                type="button"
                onClick={() =>
                  handleContextMenuAction("archive", contextMenu.item)
                }
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 transition hover:bg-slate-100"
              >
                <FolderInputIcon className="size-3.5" />
                归档到分类
              </button>
            )}
            <div className="my-1 h-px bg-slate-200" />
            <button
              type="button"
              onClick={() =>
                handleContextMenuAction("delete", contextMenu.item)
              }
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-red-600 transition hover:bg-red-50"
            >
              <Trash2Icon className="size-3.5" />
              删除
            </button>
          </div>
        )}

        <Dock className="absolute bottom-5 left-1/2 z-20 flex -translate-x-1/2 items-end gap-2.5 rounded-[24px] border border-white/45 bg-white/40 px-3.5 py-2.5 shadow-[0_18px_50px_-12px_rgba(0,0,0,0.45)] ring-1 ring-inset ring-white/40 backdrop-blur-2xl">
          {/* P2:agent 工作台作为桌面窗口应用(消费而非 fork agent 前端)。 */}
          <DockItem
            onClick={openAgentWorkspace}
            title="Agent 工作台 · 在桌面窗口里打开"
            className="rounded-[15px] bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-lg shadow-black/18 ring-1 ring-white/25"
          >
            <BotIcon className="size-6" />
          </DockItem>
          <span className="mx-1 h-10 w-px self-center bg-slate-700/16" />
          {DOCK_APPS.map((app) => {
            const Icon = app.icon;
            return (
              <DockItem
                key={app.name}
                onClick={() => openApp(app)}
                title={app.name}
                className={cn(
                  "rounded-[15px] bg-gradient-to-br text-white shadow-lg shadow-black/18 ring-1 ring-white/25",
                  app.color,
                )}
              >
                <Icon className="size-6" />
              </DockItem>
            );
          })}
          <span className="mx-1 h-10 w-px self-center bg-slate-700/16" />
          {dockApplianceApps.length > 0
            ? dockApplianceApps.map((app) => (
                <DockItem
                  key={app.id}
                  onClick={() => openApplianceApp(app)}
                  title={
                    app.state === "running"
                      ? `${app.name} · ${app.status}`
                      : `${app.name} · 已停止,点击启动`
                  }
                  className={cn(
                    "rounded-[15px] bg-white/86 text-slate-700 shadow-lg shadow-black/12 ring-1 ring-white/25",
                    app.state !== "running" && "opacity-55 saturate-50",
                  )}
                >
                  {app.icon ? (
                    <img
                      src={app.icon}
                      alt=""
                      className="size-7 rounded-[8px]"
                    />
                  ) : (
                    <span className="text-lg font-semibold">
                      {(app.name[0] ?? "?").toUpperCase()}
                    </span>
                  )}
                  {app.state === "running" && (
                    <span className="absolute bottom-1 size-1.5 rounded-full bg-emerald-500" />
                  )}
                </DockItem>
              ))
            : LOCAL_APP_PLACEHOLDERS.map((app) => {
                const Icon = app.icon;
                return (
                  <DockItem
                    key={app.name}
                    onClick={() => openApp(app)}
                    title={`${app.name} · ${app.subtitle}`}
                    className={cn(
                      "rounded-[15px] bg-gradient-to-br shadow-lg shadow-black/12 ring-1 ring-white/25",
                      app.color,
                    )}
                  >
                    <Icon className="size-6" />
                  </DockItem>
                );
              })}
          <span className="mx-1 h-10 w-px self-center bg-slate-700/16" />
          <DockItem
            onClick={openFiles}
            title={isElectronShell ? "桌面文件" : "文件"}
            className="rounded-[15px] bg-white/76 text-orange-500 shadow-lg shadow-black/12"
          >
            <FolderIcon className="size-6" />
          </DockItem>
          <DockItem
            onClick={() => setShowWidget(!showWidget)}
            title="系统监控"
            className={cn(
              "rounded-[15px] shadow-lg shadow-black/12",
              showWidget
                ? "bg-blue-500 text-white ring-2 ring-blue-300"
                : "bg-white/76 text-slate-700",
            )}
          >
            <CpuIcon className="size-6" />
          </DockItem>
          <DockItem
            onClick={() => navigate("/workspace")}
            title="设置"
            className="rounded-[15px] bg-white/76 text-slate-700 shadow-lg shadow-black/12"
          >
            <SettingsIcon className="size-6" />
          </DockItem>
        </Dock>
        {showWidget && systemInfo && (
          <div
            data-desktop-interactive
            className="absolute bottom-24 right-8 z-20 w-64 rounded-2xl border border-white/34 bg-white/78 p-4 text-slate-800 shadow-2xl shadow-black/24 backdrop-blur-2xl"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-500">
                系统监控
              </span>
              <button
                type="button"
                onClick={() => setShowWidget(false)}
                className="grid size-5 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
              >
                <XIcon className="size-3" />
              </button>
            </div>
            <div className="mt-3 space-y-3">
              <div>
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1 text-slate-600">
                    <CpuIcon className="size-3" /> CPU
                  </span>
                  <span className="font-medium">{systemInfo.cpu.usage}%</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all",
                      systemInfo.cpu.usage > 80
                        ? "bg-red-500"
                        : systemInfo.cpu.usage > 50
                          ? "bg-amber-500"
                          : "bg-emerald-500",
                    )}
                    style={{ width: `${systemInfo.cpu.usage}%` }}
                  />
                </div>
                <div className="mt-0.5 text-[10px] text-slate-400">
                  {systemInfo.cpu.model.split(" ").slice(0, 3).join(" ")} ·{" "}
                  {systemInfo.cpu.cores} 核心
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1 text-slate-600">
                    <HardDriveIcon className="size-3" /> 内存
                  </span>
                  <span className="font-medium">
                    {systemInfo.memory.percent}%
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all",
                      systemInfo.memory.percent > 80
                        ? "bg-red-500"
                        : systemInfo.memory.percent > 50
                          ? "bg-amber-500"
                          : "bg-blue-500",
                    )}
                    style={{ width: `${systemInfo.memory.percent}%` }}
                  />
                </div>
                <div className="mt-0.5 text-[10px] text-slate-400">
                  {systemInfo.memory.used} / {systemInfo.memory.total} GB
                </div>
              </div>
              <div className="text-[10px] text-slate-400">
                运行时间: {Math.floor(systemInfo.uptime / 60)} 小时{" "}
                {systemInfo.uptime % 60} 分钟
              </div>
            </div>
          </div>
        )}
      </section>

      {fileManagerOpen && (
        <FileManager onClose={() => setFileManagerOpen(false)} />
      )}

      {windows
        .filter((win) => !minimized.has(win.id))
        .map((win, i) => (
          <AppWindow
            key={win.id}
            win={win}
            index={i}
            focused={focusedWin === win.id}
            onFocus={() => setFocusedWin(win.id)}
            onClose={() => closeWindow(win.id)}
            onMinimize={() => minimizeWindow(win.id)}
          />
        ))}
    </main>
  );
}
