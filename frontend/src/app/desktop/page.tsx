import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  AppWindowIcon,
  ArchiveIcon,
  CpuIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  FileTextIcon,
  FolderIcon,
  FolderInputIcon,
  GlobeIcon,
  HardDriveIcon,
  ImageIcon,
  Loader2Icon,
  ListChecksIcon,
  MonitorIcon,
  RotateCcwIcon,
  SearchIcon,
  SettingsIcon,
  ShoppingBagIcon,
  SmartphoneIcon,
  TerminalSquareIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { authReturnToFromSearch } from "@/core/auth/return-to";
import { useAuth } from "@/providers/AuthProvider";
import { useDebounce } from "@/hooks";
import type {
  NativeDesktopItem,
  NativeNotification,
  NativeWindow,
  SystemActionCapabilities,
  SystemControlState,
  SystemUpdateCapabilities,
  SystemUpdateStatus,
} from "@/types/electron";
import {
  applianceAppsForDock,
  applianceAppsForLibrary,
  appOpenUrl,
  fetchApplianceApps,
  startApplianceApp,
  stopApplianceApp,
  useApplianceApps,
  type ApplianceApp,
} from "@/appliance/apps";
import { Dock, DockItem } from "@/appliance/dock";
import { MacLiquidGlassOptics } from "@/appliance/liquid-glass-optics";
import { MacLiquidGlassWebGL } from "@/appliance/liquid-glass-webgl";
import { calculateLiquidGlassMotion } from "@/appliance/liquid-glass-motion";
import { MacNativeLiquidGlass } from "@/appliance/native-liquid-glass";
import {
  DEFAULT_LIQUID_GLASS_TUNING,
  isDefaultLiquidGlassTuning,
  LIQUID_GLASS_TUNING_STORAGE_KEY,
  liquidGlassCssVariables,
  normalizeLiquidGlassTuning,
  parseLiquidGlassTuning,
  type LiquidGlassTuning,
} from "@/appliance/liquid-glass-settings";
import {
  findNativeFileManagerApp,
  findNativeSystemSettingsApp,
  isNativeSystemSettingsApp,
  nativeWindowMatchesApp,
  useNativeApps,
} from "@/appliance/apps-native";
import {
  fetchApplianceAuthStatus,
  hasDeviceOperatorAccess,
  type ApplianceAuthStatus,
} from "@/appliance/auth";
import { requestHighRiskApproval } from "@/appliance/approval";
import {
  agentAssetManagementRoute,
  agentAssetWindowId,
} from "@/appliance/agent-assets";
import { useAgentDesktopHealth } from "@/appliance/agent-health";
import {
  AccountSecurityPanel,
  type AccountSecuritySection,
} from "@/appliance/account-security-panel";
import type { OsAgentSettingsSection } from "@/components/workspace/settings/system-agent-settings-content";
import { ApplianceLogin } from "@/appliance/login";
import { FileManager } from "@/appliance/file-manager";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import { HubPanel } from "@/appliance/hub-panel";
import { EmbeddedAgentWorkspace } from "@/appliance/embedded-agent-workspace";
import type { HubApp } from "@/appliance/hub";
import { OPEN_ECHO_HUB_EVENT } from "@/core/apps/app-presentation";
import { PhotosPanel } from "@/appliance/photos-panel";
import { StorageCenterPanel } from "@/appliance/storage-center-panel";
import { DeviceLinkPanel } from "@/appliance/device-link-panel";
import { resolveSystemSettingsSurface } from "@/appliance/system-settings-surface";
import { TaskSpacePanel } from "@/appliance/task-space-panel";
import { useEchoTaskProjection } from "@/appliance/task-space";
import { AppWindow, type DesktopWindow } from "@/appliance/app-window";
import {
  loadAgentWorkspaceConfig,
  resolveAgentAppUrl,
} from "@/appliance/agent-workspace";
import {
  MAC_SYSTEM_APPS,
  MacAboutDialog,
  MacAppIcon,
  MacControlCenter,
  MacDesktopIcon,
  MacDesktopWidgets,
  MacDesktopWallpaperArtwork,
  MacLaunchpad,
  MacLiquidGlassPanel,
  MacMenuBar,
  MacNotificationCenter,
  MacSpotlight,
  MacSystemActionDialog,
  type MacShellApp,
  type MacLiquidGlassIntensity,
  type MacLiquidGlassStyle,
  type MacSystemAction,
  type MacSystemCapabilities,
} from "@/appliance/macos-shell";

type DesktopApp = {
  name: string;
  subtitle: string;
  route: string;
  icon: LucideIcon;
  color: string;
  // Agent 工作台类应用直接渲染在系统窗口中，不再加载另一套前端。
  windowed?: boolean;
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
    color: "linear-gradient(145deg, #141820, #020409)",
    windowed: true,
  },
  {
    name: "AI 浏览器",
    subtitle: "浏览、调研、自动化",
    route: "/browser",
    icon: GlobeIcon,
    color: "linear-gradient(145deg, #55c7ff, #087bd8)",
  },
  {
    name: "文件管家",
    subtitle: "AI 问答你的文档库",
    route: "/workspace/storage",
    icon: DatabaseIcon,
    color: "linear-gradient(145deg, #54d59d, #0c8e66)",
    windowed: true,
  },
  {
    name: "照片",
    subtitle: "本地智能相册",
    route: "/photos",
    icon: ImageIcon,
    color: "linear-gradient(145deg, #fb8aa2, #f06b38)",
  },
  {
    name: "存储中心",
    subtitle: "容量、磁盘与共享",
    route: "/storage-center",
    icon: HardDriveIcon,
    color: "linear-gradient(145deg, #46c7df, #1768cf)",
  },
  {
    name: "设备连接",
    subtitle: "手机、终端与远程访问",
    route: "/device-link",
    icon: SmartphoneIcon,
    color: "linear-gradient(145deg, #7f9cff, #4455d8)",
  },
  {
    name: "知识库",
    subtitle: "工作区与资料",
    route: "/workspace/knowledge",
    icon: FolderIcon,
    color: "linear-gradient(145deg, #ffd65d, #e58a18)",
  },
  {
    name: "Echo Hub",
    subtitle: "应用、服务与扩展",
    route: "/hub",
    icon: ShoppingBagIcon,
    color: "linear-gradient(145deg, #72b9ff, #3158d8)",
  },
  {
    name: "终端日志",
    subtitle: "运行状态",
    route: "/workspace/observability",
    icon: TerminalSquareIcon,
    color: "linear-gradient(145deg, #555d68, #1e232b)",
    windowed: true,
  },
  {
    name: "设置",
    subtitle: "账号、模型、权限",
    route: "/workspace",
    icon: SettingsIcon,
    color: "linear-gradient(145deg, #90959d, #444950)",
  },
];

const ECHO_APP_STORE_ID = "echo-app-store";

const DOCK_APPS = DESKTOP_APPS.slice(0, 4);
const OPERATOR_ONLY_APP_ROUTES = new Set([
  "/storage-center",
  "/device-link",
  "/workspace/observability",
  "/workspace",
]);
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
const DESKTOP_ORGANIZER_ENABLED_KEY = "echo:desktop-organizer-enabled";

const NO_SYSTEM_CAPABILITIES: SystemActionCapabilities = {
  nativeShell: false,
  lock: false,
  logout: false,
  suspend: false,
  restart: false,
  shutdown: false,
};

// Echo OS 原生路线:桌面即系统主页 —— 默认进入、不透明、自带壁纸+启动器。
// 母体 echo-agent 走寄生路线(透明叠加真实桌面的整理工具,类比腾讯/360
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
  const location = useLocation();
  const {
    authStatus,
    isAuthenticated,
    isLoading: authLoading,
    retryAuth,
  } = useAuth();
  const authenticatedReturnTo = useMemo(() => {
    if (!new URLSearchParams(location.search).has("returnTo")) return null;
    return authReturnToFromSearch(location.search);
  }, [location.search]);
  const [query, setQuery] = useState("");
  const [spotlightOpen, setSpotlightOpen] = useState(false);
  const [launchpadOpen, setLaunchpadOpen] = useState(false);
  const [controlCenterOpen, setControlCenterOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [nativeNotifications, setNativeNotifications] = useState<
    NativeNotification[]
  >([]);
  const [notificationServiceAvailable, setNotificationServiceAvailable] =
    useState(false);
  const [liquidGlassOpen, setLiquidGlassOpen] = useState(false);
  const [liquidGlassStyle, setLiquidGlassStyle] = useState<MacLiquidGlassStyle>(
    () => {
      if (typeof window === "undefined") return "crystal";
      const saved = localStorage.getItem("echo:liquid-glass-style");
      return saved === "softlight" || saved === "harmony"
        ? "softlight"
        : "crystal";
    },
  );
  const [liquidGlassIntensity, setLiquidGlassIntensity] =
    useState<MacLiquidGlassIntensity>(() => {
      if (typeof window === "undefined") return "balanced";
      const saved = localStorage.getItem("echo:liquid-glass-intensity");
      return saved === "weak" || saved === "strong" ? saved : "balanced";
    });
  const [liquidGlassTuning, setLiquidGlassTuning] = useState<LiquidGlassTuning>(
    () => {
      if (typeof window === "undefined") return DEFAULT_LIQUID_GLASS_TUNING;
      return parseLiquidGlassTuning(
        localStorage.getItem(LIQUID_GLASS_TUNING_STORAGE_KEY),
      );
    },
  );
  const [aboutOpen, setAboutOpen] = useState(false);
  const [accountSecurityOpen, setAccountSecurityOpen] = useState(false);
  const [accountSecuritySection, setAccountSecuritySection] =
    useState<AccountSecuritySection>("account");
  const [agentSettingsSection, setAgentSettingsSection] =
    useState<OsAgentSettingsSection>("models");
  const [taskSpaceOpen, setTaskSpaceOpen] = useState(false);
  const [systemCapabilities, setSystemCapabilities] =
    useState<SystemActionCapabilities>(NO_SYSTEM_CAPABILITIES);
  const [systemControls, setSystemControls] =
    useState<SystemControlState | null>(null);
  const [systemUpdateCapabilities, setSystemUpdateCapabilities] =
    useState<SystemUpdateCapabilities | null>(null);
  const [systemUpdateStatus, setSystemUpdateStatus] =
    useState<SystemUpdateStatus | null>(null);
  const [systemUpdateBusy, setSystemUpdateBusy] = useState(false);
  const [pendingSystemAction, setPendingSystemAction] =
    useState<MacSystemAction | null>(null);
  const [systemActionBusy, setSystemActionBusy] = useState(false);
  const [systemActionError, setSystemActionError] = useState<string | null>(
    null,
  );
  const [desktopMenu, setDesktopMenu] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [nativeWindowMenu, setNativeWindowMenu] = useState<{
    x: number;
    y: number;
    appName: string;
    window: NativeWindow;
  } | null>(null);
  const [wallpaperVariant, setWallpaperVariant] = useState<
    "orbit" | "aurora" | "sunset" | "midnight"
  >(() => {
    if (typeof window === "undefined") return "orbit";
    const saved = localStorage.getItem("echo:desktop-wallpaper");
    if (saved === "tahoe") return "orbit";
    if (saved === "sequoia") return "aurora";
    if (saved === "sonoma") return "sunset";
    return saved === "orbit" ||
      saved === "aurora" ||
      saved === "sunset" ||
      saved === "midnight"
      ? saved
      : "orbit";
  });
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
  const [systemInfoStatus, setSystemInfoStatus] = useState<
    "idle" | "loading" | "ready" | "unavailable" | "error"
  >("idle");
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
  const liquidPointerFrameRef = useRef<number | null>(null);
  const liquidMotionResetTimerRef = useRef<number | null>(null);
  const liquidPointerRef = useRef({
    x: 50,
    y: 30,
    clientX: 0,
    clientY: 0,
  });
  const liquidMotionSampleRef = useRef({ x: 0, y: 0, time: 0 });
  const liquidSurfaceRef = useRef<HTMLElement | null>(null);
  const activeLiquidSurfaceRef = useRef<HTMLElement | null>(null);

  const debouncedSearch = useDebounce(desktopSearch, 200);

  // Electron 外壳:桌面透明穿透模式(显示真实系统桌面);
  // 非 Electron(浏览器 / NAS)则铺自有极光壁纸。
  const isElectronShell =
    typeof window !== "undefined" && !!window.echo?.isElectron;

  // Appliance 单用户认证门:null=检测中,true=放行(无需认证或已登录),
  // false=需登录。仅 NAS appliance 形态会要求认证(后端 ECHO_APPLIANCE=1)。
  const [applianceAuthed, setApplianceAuthed] = useState<boolean | null>(null);
  const [applianceAuthRequired, setApplianceAuthRequired] = useState<
    boolean | null
  >(null);
  const [applianceRole, setApplianceRole] =
    useState<ApplianceAuthStatus["role"]>(null);
  const isDeviceOperator = hasDeviceOperatorAccess(
    applianceAuthRequired,
    applianceAuthed,
    applianceRole,
  );
  const visibleDesktopApps = useMemo(
    () =>
      DESKTOP_APPS.filter(
        (app) => isDeviceOperator || !OPERATOR_ONLY_APP_ROUTES.has(app.route),
      ),
    [isDeviceOperator],
  );
  const agentDesktopHealth = useAgentDesktopHealth(applianceAuthed === true);
  const {
    projection: taskProjection,
    loading: taskProjectionLoading,
    error: taskProjectionError,
    refresh: refreshTaskProjection,
    takeover: takeoverTaskProjection,
    resumeExecution: resumeTaskProjection,
  } = useEchoTaskProjection(applianceAuthed === true);
  // NAS 文件管理器(原生路线;Electron 寄生模式仍用透明桌面整理抽屉)。
  const [fileManagerOpen, setFileManagerOpen] = useState(false);
  const [photosOpen, setPhotosOpen] = useState(false);
  const [storageCenterOpen, setStorageCenterOpen] = useState(false);
  const [deviceLinkOpen, setDeviceLinkOpen] = useState(false);
  const [hubOpen, setHubOpen] = useState(false);
  const [pendingAppControl, setPendingAppControl] = useState<{
    operation: "start" | "stop";
    app: ApplianceApp;
  } | null>(null);
  const openFiles = () => {
    if (IS_NATIVE_DESKTOP) setFileManagerOpen(true);
    else if (isElectronShell) setDesktopDrawerOpen(true);
    else setFileManagerOpen(true);
  };
  const openSpotlight = () => {
    setControlCenterOpen(false);
    setNotificationsOpen(false);
    setLiquidGlassOpen(false);
    setLaunchpadOpen(false);
    setSpotlightOpen(true);
  };
  const toggleControlCenter = () => {
    setNotificationsOpen(false);
    setSpotlightOpen(false);
    setLiquidGlassOpen(false);
    setControlCenterOpen((value) => !value);
  };
  const toggleNotifications = () => {
    setControlCenterOpen(false);
    setSpotlightOpen(false);
    setLiquidGlassOpen(false);
    setNotificationsOpen((value) => !value);
  };
  const toggleLiquidGlass = () => {
    setControlCenterOpen(false);
    setNotificationsOpen(false);
    setSpotlightOpen(false);
    setLiquidGlassOpen((value) => !value);
  };
  const updateLiquidGlassStyle = (style: MacLiquidGlassStyle) => {
    setLiquidGlassStyle(style);
    localStorage.setItem("echo:liquid-glass-style", style);
  };
  const updateLiquidGlassIntensity = (intensity: MacLiquidGlassIntensity) => {
    setLiquidGlassIntensity(intensity);
    localStorage.setItem("echo:liquid-glass-intensity", intensity);
  };
  const updateLiquidGlassTuning = (patch: Partial<LiquidGlassTuning>) => {
    setLiquidGlassTuning((current) => {
      const next = normalizeLiquidGlassTuning({ ...current, ...patch });
      localStorage.setItem(
        LIQUID_GLASS_TUNING_STORAGE_KEY,
        JSON.stringify(next),
      );
      return next;
    });
  };
  const resetLiquidGlassTuning = () => {
    setLiquidGlassStyle("crystal");
    setLiquidGlassIntensity("balanced");
    setLiquidGlassTuning(DEFAULT_LIQUID_GLASS_TUNING);
    localStorage.removeItem("echo:liquid-glass-style");
    localStorage.removeItem("echo:liquid-glass-intensity");
    localStorage.removeItem(LIQUID_GLASS_TUNING_STORAGE_KEY);
  };

  const liquidGlassVariables = useMemo(
    () => liquidGlassCssVariables(liquidGlassTuning) as CSSProperties,
    [liquidGlassTuning],
  );
  const liquidGlassUsesNativeDefaults =
    isDefaultLiquidGlassTuning(liquidGlassTuning);

  useEffect(
    () => () => {
      if (liquidPointerFrameRef.current !== null) {
        window.cancelAnimationFrame(liquidPointerFrameRef.current);
      }
      if (liquidMotionResetTimerRef.current !== null) {
        window.clearTimeout(liquidMotionResetTimerRef.current);
      }
    },
    [],
  );
  const openSystemSettingsSection = (section: AccountSecuritySection) => {
    if (!isDeviceOperator) {
      toast.info("家庭成员不能修改设备级设置");
      return;
    }
    setControlCenterOpen(false);
    setNotificationsOpen(false);
    setAboutOpen(false);
    const openEchoSettings = () => {
      setAccountSecuritySection(section);
      setAccountSecurityOpen(true);
    };
    const nativeAppsApi = window.echo?.apps;
    if (
      nativeAppsApi &&
      resolveSystemSettingsSurface(!!nativeAppsApi) === "native"
    ) {
      void nativeAppsApi
        .list()
        .then(async (apps) => {
          const settingsApp = findNativeSystemSettingsApp(apps);
          if (!settingsApp) {
            throw new Error("原生 KDE 系统设置未安装");
          }
          const result = await nativeAppsApi.launch(settingsApp.id);
          if (!result.ok) {
            throw new Error(result.error || "原生系统设置启动失败");
          }
        })
        .catch((error: unknown) => {
          const message =
            error instanceof Error ? error.message : "原生系统设置启动失败";
          toast.error(`${message}，已打开内置设置`);
          openEchoSettings();
        });
      return;
    }
    openEchoSettings();
  };
  const openSystemSettings = () => openSystemSettingsSection("account");
  const openStorageSettings = () => openSystemSettingsSection("storage");
  const openSystemAgentSettings = useCallback(
    (section: OsAgentSettingsSection = "models") => {
      setAgentSettingsSection(section);
      setAccountSecuritySection("agent");
      setAccountSecurityOpen(true);
    },
    [],
  );

  useEffect(() => {
    const open = (event: Event) => {
      const requested =
        event instanceof CustomEvent &&
        typeof event.detail?.section === "string"
          ? event.detail.section
          : "models";
      const allowed = new Set<OsAgentSettingsSection>([
        "models",
        "tools",
        "memory",
        "browserAutomation",
        "desktopAutomation",
        "automationSecurity",
        "conversation",
        "notification",
        "appearance",
        "privacy",
      ]);
      openSystemAgentSettings(
        allowed.has(requested as OsAgentSettingsSection)
          ? (requested as OsAgentSettingsSection)
          : "models",
      );
    };
    window.addEventListener("echo:open-system-settings", open);
    return () => window.removeEventListener("echo:open-system-settings", open);
  }, [openSystemAgentSettings]);

  // 桌面窗口:系统应用直接渲染 React 内容，第三方应用才使用 iframe。
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
        if (alive) {
          setApplianceAuthRequired(s.authRequired);
          setApplianceAuthed(!s.authRequired || s.authenticated);
          setApplianceRole(s.role);
        }
      })
      .catch(() => {
        // 状态接口不可用(母体模式 / 未开 appliance)→ 不拦截。
        if (alive) {
          setApplianceAuthRequired(false);
          setApplianceAuthed(true);
          setApplianceRole("operator");
        }
      });
    // 只读取同源存储配置；Agent 工作台本身已经内建。
    void loadAgentWorkspaceConfig();
    return () => {
      alive = false;
    };
  }, []);

  // The OS session is the single authentication boundary. If its backend
  // identity expires while the desktop is open, return to the system login
  // screen instead of letting an embedded workbench render another login.
  useEffect(() => {
    if (authLoading || applianceAuthRequired !== true) return;
    setApplianceAuthed(isAuthenticated);
  }, [applianceAuthRequired, authLoading, isAuthenticated]);

  // Resume the originally requested workspace only after both the appliance
  // gate and the shared AuthProvider agree that the system session is valid.
  useEffect(() => {
    if (!authenticatedReturnTo || applianceAuthed !== true || authLoading) {
      return;
    }
    if (authStatus?.enabled && !isAuthenticated) return;
    navigate(authenticatedReturnTo, { replace: true });
  }, [
    applianceAuthed,
    authLoading,
    authStatus,
    authenticatedReturnTo,
    isAuthenticated,
    navigate,
  ]);

  const refreshSystemControls = useCallback(async () => {
    const controls = window.echo?.systemControls;
    if (!controls) return;
    try {
      setSystemControls(await controls.getState());
    } catch (error) {
      console.warn("[echo] native system-control refresh failed", error);
    }
  }, []);

  useEffect(() => {
    if (!window.echo?.systemControls) return;
    void refreshSystemControls();
    const timer = window.setInterval(() => {
      void refreshSystemControls();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [refreshSystemControls]);

  const refreshSystemUpdate = useCallback(async () => {
    const updates = window.echo?.updates;
    if (!updates) {
      setSystemUpdateCapabilities({
        nativeShell: false,
        status: false,
        apply: false,
        reason: "system updates require the native Linux session shell",
      });
      setSystemUpdateStatus({
        schema: 1,
        state: "unavailable",
        error: "请在 Echo OS 原生 Linux 桌面中查看系统更新。",
      });
      return;
    }
    try {
      const [capabilities, status] = await Promise.all([
        updates.getCapabilities(),
        updates.getStatus(),
      ]);
      setSystemUpdateCapabilities(capabilities);
      setSystemUpdateStatus(status);
    } catch (error) {
      setSystemUpdateStatus({
        schema: 1,
        state: "unavailable",
        error: error instanceof Error ? error.message : "系统更新状态读取失败",
      });
    }
  }, []);

  useEffect(() => {
    const updateSurfaceOpen =
      aboutOpen ||
      (accountSecurityOpen && accountSecuritySection === "general");
    if (!updateSurfaceOpen) return;
    void refreshSystemUpdate();
    if (
      !systemUpdateBusy &&
      systemUpdateStatus?.state !== "checking" &&
      systemUpdateStatus?.state !== "installing"
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshSystemUpdate();
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [
    aboutOpen,
    accountSecurityOpen,
    accountSecuritySection,
    refreshSystemUpdate,
    systemUpdateBusy,
    systemUpdateStatus?.state,
  ]);

  const applySystemUpdate = async () => {
    const updates = window.echo?.updates;
    if (!updates || systemUpdateBusy) return;
    setSystemUpdateBusy(true);
    try {
      const result = await updates.apply();
      if (!result.ok) {
        if (!result.cancelled) toast.error(result.error || "系统更新安装失败");
        return;
      }
      toast.success("系统更新已写入备用槽，重新启动后生效");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "系统更新安装失败");
    } finally {
      await refreshSystemUpdate();
      setSystemUpdateBusy(false);
    }
  };

  const refreshNativeNotifications = useCallback(async () => {
    const notifications = window.echo?.notifications;
    if (!notifications) {
      setNotificationServiceAvailable(false);
      setNativeNotifications([]);
      return;
    }
    try {
      const result = await notifications.list();
      setNotificationServiceAvailable(result.ok);
      if (result.ok) setNativeNotifications(result.notifications);
    } catch (error) {
      setNotificationServiceAvailable(false);
      console.warn("[echo] native notification refresh failed", error);
    }
  }, []);

  useEffect(() => {
    if (!window.echo?.notifications) return;
    void refreshNativeNotifications();
    const timer = window.setInterval(
      () => {
        void refreshNativeNotifications();
      },
      notificationsOpen ? 1_500 : 5_000,
    );
    return () => window.clearInterval(timer);
  }, [notificationsOpen, refreshNativeNotifications]);

  const dismissNativeNotification = async (notificationId: number) => {
    const notifications = window.echo?.notifications;
    if (!notifications) return;
    const result = await notifications.close(notificationId).catch(() => ({
      ok: false,
      error: "系统通知服务不可用",
    }));
    if (!result.ok) {
      toast.error(result.error || "通知清除失败");
      return;
    }
    setNativeNotifications((current) =>
      current.filter((notification) => notification.id !== notificationId),
    );
  };

  const clearNativeNotifications = async () => {
    const notifications = window.echo?.notifications;
    if (!notifications) return;
    const result = await notifications.clear().catch(() => ({
      ok: false,
      error: "系统通知服务不可用",
    }));
    if (!result.ok) {
      toast.error(result.error || "通知清除失败");
      return;
    }
    setNativeNotifications([]);
  };

  const applySystemControl = async (
    label: string,
    operation: () => Promise<{ ok: boolean; error?: string }>,
  ) => {
    try {
      const result = await operation();
      if (!result.ok) {
        toast.error(result.error || `${label}设置失败`);
        return;
      }
      await refreshSystemControls();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : `${label}设置失败`);
    }
  };

  useEffect(() => {
    let alive = true;
    const system = window.echo?.system;
    if (!system) return;
    system
      .getCapabilities()
      .then((capabilities) => {
        if (alive) setSystemCapabilities(capabilities);
      })
      .catch(() => {
        if (alive) setSystemCapabilities(NO_SYSTEM_CAPABILITIES);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const onSystemShortcut = (event: globalThis.KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.code === "Space") {
        event.preventDefault();
        if (spotlightOpen) setSpotlightOpen(false);
        else openSpotlight();
        return;
      }
      if (
        event.ctrlKey &&
        (event.metaKey || event.altKey) &&
        event.code === "KeyQ" &&
        systemCapabilities.lock
      ) {
        event.preventDefault();
        const system = window.echo?.system;
        if (!system) {
          toast.error("当前不是 Echo OS 原生系统会话");
          return;
        }
        void system
          .runAction("lock")
          .then((result) => {
            if (!result.ok) toast.error(result.error || "系统锁屏失败");
          })
          .catch((error: unknown) => {
            toast.error(
              error instanceof Error ? error.message : "系统锁屏失败",
            );
          });
        return;
      }
      if (event.key !== "Escape") return;
      setSpotlightOpen(false);
      setLaunchpadOpen(false);
      setControlCenterOpen(false);
      setNotificationsOpen(false);
      setAboutOpen(false);
      setAccountSecurityOpen(false);
      setTaskSpaceOpen(false);
    };
    window.addEventListener("keydown", onSystemShortcut);
    return () => window.removeEventListener("keydown", onSystemShortcut);
  }, [spotlightOpen, systemCapabilities.lock]);

  const openApp = (app: DesktopApp) => {
    if (!isDeviceOperator && OPERATOR_ONLY_APP_ROUTES.has(app.route)) {
      toast.info("家庭成员不能打开设备级管理工具");
      return;
    }
    if (app.route === "/photos") {
      setPhotosOpen(true);
      setStorageCenterOpen(false);
      setDeviceLinkOpen(false);
      setHubOpen(false);
      setLaunchpadOpen(false);
      setSpotlightOpen(false);
      return;
    }
    if (app.route === "/storage-center") {
      setStorageCenterOpen(true);
      setPhotosOpen(false);
      setDeviceLinkOpen(false);
      setHubOpen(false);
      setLaunchpadOpen(false);
      setSpotlightOpen(false);
      return;
    }
    if (app.route === "/device-link") {
      setDeviceLinkOpen(true);
      setPhotosOpen(false);
      setStorageCenterOpen(false);
      setHubOpen(false);
      setLaunchpadOpen(false);
      setSpotlightOpen(false);
      return;
    }
    if (app.route === "/hub") {
      setHubOpen(true);
      setPhotosOpen(false);
      setStorageCenterOpen(false);
      setDeviceLinkOpen(false);
      setLaunchpadOpen(false);
      setSpotlightOpen(false);
      return;
    }
    if (app.name === "设置") {
      openSystemSettings();
      return;
    }
    // Agent 工作台类应用直接作为 React 内容开进桌面窗口。
    if (app.windowed && (IS_NATIVE_DESKTOP || !isElectronShell)) {
      openWindow({
        id: `agent-app:${app.route}`,
        title: app.name,
        url: resolveAgentAppUrl(app.route),
        content: <EmbeddedAgentWorkspace initialRoute={app.route} />,
        integratedChrome: true,
      });
      return;
    }
    navigate(app.route);
  };

  // Echo OS:Dock 的"本地应用"段只接真实数据(Docker 应用注册器)。
  // API 不可用或没有应用时保持为空，不能用不可启动的占位图标伪报能力。
  const { apps: applianceApps, refresh: refreshApplianceApps } =
    useApplianceApps();
  // 原生 shell:本地已装应用(仅 Electron 会话 shell;web 端为空,Dock 不变)。
  const reportNativeAppLaunchError = useCallback(
    (message: string) => toast.error(message),
    [],
  );
  const {
    apps: nativeApps,
    windows: nativeWindows,
    open: openNativeApp,
    focus: focusNativeWindow,
    minimize: minimizeNativeWindow,
    close: closeNativeWindow,
  } = useNativeApps({
    onLaunchError: reportNativeAppLaunchError,
  });
  const appStoreApp = nativeApps.find((app) => app.id === ECHO_APP_STORE_ID);
  const nativeFileManagerApp = findNativeFileManagerApp(nativeApps);
  const dockNativeApps = nativeApps.filter(
    (app) =>
      app.id !== ECHO_APP_STORE_ID &&
      app.id !== nativeFileManagerApp?.id &&
      !isNativeSystemSettingsApp(app),
  );
  const openAppStore = useCallback(() => {
    setPhotosOpen(false);
    setStorageCenterOpen(false);
    setDeviceLinkOpen(false);
    setHubOpen(true);
  }, []);
  useEffect(() => {
    const openHubFromCatalog = () => openAppStore();
    window.addEventListener(OPEN_ECHO_HUB_EVENT, openHubFromCatalog);
    return () =>
      window.removeEventListener(OPEN_ECHO_HUB_EVENT, openHubFromCatalog);
  }, [openAppStore]);
  const openFinder = () => {
    if (nativeFileManagerApp) {
      openNativeApp(nativeFileManagerApp);
      return;
    }
    openFiles();
  };
  const libraryApplianceApps = useMemo(
    () => applianceAppsForLibrary(applianceApps),
    [applianceApps],
  );
  const dockApplianceApps = useMemo(
    () => applianceAppsForDock(applianceApps),
    [applianceApps],
  );
  const openApplianceApp = (app: ApplianceApp) => {
    if (app.state !== "running") {
      if (!isDeviceOperator) {
        toast.info("请让设备管理员先启动这个应用");
        return;
      }
      setPendingAppControl({ operation: "start", app });
      return;
    }
    const url = appOpenUrl(app);
    // 原生路线:开成桌面内窗口;Electron 寄生模式仍走新标签(无窗口系统)。
    if (!url) return;
    if (IS_NATIVE_DESKTOP || !isElectronShell) {
      openWindow({ id: app.id, title: app.name, url });
    } else {
      window.open(url, "_blank", "noopener");
    }
  };

  const confirmAppControl = async (password: string) => {
    if (!pendingAppControl) return;
    const { app, operation } = pendingAppControl;
    const approval = await requestHighRiskApproval(
      operation === "start" ? "app.start" : "app.stop",
      app.id,
      password,
    );
    if (operation === "start") {
      await startApplianceApp(app.id, approval.approvalToken);
    } else {
      await stopApplianceApp(app.id, approval.approvalToken);
      closeWindow(app.id);
    }
    refreshApplianceApps();
    setPendingAppControl(null);
    toast.success(`${app.name} 已${operation === "start" ? "启动" : "停止"}`);
  };

  const resolveHubApplianceApp = async (
    hubApp: HubApp,
  ): Promise<ApplianceApp | null> => {
    const containerId = hubApp.installation.containerId;
    if (!containerId) return null;
    const cached = applianceApps.find((app) => app.id === containerId);
    if (cached) return cached;
    try {
      const latest = await fetchApplianceApps();
      if (!latest.available) return null;
      return latest.apps.find((app) => app.id === containerId) ?? null;
    } catch {
      return null;
    }
  };

  const openHubApplianceApp = async (hubApp: HubApp) => {
    const app = await resolveHubApplianceApp(hubApp);
    if (!app) {
      refreshApplianceApps();
      toast.error("应用入口仍在同步，请刷新 Hub 后重试");
      return;
    }
    setHubOpen(false);
    openApplianceApp(app);
  };

  const macShellApps: MacShellApp[] = [
    ...visibleDesktopApps.map((app) => ({
      id: `echo:${app.route}`,
      name: app.name,
      subtitle: app.subtitle,
      icon: app.icon,
      gradient: app.color,
      iconState:
        app.route === "/workspace/realtime/new" &&
        (taskProjection?.counts.active ?? 0) > 0
          ? ("thinking" as const)
          : undefined,
      running:
        app.route === "/photos"
          ? photosOpen
          : app.route === "/storage-center"
            ? storageCenterOpen
            : app.route === "/device-link"
              ? deviceLinkOpen
              : app.route === "/hub"
                ? hubOpen
                : app.windowed &&
                  windows.some((win) => win.id === `agent-app:${app.route}`),
      onOpen: () => openApp(app),
    })),
    ...libraryApplianceApps.map((app) => ({
      id: `appliance:${app.id}`,
      name: app.name,
      subtitle: app.description || app.status,
      icon: AppWindowIcon,
      iconUrl: app.icon || undefined,
      gradient: "linear-gradient(145deg, #f8fafc, #b8c2d0)",
      running: app.state === "running",
      muted: app.state !== "running",
      onOpen: () => openApplianceApp(app),
    })),
    ...nativeApps.slice(0, 12).map((app) => ({
      id: `native:${app.id}`,
      name: app.name,
      subtitle: app.source === "flatpak" ? "沙箱应用" : "本地应用",
      icon: AppWindowIcon,
      iconUrl: app.iconDataUrl || undefined,
      gradient: "linear-gradient(145deg, #f8fafc, #b8c2d0)",
      running: nativeWindows.some((item) => nativeWindowMatchesApp(item, app)),
      onOpen: () => openNativeApp(app),
    })),
  ];

  const desktopShortcuts: MacShellApp[] = [
    macShellApps[0]!,
    {
      id: "system:files",
      name: "文件",
      subtitle: "文件",
      icon: FolderIcon,
      gradient: "linear-gradient(145deg, #6bc9ff, #1d78d4)",
      onOpen: openFinder,
    },
    {
      ...macShellApps[2]!,
      name: "文档库",
    },
    ...(nativeFileManagerApp
      ? [
          {
            id: "system:disk",
            name: "Echo HD",
            subtitle: "系统磁盘",
            icon: MAC_SYSTEM_APPS.disk.icon,
            gradient: MAC_SYSTEM_APPS.disk.gradient,
            onOpen: openFinder,
          },
        ]
      : []),
  ].filter(Boolean);

  useEffect(() => {
    if (!organizerEnabled) return;
    const off = window.echo?.on?.("desktop:organize-now", () => {
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
      void window.echo?.window?.setMousePassthrough?.(false);
    };
  }, [organizerEnabled]);

  useEffect(() => {
    // 寄生模式专属:空白处鼠标穿透到真实桌面。原生 OS 桌面不需要。
    if (IS_NATIVE_DESKTOP || !organizerEnabled) return;
    if (!window.echo?.window?.setMousePassthrough) return;

    const setPassthrough = (enabled: boolean) => {
      if (mousePassthroughRef.current === enabled) return;
      mousePassthroughRef.current = enabled;
      void window.echo?.window?.setMousePassthrough?.(enabled).catch(() => {
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
    if (!window.echo?.desktop) return;
    setLoadingItems(true);
    setItemsError(null);
    window.echo.desktop
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
    if (!showWidget) {
      setSystemInfoStatus("idle");
      return;
    }
    const getSystemInfo = window.echo?.desktop?.getSystemInfo;
    if (!getSystemInfo) {
      setSystemInfo(null);
      setSystemInfoStatus("unavailable");
      return;
    }
    let alive = true;
    setSystemInfoStatus("loading");
    const poll = async () => {
      try {
        const result = await getSystemInfo();
        if (!alive) return;
        if (
          result.ok &&
          result.cpu &&
          result.memory &&
          typeof result.uptime === "number"
        ) {
          setSystemInfo({
            cpu: result.cpu,
            memory: result.memory,
            uptime: result.uptime,
          });
          setSystemInfoStatus("ready");
        } else {
          setSystemInfoStatus("error");
        }
      } catch {
        if (alive) setSystemInfoStatus("error");
      }
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
    const onClick = () => {
      setContextMenu(null);
      setDesktopMenu(null);
      setNativeWindowMenu(null);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  const updateWallpaper = (
    next: "orbit" | "aurora" | "sunset" | "midnight",
  ) => {
    setWallpaperVariant(next);
    localStorage.setItem("echo:desktop-wallpaper", next);
  };

  const cycleWallpaper = () => {
    const next =
      wallpaperVariant === "orbit"
        ? "aurora"
        : wallpaperVariant === "aurora"
          ? "sunset"
          : wallpaperVariant === "sunset"
            ? "midnight"
            : "orbit";
    updateWallpaper(next);
    setDesktopMenu(null);
  };

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
    void window.echo?.desktop?.openItem(item.path);
  };

  const refreshDesktopItems = () => {
    if (!window.echo?.desktop) return;
    setLoadingItems(true);
    setItemsError(null);
    window.echo.desktop
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
    if (!window.echo?.desktop?.moveItemsBatch) return;
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
      const result = await window.echo.desktop.moveItemsBatch(batch);
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
    if (!window.echo?.desktop?.undoMoves) return;
    setUndoing(true);
    try {
      const result = await window.echo.desktop.undoMoves();
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
      if (!window.echo?.desktop?.moveItem) return;
      const category = getDesktopItemCategory(item);
      if (category === "app" || category === "folder") {
        toast.info("仅支持归档文件");
        return;
      }
      try {
        const listResult = await window.echo.desktop.listItems();
        const desktopPath = listResult.ok ? listResult.desktopPath || "" : "";
        const folderName = ARCHIVE_FOLDER_MAP[category];
        if (!folderName || !desktopPath) return;
        const destDir = desktopPath + "\\" + folderName;
        const result = await window.echo.desktop.moveItem(item.path, destDir);
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
    const app = visibleDesktopApps.find((item) => {
      const haystack = `${item.name} ${item.subtitle}`.toLowerCase();
      return (
        haystack.includes(value) || value.includes(item.name.toLowerCase())
      );
    });
    if (app) {
      setSpotlightOpen(false);
      openApp(app);
      return;
    }
    setSpotlightOpen(false);
    navigate(`/browser?q=${encodeURIComponent(query.trim())}`);
  };

  const enableOrganizer = () => {
    localStorage.setItem(DESKTOP_ORGANIZER_ENABLED_KEY, "true");
    setOrganizerEnabled(true);
  };

  const availableSystemActions: MacSystemCapabilities = {
    lock: systemCapabilities.lock,
    logout: systemCapabilities.logout,
    suspend: systemCapabilities.suspend,
    restart: systemCapabilities.restart,
    shutdown: systemCapabilities.shutdown,
  };

  const closeTransientPanels = () => {
    setSpotlightOpen(false);
    setLaunchpadOpen(false);
    setControlCenterOpen(false);
    setNotificationsOpen(false);
    setAboutOpen(false);
    setAccountSecurityOpen(false);
    setTaskSpaceOpen(false);
  };

  const lockScreen = async () => {
    if (!availableSystemActions.lock) return;
    closeTransientPanels();
    const system = window.echo?.system;
    if (!system) {
      toast.error("当前不是 Echo OS 原生系统会话");
      return;
    }
    try {
      const result = await system.runAction("lock");
      if (!result.ok) toast.error(result.error || "系统锁屏失败");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "系统锁屏失败");
    }
  };

  const requestSystemAction = (action: MacSystemAction) => {
    if (!availableSystemActions[action]) return;
    closeTransientPanels();
    setSystemActionError(null);
    setPendingSystemAction(action);
  };

  const cancelSystemAction = () => {
    if (systemActionBusy) return;
    setPendingSystemAction(null);
    setSystemActionError(null);
  };

  const confirmSystemAction = async () => {
    if (!pendingSystemAction || systemActionBusy) return;
    const system = window.echo?.system;
    if (!system) {
      setSystemActionError("当前不是 Echo OS 原生系统会话");
      return;
    }
    setSystemActionBusy(true);
    setSystemActionError(null);
    try {
      const result = await system.runAction(pendingSystemAction);
      if (!result.ok) {
        setSystemActionError(result.error || "系统动作执行失败");
        return;
      }
      setPendingSystemAction(null);
    } catch (error) {
      setSystemActionError(
        error instanceof Error ? error.message : "系统动作执行失败",
      );
    } finally {
      setSystemActionBusy(false);
    }
  };

  // Appliance 认证门:需登录且未登录时显示原生登录屏。检测中(null)先不渲染
  // 桌面,避免未登录态一闪而过。
  if (applianceAuthed === false) {
    return (
      <>
        <ApplianceLogin
          onSuccess={() => {
            void retryAuth()
              .then(() => fetchApplianceAuthStatus())
              .then((status) => {
                setApplianceAuthRequired(status.authRequired);
                setApplianceRole(status.role);
                setApplianceAuthed(
                  !status.authRequired || status.authenticated,
                );
              })
              .catch(() => {
                setApplianceAuthRequired(true);
                setApplianceRole(null);
                setApplianceAuthed(false);
                toast.error("登录成功，但无法确认当前账号权限");
              });
          }}
          systemCapabilities={availableSystemActions}
          onSystemAction={requestSystemAction}
        />
        <MacSystemActionDialog
          action={pendingSystemAction}
          busy={systemActionBusy}
          error={systemActionError}
          onCancel={cancelSystemAction}
          onConfirm={() => void confirmSystemAction()}
        />
      </>
    );
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
                Echo
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

  const focusedWindowTitle = focusedWin
    ? windows.find((win) => win.id === focusedWin)?.title
    : null;
  const menuBarActiveApp =
    !focusedWindowTitle || focusedWindowTitle === "文件"
      ? "文件管理器"
      : focusedWindowTitle;

  return (
    <main
      aria-label="Echo OS 桌面"
      className={cn(
        "macos-desktop-root relative h-screen overflow-hidden bg-transparent text-white",
        `mac-wallpaper-${wallpaperVariant}`,
        `mac-liquid-${liquidGlassStyle}`,
      )}
      style={liquidGlassVariables}
      data-liquid-intensity={liquidGlassIntensity}
      data-liquid-custom="true"
      data-liquid-tuning={liquidGlassUsesNativeDefaults ? "default" : "custom"}
      data-liquid-transparency={liquidGlassTuning.transparency}
      data-liquid-refraction={liquidGlassTuning.refraction}
      data-liquid-frost={liquidGlassTuning.frost}
      data-liquid-thickness={liquidGlassTuning.thickness}
      data-liquid-dispersion={liquidGlassTuning.dispersion}
      data-liquid-saturation={liquidGlassTuning.saturation}
      data-liquid-tint={liquidGlassTuning.tint}
      data-liquid-tint-strength={liquidGlassTuning.tintStrength}
      onPointerMove={(event) => {
        const now = event.timeStamp || performance.now();
        const previousMotionSample = liquidMotionSampleRef.current;
        const motion = calculateLiquidGlassMotion(
          event.clientX - previousMotionSample.x,
          event.clientY - previousMotionSample.y,
          previousMotionSample.time > 0 ? now - previousMotionSample.time : 0,
        );
        liquidMotionSampleRef.current = {
          x: event.clientX,
          y: event.clientY,
          time: now,
        };
        liquidPointerRef.current = {
          x: (event.clientX / window.innerWidth) * 100,
          y: (event.clientY / window.innerHeight) * 100,
          clientX: event.clientX,
          clientY: event.clientY,
        };
        liquidSurfaceRef.current =
          event.target instanceof Element
            ? event.target.closest<HTMLElement>("[data-liquid-surface]")
            : null;
        if (liquidPointerFrameRef.current !== null) return;
        const root = event.currentTarget;
        liquidPointerFrameRef.current = window.requestAnimationFrame(() => {
          const surface = liquidSurfaceRef.current;
          const hasInteractiveGlass = !!(
            surface || activeLiquidSurfaceRef.current
          );
          if (hasInteractiveGlass) {
            root.style.setProperty(
              "--liquid-pointer-x",
              `${liquidPointerRef.current.x.toFixed(2)}%`,
            );
            root.style.setProperty(
              "--liquid-pointer-y",
              `${liquidPointerRef.current.y.toFixed(2)}%`,
            );
            root.style.setProperty(
              "--liquid-shift-x",
              `${((liquidPointerRef.current.x - 50) * 0.14).toFixed(2)}px`,
            );
            root.style.setProperty(
              "--liquid-shift-y",
              `${((liquidPointerRef.current.y - 50) * 0.1).toFixed(2)}px`,
            );
            root.style.setProperty(
              "--liquid-motion-x",
              `${(motion.x * 6).toFixed(2)}px`,
            );
            root.style.setProperty(
              "--liquid-motion-y",
              `${(motion.y * 5).toFixed(2)}px`,
            );
            root.style.setProperty(
              "--liquid-motion-energy",
              motion.energy.toFixed(3),
            );
            root.dataset.liquidMotion = motion.energy > 0 ? "active" : "idle";
          }

          if (activeLiquidSurfaceRef.current !== surface) {
            const previousSurface = activeLiquidSurfaceRef.current;
            previousSurface?.removeAttribute("data-liquid-active");
            previousSurface?.style.setProperty("--liquid-motion-x", "0px");
            previousSurface?.style.setProperty("--liquid-motion-y", "0px");
            previousSurface?.style.setProperty("--liquid-motion-energy", "0");
            surface?.setAttribute("data-liquid-active", "true");
            activeLiquidSurfaceRef.current = surface;
          }
          if (surface) {
            const bounds = surface.getBoundingClientRect();
            const localX = Math.max(
              0,
              Math.min(
                100,
                ((liquidPointerRef.current.clientX - bounds.left) /
                  Math.max(1, bounds.width)) *
                  100,
              ),
            );
            const localY = Math.max(
              0,
              Math.min(
                100,
                ((liquidPointerRef.current.clientY - bounds.top) /
                  Math.max(1, bounds.height)) *
                  100,
              ),
            );
            surface.style.setProperty(
              "--liquid-local-x",
              `${localX.toFixed(2)}%`,
            );
            surface.style.setProperty(
              "--liquid-local-y",
              `${localY.toFixed(2)}%`,
            );
            surface.style.setProperty(
              "--liquid-local-shift-x",
              `${((localX - 50) * 0.08).toFixed(2)}px`,
            );
            surface.style.setProperty(
              "--liquid-local-shift-y",
              `${((localY - 50) * 0.06).toFixed(2)}px`,
            );
            surface.style.setProperty(
              "--liquid-motion-x",
              `${(motion.x * 6).toFixed(2)}px`,
            );
            surface.style.setProperty(
              "--liquid-motion-y",
              `${(motion.y * 5).toFixed(2)}px`,
            );
            surface.style.setProperty(
              "--liquid-motion-energy",
              motion.energy.toFixed(3),
            );
          }

          if (liquidMotionResetTimerRef.current !== null) {
            window.clearTimeout(liquidMotionResetTimerRef.current);
          }
          if (surface) {
            const movingSurface = surface;
            liquidMotionResetTimerRef.current = window.setTimeout(() => {
              root.style.setProperty("--liquid-motion-x", "0px");
              root.style.setProperty("--liquid-motion-y", "0px");
              root.style.setProperty("--liquid-motion-energy", "0");
              root.dataset.liquidMotion = "idle";
              movingSurface?.style.setProperty("--liquid-motion-x", "0px");
              movingSurface?.style.setProperty("--liquid-motion-y", "0px");
              movingSurface?.style.setProperty("--liquid-motion-energy", "0");
              liquidMotionResetTimerRef.current = null;
            }, 72);
          } else {
            root.style.setProperty("--liquid-motion-x", "0px");
            root.style.setProperty("--liquid-motion-y", "0px");
            root.style.setProperty("--liquid-motion-energy", "0");
            root.dataset.liquidMotion = "idle";
          }
          liquidPointerFrameRef.current = null;
        });
      }}
      onContextMenu={(event) => {
        const target = event.target;
        if (
          target instanceof Element &&
          target.closest("[data-desktop-interactive]")
        ) {
          return;
        }
        event.preventDefault();
        setDesktopMenu({ x: event.clientX, y: event.clientY });
      }}
    >
      <MacLiquidGlassOptics />
      {/* 桌面壁纸。原生 OS 桌面始终铺壁纸；母体寄生模式仅浏览器铺，
          Electron 叠加时不渲染以让真实系统桌面透过来。 */}
      {(IS_NATIVE_DESKTOP || !isElectronShell) && (
        <div aria-hidden className="desktop-wallpaper absolute inset-0 z-0">
          <MacDesktopWallpaperArtwork />
        </div>
      )}
      <MacNativeLiquidGlass
        enabled={
          liquidGlassStyle === "crystal" && liquidGlassUsesNativeDefaults
        }
        wallpaper={wallpaperVariant}
      />
      <MacLiquidGlassWebGL />
      <div aria-hidden className="mac-liquid-atmosphere">
        <span className="mac-liquid-caustic is-a" />
        <span className="mac-liquid-caustic is-b" />
        <span className="mac-liquid-grain" />
      </div>
      <section className="relative z-10 flex h-full min-h-0 flex-col">
        <MacMenuBar
          activeApp={menuBarActiveApp}
          controlCenterOpen={controlCenterOpen}
          notificationsOpen={notificationsOpen}
          liquidGlassOpen={liquidGlassOpen}
          onOpenSpotlight={openSpotlight}
          onToggleControlCenter={toggleControlCenter}
          onToggleNotifications={toggleNotifications}
          onToggleLiquidGlass={toggleLiquidGlass}
          onOpenAbout={() => setAboutOpen(true)}
          onOpenFiles={openFinder}
          onOpenSettings={openSystemSettings}
          appStoreAvailable
          onOpenAppStore={openAppStore}
          onOpenLaunchpad={() => setLaunchpadOpen(true)}
          systemCapabilities={availableSystemActions}
          systemControls={systemControls}
          onLockScreen={lockScreen}
          onSystemAction={requestSystemAction}
          notificationCount={nativeNotifications.length}
        />
        <div className="relative min-h-0 flex-1 pt-[25px]">
          <MacDesktopWidgets
            agentHealth={agentDesktopHealth}
            onOpenWorkspace={() => openApp(DESKTOP_APPS[0]!)}
            onOpenNotifications={toggleNotifications}
          />
          <div className="mac-desktop-icons">
            {desktopShortcuts.map((app) => (
              <MacDesktopIcon key={app.id} app={app} />
            ))}
          </div>
        </div>

        {window.echo?.desktop && (
          <div
            aria-hidden={!desktopDrawerOpen}
            className={cn(
              "absolute inset-0 z-[65] transition-all duration-300 ease-out",
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
                        : `${nativeDesktopItems.length} 个桌面项目已收纳`}
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  {archiveResult && !itemsError && (
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                      已整理 {archiveResult.moved} 项
                    </span>
                  )}
                  {!itemsError && (
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
                  {!itemsError && (
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
                            const srcPath =
                              e.dataTransfer.getData("text/plain");
                            if (!srcPath || !window.echo?.desktop?.moveItem)
                              return;
                            const desktopPath = await window.echo.desktop
                              .listItems()
                              .then((r) => r.desktopPath || "");
                            const folderName = ARCHIVE_FOLDER_MAP[category.key];
                            if (!folderName) return;
                            const destDir = desktopPath + "\\" + folderName;
                            const result = await window.echo.desktop.moveItem(
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
        )}

        {desktopMenu && (
          <div
            data-desktop-interactive
            data-liquid-surface="thick"
            className="mac-desktop-context-menu"
            style={{
              left: Math.min(desktopMenu.x, window.innerWidth - 230),
              top: Math.min(desktopMenu.y, window.innerHeight - 260),
            }}
            onClick={(event) => {
              event.stopPropagation();
              setDesktopMenu(null);
            }}
          >
            <button
              type="button"
              onClick={() => toast.info("请在文件管理器中创建新文件夹")}
            >
              新建文件夹<span>Ctrl+Shift+N</span>
            </button>
            <div />
            <button type="button" onClick={() => setAboutOpen(true)}>
              显示简介<span>Ctrl+I</span>
            </button>
            <button type="button" onClick={cycleWallpaper}>
              更改墙纸…
            </button>
            <button type="button" onClick={() => setLiquidGlassOpen(true)}>
              流光玻璃…
              <span>{liquidGlassStyle === "crystal" ? "晶透" : "柔光"}</span>
            </button>
            {window.echo?.desktop && (
              <>
                <div />
                <button
                  type="button"
                  onClick={() => setDesktopDrawerOpen(true)}
                >
                  整理<span>Ctrl+0</span>
                </button>
              </>
            )}
            <button type="button" onClick={() => setLaunchpadOpen(true)}>
              显示应用库
            </button>
            <button type="button" onClick={() => setControlCenterOpen(true)}>
              查看显示选项
            </button>
          </div>
        )}

        {nativeWindowMenu && (
          <div
            data-desktop-interactive
            data-liquid-surface="thick"
            className="mac-desktop-context-menu"
            style={{
              left: Math.min(nativeWindowMenu.x, window.innerWidth - 230),
              top: Math.min(nativeWindowMenu.y, window.innerHeight - 210),
            }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => {
                focusNativeWindow(nativeWindowMenu.window.id);
                setNativeWindowMenu(null);
              }}
            >
              显示 {nativeWindowMenu.appName}
            </button>
            <button
              type="button"
              onClick={() => {
                minimizeNativeWindow(nativeWindowMenu.window.id);
                setNativeWindowMenu(null);
              }}
            >
              最小化
            </button>
            <div />
            <button
              type="button"
              onClick={() => {
                closeNativeWindow(nativeWindowMenu.window.id);
                setNativeWindowMenu(null);
              }}
            >
              退出
            </button>
          </div>
        )}

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

        <Dock className="mac-dock">
          <DockItem onClick={openFinder} title="文件" running>
            <MacAppIcon
              icon={FolderIcon}
              gradient="linear-gradient(145deg, #6fd0ff, #1c78d3)"
              appId="system:finder"
            />
          </DockItem>
          <DockItem onClick={() => setLaunchpadOpen(true)} title="应用库">
            <MacAppIcon
              icon={MAC_SYSTEM_APPS.launchpad.icon}
              gradient={MAC_SYSTEM_APPS.launchpad.gradient}
              appId="system:launchpad"
            />
          </DockItem>
          <DockItem onClick={openAppStore} title="Echo Hub" running={hubOpen}>
            <MacAppIcon
              icon={ShoppingBagIcon}
              iconUrl={appStoreApp?.iconDataUrl || undefined}
              gradient={MAC_SYSTEM_APPS.appStore.gradient}
              appId="system:app-store"
            />
          </DockItem>
          {DOCK_APPS.map((app) => {
            const Icon = app.icon;
            const running =
              app.route === "/photos"
                ? photosOpen
                : app.route === "/storage-center"
                  ? storageCenterOpen
                  : app.windowed &&
                    windows.some((win) => win.id === `agent-app:${app.route}`);
            return (
              <DockItem
                key={app.name}
                onClick={() => openApp(app)}
                title={app.name}
                running={running}
              >
                <MacAppIcon
                  icon={Icon}
                  gradient={app.color}
                  appId={`echo:${app.route}`}
                  state={
                    app.route === "/workspace/realtime/new" &&
                    (taskProjection?.counts.active ?? 0) > 0
                      ? "thinking"
                      : running
                        ? "active"
                        : "default"
                  }
                />
              </DockItem>
            );
          })}
          {dockApplianceApps.length > 0 && (
            <>
              <span className="mac-dock-separator" />
              {dockApplianceApps.map((app) => (
                <DockItem
                  key={app.id}
                  onClick={() => openApplianceApp(app)}
                  title={
                    app.state === "running"
                      ? `${app.name} · ${app.status}`
                      : `${app.name} · 已停止,点击启动`
                  }
                  className={cn(
                    app.state !== "running" && "opacity-55 saturate-50",
                  )}
                  running={app.state === "running"}
                >
                  <MacAppIcon
                    icon={AppWindowIcon}
                    iconUrl={app.icon || undefined}
                    gradient="linear-gradient(145deg, #f8fafc, #b8c2d0)"
                    appId={`appliance:${app.id}`}
                  />
                </DockItem>
              ))}
            </>
          )}
          {dockNativeApps.length > 0 && (
            <>
              <span className="mac-dock-separator" />
              {dockNativeApps.slice(0, 8).map((app) => (
                <DockItem
                  key={`native:${app.id}`}
                  onClick={() => openNativeApp(app)}
                  onContextMenu={(event) => {
                    const nativeWindow = nativeWindows.find((item) =>
                      nativeWindowMatchesApp(item, app),
                    );
                    if (!nativeWindow) return;
                    event.preventDefault();
                    event.stopPropagation();
                    setNativeWindowMenu({
                      x: event.clientX,
                      y: event.clientY,
                      appName: app.name,
                      window: nativeWindow,
                    });
                  }}
                  title={`${app.name} · ${app.source === "flatpak" ? "沙箱应用" : "本地应用"}`}
                  running={nativeWindows.some((item) =>
                    nativeWindowMatchesApp(item, app),
                  )}
                >
                  <MacAppIcon
                    icon={AppWindowIcon}
                    iconUrl={app.iconDataUrl || undefined}
                    gradient="linear-gradient(145deg, #f8fafc, #b8c2d0)"
                    appId={`native:${app.id}`}
                  />
                </DockItem>
              ))}
            </>
          )}
          <DockItem
            onClick={() => setTaskSpaceOpen(true)}
            title="任务空间"
            running={taskSpaceOpen}
          >
            <div className="relative">
              <MacAppIcon
                icon={ListChecksIcon}
                gradient="linear-gradient(145deg, #7c9cff, #4f46d8)"
                appId="system:tasks"
              />
              {(taskProjection?.counts.waitingApproval ?? 0) > 0 && (
                <span className="absolute -right-1 -top-1 grid min-w-4 place-items-center rounded-full border border-white/80 bg-amber-500 px-1 text-[9px] font-semibold leading-4 text-white shadow-sm">
                  {Math.min(taskProjection!.counts.waitingApproval, 99)}
                </span>
              )}
            </div>
          </DockItem>
          <DockItem
            onClick={() => setShowWidget((value) => !value)}
            title="活动监视器"
            running={showWidget}
          >
            <MacAppIcon
              icon={CpuIcon}
              gradient="linear-gradient(145deg, #29333f, #090d11)"
              appId="system:activity-monitor"
            />
          </DockItem>
          <DockItem onClick={openSystemSettings} title="系统设置">
            <MacAppIcon
              icon={MAC_SYSTEM_APPS.settings.icon}
              gradient={MAC_SYSTEM_APPS.settings.gradient}
              appId="system:settings"
            />
          </DockItem>
          <span className="mac-dock-separator" />
          <DockItem onClick={() => toast.info("废纸篓为空")} title="废纸篓">
            <MacAppIcon
              icon={Trash2Icon}
              gradient="linear-gradient(145deg, #f9fbfc, #aeb9c7)"
              appId="system:trash"
            />
          </DockItem>
        </Dock>
        {showWidget && (
          <div
            data-desktop-interactive
            role="dialog"
            aria-label="活动监视器"
            className="absolute bottom-24 right-8 z-20 w-64 rounded-2xl border border-white/34 bg-white/78 p-4 text-slate-800 shadow-2xl shadow-black/24 backdrop-blur-2xl"
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-500">
                活动监视器
              </span>
              <button
                type="button"
                onClick={() => setShowWidget(false)}
                aria-label="关闭活动监视器"
                className="grid size-5 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
              >
                <XIcon className="size-3" />
              </button>
            </div>
            {systemInfo ? (
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
            ) : (
              <div className="mt-3 rounded-xl border border-slate-200/80 bg-white/55 px-3 py-4 text-center">
                {systemInfoStatus === "loading" ? (
                  <Loader2Icon className="mx-auto size-5 animate-spin text-blue-500" />
                ) : (
                  <MonitorIcon className="mx-auto size-5 text-slate-400" />
                )}
                <p className="mt-2 text-xs font-medium text-slate-600">
                  {systemInfoStatus === "loading"
                    ? "正在读取系统状态…"
                    : systemInfoStatus === "error"
                      ? "暂时无法读取系统状态"
                      : "浏览器模式未连接系统监控"}
                </p>
                {systemInfoStatus !== "loading" && (
                  <p className="mt-1 text-[10px] leading-4 text-slate-400">
                    在 Echo OS 桌面客户端中可查看实时 CPU、内存与运行时间。
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </section>

      {fileManagerOpen && (
        <FileManager
          onClose={() => setFileManagerOpen(false)}
          onOpenSystemFiles={
            nativeFileManagerApp
              ? () => {
                  setFileManagerOpen(false);
                  openNativeApp(nativeFileManagerApp);
                }
              : undefined
          }
          onOpenSettings={() => {
            setFileManagerOpen(false);
            openStorageSettings();
          }}
        />
      )}

      <HubPanel
        open={hubOpen}
        canManageDevice={isDeviceOperator}
        onClose={() => setHubOpen(false)}
        onAppsChanged={refreshApplianceApps}
        onOpenDeviceApp={(app) => void openHubApplianceApp(app)}
        onOpenAgentAssets={(asset) => {
          setHubOpen(false);
          openWindow({
            id: agentAssetWindowId(asset),
            title: asset.kind === "skill" ? "Agent 技能" : "Agent 插件",
            url: resolveAgentAppUrl(agentAssetManagementRoute(asset)),
            content: (
              <EmbeddedAgentWorkspace
                initialRoute={agentAssetManagementRoute(asset)}
              />
            ),
            integratedChrome: true,
          });
        }}
      />

      <PhotosPanel open={photosOpen} onClose={() => setPhotosOpen(false)} />

      {isDeviceOperator && (
        <StorageCenterPanel
          open={storageCenterOpen}
          onClose={() => setStorageCenterOpen(false)}
          onOpenFiles={() => {
            setStorageCenterOpen(false);
            openFiles();
          }}
        />
      )}

      {isDeviceOperator && (
        <DeviceLinkPanel
          open={deviceLinkOpen}
          onClose={() => setDeviceLinkOpen(false)}
        />
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

      {/* System-owned surfaces are siblings of application windows so their
          z-index is not trapped inside the desktop-content stacking context. */}
      <MacSpotlight
        open={spotlightOpen}
        query={query}
        apps={macShellApps}
        onQueryChange={setQuery}
        onClose={() => setSpotlightOpen(false)}
        onSubmit={submit}
      />
      <MacLaunchpad
        open={launchpadOpen}
        apps={macShellApps}
        onClose={() => setLaunchpadOpen(false)}
      />
      <MacControlCenter
        open={controlCenterOpen}
        onClose={() => setControlCenterOpen(false)}
        onOpenSettings={openSystemSettings}
        systemControls={systemControls}
        onSetWifiEnabled={(enabled) =>
          applySystemControl("Wi-Fi", () =>
            window.echo!.systemControls!.setWifiEnabled(enabled),
          )
        }
        onSetBluetoothEnabled={(enabled) =>
          applySystemControl("蓝牙", () =>
            window.echo!.systemControls!.setBluetoothEnabled(enabled),
          )
        }
        onSetAudioVolume={(percentage) =>
          applySystemControl("音量", () =>
            window.echo!.systemControls!.setAudioVolume(percentage),
          )
        }
        onSetDisplayBrightness={(percentage) =>
          applySystemControl("显示器亮度", () =>
            window.echo!.systemControls!.setDisplayBrightness(percentage),
          )
        }
      />
      <MacLiquidGlassPanel
        open={liquidGlassOpen}
        style={liquidGlassStyle}
        intensity={liquidGlassIntensity}
        tuning={liquidGlassTuning}
        onStyleChange={updateLiquidGlassStyle}
        onIntensityChange={updateLiquidGlassIntensity}
        onTuningChange={updateLiquidGlassTuning}
        onResetTuning={resetLiquidGlassTuning}
        onClose={() => setLiquidGlassOpen(false)}
      />
      <MacNotificationCenter
        open={notificationsOpen}
        onClose={() => setNotificationsOpen(false)}
        notifications={nativeNotifications}
        nativeServiceAvailable={notificationServiceAvailable}
        onDismiss={(notificationId) => {
          void dismissNativeNotification(notificationId);
        }}
        onClear={() => {
          void clearNativeNotifications();
        }}
      />
      <MacAboutDialog
        open={aboutOpen}
        onClose={() => setAboutOpen(false)}
        onOpenSettings={openSystemSettings}
        agentHealth={agentDesktopHealth}
        updateStatus={systemUpdateStatus}
        updateCapabilities={systemUpdateCapabilities}
        updateBusy={systemUpdateBusy}
        onRefreshUpdate={() => void refreshSystemUpdate()}
        onApplyUpdate={() => void applySystemUpdate()}
        onRestart={
          availableSystemActions.restart
            ? () => requestSystemAction("restart")
            : undefined
        }
      />
      {isDeviceOperator && (
        <AccountSecurityPanel
          open={accountSecurityOpen}
          initialSection={accountSecuritySection}
          initialAgentSection={agentSettingsSection}
          systemDeviceSettings={{
            controls: systemControls,
            wallpaper: wallpaperVariant,
            notificationCount: nativeNotifications.length,
            notificationServiceAvailable,
            updateCapabilities: systemUpdateCapabilities,
            updateStatus: systemUpdateStatus,
            updateBusy: systemUpdateBusy,
            lockAvailable: availableSystemActions.lock,
            onSetWifiEnabled: (enabled) =>
              applySystemControl("Wi-Fi", () =>
                window.echo!.systemControls!.setWifiEnabled(enabled),
              ),
            onSetBluetoothEnabled: (enabled) =>
              applySystemControl("蓝牙", () =>
                window.echo!.systemControls!.setBluetoothEnabled(enabled),
              ),
            onSetAudioVolume: (percentage) =>
              applySystemControl("音量", () =>
                window.echo!.systemControls!.setAudioVolume(percentage),
              ),
            onSetDisplayBrightness: (percentage) =>
              applySystemControl("显示器亮度", () =>
                window.echo!.systemControls!.setDisplayBrightness(percentage),
              ),
            onWallpaperChange: updateWallpaper,
            onOpenNotifications: () => {
              setAccountSecurityOpen(false);
              setNotificationsOpen(true);
            },
            onLock: () => void lockScreen(),
            onRefreshUpdate: () => void refreshSystemUpdate(),
            onApplyUpdate: () => void applySystemUpdate(),
          }}
          onClose={() => setAccountSecurityOpen(false)}
          onSessionEnded={(message) => {
            setAccountSecurityOpen(false);
            setApplianceRole(null);
            setApplianceAuthed(false);
            toast.success(message);
          }}
        />
      )}
      <TaskSpacePanel
        open={taskSpaceOpen}
        projection={taskProjection}
        loading={taskProjectionLoading}
        error={taskProjectionError}
        onClose={() => setTaskSpaceOpen(false)}
        onRefresh={refreshTaskProjection}
        onTakeover={takeoverTaskProjection}
        onResumeExecution={resumeTaskProjection}
        onOpenWorkspace={(task) => {
          setTaskSpaceOpen(false);
          if (task?.threadId) {
            openWindow({
              id: `agent-task:${task.id}`,
              title: `任务 · ${task.title}`,
              url: resolveAgentAppUrl(
                `/workspace/realtime/${encodeURIComponent(task.threadId)}`,
              ),
              content: (
                <EmbeddedAgentWorkspace
                  initialRoute={`/workspace/realtime/${encodeURIComponent(task.threadId)}`}
                />
              ),
              integratedChrome: true,
            });
            return;
          }
          openApp(DESKTOP_APPS[0]!);
        }}
      />
      <MacSystemActionDialog
        action={pendingSystemAction}
        busy={systemActionBusy}
        error={systemActionError}
        onCancel={cancelSystemAction}
        onConfirm={() => void confirmSystemAction()}
      />

      <HighRiskApprovalDialog
        open={pendingAppControl !== null}
        title={`${pendingAppControl?.operation === "stop" ? "停止" : "启动"}“${pendingAppControl?.app.name ?? "应用"}”？`}
        description={
          pendingAppControl?.operation === "stop"
            ? "停止后应用页面将不可访问，但配置和 NAS 数据不会删除；之后可以随时重新启动。"
            : "启动容器会改变设备运行状态并占用存储、内存和网络端口，需要管理员本人复核。"
        }
        targetLabel={
          pendingAppControl?.app.description || pendingAppControl?.app.image
        }
        confirmLabel={
          pendingAppControl?.operation === "stop" ? "确认停止" : "确认启动"
        }
        onCancel={() => setPendingAppControl(null)}
        onConfirm={confirmAppControl}
      />
    </main>
  );
}
