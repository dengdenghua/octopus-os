/* Implementation note. */

import {
  ArrowLeftIcon,
  ArrowRightIcon,
  MinusIcon,
  MoreHorizontalIcon,
  PanelLeftIcon,
  PlusIcon,
  RefreshCwIcon,
  HouseIcon,
  ImageIcon,
  LaptopIcon,
  PuzzleIcon,
  TabletIcon,
  SmartphoneIcon,
  SparklesIcon,
  StarIcon,
  ShieldCheckIcon,
  ClockIcon,
  Trash2Icon,
  ExternalLinkIcon,
  SearchIcon,
  RotateCcwIcon,
  DownloadIcon,
  FolderOpenIcon,
  FileIcon,
  CheckCircleIcon,
  AlertCircleIcon,
  CookieIcon,
  KeyRoundIcon,
  Settings2Icon,
  PauseIcon,
  PlayIcon,
  XIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";
import { toast } from "sonner";

import {
  queueComposerImageEntry,
  readLastComposerTarget,
} from "@/core/composer-image-inbox";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { swallow } from "@/core/utils/log";
import { jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import {
  BROWSER_AGENT_POLICY_EVENT,
  clearBrowserAgentAudit,
  listBrowserAgentAudit,
  listBrowserAgentPermissions,
  setBrowserAgentPermission,
  type BrowserAgentAuditEntry,
  type BrowserAgentSitePermission,
} from "@/core/browser/agent-permissions";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import type { DevicePreset } from "../workspace/embedded-browser/browser-context";
import {
  BROWSER_EDIT_HOME_EVENT,
  BROWSER_HOME_URL,
  SEARCH_ENGINE_URLS,
  useBrowserStore,
  type Bookmark,
  type HistoryEntry,
} from "./browser-store";
import type { WebviewTabHandle } from "./webview-tab";

const DEVICE_ORDER: DevicePreset[] = ["desktop", "tablet", "mobile"];
const DEVICE_ICONS = {
  desktop: LaptopIcon,
  tablet: TabletIcon,
  mobile: SmartphoneIcon,
} as const;

type BrowserDownloadState =
  | "progressing"
  | "completed"
  | "cancelled"
  | "interrupted";

interface BrowserDownload {
  id: string;
  filename: string;
  url: string;
  state: BrowserDownloadState;
  receivedBytes: number;
  totalBytes: number;
  createdAt: number;
  paused?: boolean;
  canResume?: boolean;
  risk?: "low" | "medium" | "high";
  sourceOrigin?: string;
}

interface StoredBrowserPassword {
  id: string;
  origin: string;
  username: string;
  updatedAt: number;
}

interface StoredSitePermission {
  origin: string;
  permission:
    | "camera"
    | "microphone"
    | "camera-microphone"
    | "location"
    | "notifications"
    | "clipboard";
  decision: "allow" | "block";
  updatedAt: number;
}

const SITE_PERMISSION_LABELS: Record<
  StoredSitePermission["permission"],
  string
> = {
  camera: "摄像头",
  microphone: "麦克风",
  "camera-microphone": "摄像头和麦克风",
  location: "位置信息",
  notifications: "通知",
  clipboard: "剪贴板读取",
};

const DOWNLOAD_HISTORY_KEY = "echo:browser-download-history.v1";

function loadDownloadHistory(): BrowserDownload[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(DOWNLOAD_HISTORY_KEY) || "[]",
    );
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is BrowserDownload =>
          Boolean(item) &&
          typeof item.id === "string" &&
          typeof item.filename === "string" &&
          typeof item.url === "string" &&
          typeof item.createdAt === "number",
      )
      .slice(0, 50);
  } catch (error) {
    swallow(error, "download-history");
    return [];
  }
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

function looksLikeUrl(input: string): boolean {
  if (/^https?:\/\//i.test(input)) return true;
  if (/^[a-zA-Z0-9-]+:\/\//.test(input)) return true;
  if (/\s/.test(input)) return false;
  return /\./.test(input);
}

function normalize(input: string, searchUrl: string): string {
  const trimmed = input.trim();
  if (!trimmed) return "";
  if (looksLikeUrl(trimmed)) {
    return /^[a-zA-Z]+:\/\//.test(trimmed) ? trimmed : `https://${trimmed}`;
  }
  return `${searchUrl}${encodeURIComponent(trimmed)}`;
}

interface Props {
  webviewHandle: WebviewTabHandle | null;
  onOpenExtensions?: () => void;
}

export function UrlBar({ webviewHandle, onOpenExtensions }: Props) {
  const { t } = useI18n();
  const ub = t.browser.urlBar;
  const bp = t.browserPreviewPanel;
  const {
    activeTab,
    patchTab,
    toggleCopilot,
    state,
    history,
    bookmarks,
    addBookmark,
    removeBookmark,
    isBookmarked,
    clearHistory,
    settings,
  } = useBrowserStore();
  const [draft, setDraft] = useState(activeTab?.url ?? "");
  const [canBack, setCanBack] = useState(false);
  const [canForward, setCanForward] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [siteInfoOpen, setSiteInfoOpen] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [suggestionIndex, setSuggestionIndex] = useState(-1);
  const [downloadsOpen, setDownloadsOpen] = useState(false);
  const [downloads, setDownloads] =
    useState<BrowserDownload[]>(loadDownloadHistory);
  const [dataCenterOpen, setDataCenterOpen] = useState(false);
  const [clearingBrowsingData, setClearingBrowsingData] = useState(false);
  const [siteDataStatus, setSiteDataStatus] = useState<string | null>(null);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [zoomByTab, setZoomByTab] = useState<Record<string, number>>({});
  const [findDialogOpen, setFindDialogOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const historyBtnRef = useRef<HTMLButtonElement>(null);
  const downloadsBtnRef = useRef<HTMLButtonElement>(null);
  const actionsBtnRef = useRef<HTMLButtonElement>(null);
  const addressBarRef = useRef<HTMLDivElement>(null);
  const siteInfoBtnRef = useRef<HTMLButtonElement>(null);
  const siteInfoPanelRef = useRef<HTMLDivElement>(null);
  const { confirm, confirmDialog } = useConfirmDialog();

  const deviceLabelMap = useMemo<Record<DevicePreset, string>>(
    () => ({
      desktop: ub.deviceDesktop,
      tablet: ub.deviceTablet,
      mobile: ub.deviceMobile,
    }),
    [ub],
  );

  useEffect(() => {
    setDraft(activeTab?.url ?? "");
  }, [activeTab?.id, activeTab?.url]);

  useEffect(() => {
    if (!webviewHandle) {
      setCanBack(false);
      setCanForward(false);
      return;
    }
    setCanBack(webviewHandle.canGoBack());
    setCanForward(webviewHandle.canGoForward());
  }, [webviewHandle, activeTab?.isLoading, activeTab?.url]);

  const submit = useCallback(() => {
    const target = normalize(draft, SEARCH_ENGINE_URLS[settings.searchEngine]);
    if (!target) return;
    if (activeTab && webviewHandle) {
      webviewHandle.loadURL(target);
      patchTab(activeTab.id, { url: target });
    }
    setSuggestionsOpen(false);
    setSuggestionIndex(-1);
  }, [activeTab, draft, patchTab, settings.searchEngine, webviewHandle]);

  const addressSuggestions = useMemo(() => {
    const query = draft.trim().toLowerCase();
    if (!query) return [];
    const seen = new Set<string>();
    const merged = [
      ...bookmarks.map((item) => ({ ...item, source: "bookmark" as const })),
      ...history.map((item) => ({ ...item, source: "history" as const })),
    ];
    return merged
      .filter((item) => {
        if (!item.url || seen.has(item.url)) return false;
        seen.add(item.url);
        const haystack = `${item.title || ""} ${item.url}`.toLowerCase();
        return haystack.includes(query);
      })
      .slice(0, 6);
  }, [bookmarks, draft, history]);

  const onDeviceChange = useCallback(
    (device: DevicePreset) => {
      if (activeTab) patchTab(activeTab.id, { device });
    },
    [activeTab, patchTab],
  );

  const toggleBookmark = useCallback(() => {
    if (!activeTab) return;
    if (isBookmarked(activeTab.url)) {
      removeBookmark(activeTab.url);
    } else {
      addBookmark({
        url: activeTab.url,
        title: activeTab.title || activeTab.url,
        favicon: activeTab.favicon,
      });
    }
  }, [activeTab, addBookmark, removeBookmark, isBookmarked]);

  const goHome = useCallback(() => {
    if (!activeTab) return;
    patchTab(activeTab.id, {
      url: BROWSER_HOME_URL,
      title: t.browser.webviewTab.aiBrowserDesktop,
      isLoading: false,
    });
    setDraft(BROWSER_HOME_URL);
  }, [activeTab, patchTab, t.browser.webviewTab.aiBrowserDesktop]);

  const customizeHome = useCallback(() => {
    window.dispatchEvent(new Event(BROWSER_EDIT_HOME_EVENT));
  }, []);

  const attachScreenshotToNextComposer = useCallback(async () => {
    if (!webviewHandle) return;
    try {
      const shot = await webviewHandle.capturePage();
      if (!shot?.dataUrl) {
        toast.error(bp.attachScreenshotFailed);
        return;
      }
      const host = (() => {
        try {
          return activeTab?.url ? new URL(activeTab.url).hostname : "browser";
        } catch {
          return "browser";
        }
      })();
      queueComposerImageEntry({
        dataUrl: shot.dataUrl,
        filename: `browser-shot-${host}-${Date.now()}.png`,
        sourceLabel: bp.attachScreenshotSource,
      });
      const target = readLastComposerTarget();
      if (target) {
        window.location.hash = target;
      }
      toast.success(bp.attachScreenshotSuccess);
    } catch (error) {
      swallow(error);
      toast.error(bp.attachScreenshotFailed);
    }
  }, [activeTab?.url, bp, webviewHandle]);

  const goTo = useCallback(
    (url: string) => {
      if (activeTab && webviewHandle) {
        webviewHandle.loadURL(url);
        patchTab(activeTab.id, { url });
        setDraft(url);
      }
      setHistoryOpen(false);
      setSuggestionsOpen(false);
      setSuggestionIndex(-1);
    },
    [activeTab, patchTab, webviewHandle],
  );

  const onKey = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "ArrowDown" && addressSuggestions.length > 0) {
        e.preventDefault();
        setSuggestionsOpen(true);
        setSuggestionIndex((i) =>
          Math.min(i + 1, addressSuggestions.length - 1),
        );
        return;
      }
      if (e.key === "ArrowUp" && addressSuggestions.length > 0) {
        e.preventDefault();
        setSuggestionsOpen(true);
        setSuggestionIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter") {
        if (
          suggestionsOpen &&
          suggestionIndex >= 0 &&
          addressSuggestions[suggestionIndex]
        ) {
          e.preventDefault();
          goTo(addressSuggestions[suggestionIndex].url);
          return;
        }
        submit();
      }
      if (e.key === "Escape") {
        setDraft(activeTab?.url ?? "");
        setSuggestionsOpen(false);
        setSuggestionIndex(-1);
      }
    },
    [
      activeTab?.url,
      addressSuggestions,
      goTo,
      submit,
      suggestionIndex,
      suggestionsOpen,
    ],
  );

  const clearCurrentSiteData = useCallback(async () => {
    if (!webviewHandle || !window.echo?.browser) return;
    const wcId = webviewHandle.getWebContentsId();
    if (wcId == null) return;
    const confirmed = await confirm({
      title: ub.clearData,
      description: ub.confirmClearSiteData,
      confirmLabel: ub.clearData,
    });
    if (!confirmed) return;
    setSiteDataStatus(ub.clearingSiteData);
    try {
      const result = await window.echo.browser.clearSiteData(wcId);
      if (!result.ok) {
        setSiteDataStatus(result.error || ub.clearFailed);
        return;
      }
      setSiteDataStatus(ub.siteCleared(result.origin || ub.currentSite));
      setSiteInfoOpen(false);
      webviewHandle.reload();
    } catch (error) {
      swallow(error);
      setSiteDataStatus(
        error instanceof Error ? error.message : ub.clearFailed,
      );
    }
  }, [confirm, ub, webviewHandle]);

  const clearAllBrowsingData = useCallback(async () => {
    const confirmed = await confirm({
      title: "清除浏览数据",
      description:
        "将清除 Echo 浏览器中的 Cookie、缓存、网站存储、浏览历史和下载记录。所有网站会退出登录，但不会删除已下载的文件。",
      confirmLabel: "确认清除",
    });
    if (!confirmed) return;
    setClearingBrowsingData(true);
    try {
      if (window.echo?.browser?.clearBrowsingData) {
        const result = await window.echo.browser.clearBrowsingData();
        if (!result.ok) throw new Error(result.error || "清除失败");
      } else {
        const response = await fetch(
          `${getBackendBaseURL()}/api/browser/data/clear`,
          { method: "POST", headers: jsonAuthHeaders() },
        );
        if (!response.ok) {
          const detail = (await response.json().catch(() => null)) as {
            detail?: string;
          } | null;
          throw new Error(detail?.detail || `HTTP ${response.status}`);
        }
      }
      clearHistory();
      setDownloads([]);
      setSiteDataStatus(null);
      setDataCenterOpen(false);
      toast.success("浏览数据已清除");
      webviewHandle?.reload();
    } catch (error) {
      swallow(error, "clear-browser-data");
      toast.error(error instanceof Error ? error.message : "清除浏览数据失败");
    } finally {
      setClearingBrowsingData(false);
    }
  }, [clearHistory, confirm, webviewHandle]);

  const siteOrigin = useMemo(() => {
    if (!activeTab?.url) return null;
    try {
      const parsed = new URL(activeTab.url);
      if (!["http:", "https:"].includes(parsed.protocol)) return null;
      return parsed.origin;
    } catch (e) {
      swallow(e);
      return null;
    }
  }, [activeTab?.url]);

  const openExternalCurrentSite = useCallback(() => {
    if (!activeTab?.url || !window.echo?.app) return;
    void window.echo.app.openExternal(activeTab.url);
    setSiteInfoOpen(false);
  }, [activeTab?.url]);

  const device = activeTab?.device ?? "desktop";
  const activeZoom = activeTab ? (zoomByTab[activeTab.id] ?? 100) : 100;
  const bookmarked = activeTab ? isBookmarked(activeTab.url) : false;
  const activeDownloadCount = downloads.filter(
    (d) => d.state === "progressing",
  ).length;
  const latestDownload = downloads[0] ?? null;
  const canManageSiteData =
    typeof window !== "undefined" &&
    !!window.echo?.browser &&
    !!activeTab?.url &&
    (activeTab.url.startsWith("http://") ||
      activeTab.url.startsWith("https://"));
  const canUsePageActions = Boolean(
    activeTab?.url &&
    !activeTab.url.startsWith("echo:") &&
    !activeTab.url.startsWith("about:"),
  );

  const applyZoom = useCallback(
    (nextZoom: number) => {
      const normalized = Math.max(50, Math.min(200, nextZoom));
      if (activeTab) {
        setZoomByTab((prev) => ({ ...prev, [activeTab.id]: normalized }));
      }
      if (!webviewHandle || !canUsePageActions) return;
      const scale = (normalized / 100).toFixed(2);
      void webviewHandle.executeJS(`
        (() => {
          const scale = ${JSON.stringify(scale)};
          document.documentElement.style.zoom = scale;
          document.body.style.zoom = scale;
          return true;
        })();
      `);
    },
    [activeTab, canUsePageActions, webviewHandle],
  );

  const findInPage = useCallback(() => {
    if (!webviewHandle || !canUsePageActions) return;
    setFindQuery("");
    setActionsOpen(false);
    setFindDialogOpen(true);
  }, [canUsePageActions, webviewHandle]);

  const executeFind = useCallback(() => {
    if (!webviewHandle || !findQuery.trim()) {
      setFindDialogOpen(false);
      return;
    }
    void webviewHandle.executeJS(`
      (() => window.find(${JSON.stringify(findQuery.trim())}, false, false, true, false, true, false))();
    `);
    setFindDialogOpen(false);
  }, [findQuery, webviewHandle]);

  useEffect(() => {
    if (!window.echo?.on) return;
    return window.echo.on("browser:download-event", (payload) => {
      const item = payload as BrowserDownload;
      if (!item?.id) return;
      setDownloads((prev) => {
        const next = [
          item,
          ...prev.filter((download) => download.id !== item.id),
        ].sort((a, b) => b.createdAt - a.createdAt);
        return next.slice(0, 20);
      });
      setDownloadsOpen(true);
    });
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        DOWNLOAD_HISTORY_KEY,
        JSON.stringify(downloads.slice(0, 50)),
      );
    } catch (error) {
      swallow(error, "download-history");
    }
  }, [downloads]);

  useEffect(() => {
    if (!siteInfoOpen) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (siteInfoPanelRef.current?.contains(target)) return;
      if (siteInfoBtnRef.current?.contains(target)) return;
      setSiteInfoOpen(false);
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [siteInfoOpen]);

  useEffect(() => {
    setSiteInfoOpen(false);
  }, [activeTab?.id, activeTab?.url]);

  useEffect(() => {
    if (!activeTab || !webviewHandle || !canUsePageActions) return;
    const zoom = zoomByTab[activeTab.id] ?? 100;
    const scale = (zoom / 100).toFixed(2);
    void webviewHandle.executeJS(`
      (() => {
        const scale = ${JSON.stringify(scale)};
        document.documentElement.style.zoom = scale;
        document.body.style.zoom = scale;
        return true;
      })();
    `);
  }, [
    activeTab?.id,
    activeTab?.url,
    activeTab,
    canUsePageActions,
    webviewHandle,
    zoomByTab,
  ]);

  useEffect(() => {
    if (!suggestionsOpen) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (addressBarRef.current?.contains(target)) return;
      setSuggestionsOpen(false);
      setSuggestionIndex(-1);
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [suggestionsOpen]);

  useEffect(() => {
    setSuggestionIndex(-1);
    if (draft.trim().length === 0 || addressSuggestions.length === 0) {
      setSuggestionsOpen(false);
    }
  }, [addressSuggestions.length, draft]);

  return (
    <div
      className="flex h-12 min-w-0 items-center gap-1 rounded-none border-x-0 border-border-subtle px-2 sm:px-3"
      style={{ WebkitAppRegion: "no-drag" } as CSSProperties}
    >
      <button
        onClick={() => webviewHandle?.goBack()}
        disabled={!canBack}
        className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground disabled:pointer-events-none disabled:opacity-25"
        title={ub.back}
      >
        <ArrowLeftIcon className="size-4" />
      </button>
      <button
        onClick={() => webviewHandle?.goForward()}
        disabled={!canForward}
        className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground disabled:pointer-events-none disabled:opacity-25"
        title={ub.forward}
      >
        <ArrowRightIcon className="size-4" />
      </button>
      <button
        onClick={() => webviewHandle?.reload()}
        disabled={activeTab?.url === BROWSER_HOME_URL}
        className="hidden size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground disabled:pointer-events-none disabled:opacity-25 sm:grid"
        title={ub.refresh}
      >
        <RefreshCwIcon className="size-4" />
      </button>
      <div ref={addressBarRef} className="relative ml-1 min-w-0 flex-1">
        <div className="flex h-9 items-center gap-1 rounded-xl border border-border-subtle bg-card/80 px-3 backdrop-blur-sm transition-colors focus-within:border-primary/30 focus-within:ring-2 focus-within:ring-primary/12">
          <input
            type="text"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setSuggestionsOpen(e.target.value.trim().length > 0);
            }}
            onKeyDown={onKey}
            onFocus={(e) => {
              e.currentTarget.select();
              if (addressSuggestions.length > 0) setSuggestionsOpen(true);
            }}
            placeholder={ub.searchOrUrl}
            className="min-w-0 flex-1 bg-transparent px-2 text-sm font-medium outline-none placeholder:text-muted-foreground/65"
          />
          {canManageSiteData && (
            <div className="relative">
              <button
                ref={siteInfoBtnRef}
                onClick={() => setSiteInfoOpen((v) => !v)}
                className={cn(
                  "grid size-7 place-items-center rounded-md transition-colors",
                  siteInfoOpen
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground",
                )}
                title={ub.siteInfo}
                aria-label={ub.siteInfo}
              >
                <ShieldCheckIcon className="size-3.5" />
              </button>
              {siteInfoOpen && (
                <div
                  ref={siteInfoPanelRef}
                  className="absolute right-0 top-full z-50 mt-2 w-[320px] max-w-[calc(100vw-1rem)] rounded-xl bg-popover p-3 text-popover-foreground shadow-lg"
                >
                  <div className="flex items-start gap-2.5">
                    <div className="grid size-8 shrink-0 place-items-center rounded-full bg-primary/10 text-primary">
                      <ShieldCheckIcon className="size-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-semibold">{ub.siteInfo}</div>
                      <div className="mt-0.5 truncate text-xs text-muted-foreground">
                        {siteOrigin || activeTab?.url}
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 rounded-lg bg-muted/50 px-3 py-2 text-xs leading-5 text-muted-foreground">
                    {ub.siteInfoDesc}
                  </div>
                  {siteDataStatus && (
                    <div className="mt-2 rounded-lg border border-border-default px-3 py-2 text-xs text-muted-foreground">
                      {siteDataStatus}
                    </div>
                  )}
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      onClick={clearCurrentSiteData}
                      className="flex h-8 flex-1 items-center justify-center gap-1.5 border border-border-default bg-background text-xs font-medium text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                    >
                      <Trash2Icon className="size-3.5" />
                      {ub.clearData}
                    </button>
                    <button
                      onClick={openExternalCurrentSite}
                      className="flex h-8 flex-1 items-center justify-center gap-1.5 bg-primary text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90"
                    >
                      <ExternalLinkIcon className="size-3.5" />
                      {ub.openExternally}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
          {/* Implementation note. */}
          {activeTab &&
            activeTab.url &&
            !activeTab.url.startsWith("about:") &&
            !activeTab.url.startsWith("echo:") && (
              <button
                onClick={toggleBookmark}
                className={cn(
                  "grid size-7 place-items-center rounded-full transition-colors",
                  bookmarked
                    ? "text-warning hover:bg-warning/10"
                    : "text-muted-foreground hover:bg-foreground/10 hover:text-foreground",
                )}
                title={bookmarked ? ub.removeBookmark : ub.addBookmark}
              >
                <StarIcon
                  className={cn("size-3.5", bookmarked && "fill-chart-4")}
                />
              </button>
            )}
        </div>
        {suggestionsOpen && addressSuggestions.length > 0 && (
          <div className="absolute left-0 right-0 top-full z-40 mt-2 overflow-hidden rounded-xl bg-popover py-1.5 text-popover-foreground shadow-lg">
            {addressSuggestions.map((item, index) => {
              const active = index === suggestionIndex;
              const Icon = item.source === "bookmark" ? StarIcon : ClockIcon;
              return (
                <button
                  key={`${item.source}-${item.url}`}
                  onMouseEnter={() => setSuggestionIndex(index)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => goTo(item.url)}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors",
                    active ? "bg-primary/10" : "hover:bg-muted/70",
                  )}
                >
                  {item.favicon ? (
                    <img
                      src={item.favicon}
                      alt=""
                      className="size-4 shrink-0 rounded-sm"
                      onError={(e) => (e.currentTarget.style.display = "none")}
                    />
                  ) : (
                    <Icon className="size-4 shrink-0 text-muted-foreground" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium">
                      {item.title || item.url}
                    </div>
                    <div className="truncate text-mini text-muted-foreground">
                      {item.url}
                    </div>
                  </div>
                  <div className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-micro text-muted-foreground">
                    {item.source === "bookmark"
                      ? ub.bookmarkLabel
                      : ub.historyLabel}
                  </div>
                </button>
              );
            })}
            <button
              onMouseDown={(e) => e.preventDefault()}
              onClick={submit}
              className="mt-1 flex w-full items-center gap-2 border-t border-border-default px-3 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
            >
              <SearchIcon className="size-4" />
              {ub.searchOrOpen(draft.trim())}
            </button>
          </div>
        )}
      </div>

      {/* Implementation note. */}
      <div className="relative">
        <button
          ref={downloadsBtnRef}
          onClick={() => setDownloadsOpen((v) => !v)}
          className={cn(
            "relative ml-1 grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground",
            activeDownloadCount > 0 && "text-primary",
          )}
          title={ub.downloads}
          aria-label={ub.downloads}
        >
          <DownloadIcon className="size-4" />
          {activeDownloadCount > 0 && (
            <span className="absolute right-0.5 top-0.5 size-2 rounded-full bg-primary" />
          )}
        </button>
        {downloadsOpen && (
          <DownloadDropdown
            downloads={downloads}
            latestDownload={latestDownload}
            onClose={() => setDownloadsOpen(false)}
            anchorRef={downloadsBtnRef}
          />
        )}
      </div>

      <div className="relative">
        <button
          ref={historyBtnRef}
          onClick={() => setHistoryOpen((v) => !v)}
          className="hidden size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground lg:grid"
          title={ub.historyAndBookmarks}
        >
          <ClockIcon className="size-4" />
        </button>
        {historyOpen && (
          <HistoryDropdown
            history={history}
            bookmarks={bookmarks}
            onPick={goTo}
            onRemoveBookmark={removeBookmark}
            onClearHistory={clearHistory}
            onClose={() => setHistoryOpen(false)}
            anchorRef={historyBtnRef}
          />
        )}
      </div>

      {/* AI assistant toggle */}
      <button
        onClick={toggleCopilot}
        className={cn(
          "ml-1 flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-semibold transition-colors",
          state.copilotOpen
            ? "border-primary/25 bg-primary/10 text-primary"
            : "border-border-subtle bg-background/65 text-muted-foreground hover:border-primary/20 hover:bg-primary/5 hover:text-foreground",
        )}
        title={ub.aiAssistant}
      >
        <SparklesIcon className="size-3.5" />
        <span>AI</span>
      </button>

      <div className="relative">
        <button
          ref={actionsBtnRef}
          onClick={() => setActionsOpen((v) => !v)}
          className="ml-1 grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground sm:size-9"
          title={ub.moreActions}
          aria-label={ub.moreActions}
        >
          <MoreHorizontalIcon className="size-4" />
        </button>
        {actionsOpen && (
          <BrowserActionsMenu
            activeZoom={activeZoom}
            anchorRef={actionsBtnRef}
            canManageSiteData={canManageSiteData}
            canUsePageActions={canUsePageActions}
            canAttachScreenshot={
              Boolean(webviewHandle) && activeTab?.url !== BROWSER_HOME_URL
            }
            canGoHome={Boolean(activeTab && activeTab.url !== BROWSER_HOME_URL)}
            canCustomizeHome={activeTab?.url === BROWSER_HOME_URL}
            device={device}
            deviceLabelMap={deviceLabelMap}
            onClearSiteData={clearCurrentSiteData}
            onClose={() => setActionsOpen(false)}
            onAttachScreenshot={() => void attachScreenshotToNextComposer()}
            onDeviceChange={onDeviceChange}
            onFindInPage={findInPage}
            onGoHome={goHome}
            onCustomizeHome={customizeHome}
            onOpenExtensions={onOpenExtensions}
            onOpenDataCenter={() => {
              setActionsOpen(false);
              setDataCenterOpen(true);
            }}
            onReload={() => webviewHandle?.reload()}
            onZoomChange={applyZoom}
            siteDataStatus={siteDataStatus}
          />
        )}
      </div>
      {confirmDialog}
      <Dialog open={findDialogOpen} onOpenChange={setFindDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{ub.findPrompt}</DialogTitle>
          </DialogHeader>
          <Input
            value={findQuery}
            onChange={(e) => setFindQuery(e.target.value)}
            placeholder={ub.findPrompt}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                executeFind();
              }
            }}
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setFindDialogOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button onClick={executeFind}>{t.common.confirm}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <BrowserDataCenterDialog
        open={dataCenterOpen}
        onOpenChange={setDataCenterOpen}
        historyCount={history.length}
        downloadCount={downloads.length}
        currentOrigin={siteOrigin}
        webContentsId={webviewHandle?.getWebContentsId() ?? null}
        canClearCurrentSite={canManageSiteData}
        clearing={clearingBrowsingData}
        onClearCurrentSite={() => void clearCurrentSiteData()}
        onClearHistory={() => {
          clearHistory();
          toast.success("浏览历史已清空");
        }}
        onClearDownloads={() => {
          setDownloads([]);
          toast.success("下载记录已清空，文件仍保留在磁盘中");
        }}
        onClearAll={() => void clearAllBrowsingData()}
      />
    </div>
  );
}

function BrowserDataCenterDialog({
  open,
  onOpenChange,
  historyCount,
  downloadCount,
  currentOrigin,
  webContentsId,
  canClearCurrentSite,
  clearing,
  onClearCurrentSite,
  onClearHistory,
  onClearDownloads,
  onClearAll,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  historyCount: number;
  downloadCount: number;
  currentOrigin: string | null;
  webContentsId: number | null;
  canClearCurrentSite: boolean;
  clearing: boolean;
  onClearCurrentSite: () => void;
  onClearHistory: () => void;
  onClearDownloads: () => void;
  onClearAll: () => void;
}) {
  const rowClass =
    "flex items-center gap-3 rounded-xl border border-border-subtle bg-muted/20 p-3";
  const [passwordAvailable, setPasswordAvailable] = useState(false);
  const [passwordEntries, setPasswordEntries] = useState<
    StoredBrowserPassword[]
  >([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [sitePermissions, setSitePermissions] = useState<
    BrowserAgentSitePermission[]
  >([]);
  const [agentAudit, setAgentAudit] = useState<BrowserAgentAuditEntry[]>([]);
  const [siteDevicePermissions, setSiteDevicePermissions] = useState<
    StoredSitePermission[]
  >([]);
  const [sitePermissionsAvailable, setSitePermissionsAvailable] =
    useState(false);

  const refreshAgentPolicy = useCallback(() => {
    setSitePermissions(listBrowserAgentPermissions());
    setAgentAudit(listBrowserAgentAudit());
  }, []);

  const refreshPasswords = useCallback(async () => {
    if (!window.echo?.browser?.listPasswords) {
      setPasswordAvailable(false);
      setPasswordEntries([]);
      return;
    }
    const result = await window.echo.browser.listPasswords(
      currentOrigin ?? undefined,
    );
    setPasswordAvailable(result.ok && result.available);
    setPasswordEntries(result.ok ? result.entries : []);
  }, [currentOrigin]);

  const refreshSiteDevicePermissions = useCallback(async () => {
    if (!window.echo?.browser?.listSitePermissions) {
      setSitePermissionsAvailable(false);
      setSiteDevicePermissions([]);
      return;
    }
    const result = await window.echo.browser.listSitePermissions();
    setSitePermissionsAvailable(result.ok);
    setSiteDevicePermissions(result.ok ? result.entries : []);
  }, []);

  useEffect(() => {
    if (!open) return;
    void refreshPasswords();
    void refreshSiteDevicePermissions();
    refreshAgentPolicy();
  }, [
    open,
    refreshAgentPolicy,
    refreshPasswords,
    refreshSiteDevicePermissions,
  ]);

  useEffect(() => {
    window.addEventListener(BROWSER_AGENT_POLICY_EVENT, refreshAgentPolicy);
    return () =>
      window.removeEventListener(
        BROWSER_AGENT_POLICY_EVENT,
        refreshAgentPolicy,
      );
  }, [refreshAgentPolicy]);

  const savePassword = async () => {
    if (!currentOrigin || !username.trim() || !password) return;
    setPasswordBusy(true);
    try {
      const result = await window.echo?.browser.savePassword({
        origin: currentOrigin,
        username: username.trim(),
        password,
      });
      if (!result?.ok) throw new Error(result?.error || "保存失败");
      setPassword("");
      await refreshPasswords();
      toast.success("密码已安全保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存密码失败");
    } finally {
      setPasswordBusy(false);
    }
  };

  const fillPassword = async (id: string) => {
    if (webContentsId == null) return;
    const result = await window.echo?.browser.fillPassword(
      webContentsId,
      id,
    );
    if (result?.ok) toast.success("已填充当前登录页面");
    else toast.error(result?.error || "当前页面没有可填充的登录表单");
  };

  const deletePassword = async (id: string) => {
    const result = await window.echo?.browser.deletePassword(id);
    if (!result?.ok) {
      toast.error(result?.error || "删除失败");
      return;
    }
    await refreshPasswords();
    toast.success("已删除保存的密码");
  };

  const updateSiteDevicePermission = async (
    entry: StoredSitePermission,
    decision: "ask" | "allow" | "block",
  ) => {
    const result = await window.echo?.browser.setSitePermission(
      entry.origin,
      entry.permission,
      decision,
    );
    if (!result?.ok) {
      toast.error(result?.error || "权限更新失败");
      return;
    }
    await refreshSiteDevicePermissions();
    toast.success(decision === "ask" ? "已恢复为每次询问" : "网站权限已更新");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[82vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>浏览器数据与隐私</DialogTitle>
          <DialogDescription className="sr-only">
            管理 Cookie、浏览历史、下载记录、Agent 网站权限与安全密码。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <section className={rowClass}>
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
              <CookieIcon className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">Cookie 与站点数据</div>
              <div className="mt-0.5 text-xs leading-5 text-muted-foreground">
                登录状态保存在独立的 Echo 浏览器配置中，不与系统浏览器混用。
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={!canClearCurrentSite || clearing}
              onClick={onClearCurrentSite}
            >
              清除当前网站
            </Button>
          </section>

          <section className={rowClass}>
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
              <ClockIcon className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">浏览历史</div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                当前保存 {historyCount} 条访问记录
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={historyCount === 0 || clearing}
              onClick={onClearHistory}
            >
              清空记录
            </Button>
          </section>

          <section className={rowClass}>
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
              <DownloadIcon className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">下载记录</div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                当前保存 {downloadCount} 条记录；清空记录不会删除文件
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={downloadCount === 0 || clearing}
              onClick={onClearDownloads}
            >
              清空记录
            </Button>
          </section>

          <section className={cn(rowClass, "items-start")}>
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
              <ShieldCheckIcon className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-sm font-semibold">
                网页设备权限
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-micro font-medium",
                    sitePermissionsAvailable
                      ? "bg-success/12 text-success"
                      : "bg-muted text-muted-foreground",
                  )}
                >
                  {sitePermissionsAvailable ? "桌面防护已开启" : "仅桌面应用"}
                </span>
              </div>
              <div className="mt-0.5 text-xs leading-5 text-muted-foreground">
                摄像头、麦克风、位置、通知和剪贴板默认询问；未支持的网页权限默认拒绝。
              </div>
              {sitePermissionsAvailable &&
              siteDevicePermissions.length === 0 ? (
                <div className="mt-2 rounded-lg bg-background/60 px-2.5 py-2 text-xs text-muted-foreground">
                  尚未记住任何设备权限
                </div>
              ) : (
                <div className="mt-2 space-y-1.5">
                  {siteDevicePermissions.map((entry) => (
                    <div
                      key={`${entry.origin}:${entry.permission}`}
                      className="flex items-center gap-2 rounded-lg bg-background/70 px-2.5 py-2"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs">{entry.origin}</div>
                        <div className="text-micro text-muted-foreground">
                          {SITE_PERMISSION_LABELS[entry.permission]}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          void updateSiteDevicePermission(
                            entry,
                            entry.decision === "allow" ? "block" : "allow",
                          )
                        }
                        className={cn(
                          "rounded-full px-2 py-0.5 text-micro font-medium",
                          entry.decision === "allow"
                            ? "bg-success/12 text-success"
                            : "bg-destructive/10 text-destructive",
                        )}
                      >
                        {entry.decision === "allow" ? "允许" : "阻止"}
                      </button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          void updateSiteDevicePermission(entry, "ask")
                        }
                      >
                        每次询问
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          <section className={cn(rowClass, "items-start")}>
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
              <ShieldCheckIcon className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">Agent 网站权限</div>
              <div className="mt-0.5 text-xs leading-5 text-muted-foreground">
                默认首次询问。网站权限只允许页面操作，提交、支付和删除等敏感动作仍会再次确认。
              </div>
              {sitePermissions.length === 0 ? (
                <div className="mt-2 rounded-lg bg-background/60 px-2.5 py-2 text-xs text-muted-foreground">
                  尚未记住任何网站权限
                </div>
              ) : (
                <div className="mt-2 space-y-1.5">
                  {sitePermissions.map((entry) => (
                    <div
                      key={entry.origin}
                      className="flex items-center gap-2 rounded-lg bg-background/70 px-2.5 py-2"
                    >
                      <span className="min-w-0 flex-1 truncate text-xs">
                        {entry.origin}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          setBrowserAgentPermission(
                            entry.origin,
                            entry.permission === "allow" ? "block" : "allow",
                          )
                        }
                        className={cn(
                          "rounded-full px-2 py-0.5 text-micro font-medium",
                          entry.permission === "allow"
                            ? "bg-success/12 text-success"
                            : "bg-destructive/10 text-destructive",
                        )}
                      >
                        {entry.permission === "allow" ? "允许" : "阻止"}
                      </button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          setBrowserAgentPermission(entry.origin, "ask")
                        }
                      >
                        重置
                      </Button>
                    </div>
                  ))}
                </div>
              )}
              <div className="mt-3 flex items-center justify-between gap-2">
                <div className="text-xs font-medium">
                  最近操作记录 · {agentAudit.length}
                </div>
                {agentAudit.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => clearBrowserAgentAudit()}
                  >
                    清空
                  </Button>
                )}
              </div>
              {agentAudit.length > 0 && (
                <div className="mt-1 max-h-32 space-y-1 overflow-y-auto rounded-lg bg-background/60 p-1.5">
                  {agentAudit.slice(0, 20).map((entry) => (
                    <div
                      key={entry.id}
                      className="flex items-center gap-2 rounded-md px-1.5 py-1 text-micro"
                    >
                      <span
                        className={cn(
                          "size-1.5 shrink-0 rounded-full",
                          entry.outcome === "blocked" ||
                            entry.outcome === "failed"
                            ? "bg-destructive"
                            : "bg-success",
                        )}
                      />
                      <span className="min-w-0 flex-1 truncate">
                        {entry.origin} · {entry.action}
                      </span>
                      <span className="shrink-0 text-muted-foreground">
                        {new Date(entry.createdAt).toLocaleTimeString()}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          <section className={cn(rowClass, "items-start")}>
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground">
              <KeyRoundIcon className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-sm font-semibold">
                密码与自动填充
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-micro font-medium",
                    passwordAvailable
                      ? "bg-success/12 text-success"
                      : "bg-warning/12 text-warning-foreground",
                  )}
                >
                  {passwordAvailable ? "系统加密可用" : "当前环境不可用"}
                </span>
              </div>
              <div className="mt-0.5 text-xs leading-5 text-muted-foreground">
                {passwordAvailable
                  ? "密码由系统钥匙串加密，只能在域名完全匹配时主动填充。"
                  : "网页版不保存密码；请在桌面应用中使用系统钥匙串。"}
              </div>
              {passwordAvailable && currentOrigin && (
                <div className="mt-3 space-y-2">
                  <div className="grid gap-2 sm:grid-cols-2">
                    <Input
                      value={username}
                      onChange={(event) => setUsername(event.target.value)}
                      placeholder="账号或邮箱"
                      autoComplete="off"
                    />
                    <Input
                      type="password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      placeholder="密码"
                      autoComplete="new-password"
                    />
                  </div>
                  <Button
                    size="sm"
                    disabled={passwordBusy || !username.trim() || !password}
                    onClick={() => void savePassword()}
                  >
                    保存到系统钥匙串
                  </Button>
                  {passwordEntries.map((entry) => (
                    <div
                      key={entry.id}
                      className="flex items-center gap-2 rounded-lg bg-background/70 px-2.5 py-2"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium">
                          {entry.username}
                        </div>
                        <div className="truncate text-micro text-muted-foreground">
                          {entry.origin}
                        </div>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={webContentsId == null}
                        onClick={() => void fillPassword(entry.id)}
                      >
                        填充
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void deletePassword(entry.id)}
                      >
                        删除
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          <div className="rounded-xl border border-destructive/20 bg-destructive/5 p-3">
            <div className="text-sm font-semibold">清除全部浏览数据</div>
            <div className="mt-1 text-xs leading-5 text-muted-foreground">
              清除
              Cookie、缓存、网站存储、浏览历史和下载记录，并退出所有网站登录。
            </div>
            <Button
              variant="destructive"
              size="sm"
              className="mt-3"
              disabled={clearing}
              onClick={onClearAll}
            >
              {clearing ? "正在清除…" : "清除全部数据"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

interface BrowserActionsMenuProps {
  activeZoom: number;
  anchorRef: React.RefObject<HTMLButtonElement | null>;
  canAttachScreenshot: boolean;
  canCustomizeHome: boolean;
  canGoHome: boolean;
  canManageSiteData: boolean;
  canUsePageActions: boolean;
  device: DevicePreset;
  deviceLabelMap: Record<DevicePreset, string>;
  onClearSiteData: () => void;
  onClose: () => void;
  onAttachScreenshot: () => void;
  onDeviceChange: (device: DevicePreset) => void;
  onFindInPage: () => void;
  onCustomizeHome: () => void;
  onGoHome: () => void;
  onOpenExtensions?: () => void;
  onOpenDataCenter: () => void;
  onReload: () => void;
  onZoomChange: (zoom: number) => void;
  siteDataStatus: string | null;
}

function BrowserActionsMenu({
  activeZoom,
  anchorRef,
  canAttachScreenshot,
  canCustomizeHome,
  canGoHome,
  canManageSiteData,
  canUsePageActions,
  device,
  deviceLabelMap,
  onClearSiteData,
  onClose,
  onAttachScreenshot,
  onDeviceChange,
  onFindInPage,
  onCustomizeHome,
  onGoHome,
  onOpenExtensions,
  onOpenDataCenter,
  onReload,
  onZoomChange,
  siteDataStatus,
}: BrowserActionsMenuProps) {
  const { t } = useI18n();
  const ub = t.browser.urlBar;
  const bp = t.browserPreviewPanel;
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onClose();
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [anchorRef, onClose]);

  const menuButtonClass =
    "flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-left text-xs font-medium text-muted-foreground transition-colors hover:bg-background/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-40";

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-50 mt-1 w-[292px] overflow-hidden rounded-xl bg-popover p-2 text-popover-foreground shadow-lg"
    >
      <div className="px-1.5 pb-1.5 text-mini font-semibold uppercase tracking-[0.08em] text-muted-foreground">
        {ub.pageActions}
      </div>
      <button
        type="button"
        disabled={!canUsePageActions}
        onClick={() => {
          onReload();
          onClose();
        }}
        className={menuButtonClass}
      >
        <RefreshCwIcon className="size-4" />
        {ub.refresh}
      </button>
      <button
        type="button"
        disabled={!canUsePageActions}
        onClick={onFindInPage}
        className={menuButtonClass}
      >
        <SearchIcon className="size-4" />
        {ub.findInPage}
      </button>
      <button
        type="button"
        disabled={!canGoHome}
        onClick={() => {
          onGoHome();
          onClose();
        }}
        className={menuButtonClass}
      >
        <HouseIcon className="size-4" />
        {ub.backToHome}
      </button>
      {canCustomizeHome && (
        <button
          type="button"
          onClick={() => {
            onCustomizeHome();
            onClose();
          }}
          className={menuButtonClass}
        >
          <PanelLeftIcon className="size-4" />
          {t.browser.webviewTab.editDesktop}
        </button>
      )}
      <button
        type="button"
        disabled={!canAttachScreenshot}
        onClick={() => {
          onAttachScreenshot();
          onClose();
        }}
        className={menuButtonClass}
      >
        <ImageIcon className="size-4" />
        {bp.attachScreenshotToComposer}
      </button>

      <div className="my-2 h-px bg-border/55" />

      <div className="flex h-10 items-center gap-2 rounded-xl bg-background/45 px-2">
        <span className="min-w-0 flex-1 text-xs font-medium text-muted-foreground">
          {ub.zoom}
        </span>
        <button
          type="button"
          disabled={!canUsePageActions || activeZoom <= 50}
          onClick={() => onZoomChange(activeZoom - 10)}
          className="grid size-7 place-items-center text-muted-foreground transition-colors hover:bg-background hover:text-foreground disabled:pointer-events-none disabled:opacity-35"
          title={ub.zoomOut}
          aria-label={ub.zoomOut}
        >
          <MinusIcon className="size-3.5" />
        </button>
        <span className="w-12 text-center text-sm font-semibold tabular-nums">
          {activeZoom}%
        </span>
        <button
          type="button"
          disabled={!canUsePageActions || activeZoom >= 200}
          onClick={() => onZoomChange(activeZoom + 10)}
          className="grid size-7 place-items-center text-muted-foreground transition-colors hover:bg-background hover:text-foreground disabled:pointer-events-none disabled:opacity-35"
          title={ub.zoomIn}
          aria-label={ub.zoomIn}
        >
          <PlusIcon className="size-3.5" />
        </button>
        <button
          type="button"
          disabled={!canUsePageActions || activeZoom === 100}
          onClick={() => onZoomChange(100)}
          className="grid size-7 place-items-center text-muted-foreground transition-colors hover:bg-background hover:text-foreground disabled:pointer-events-none disabled:opacity-35"
          title={ub.resetZoom}
          aria-label={ub.resetZoom}
        >
          <RotateCcwIcon className="size-3.5" />
        </button>
      </div>

      <div className="mt-2 rounded-xl bg-background/35 p-1">
        <div className="px-1.5 pb-1.5 text-mini font-medium text-muted-foreground">
          {ub.devicePreview}
        </div>
        <div className="grid grid-cols-3 gap-1">
          {DEVICE_ORDER.map((d) => {
            const Icon = DEVICE_ICONS[d];
            const active = device === d;
            return (
              <button
                key={d}
                type="button"
                onClick={() => onDeviceChange(d)}
                className={cn(
                  "flex h-8 items-center justify-center gap-1 rounded-lg text-xs font-medium transition-colors",
                  active
                    ? "bg-background text-foreground shadow-[var(--shadow-xs)] ring-1 ring-border-subtle"
                    : "text-muted-foreground hover:bg-background/65 hover:text-foreground",
                )}
                title={ub.switchToDevice(deviceLabelMap[d])}
                aria-label={ub.switchToDevice(deviceLabelMap[d])}
              >
                <Icon className="size-3.5" />
                <span>{deviceLabelMap[d]}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="my-2 h-px bg-border/55" />

      <button
        type="button"
        onClick={() => {
          onOpenExtensions?.();
          onClose();
        }}
        className={menuButtonClass}
      >
        <PuzzleIcon className="size-4" />
        {ub.browserExtensions}
      </button>
      <button
        type="button"
        onClick={onOpenDataCenter}
        className={menuButtonClass}
      >
        <Settings2Icon className="size-4" />
        浏览器数据与隐私
      </button>
      <button
        type="button"
        disabled={!canManageSiteData}
        onClick={onClearSiteData}
        className={cn(
          menuButtonClass,
          "hover:bg-destructive/10 hover:text-destructive",
        )}
      >
        <Trash2Icon className="size-4" />
        {ub.clearData}
      </button>
      {siteDataStatus && (
        <div className="mt-1 rounded-lg border border-border-default px-2.5 py-2 text-mini text-muted-foreground">
          {siteDataStatus}
        </div>
      )}
    </div>
  );
}

interface HistoryDropdownProps {
  history: HistoryEntry[];
  bookmarks: Bookmark[];
  onPick: (url: string) => void;
  onRemoveBookmark: (url: string) => void;
  onClearHistory: () => void;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLButtonElement | null>;
}

interface DownloadDropdownProps {
  downloads: BrowserDownload[];
  latestDownload: BrowserDownload | null;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLButtonElement | null>;
}

function DownloadDropdown({
  downloads,
  latestDownload,
  onClose,
  anchorRef,
}: DownloadDropdownProps) {
  const { t } = useI18n();
  const ub = t.browser.urlBar;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onClose();
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [anchorRef, onClose]);

  const hasActive = downloads.some((item) => item.state === "progressing");
  const title = hasActive
    ? ub.downloading
    : latestDownload
      ? ub.recentDownload
      : ub.downloads;

  const controlDownload = async (
    action: "pause" | "resume" | "cancel" | "retry",
    id: string,
  ) => {
    const browser = window.echo?.browser;
    const handler =
      action === "pause"
        ? browser?.pauseDownload
        : action === "resume"
          ? browser?.resumeDownload
          : action === "cancel"
            ? browser?.cancelDownload
            : browser?.retryDownload;
    if (!handler) {
      toast.error("下载控制仅在桌面应用中可用");
      return;
    }
    const result = await handler(id);
    if (!result.ok) toast.error(result.error || "下载操作失败");
  };

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-50 mt-1 w-[360px] max-w-[calc(100vw-1rem)] overflow-hidden rounded-xl bg-popover text-popover-foreground shadow-lg"
    >
      <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <DownloadIcon className="size-4" />
          {title}
        </div>
        <span className="text-mini text-muted-foreground">
          {downloads.length > 0
            ? ub.downloadCount(downloads.length)
            : ub.noDownloads}
        </span>
      </div>
      <div className="max-h-[360px] overflow-y-auto py-1">
        {downloads.length === 0 ? (
          <div className="px-3 py-8 text-center text-xs text-muted-foreground">
            {ub.noDownloadRecords}
          </div>
        ) : (
          downloads.map((item) => {
            const completed = item.state === "completed";
            const failed =
              item.state === "cancelled" || item.state === "interrupted";
            const percent =
              item.totalBytes > 0
                ? Math.min(
                    100,
                    Math.round((item.receivedBytes / item.totalBytes) * 100),
                  )
                : 0;
            return (
              <div
                key={item.id}
                className="group px-3 py-2 transition-colors hover:bg-muted/60"
              >
                <div className="flex items-start gap-2">
                  <div
                    className={cn(
                      "mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg",
                      completed
                        ? "bg-success/10 text-success"
                        : failed
                          ? "bg-destructive/10 text-destructive"
                          : "bg-primary/10 text-primary",
                    )}
                  >
                    {completed ? (
                      <CheckCircleIcon className="size-4" />
                    ) : failed ? (
                      <AlertCircleIcon className="size-4" />
                    ) : (
                      <FileIcon className="size-4" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium">
                      {item.filename || ub.unnamedDownload}
                    </div>
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-micro text-muted-foreground">
                      {item.risk && item.risk !== "low" && (
                        <span
                          className={cn(
                            "shrink-0 rounded-full px-1.5 py-0.5 font-medium",
                            item.risk === "high"
                              ? "bg-destructive/10 text-destructive"
                              : "bg-warning/12 text-warning-foreground",
                          )}
                        >
                          {item.risk === "high" ? "高风险文件" : "压缩文件"}
                        </span>
                      )}
                      {item.sourceOrigin && (
                        <span className="truncate" title={item.sourceOrigin}>
                          {item.sourceOrigin}
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 truncate text-mini text-muted-foreground">
                      {completed
                        ? ub.downloadCompleted
                        : failed
                          ? ub.downloadIncomplete
                          : item.paused
                            ? `已暂停 · ${formatBytes(item.receivedBytes)}`
                            : `${formatBytes(item.receivedBytes)} / ${
                                item.totalBytes > 0
                                  ? formatBytes(item.totalBytes)
                                  : ub.unknownSize
                              }`}
                    </div>
                    {!completed && !failed && (
                      <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary transition-all"
                          style={{ width: `${percent || 8}%` }}
                        />
                      </div>
                    )}
                  </div>
                </div>
                {!completed && !failed && (
                  <div className="mt-2 flex justify-end gap-1.5">
                    <button
                      type="button"
                      onClick={() =>
                        void controlDownload(
                          item.paused ? "resume" : "pause",
                          item.id,
                        )
                      }
                      disabled={item.paused && item.canResume === false}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-mini text-muted-foreground transition-colors hover:bg-background hover:text-foreground disabled:opacity-40"
                    >
                      {item.paused ? (
                        <PlayIcon className="size-3" />
                      ) : (
                        <PauseIcon className="size-3" />
                      )}
                      {item.paused ? "继续" : "暂停"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void controlDownload("cancel", item.id)}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-mini text-destructive transition-colors hover:bg-destructive/10"
                    >
                      <XIcon className="size-3" />
                      取消
                    </button>
                  </div>
                )}
                {failed && (
                  <div className="mt-2 flex justify-end">
                    <button
                      type="button"
                      onClick={() => void controlDownload("retry", item.id)}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-mini text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                    >
                      <RefreshCwIcon className="size-3" />
                      重试
                    </button>
                  </div>
                )}
                {completed && (
                  <div className="mt-2 flex justify-end gap-1.5">
                    <button
                      onClick={() =>
                        void window.echo?.browser.openDownload(item.id)
                      }
                      className="rounded-md px-2 py-1 text-mini text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                    >
                      {ub.openFile}
                    </button>
                    <button
                      onClick={() =>
                        void window.echo?.browser.showDownloadInFolder(
                          item.id,
                        )
                      }
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-mini text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                    >
                      <FolderOpenIcon className="size-3" />
                      {ub.openFolder}
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function HistoryDropdown({
  history,
  bookmarks,
  onPick,
  onRemoveBookmark,
  onClearHistory,
  onClose,
  anchorRef,
}: HistoryDropdownProps) {
  const { t } = useI18n();
  const ub = t.browser.urlBar;
  const [tab, setTab] = useState<"history" | "bookmarks">("bookmarks");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onClose();
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [anchorRef, onClose]);

  const items = tab === "history" ? history : bookmarks;

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full z-50 mt-1 w-[420px] max-w-[calc(100vw-1rem)] rounded-lg bg-popover text-popover-foreground shadow-lg"
    >
      <div className="flex items-center justify-between border-b">
        <div className="flex">
          <button
            onClick={() => setTab("bookmarks")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs",
              tab === "bookmarks"
                ? "border-b-2 border-primary text-foreground font-medium"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <StarIcon className="size-3.5" />
            {ub.bookmarksTab(bookmarks.length)}
          </button>
          <button
            onClick={() => setTab("history")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs",
              tab === "history"
                ? "border-b-2 border-primary text-foreground font-medium"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <ClockIcon className="size-3.5" />
            {ub.historyTab(history.length)}
          </button>
        </div>
        {tab === "history" && history.length > 0 && (
          <button
            onClick={onClearHistory}
            className="mr-2 flex items-center gap-1 rounded px-2 py-0.5 text-micro text-muted-foreground hover:bg-muted hover:text-destructive"
            title={ub.clearHistoryTitle}
          >
            <Trash2Icon className="size-3" />
            {ub.clearHistory}
          </button>
        )}
      </div>
      <div className="max-h-[420px] overflow-y-auto py-1">
        {items.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground">
            {tab === "bookmarks" ? ub.noBookmarks : ub.noHistory}
          </div>
        ) : (
          items.map((item) => {
            const isBookmark = "addedAt" in item;
            const ts = isBookmark ? item.addedAt : item.visitedAt;
            return (
              <div
                key={`${item.url}-${ts}`}
                className="group flex items-center gap-2 px-3 py-1.5 hover:bg-muted/60"
              >
                {item.favicon ? (
                  <img
                    src={item.favicon}
                    alt=""
                    className="size-4 shrink-0"
                    onError={(e) => (e.currentTarget.style.display = "none")}
                  />
                ) : (
                  <div className="size-4 shrink-0 rounded bg-muted-foreground/20" />
                )}
                <button
                  onClick={() => onPick(item.url)}
                  className="flex min-w-0 flex-1 flex-col text-left"
                >
                  <div className="truncate text-xs font-medium">
                    {item.title || item.url}
                  </div>
                  <div className="truncate text-micro text-muted-foreground">
                    {item.url}
                  </div>
                </button>
                {isBookmark && (
                  <button
                    onClick={() => onRemoveBookmark(item.url)}
                    className="grid size-5 shrink-0 place-items-center rounded text-muted-foreground/40 opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                    title={ub.removeBookmarkTitle}
                  >
                    <Trash2Icon className="size-3" />
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
