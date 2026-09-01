/* Implementation note. */

import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import {
  ClockIcon,
  CopyIcon,
  ExternalLinkIcon,
  GlobeIcon,
  MenuIcon,
  MonitorIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  ServerIcon,
  SmartphoneIcon,
  StarIcon,
  TabletIcon,
  XIcon,
} from "lucide-react";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";

import { TabBar } from "@/components/browser/tab-bar";
import { UrlBar } from "@/components/browser/url-bar";
import {
  BROWSER_HOME_URL,
  BROWSER_OPEN_URL_REQUEST_KEY,
  BROWSER_OPEN_URL_ACK_EVENT,
  BrowserStoreProvider,
  SEARCH_ENGINE_URLS,
  setAppMode,
  useBrowserStore,
  type BrowserOpenUrlRequest,
  type BrowserOpenUrlAck,
  BROWSER_OPEN_URL_REQUEST_EVENT,
} from "@/components/browser/browser-store";
import type { WebviewTabHandle } from "@/components/browser/webview-tab";
import { WorkspaceSurfaceHeader } from "@/components/workspace/workspace-surface-header";
import { useActiveAgentId } from "@/core/agents/active";
import {
  isLocalPreviewUrl,
  localPreviewPort,
} from "@/core/browser/local-services";
import { workspacePresetForAgent } from "@/core/workspace/workspace-presets";

const AssistantPanel = lazy(() =>
  import("@/components/browser/assistant-panel").then((module) => ({
    default: module.AssistantPanel,
  })),
);
const WebviewTab = lazy(() =>
  import("@/components/browser/webview-tab").then((module) => ({
    default: module.WebviewTab,
  })),
);

const isWindows = (): boolean =>
  typeof navigator !== "undefined" && navigator.userAgent.includes("Windows");
const inElectron = (): boolean =>
  typeof window !== "undefined" && !!window.echo?.isElectron;

const CHROME_WEB_STORE_EXTENSIONS_URL =
  "https://chromewebstore.google.com/category/extensions";

const isBrowserDevice = (
  value: unknown,
): value is BrowserOpenUrlRequest["device"] =>
  value === "desktop" || value === "tablet" || value === "mobile";

/* Implementation note. */
const DEVICE_STAGE = {
  desktop: {
    width: 0,
    height: 0,
    label: "Desktop",
    description: "Fluid viewport",
  },
  tablet: {
    width: 768,
    height: 1024,
    label: "iPad",
    description: "768 x 1024",
  },
  mobile: {
    width: 390,
    height: 844,
    label: "iPhone",
    description: "390 x 844",
  },
} as const;

function BrowserShell() {
  const { t } = useI18n();
  const activeAgentId = useActiveAgentId() ?? "general";
  const personaThemeId = workspacePresetForAgent(activeAgentId).themeId;
  const {
    state,
    activeTab,
    patchTab,
    openTab,
    closeTab,
    restoreClosedTab,
    activateTab,
    recordVisit,
  } = useBrowserStore();
  // Implementation note.
  const handlesRef = useRef<Map<string, WebviewTabHandle | null>>(new Map());
  // Implementation note.
  const [activeHandle, setActiveHandle] = useState<WebviewTabHandle | null>(
    null,
  );
  const [sidePanelHovered, setSidePanelHovered] = useState(false);
  const [sidePanelPinned, setSidePanelPinned] = useState(false);
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
  const stageRef = useRef<HTMLDivElement>(null);
  const sidePanelCloseTimerRef = useRef<number | null>(null);
  const electron = inElectron();
  const activeTabId = activeTab?.id ?? null;
  const activeTabUrl = activeTab?.url ?? "";
  const activeTabTitle = activeTab?.title ?? "";
  const activeTabFavicon = activeTab?.favicon;
  const activeTabLoading = activeTab?.isLoading ?? false;
  const localPreview = isLocalPreviewUrl(activeTabUrl);
  const previewPort = localPreviewPort(activeTabUrl);

  const acknowledgeOpenRequest = useCallback(
    (request: BrowserOpenUrlRequest, accepted: boolean) => {
      if (!request.requestId) return;
      window.dispatchEvent(
        new CustomEvent<BrowserOpenUrlAck>(BROWSER_OPEN_URL_ACK_EVENT, {
          detail: { requestId: request.requestId, accepted },
        }),
      );
    },
    [],
  );

  const openExtensionsStore = useCallback(() => {
    if (window.echo?.app?.openExternal) {
      void window.echo.app.openExternal(CHROME_WEB_STORE_EXTENSIONS_URL);
      return;
    }

    const opened = window.open(
      CHROME_WEB_STORE_EXTENSIONS_URL,
      "_blank",
      "noopener,noreferrer",
    );
    if (!opened) openTab(CHROME_WEB_STORE_EXTENSIONS_URL);
  }, [openTab]);

  // Implementation note.
  // Implementation note.
  useEffect(() => {
    const openRequestedUrl = (event: Event) => {
      const request = (event as CustomEvent<BrowserOpenUrlRequest>).detail;
      if (!request?.url) return;
      try {
        localStorage.removeItem(BROWSER_OPEN_URL_REQUEST_KEY);
        openTab(request.url, {
          ...(request.title ? { title: request.title } : {}),
          ...(request.device ? { device: request.device } : {}),
        });
        acknowledgeOpenRequest(request, true);
      } catch (error) {
        swallow(error, "browser-open-request");
        acknowledgeOpenRequest(request, false);
      }
    };
    window.addEventListener(BROWSER_OPEN_URL_REQUEST_EVENT, openRequestedUrl);
    return () =>
      window.removeEventListener(
        BROWSER_OPEN_URL_REQUEST_EVENT,
        openRequestedUrl,
      );
  }, [acknowledgeOpenRequest, openTab]);

  useEffect(() => {
    if (!activeTabId) {
      setActiveHandle(null);
      window.echo?.bridge.setActiveTab(null);
      return;
    }
    const h = handlesRef.current.get(activeTabId) ?? null;
    setActiveHandle(h);
    const wcId = h?.getWebContentsId() ?? null;
    if (wcId != null) window.echo?.bridge.setActiveTab(wcId);
  }, [activeTabId]);

  const activeDevice = activeTab?.device ?? "desktop";
  const renderDevice =
    activeTabUrl === BROWSER_HOME_URL &&
    activeDevice === "desktop" &&
    stageSize.width > 0 &&
    stageSize.width < 640
      ? "mobile"
      : activeDevice;
  const activeStage = DEVICE_STAGE[renderDevice];
  const activeScale =
    renderDevice === "desktop"
      ? 1
      : Math.min(
          1,
          Math.max(0.2, (stageSize.width - 40) / activeStage.width),
          Math.max(0.2, (stageSize.height - 40) / activeStage.height),
        );

  const openPreviewExternally = useCallback(() => {
    if (!activeTabUrl) return;
    if (window.echo?.app?.openExternal) {
      void window.echo.app.openExternal(activeTabUrl);
      return;
    }
    window.open(activeTabUrl, "_blank", "noopener,noreferrer");
  }, [activeTabUrl]);

  useEffect(() => {
    const node = stageRef.current;
    if (!node) return;
    const update = () => {
      const rect = node.getBoundingClientRect();
      setStageSize({ width: rect.width, height: rect.height });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Implementation note.
  // Implementation note.
  useEffect(() => {
    if (!activeTabUrl || activeTabLoading) return;
    if (
      activeTabUrl.startsWith("about:") ||
      activeTabUrl.startsWith("echo:")
    ) {
      return;
    }
    const t = setTimeout(() => {
      recordVisit({
        url: activeTabUrl,
        title: activeTabTitle || activeTabUrl,
        favicon: activeTabFavicon,
      });
    }, 800);
    return () => clearTimeout(t);
  }, [
    activeTabFavicon,
    activeTabLoading,
    activeTabTitle,
    activeTabUrl,
    recordVisit,
  ]);

  // Implementation note.
  useEffect(() => {
    if (!window.echo) return;
    const off = window.echo.on("browser:open-tab", (...args) => {
      const payload = args[0] as { url?: string } | undefined;
      if (payload?.url) openTab(payload.url);
    });
    return () => off();
  }, [openTab]);

  useEffect(() => {
    try {
      const rawRequest = localStorage.getItem(BROWSER_OPEN_URL_REQUEST_KEY);
      if (!rawRequest) return;
      localStorage.removeItem(BROWSER_OPEN_URL_REQUEST_KEY);
      let request: BrowserOpenUrlRequest | null = null;
      try {
        const parsed = JSON.parse(rawRequest) as Partial<BrowserOpenUrlRequest>;
        if (typeof parsed.url === "string" && parsed.url.trim()) {
          request = {
            url: parsed.url,
            requestId:
              typeof parsed.requestId === "string"
                ? parsed.requestId
                : undefined,
            title: typeof parsed.title === "string" ? parsed.title : undefined,
            device: isBrowserDevice(parsed.device) ? parsed.device : undefined,
            source:
              typeof parsed.source === "string" ? parsed.source : undefined,
            sessionId:
              typeof parsed.sessionId === "string"
                ? parsed.sessionId
                : undefined,
          };
        }
      } catch {
        request = { url: rawRequest };
      }
      if (!request?.url) return;
      try {
        openTab(request.url, {
          ...(request.title ? { title: request.title } : {}),
          ...(request.device ? { device: request.device } : {}),
        });
        acknowledgeOpenRequest(request, true);
      } catch (error) {
        swallow(error, "browser-open-request");
        acknowledgeOpenRequest(request, false);
      }
    } catch (e) {
      swallow(e);
    }
  }, [acknowledgeOpenRequest, openTab]);

  // Implementation note.
  // Implementation note.
  // Implementation note.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isMac = navigator.userAgent.includes("Mac");
      const mod = isMac ? e.metaKey : e.ctrlKey;
      if (!mod) return;
      const k = e.key.toLowerCase();

      // Implementation note.
      if (k === "t" && !e.shiftKey) {
        e.preventDefault();
        openTab();
        return;
      }
      if (k === "t" && e.shiftKey) {
        e.preventDefault();
        restoreClosedTab();
        return;
      }
      // Implementation note.
      if (k === "w" && !e.shiftKey) {
        e.preventDefault();
        if (activeTab) closeTab(activeTab.id);
        return;
      }
      // Implementation note.
      if (k === "l") {
        e.preventDefault();
        const input = document.querySelector<HTMLInputElement>(
          'input[placeholder="搜索或输入网址"]',
        );
        input?.focus();
        input?.select();
        return;
      }
      // Implementation note.
      if (e.key === "Tab") {
        e.preventDefault();
        const idx = state.tabs.findIndex((t) => t.id === state.activeId);
        const next = e.shiftKey
          ? (idx - 1 + state.tabs.length) % state.tabs.length
          : (idx + 1) % state.tabs.length;
        const tab = state.tabs[next];
        if (tab) activateTab(tab.id);
        return;
      }
      // Implementation note.
      if (/^[1-9]$/.test(e.key)) {
        e.preventDefault();
        const n = parseInt(e.key, 10);
        const idx = n === 9 ? state.tabs.length - 1 : n - 1;
        const tab = state.tabs[idx];
        if (tab) activateTab(tab.id);
        return;
      }
    };
    window.addEventListener("keydown", onKey);

    // Implementation note.
    // Implementation note.
    // Implementation note.
    const offIpc = window.echo?.on(
      "browser:keyboard-shortcut",
      (...args) => {
        const p = args[0] as
          | {
              key: string;
              shift: boolean;
              alt: boolean;
              meta: boolean;
              control: boolean;
            }
          | undefined;
        if (!p) return;
        onKey(
          new KeyboardEvent("keydown", {
            key: p.key,
            shiftKey: p.shift,
            altKey: p.alt,
            metaKey: p.meta,
            ctrlKey: p.control,
          }),
        );
      },
    );

    return () => {
      window.removeEventListener("keydown", onKey);
      offIpc?.();
    };
  }, [
    openTab,
    closeTab,
    restoreClosedTab,
    activateTab,
    activeTab,
    state.tabs,
    state.activeId,
  ]);

  // Implementation note.
  // Implementation note.
  // Implementation note.
  useEffect(() => {
    if (!isWindows() || !window.echo) return;
    const apply = () => {
      // Implementation note.
      const root = document.documentElement;
      const bg = getComputedStyle(root).getPropertyValue("--background").trim();
      const fg = getComputedStyle(root).getPropertyValue("--foreground").trim();
      // Implementation note.
      const toCss = (v: string) =>
        v.startsWith("#") || v.startsWith("rgb") ? v : `hsl(${v})`;
      void window
        .echo!.window.setTitleBarOverlay({
          color: toCss(bg) || "#f1f1f3",
          symbolColor: toCss(fg) || "#525252",
        })
        .catch((e) => {
          swallow(e);
        });
    };
    apply();
    const obs = new MutationObserver(apply);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class"],
    });
    return () => obs.disconnect();
  }, []);

  const sidePanelOpen = sidePanelHovered || sidePanelPinned;
  const clearSidePanelCloseTimer = useCallback(() => {
    if (sidePanelCloseTimerRef.current === null) return;
    window.clearTimeout(sidePanelCloseTimerRef.current);
    sidePanelCloseTimerRef.current = null;
  }, []);
  const showSidePanel = useCallback(() => {
    clearSidePanelCloseTimer();
    setSidePanelHovered(true);
  }, [clearSidePanelCloseTimer]);
  const scheduleSidePanelClose = useCallback(() => {
    clearSidePanelCloseTimer();
    sidePanelCloseTimerRef.current = window.setTimeout(() => {
      setSidePanelHovered(false);
      sidePanelCloseTimerRef.current = null;
    }, 180);
  }, [clearSidePanelCloseTimer]);

  useEffect(() => clearSidePanelCloseTimer, [clearSidePanelCloseTimer]);

  return (
    <div
      data-persona-theme={personaThemeId}
      className="persona-shell browser-shell relative flex h-screen overflow-hidden bg-[linear-gradient(135deg,var(--muted)_0%,var(--background)_42%,var(--muted)_100%)] text-foreground"
    >
      <BrowserSidePanel
        open={sidePanelOpen}
        pinned={sidePanelPinned}
        onMouseEnter={showSidePanel}
        onMouseLeave={scheduleSidePanelClose}
      />
      {/* Implementation note. */}
      <div className="relative z-[1] flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="relative z-[80] shrink-0">
          <div
            className="flex h-10 shrink-0 items-center gap-0.5 rounded-none border-x-0 border-t-0 border-b border-border-subtle px-1.5"
            style={
              {
                paddingLeft: 10,
                paddingRight: isWindows() && electron ? 154 : 6,
                WebkitAppRegion: "drag",
              } as React.CSSProperties
            }
          >
            <div
              className="flex h-8 shrink-0 items-center justify-start gap-2"
              style={
                {
                  WebkitAppRegion: "no-drag",
                } as React.CSSProperties
              }
            >
              <WorkspaceSurfaceHeader active="browser" />
            </div>
            <div className="flex h-8 min-w-0 flex-1 items-center">
              <TabBar />
            </div>
            <button
              type="button"
              title={
                sidePanelPinned
                  ? t.browser.sidePanel.unpin
                  : t.browser.sidePanel.expand
              }
              aria-label={
                sidePanelPinned
                  ? t.browser.sidePanel.unpin
                  : t.browser.sidePanel.expand
              }
              onMouseEnter={showSidePanel}
              onMouseLeave={scheduleSidePanelClose}
              onClick={() => {
                clearSidePanelCloseTimer();
                setSidePanelPinned((value) => {
                  const nextPinned = !value;
                  setSidePanelHovered(nextPinned);
                  return nextPinned;
                });
              }}
              className={cn(
                "grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground",
                sidePanelOpen && "bg-foreground/5 text-foreground",
              )}
              style={
                {
                  WebkitAppRegion: "no-drag",
                } as React.CSSProperties
              }
            >
              <MenuIcon className="size-3.5" />
            </button>
          </div>

          {/* URL bar */}
          <UrlBar
            webviewHandle={activeHandle}
            onOpenExtensions={openExtensionsStore}
          />
        </div>

        <div className="relative z-0 flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex min-h-0 flex-1 overflow-hidden">
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
              {localPreview && activeTab ? (
                <div
                  data-testid="local-preview-toolbar"
                  className="flex h-10 shrink-0 items-center gap-2 border-b border-border-subtle bg-muted/35 px-3"
                >
                  <span className="grid size-6 place-items-center rounded-md bg-emerald-500/10 text-emerald-600">
                    <ServerIcon className="size-3.5" />
                  </span>
                  <span className="text-[11px] font-semibold">
                    {t.browserPreviewPanel.localPreviewMode}
                  </span>
                  <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                    <span className="size-1.5 rounded-full bg-emerald-500" />
                    {t.browserPreviewPanel.localPreviewRunning(previewPort)}
                  </span>
                  <span className="mx-1 h-4 w-px bg-border-subtle" />
                  <div className="flex rounded-lg bg-background/80 p-0.5 shadow-[var(--shadow-xs)]">
                    {(
                      [
                        ["desktop", MonitorIcon, DEVICE_STAGE.desktop.label],
                        ["tablet", TabletIcon, DEVICE_STAGE.tablet.label],
                        ["mobile", SmartphoneIcon, DEVICE_STAGE.mobile.label],
                      ] as const
                    ).map(([device, Icon, label]) => (
                      <button
                        key={device}
                        type="button"
                        onClick={() => patchTab(activeTab.id, { device })}
                        className={cn(
                          "flex h-7 items-center gap-1.5 rounded-md px-2 text-[10px] text-muted-foreground transition-colors hover:text-foreground",
                          activeDevice === device &&
                            "bg-foreground text-background shadow-sm hover:text-background",
                        )}
                        title={DEVICE_STAGE[device].description}
                      >
                        <Icon className="size-3.5" />
                        <span className="hidden xl:inline">{label}</span>
                      </button>
                    ))}
                  </div>
                  <span className="flex-1" />
                  <button
                    type="button"
                    onClick={() => activeHandle?.reload()}
                    className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                    title={t.browserPreviewPanel.localPreviewRefresh}
                    aria-label={t.browserPreviewPanel.localPreviewRefresh}
                  >
                    <RefreshCwIcon
                      className={cn(
                        "size-3.5",
                        activeTabLoading && "animate-spin",
                      )}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={openPreviewExternally}
                    className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                    title={t.browserPreviewPanel.localPreviewOpenExternal}
                    aria-label={t.browserPreviewPanel.localPreviewOpenExternal}
                  >
                    <ExternalLinkIcon className="size-3.5" />
                  </button>
                </div>
              ) : null}
              <div
                ref={stageRef}
                className={cn(
                  "relative min-h-0 min-w-0 flex-1 overflow-hidden bg-background",
                  renderDevice !== "desktop" &&
                    "flex items-center justify-center bg-[radial-gradient(circle_at_top,var(--muted)_0%,var(--background)_58%)] p-5",
                )}
              >
                <div
                  className={cn(
                    "relative overflow-hidden bg-background",
                    renderDevice === "desktop"
                      ? "h-full w-full"
                      : "h-full max-h-full max-w-full rounded-4xl border-[6px] border-foreground/22 shadow-[0_22px_64px_rgba(15,23,42,0.18)]",
                  )}
                  style={
                    renderDevice === "desktop"
                      ? undefined
                      : {
                          width: activeStage.width,
                          height: activeStage.height,
                          flex: "0 0 auto",
                          transform: `scale(${activeScale})`,
                          transformOrigin: "center",
                        }
                  }
                >
                  <Suspense
                    fallback={
                      <div
                        className="size-full animate-pulse bg-muted/25"
                        role="status"
                        aria-label="加载网页内容"
                      />
                    }
                  >
                    {state.tabs.map((tab) => (
                      <WebviewTab
                        key={tab.id}
                        tab={tab}
                        active={tab.id === state.activeId}
                        renderDevice={
                          tab.id === state.activeId ? renderDevice : tab.device
                        }
                        onPatch={(patch) => patchTab(tab.id, patch)}
                        onClose={() => closeTab(tab.id)}
                        ref={(handle) => {
                          if (handle) {
                            handlesRef.current.set(tab.id, handle);
                            if (tab.id === state.activeId) {
                              setActiveHandle((prev) => prev ?? handle);
                            }
                          } else {
                            handlesRef.current.delete(tab.id);
                          }
                        }}
                      />
                    ))}
                  </Suspense>
                </div>
              </div>
            </div>
            {state.copilotOpen && (
              <div
                className="flex min-h-0 border-l border-border-subtle bg-background"
                style={{
                  flex:
                    renderDevice !== "desktop"
                      ? "1 1 0"
                      : `0 0 ${state.copilotWidth}px`,
                  minWidth: renderDevice !== "desktop" ? 280 : undefined,
                }}
              >
                <Suspense
                  fallback={
                    <div
                      className="size-full min-w-72 animate-pulse bg-muted/25"
                      role="status"
                      aria-label="加载 AI 助手"
                    />
                  }
                >
                  <AssistantPanel webviewHandle={activeHandle} />
                </Suspense>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function BrowserSidePanel({
  open,
  pinned,
  onMouseEnter,
  onMouseLeave,
}: {
  open: boolean;
  pinned: boolean;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}) {
  const { t } = useI18n();
  const {
    state,
    activeTab,
    openTab,
    closeTab,
    restoreClosedTab,
    activateTab,
    history,
    bookmarks,
  } = useBrowserStore();
  const [query, setQuery] = useState("");
  const [copiedField, setCopiedField] = useState<"url" | "title" | null>(null);
  const [panelMode, setPanelMode] = useState<"tabs" | "history" | "bookmarks">(
    "tabs",
  );
  const filteredTabs = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return state.tabs;
    return state.tabs.filter((tab) =>
      `${tab.title} ${tab.url}`.toLowerCase().includes(needle),
    );
  }, [query, state.tabs]);
  const filteredHistory = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const items = history.slice(0, 30);
    if (!needle) return items;
    return items.filter((item) =>
      `${item.title} ${item.url}`.toLowerCase().includes(needle),
    );
  }, [history, query]);
  const filteredClosedTabs = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const items = state.closedTabs.slice(0, 10);
    if (!needle) return items;
    return items.filter((item) =>
      `${item.title} ${item.url}`.toLowerCase().includes(needle),
    );
  }, [query, state.closedTabs]);
  const filteredBookmarks = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return bookmarks;
    return bookmarks.filter((item) =>
      `${item.title} ${item.url}`.toLowerCase().includes(needle),
    );
  }, [bookmarks, query]);
  const panelCounts = {
    tabs: state.tabs.length,
    history: history.length + state.closedTabs.length,
    bookmarks: bookmarks.length,
  };
  const visibleItems =
    panelMode === "tabs"
      ? filteredTabs.length
      : panelMode === "history"
        ? filteredHistory.length + filteredClosedTabs.length
        : filteredBookmarks.length;
  const emptyLabel =
    query.trim().length > 0
      ? t.browser.empty.noMatch
      : panelMode === "tabs"
        ? t.browser.empty.noTabs
        : panelMode === "history"
          ? t.browser.empty.noRecent
          : t.browser.empty.noFavorites;
  const activeTabLabel =
    activeTab?.title || activeTab?.url || t.browser.defaultTabTitle;
  const activeTabUrl = activeTab?.url ?? "";
  const canCopyActiveUrl =
    activeTabUrl.length > 0 && !activeTabUrl.startsWith("echo:");

  const copyText = useCallback(async (text: string, field: "url" | "title") => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(field);
      window.setTimeout(() => setCopiedField(null), 1200);
    } catch (e) {
      swallow(e);
    }
  }, []);

  const closeOtherTabs = useCallback(() => {
    if (!activeTab) return;
    state.tabs
      .filter((tab) => tab.id !== activeTab.id)
      .forEach((tab) => closeTab(tab.id));
  }, [activeTab, closeTab, state.tabs]);

  return (
    <div
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      className={cn(
        "absolute right-3 top-[6.75rem] z-[40] hidden h-[calc(100vh-7.5rem)] w-[280px] flex-col rounded-2xl bg-popover px-3 py-3 text-popover-foreground shadow-lg transition-[opacity,transform] duration-fast md:flex",
        open ? "pointer-events-auto" : "pointer-events-none",
      )}
      style={{
        opacity: open ? 1 : 0,
        transform: open ? "translateY(0)" : "translateY(-8px)",
      }}
    >
      <div
        className="flex h-11 w-full items-center gap-2 rounded-xl px-1"
        style={
          {
            WebkitAppRegion: "no-drag",
          } as React.CSSProperties
        }
      >
        <div className="grid size-7 place-items-center bg-muted text-foreground">
          <MenuIcon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{t.browser.pageTitle}</div>
          <div className="text-mini text-muted-foreground">
            {t.browser.pageSubtitle(pinned)}
          </div>
        </div>
      </div>

      <div className="mt-4 flex h-9 items-center gap-2 rounded-full bg-muted px-3 text-xs text-muted-foreground">
        <SearchIcon className="size-4 shrink-0" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t.browser.searchPlaceholder}
          aria-label={t.browser.searchPlaceholder}
          className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground"
        />
      </div>

      {activeTab && (
        <div className="mt-3 rounded-2xl bg-card p-2.5">
          <div className="flex items-start gap-2">
            {activeTab.favicon ? (
              <img
                src={activeTab.favicon}
                alt=""
                className="mt-0.5 size-4 shrink-0 rounded-sm"
                onError={(e) => {
                  e.currentTarget.style.display = "none";
                }}
              />
            ) : (
              <GlobeIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-semibold text-foreground">
                {activeTabLabel}
              </div>
              <div className="mt-0.5 truncate text-mini text-muted-foreground">
                {activeTabUrl}
              </div>
            </div>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            <button
              type="button"
              disabled={!canCopyActiveUrl}
              onClick={() => copyText(activeTabUrl, "url")}
              className="flex h-8 items-center justify-center gap-1.5 rounded-lg bg-muted/60 px-2 text-mini font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-45"
            >
              <CopyIcon className="size-3.5" />
              {copiedField === "url"
                ? t.browser.copy.copied
                : t.browser.copy.link}
            </button>
            <button
              type="button"
              onClick={() => copyText(activeTabLabel, "title")}
              className="flex h-8 items-center justify-center gap-1.5 rounded-lg bg-muted/60 px-2 text-mini font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <CopyIcon className="size-3.5" />
              {copiedField === "title"
                ? t.browser.copy.copied
                : t.browser.copy.title}
            </button>
            <button
              type="button"
              onClick={() => openTab(activeTabUrl)}
              className="flex h-8 items-center justify-center gap-1.5 rounded-lg bg-muted/60 px-2 text-mini font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <PlusIcon className="size-3.5" />
              {t.browser.copy.tabMenuItem}
            </button>
            <button
              type="button"
              disabled={state.tabs.length <= 1}
              onClick={closeOtherTabs}
              className="flex h-8 items-center justify-center gap-1.5 rounded-lg bg-muted/60 px-2 text-mini font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-45"
            >
              <XIcon className="size-3.5" />
              {t.browser.menu.closeOtherTabs}
            </button>
          </div>
        </div>
      )}

      <div className="mt-3 grid grid-cols-3 rounded-xl bg-muted p-1 text-mini font-medium text-muted-foreground">
        {[
          { id: "tabs", label: t.browser.tabs.label, icon: MenuIcon },
          { id: "history", label: t.browser.tabs.recent, icon: ClockIcon },
          { id: "bookmarks", label: t.browser.tabs.favorites, icon: StarIcon },
        ].map((item) => {
          const Icon = item.icon;
          const active = panelMode === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() =>
                setPanelMode(item.id as "tabs" | "history" | "bookmarks")
              }
              className={cn(
                "flex h-8 items-center justify-center gap-1 rounded-lg transition-colors",
                active
                  ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                  : "hover:bg-background/60 hover:text-foreground",
              )}
            >
              <Icon className="size-3.5" />
              <span>{item.label}</span>
              <span className="text-micro text-muted-foreground">
                {panelCounts[item.id as keyof typeof panelCounts]}
              </span>
            </button>
          );
        })}
      </div>

      <button
        type="button"
        onClick={() => openTab()}
        className="mt-3 flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium text-foreground transition-colors hover:bg-background/70"
      >
        <PlusIcon className="size-4" />
        {t.browser.newTab}
      </button>

      <div className="mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
        {panelMode === "tabs" &&
          filteredTabs.map((tab) => {
            const active = tab.id === state.activeId;
            return (
              <div
                key={tab.id}
                role="button"
                tabIndex={0}
                onClick={() => activateTab(tab.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    activateTab(tab.id);
                  }
                }}
                className={cn(
                  "group flex h-10 cursor-pointer items-center gap-2 rounded-xl px-2.5 text-sm transition-colors",
                  active
                    ? "bg-background text-foreground shadow-[var(--shadow-xs)] ring-1 ring-border-default"
                    : "text-muted-foreground hover:bg-background/60 hover:text-foreground",
                )}
                title={tab.title || tab.url}
              >
                {tab.favicon ? (
                  <img
                    src={tab.favicon}
                    alt=""
                    className="size-4 shrink-0 rounded-sm"
                    onError={(e) => {
                      e.currentTarget.style.display = "none";
                    }}
                  />
                ) : (
                  <GlobeIcon className="size-4 shrink-0 opacity-70" />
                )}
                <span className="min-w-0 flex-1 truncate">
                  {tab.title || tab.url}
                </span>
                {tab.device !== "desktop" && (
                  <span className="rounded-full bg-muted px-1.5 py-0.5 text-micro uppercase text-muted-foreground">
                    {tab.device}
                  </span>
                )}
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    closeTab(tab.id);
                  }}
                  className="grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground/70 opacity-0 transition-opacity hover:bg-foreground/10 hover:text-foreground group-hover:opacity-100 data-[active=true]:opacity-100"
                  data-active={active}
                  title={t.browser.closeTab}
                >
                  <XIcon className="size-3.5" />
                </button>
              </div>
            );
          })}
        {panelMode === "history" && filteredClosedTabs.length > 0 && (
          <div className="mb-2 rounded-xl bg-muted/45 p-1.5">
            <div className="flex items-center justify-between px-1.5 pb-1 text-micro font-semibold text-muted-foreground">
              <span>最近关闭</span>
              <span>{isWindows() ? "Ctrl+Shift+T" : "⌘⇧T"}</span>
            </div>
            {filteredClosedTabs.map((item) => (
              <button
                key={`closed-${item.id}-${item.closedAt}`}
                type="button"
                onClick={() => restoreClosedTab(item.id)}
                className="flex h-10 w-full items-center gap-2 rounded-lg px-2 text-left transition-colors hover:bg-background/70"
                title={`恢复 ${item.title || item.url}`}
              >
                <RefreshCwIcon className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-foreground">
                    {item.title || item.url}
                  </span>
                  <span className="block truncate text-micro text-muted-foreground">
                    {item.url}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
        {panelMode === "history" &&
          filteredHistory.map((item) => (
            <button
              key={`${item.url}-${item.visitedAt}`}
              type="button"
              onClick={() => openTab(item.url)}
              className="group flex h-11 w-full items-center gap-2 rounded-xl px-2.5 text-left text-sm text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground"
              title={item.title || item.url}
            >
              {item.favicon ? (
                <img
                  src={item.favicon}
                  alt=""
                  className="size-4 shrink-0 rounded-sm"
                  onError={(e) => {
                    e.currentTarget.style.display = "none";
                  }}
                />
              ) : (
                <ClockIcon className="size-4 shrink-0 opacity-70" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-foreground">
                  {item.title || item.url}
                </span>
                <span className="block truncate text-mini">{item.url}</span>
              </span>
            </button>
          ))}
        {panelMode === "bookmarks" &&
          filteredBookmarks.map((item) => (
            <button
              key={item.url}
              type="button"
              onClick={() => openTab(item.url)}
              className="group flex h-11 w-full items-center gap-2 rounded-xl px-2.5 text-left text-sm text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground"
              title={item.title || item.url}
            >
              {item.favicon ? (
                <img
                  src={item.favicon}
                  alt=""
                  className="size-4 shrink-0 rounded-sm"
                  onError={(e) => {
                    e.currentTarget.style.display = "none";
                  }}
                />
              ) : (
                <StarIcon className="size-4 shrink-0 text-warning" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-foreground">
                  {item.title || item.url}
                </span>
                <span className="block truncate text-mini">{item.url}</span>
              </span>
            </button>
          ))}
        {visibleItems === 0 && (
          <div className="px-3 py-8 text-center text-xs text-muted-foreground">
            {emptyLabel}
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={() => openTab()}
        className="mt-3 flex h-10 items-center justify-center gap-2 rounded-xl bg-muted text-sm font-medium text-foreground"
      >
        <PlusIcon className="size-4" />
        {t.browser.newTabPage}
      </button>
    </div>
  );
}

function BrowserRouteQuery() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { openTab, settings } = useBrowserStore();
  const query = searchParams.get("q")?.trim() ?? "";

  useEffect(() => {
    if (!query) return;
    openTab(
      `${SEARCH_ENGINE_URLS[settings.searchEngine]}${encodeURIComponent(query)}`,
      { title: query },
    );
    const next = new URLSearchParams(searchParams);
    next.delete("q");
    setSearchParams(next, { replace: true });
  }, [openTab, query, searchParams, setSearchParams, settings.searchEngine]);

  return null;
}

export default function BrowserPage() {
  // Implementation note.
  useEffect(() => {
    setAppMode("browser");
  }, []);
  return (
    <BrowserStoreProvider>
      <BrowserRouteQuery />
      <BrowserShell />
    </BrowserStoreProvider>
  );
}
