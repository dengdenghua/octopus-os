/* Implementation note. */

import {
  forwardRef,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ComponentType,
} from "react";
import {
  CopyIcon,
  PlugIcon,
} from "lucide-react";

import { swallow } from "@/core/utils/log";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { copyTextToClipboard } from "@/core/clipboard";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { loadProjectsPage } from "@/core/navigation/workspace-route-preload";
import { WORKBENCH_BUILTIN_APPS } from "@/core/workbench/apps";
import { RemoteWorkbenchSurface } from "@/core/workbench/remote-surface";
import { WorkbenchSurfaceProvider } from "@/core/workbench/workbench-surface";
import { useActiveAgentId } from "@/core/agents/active";
import { useEnabledModuleIds } from "@/core/modules/enabled-modules";
import { useWorkbenchAvailabilitySync } from "@/core/workbench/availability";
import {
  AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
  AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME,
  AUTOMATION_CAPSULE_SURFACE_CLASS_NAME,
} from "@/components/ui/automation-capsule";

import {
  BROWSER_HOME_URL,
  type BrowserTab,
} from "./browser-store";
import {
  RELAY_STATUS_REFRESH_MS,
  getRelayStatusRetryDelay,
} from "./relay-polling";

const BrowserHome = lazy(() =>
  import("./browser-home").then((module) => ({
    default: module.BrowserHome,
  })),
);

interface Props {
  tab: BrowserTab;
  active: boolean;
  onPatch: (patch: Partial<BrowserTab>) => void;
  onClose?: () => void;
  renderDevice?: BrowserTab["device"];
}

const BROWSER_CORE_COMPONENTS: Record<string, ComponentType> = {
  projects: lazy(loadProjectsPage),
};

/* Implementation note. */
type CrashInfo = NonNullable<BrowserTab["crash"]>;

type WebviewElement = HTMLElement & {
  src: string;
  getWebContentsId: () => number;
  reload: () => void;
  goBack: () => void;
  goForward: () => void;
  canGoBack: () => boolean;
  canGoForward: () => boolean;
  getTitle: () => string;
  isLoading: () => boolean;
  loadURL: (url: string) => Promise<void>;
};

export interface WebviewTabHandle {
  reload: () => void;
  goBack: () => void;
  goForward: () => void;
  loadURL: (url: string) => void;
  canGoBack: () => boolean;
  canGoForward: () => boolean;
  executeJS: (code: string) => Promise<unknown>;
  getWebContentsId: () => number | null;
  extractText: () => Promise<{
    url: string;
    title: string;
    text: string;
    truncated?: boolean;
    textLength?: number;
    pageAgent?: unknown;
  }>;
  capturePage: () => Promise<{
    dataUrl: string;
    width: number;
    height: number;
  }>;
  runAction: (
    action: string,
    params?: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
  setControlIndicator?: (
    mode: "idle" | "action" | "paused",
    detail?: Record<string, unknown>,
  ) => void;
}

export function browserWebContentsAdoptionLease(tabId: string): string {
  return `echo-webcontents:${tabId}`;
}

interface BrowserRelayStatus {
  connected: boolean;
  extension_version?: string;
  active_tab?: {
    id?: number | string;
    url?: string;
    title?: string;
  } | null;
  manifest_exists?: boolean;
  pending_commands?: number;
}

class BrowserHttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "BrowserHttpError";
  }
}

async function browserJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, init);
  if (!res.ok) {
    const data = (await res
      .json()
      .catch(() => ({ detail: res.statusText }))) as { detail?: unknown };
    const detail = typeof data.detail === "string" ? data.detail : "";
    throw new BrowserHttpError(
      res.status,
      detail || res.statusText || `HTTP ${res.status}`,
    );
  }
  return res.json();
}

function BackendBrowserTab({
  tab,
  active,
  onPatch,
  imperativeRef,
}: Omit<Props, "renderDevice"> & {
  imperativeRef: React.ForwardedRef<WebviewTabHandle>;
}) {
  const sessionId = `browser-page:${tab.id}`;
  const { t } = useI18n();
  const wt = t.browser.webviewTab;
  const [screenshot, setScreenshot] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [extensionHint, setExtensionHint] = useState("");
  const [extensionPath, setExtensionPath] = useState("");
  const [extensionGuideOpen, setExtensionGuideOpen] = useState(false);
  const [relayStatus, setRelayStatus] = useState<BrowserRelayStatus | null>(
    null,
  );
  const bookmarkletHref = `javascript:(()=>{const s=document.createElement("script");s.src=${JSON.stringify(
    `${getBackendBaseURL()}/api/browser/relay/bookmarklet.js?app=`,
  )}+encodeURIComponent(location.origin)+"&t="+Date.now();document.documentElement.appendChild(s);})()`;

  const refreshRelayStatus = async () => {
    const status = await browserJson<BrowserRelayStatus>(
      "/api/browser/relay/status",
      {
        headers: authHeaders(),
      },
    );
    setRelayStatus(status);
    return status;
  };

  const openExtensionFolder = async () => {
    try {
      const data = await browserJson<{ opened: boolean; path: string }>(
        "/api/browser/open-extension-folder",
        { method: "POST", headers: jsonAuthHeaders() },
      );
      setExtensionPath(data.path);
      setExtensionHint(wt.pluginDirectoryOpened(data.path));
      setExtensionGuideOpen(true);
    } catch (e) {
      swallow(e);
      setExtensionHint(e instanceof Error ? e.message : String(e));
    }
  };

  const copyExtensionPath = async () => {
    if (!extensionPath) {
      await openExtensionFolder();
      return;
    }
    try {
      await copyTextToClipboard(extensionPath);
      setExtensionHint(wt.pluginPathCopied);
    } catch (e) {
      swallow(e);
      setExtensionHint(extensionPath);
    }
  };

  const copyBookmarklet = async () => {
    try {
      await copyTextToClipboard(bookmarkletHref);
      setExtensionHint(wt.bookmarkletCopied);
    } catch (e) {
      swallow(e);
      setExtensionHint(bookmarkletHref);
    }
  };

  const relayCommand = async (
    action: string,
    params: Record<string, unknown> = {},
  ) =>
    browserJson<Record<string, unknown>>("/api/browser/relay/command", {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ action, timeout_seconds: 12, ...params }),
    });

  const preferRelay = async () => {
    const status = relayStatus?.connected
      ? relayStatus
      : await refreshRelayStatus();
    return Boolean(status.connected);
  };

  const refreshScreenshot = async () => {
    if (await preferRelay()) {
      const shot = await relayCommand("screenshot");
      if (typeof shot.dataUrl === "string") {
        setScreenshot(shot.dataUrl);
        return;
      }
    }
    const shot = await browserJson<{
      base64: string;
      width: number;
      height: number;
    }>(
      `/api/browser/screenshot/base64?session_id=${encodeURIComponent(sessionId)}`,
      { headers: authHeaders() },
    );
    setScreenshot(`data:image/png;base64,${shot.base64}`);
  };

  const launch = async () => {
    await browserJson<{ status: string }>("/api/browser/launch", {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ session_id: sessionId, headless: false }),
    });
  };

  const navigate = async (url: string) => {
    setLoading(true);
    setError(null);
    onPatch({ isLoading: true });
    try {
      if (await preferRelay()) {
        const info = await relayCommand("navigate", { url });
        const nextUrl = typeof info.url === "string" ? info.url : url;
        const nextTitle = typeof info.title === "string" ? info.title : nextUrl;
        onPatch({ url: nextUrl, title: nextTitle, isLoading: false });
        await refreshScreenshot();
        return;
      }
      await launch();
      const info = await browserJson<{ url: string; title: string }>(
        "/api/browser/navigate",
        {
          method: "POST",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({ session_id: sessionId, url }),
        },
      );
      onPatch({ url: info.url, title: info.title, isLoading: false });
      await refreshScreenshot();
    } catch (e) {
      swallow(e);
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      onPatch({ isLoading: false });
    } finally {
      setLoading(false);
    }
  };

  const runAction = async (
    action: string,
    params: Record<string, unknown> = {},
  ) => {
    if (await preferRelay()) {
      const data = await relayCommand(action, params);
      const nextUrl = typeof data.url === "string" ? data.url : undefined;
      const nextTitle = typeof data.title === "string" ? data.title : undefined;
      if (nextUrl || nextTitle) {
        onPatch({
          ...(nextUrl ? { url: nextUrl } : {}),
          ...(nextTitle ? { title: nextTitle } : {}),
        });
      }
      await refreshScreenshot().catch((e) => {
        swallow(e);
      });
      return data;
    }
    await launch();
    const data = await browserJson<Record<string, unknown>>(
      "/api/browser/action",
      {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({ session_id: sessionId, action, ...params }),
      },
    );
    const nextUrl = typeof data.url === "string" ? data.url : undefined;
    const nextTitle = typeof data.title === "string" ? data.title : undefined;
    if (nextUrl || nextTitle) {
      onPatch({
        ...(nextUrl ? { url: nextUrl } : {}),
        ...(nextTitle ? { title: nextTitle } : {}),
      });
    }
    await refreshScreenshot().catch((e) => {
      swallow(e);
    });
    return data;
  };

  useImperativeHandle(
    imperativeRef,
    () => ({
      reload: () => void runAction("reload"),
      goBack: () => void runAction("back"),
      goForward: () => void runAction("forward"),
      loadURL: (url) => void navigate(url),
      canGoBack: () => true,
      canGoForward: () => true,
      executeJS: async () => undefined,
      getWebContentsId: () => null,
      extractText: async () => {
        if (await preferRelay()) {
          const data = await relayCommand("extract");
          return {
            url: typeof data.url === "string" ? data.url : "",
            title: typeof data.title === "string" ? data.title : "",
            text: typeof data.text === "string" ? data.text : "",
            truncated: Boolean(data.truncated),
            textLength:
              typeof data.textLength === "number" ? data.textLength : undefined,
            pageAgent: data.pageAgent,
          };
        }
        await launch();
        return browserJson<{
          url: string;
          title: string;
          text: string;
          truncated?: boolean;
          textLength?: number;
        }>(
          `/api/browser/extract-text?session_id=${encodeURIComponent(sessionId)}`,
          { headers: authHeaders() },
        );
      },
      capturePage: async () => {
        if (await preferRelay()) {
          const shot = await relayCommand("screenshot");
          const dataUrl = typeof shot.dataUrl === "string" ? shot.dataUrl : "";
          if (dataUrl) {
            setScreenshot(dataUrl);
            return { dataUrl, width: 0, height: 0 };
          }
        }
        await launch();
        const shot = await browserJson<{
          base64: string;
          width: number;
          height: number;
        }>(
          `/api/browser/screenshot/base64?session_id=${encodeURIComponent(sessionId)}`,
          { headers: authHeaders() },
        );
        const dataUrl = `data:image/png;base64,${shot.base64}`;
        setScreenshot(dataUrl);
        return { dataUrl, width: shot.width, height: shot.height };
      },
      runAction,
      setControlIndicator: () => undefined,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- imperative handle exposes local async helpers that depend on session/tab state; stabilizing them all would require extensive restructuring
    [sessionId, tab.url, relayStatus?.connected],
  );

  useEffect(() => {
    if (!active) return;
    void navigate(tab.url);
    // The tab URL is the source of truth; URL-bar navigation updates it.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- navigate is a local async helper tied to this tab instance; active/tab.id are the intended triggers
  }, [active, tab.id]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: number | undefined;
    let consecutiveFailures = 0;

    const schedule = (delay: number) => {
      if (cancelled) return;
      timer = window.setTimeout(() => void tick(), delay);
    };

    const tick = async () => {
      try {
        const status = await browserJson<BrowserRelayStatus>(
          "/api/browser/relay/status",
          {
            headers: authHeaders(),
          },
        );
        if (!cancelled) {
          consecutiveFailures = 0;
          setRelayStatus(status);
          schedule(RELAY_STATUS_REFRESH_MS);
        }
      } catch (e) {
        swallow(e);
        if (!cancelled) {
          consecutiveFailures += 1;
          setRelayStatus(null);
          schedule(
            getRelayStatusRetryDelay(
              e instanceof BrowserHttpError ? e.status : null,
              consecutiveFailures,
            ),
          );
        }
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [active]);

  return (
    <div
      style={
        active
          ? { display: "flex", width: "100%", height: "100%" }
          : {
              display: "flex",
              position: "absolute",
              width: 0,
              height: 0,
              visibility: "hidden",
              pointerEvents: "none",
            }
      }
      className="relative flex-col overflow-auto bg-muted/20"
    >
      <div
        className={cn(
          AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME,
          "absolute inset-x-3 top-3 z-10 flex justify-end",
        )}
      >
        <div
          className={cn(
            AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
            AUTOMATION_CAPSULE_SURFACE_CLASS_NAME,
            "relative flex items-center gap-1.5 p-1 text-mini",
          )}
        >
          <button
            type="button"
            onClick={() => setExtensionGuideOpen((v) => !v)}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded-full px-2.5 font-medium transition-colors",
              relayStatus?.connected
                ? "bg-success/10 text-success"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <PlugIcon className="size-3.5" />
            {wt.extPluginButton}
          </button>
          <button
            type="button"
            className="h-7 rounded-full bg-primary px-2.5 font-medium text-primary-foreground hover:bg-primary/90"
            onClick={openExtensionFolder}
          >
            {wt.openDirectory}
          </button>
          {extensionGuideOpen && (
            <div className="absolute right-0 top-full mt-2 w-[360px] max-w-[calc(100vw-1rem)] rounded-2xl border bg-background/98 p-4 text-left shadow-xl backdrop-blur">
              <div className="flex items-start gap-3">
                <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
                  <PlugIcon className="size-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold text-foreground">
                    {wt.extPluginTitle}
                  </div>
                  <div className="mt-1 text-xs leading-5 text-muted-foreground">
                    {wt.extPluginDesc}
                  </div>
                </div>
              </div>
              <div className="mt-3 rounded-2xl border bg-primary/5 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold text-foreground">
                      {wt.dragToBookmarks}
                    </div>
                    <div className="mt-1 text-mini leading-4 text-muted-foreground">
                      {wt.dragToBookmarksDesc}
                    </div>
                  </div>
                  <a
                    href={bookmarkletHref}
                    draggable
                    onClick={(event) => event.preventDefault()}
                    className="shrink-0 rounded-full bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-[var(--shadow-xs)] hover:bg-primary/90"
                    title={wt.dragToBookmarksTitle}
                  >
                    EchoAI
                  </a>
                </div>
              </div>
              <div className="mt-3 grid gap-2 text-xs">
                <div className="rounded-xl bg-muted/55 px-3 py-2">
                  {wt.step1Temporary}
                </div>
                <div className="rounded-xl bg-muted/55 px-3 py-2">
                  {wt.step2LongTerm}
                </div>
                <div className="rounded-xl bg-muted/55 px-3 py-2">
                  {wt.step3LoadExtension}
                </div>
              </div>
              {extensionPath && (
                <div className="mt-3 rounded-xl border bg-muted/25 px-3 py-2">
                  <div className="text-mini text-muted-foreground">
                    {wt.pluginDirectory}
                  </div>
                  <div className="mt-1 break-all font-mono text-mini text-foreground">
                    {extensionPath}
                  </div>
                </div>
              )}
              <div className="mt-3 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={copyBookmarklet}
                  className="flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <CopyIcon className="size-3.5" />
                  {wt.copyBookmarklet}
                </button>
                <button
                  type="button"
                  onClick={copyExtensionPath}
                  className="h-8 rounded-full border px-3 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  {wt.copyPath}
                </button>
                <button
                  type="button"
                  onClick={openExtensionFolder}
                  className="h-8 rounded-full bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                >
                  {wt.openPluginDirectory}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
      {screenshot ? (
        <img
          src={screenshot}
          alt={tab.title || tab.url}
          className="w-full object-contain"
          onClick={() => void refreshScreenshot()}
        />
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
          <div className="text-sm font-medium">{wt.connectingPlugin}</div>
          <div className="max-w-sm text-xs leading-relaxed text-muted-foreground">
            {wt.connectingPluginDesc}
          </div>
        </div>
      )}
      {loading && (
        <div className="absolute inset-0 grid place-items-center bg-background/60 text-xs text-muted-foreground">
          Loading...
        </div>
      )}
      {error && (
        <div className="absolute left-3 right-3 top-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}
      {extensionHint && (
        <div className="absolute bottom-3 left-3 right-3 rounded-md border bg-background/95 px-3 py-2 text-xs text-muted-foreground shadow-[var(--shadow-xs)]">
          {extensionHint}
        </div>
      )}
    </div>
  );
}

export const WebviewTab = forwardRef<WebviewTabHandle, Props>(
  function WebviewTab(
    { tab, active, onPatch, onClose, renderDevice },
    imperativeRef,
  ) {
    useWorkbenchAvailabilitySync();
    const activeAgentId = useActiveAgentId() ?? "general";
    const enabledModuleIds = useEnabledModuleIds(activeAgentId);
    const enabledModuleIdSet = useMemo(
      () => new Set(enabledModuleIds),
      [enabledModuleIds],
    );
    const ref = useRef<WebviewElement | null>(null);
    const { t } = useI18n();
    const wt = t.browser.webviewTab;
    const [crash, setCrash] = useState<CrashInfo | null>(tab.crash ?? null);
    const [reloadSeed, setReloadSeed] = useState(0); // Implementation note.
    const crashTimesRef = useRef<number[]>([]);
    const autoRecoveryTimerRef = useRef<number | null>(null);
    const [controlIndicator, setControlIndicatorState] = useState<{
      mode: "idle" | "action" | "paused";
      action?: string;
      reason?: string;
      nonce: number;
    }>({ mode: "idle", nonce: 0 });
    const controlIndicatorTimerRef = useRef<number | null>(null);
    const adoptionLease = useMemo(
      () => browserWebContentsAdoptionLease(tab.id),
      [tab.id],
    );

    // Implementation note.
    // Implementation note.
    // "The WebView must be attached to the DOM and the dom-ready event
    // emitted before this method can be called."
    const readyRef = useRef(false);

    // Implementation note.
    const safe = <T,>(fn: () => T, fallback: T): T => {
      if (!ref.current || !readyRef.current) return fallback;
      try {
        return fn();
      } catch (e) {
        swallow(e);
        return fallback;
      }
    };

    const setControlIndicator = useCallback(
      (
        mode: "idle" | "action" | "paused",
        detail: Record<string, unknown> = {},
      ) => {
        if (controlIndicatorTimerRef.current !== null) {
          window.clearTimeout(controlIndicatorTimerRef.current);
          controlIndicatorTimerRef.current = null;
        }
        const next = {
          mode,
          action: typeof detail.action === "string" ? detail.action : undefined,
          reason: typeof detail.reason === "string" ? detail.reason : undefined,
          nonce: Date.now(),
        };
        setControlIndicatorState(next);
        if (mode === "paused") {
          controlIndicatorTimerRef.current = window.setTimeout(() => {
            setControlIndicatorState((prev) =>
              prev.nonce === next.nonce
                ? { mode: "idle", nonce: Date.now() }
                : prev,
            );
            controlIndicatorTimerRef.current = null;
          }, 1600);
        }
      },
      [],
    );

    useImperativeHandle(
      imperativeRef,
      () => ({
        reload: () =>
          safe(() => {
            ref.current!.reload();
            return undefined;
          }, undefined),
        goBack: () =>
          safe(() => {
            ref.current!.goBack();
            return undefined;
          }, undefined),
        goForward: () =>
          safe(() => {
            ref.current!.goForward();
            return undefined;
          }, undefined),
        loadURL: (url) => {
          if (!ref.current || !readyRef.current) {
            // Implementation note.
            if (ref.current) ref.current.src = url;
            return;
          }
          try {
            void ref.current.loadURL(url).catch((e) => {
              swallow(e);
            });
          } catch (e) {
            swallow(e);
            ref.current.src = url;
          }
        },
        canGoBack: () => safe(() => ref.current!.canGoBack(), false),
        canGoForward: () => safe(() => ref.current!.canGoForward(), false),
        executeJS: async (code) => {
          const wv = ref.current;
          const api = window.echo;
          if (!wv || !api || !readyRef.current) return undefined;
          try {
            return await api.browser.executeJS(wv.getWebContentsId(), code);
          } catch (e) {
            swallow(e);
            return undefined;
          }
        },
        getWebContentsId: () =>
          safe(() => ref.current!.getWebContentsId(), null),
        extractText: async () => {
          const wv = ref.current;
          const api = window.echo;
          if (!wv || !api || !readyRef.current) {
            throw new Error("webview not ready");
          }
          return api.browser.extractText(wv.getWebContentsId());
        },
        capturePage: async () => {
          const wv = ref.current;
          const api = window.echo;
          if (!wv || !api || !readyRef.current) {
            throw new Error("webview not ready");
          }
          return api.browser.capturePage(wv.getWebContentsId());
        },
        runAction: async (action, params = {}) => {
          const wv = ref.current;
          const api = window.echo;
          if (!wv || !api || !readyRef.current) {
            throw new Error("webview not ready");
          }
          const wcId = wv.getWebContentsId();
          switch (action) {
            case "click":
              return api.browser.click(wcId, String(params.selector || ""));
            case "type":
              return api.browser.type(
                wcId,
                String(params.selector || ""),
                String(params.text || ""),
                { clear: !!params.clear },
              );
            case "hover":
              return api.browser.hover(wcId, String(params.selector || ""));
            case "scroll":
              return api.browser.scroll(wcId, params);
            case "wait":
              return api.browser.waitFor(
                wcId,
                String(params.selector || ""),
                Number(params.timeout || 10_000),
              );
            case "press":
              return api.browser.pressKey(wcId, String(params.key || "Enter"));
            case "aria":
              return api.browser.getAriaTree(wcId, {
                maxDepth:
                  typeof params.maxDepth === "number"
                    ? params.maxDepth
                    : undefined,
              });
            default:
              return { ok: false, error: `unsupported action: ${action}` };
          }
        },
        setControlIndicator,
      }),
      [setControlIndicator],
    );

    useEffect(
      () => () => {
        if (controlIndicatorTimerRef.current !== null) {
          window.clearTimeout(controlIndicatorTimerRef.current);
        }
      },
      [],
    );

    // Implementation note.
    useEffect(() => {
      const wv = ref.current;
      if (!wv) return;

      const onDomReady = () => {
        readyRef.current = true;
        // Every tab remains mounted while hidden. Publish a stable logical
        // lease and current native id so the desktop host can move the same
        // live webContents between surfaces without navigating it again.
        wv.setAttribute(
          "data-echo-webcontents-adoption-lease",
          adoptionLease,
        );
        try {
          wv.setAttribute(
            "data-echo-adopted-web-contents-id",
            String(wv.getWebContentsId()),
          );
        } catch (error) {
          swallow(error);
        }
      };
      wv.addEventListener("dom-ready", onDomReady);

      const onTitle = (e: Event & { title?: string }) => {
        const t = e.title || wv.getTitle();
        if (t) onPatch({ title: t });
      };
      const onFavicon = (e: Event & { favicons?: string[] }) => {
        const f = e.favicons?.[0];
        if (f) onPatch({ favicon: f });
      };
      const onStart = () => onPatch({ isLoading: true });
      const onStop = () => onPatch({ isLoading: false });
      const onNavigated = (e: Event & { url?: string }) => {
        if (e.url) onPatch({ url: e.url });
      };

      // Implementation note.
      const onCrashed = (e: Event & { reason?: string; exitCode?: number }) => {
        readyRef.current = false;
        const occurredAt = Date.now();
        crashTimesRef.current = [
          ...crashTimesRef.current.filter(
            (timestamp) => occurredAt - timestamp < 60_000,
          ),
          occurredAt,
        ];
        const attempts = crashTimesRef.current.length;
        const info: CrashInfo = {
          reason: e.reason || "render-process-gone",
          exitCode: e.exitCode ?? -1,
          occurredAt,
          attempts,
          autoRecovering: attempts === 1,
        };
        setCrash(info);
        onPatch({ isLoading: false, crash: info });
        if (attempts === 1) {
          if (autoRecoveryTimerRef.current !== null) {
            window.clearTimeout(autoRecoveryTimerRef.current);
          }
          autoRecoveryTimerRef.current = window.setTimeout(() => {
            setCrash(null);
            onPatch({ crash: undefined, isLoading: true });
            setReloadSeed((seed) => seed + 1);
            autoRecoveryTimerRef.current = null;
          }, 900);
        }
      };

      wv.addEventListener("page-title-updated", onTitle as EventListener);
      wv.addEventListener("page-favicon-updated", onFavicon as EventListener);
      wv.addEventListener("did-start-loading", onStart);
      wv.addEventListener("did-stop-loading", onStop);
      wv.addEventListener("did-navigate", onNavigated as EventListener);
      wv.addEventListener("crashed", onCrashed as EventListener);
      wv.addEventListener("render-process-gone", onCrashed as EventListener);
      wv.addEventListener("did-navigate-in-page", onNavigated as EventListener);

      return () => {
        wv.removeEventListener("dom-ready", onDomReady);
        wv.removeEventListener("page-title-updated", onTitle as EventListener);
        wv.removeEventListener(
          "page-favicon-updated",
          onFavicon as EventListener,
        );
        wv.removeEventListener("did-start-loading", onStart);
        wv.removeEventListener("did-stop-loading", onStop);
        wv.removeEventListener("did-navigate", onNavigated as EventListener);
        wv.removeEventListener(
          "did-navigate-in-page",
          onNavigated as EventListener,
        );
        wv.removeEventListener("crashed", onCrashed as EventListener);
        wv.removeEventListener(
          "render-process-gone",
          onCrashed as EventListener,
        );
      };
    }, [adoptionLease, onPatch, reloadSeed]);

    useEffect(
      () => () => {
        if (autoRecoveryTimerRef.current !== null) {
          window.clearTimeout(autoRecoveryTimerRef.current);
        }
      },
      [],
    );

    // Implementation note.
    useEffect(() => {
      const wv = ref.current;
      const api = window.echo;
      if (!wv || !api) return;
      let cancelled = false;
      const apply = async () => {
        try {
          if (cancelled) return;
          const id = wv.getWebContentsId();
          await api.browser.setDevice(id, tab.device);
        } catch (e) {
          swallow(e);
        }
      };
      const handler = () => void apply();
      if (readyRef.current) {
        void apply();
      } else {
        wv.addEventListener("dom-ready", handler, { once: true });
      }
      return () => {
        cancelled = true;
        wv.removeEventListener("dom-ready", handler);
      };
    }, [tab.device]);

    // Implementation note.
    // Implementation note.
    const style: CSSProperties = active
      ? { display: "inline-flex", width: "100%", height: "100%" }
      : {
          display: "inline-flex",
          position: "absolute",
          width: 0,
          height: 0,
          visibility: "hidden",
          pointerEvents: "none",
        };

    // Implementation note.
    // Implementation note.
    const onReloadAfterCrash = () => {
      setCrash(null);
      onPatch({ crash: undefined, isLoading: true });
      setReloadSeed((s) => s + 1);
    };

    const homeDevice = renderDevice ?? tab.device;
    if (tab.url === BROWSER_HOME_URL) {
      return (
        <Suspense
          fallback={
            <div
              className="size-full animate-pulse bg-muted/25"
              role="status"
              aria-label="加载浏览器桌面"
            />
          }
        >
          <BrowserHome
            active={active}
            device={homeDevice}
            onOpen={(url) => {
              const builtinApp = WORKBENCH_BUILTIN_APPS.find(
                (app) => app.launchUrl === url,
              );
              onPatch({
                url,
                title: builtinApp?.name ?? url,
                isLoading: !builtinApp,
              });
            }}
          />
        </Suspense>
      );
    }

    const requestedBuiltinApp = WORKBENCH_BUILTIN_APPS.find(
      (app) => app.launchUrl === tab.url,
    );
    const builtinApp =
      requestedBuiltinApp &&
      enabledModuleIdSet.has(requestedBuiltinApp.moduleId)
        ? requestedBuiltinApp
        : undefined;
    if (requestedBuiltinApp && !builtinApp) {
      return (
        <div
          style={style}
          className="grid size-full place-items-center bg-background p-6"
        >
          <div className="max-w-sm text-center">
            <PlugIcon className="mx-auto size-8 text-muted-foreground" />
            <h2 className="mt-3 text-base font-semibold">应用未安装或已停用</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              请在 HUB 的应用市场中安装并启用{requestedBuiltinApp.name}。
            </p>
            <button
              type="button"
              className="mt-4 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
              onClick={() =>
                onPatch({
                  url: BROWSER_HOME_URL,
                  title: "浏览器桌面",
                  isLoading: false,
                })
              }
            >
              返回桌面
            </button>
          </div>
        </div>
      );
    }
    const CoreBuiltinPage = builtinApp
      ? BROWSER_CORE_COMPONENTS[builtinApp.id]
      : undefined;
    if (builtinApp && (builtinApp.delivery === "remote" || CoreBuiltinPage)) {
      return (
        <div
          style={style}
          className="relative min-h-0 flex-col overflow-hidden bg-background"
          data-browser-native-app={builtinApp.id}
        >
          <WorkbenchSurfaceProvider surface="browser">
            <Suspense
              fallback={
                <div className="grid h-full place-items-center text-sm text-muted-foreground">
                  正在打开{builtinApp.name}…
                </div>
              }
            >
              {builtinApp.delivery === "remote" ? (
                <RemoteWorkbenchSurface
                  app={builtinApp}
                  hostPath={builtinApp.workspaceRoute}
                />
              ) : CoreBuiltinPage ? (
                <CoreBuiltinPage />
              ) : null}
            </Suspense>
          </WorkbenchSurfaceProvider>
        </div>
      );
    }

    if (crash && active) {
      return (
        <div
          style={{ width: "100%", height: "100%" }}
          className="flex flex-col items-center justify-center gap-3 bg-muted/30 px-6 text-center"
        >
          <div className="text-4xl">{crash.autoRecovering ? "🔄" : "😵"}</div>
          <div className="text-base font-semibold">
            {crash.autoRecovering ? "正在恢复标签页" : wt.crashTitle}
          </div>
          <div className="max-w-md text-xs text-muted-foreground">
            {crash.reason} (exit {crash.exitCode}) ·{" "}
            {crash.autoRecovering
              ? "首次异常，正在自动重建网页进程…"
              : `${wt.crashDesc} · 60 秒内已异常 ${crash.attempts} 次，已停止自动重试。`}
          </div>
          {!crash.autoRecovering && (
            <div className="flex items-center gap-2">
              <button
                onClick={onReloadAfterCrash}
                className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground transition-colors hover:bg-primary/90"
              >
                {wt.crashReload}
              </button>
              {onClose && (
                <button
                  onClick={onClose}
                  className="rounded-md border border-border-default px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                >
                  关闭标签页
                </button>
              )}
            </div>
          )}
        </div>
      );
    }

    if (!window.echo?.isElectron) {
      return (
        <BackendBrowserTab
          tab={tab}
          active={active}
          onPatch={onPatch}
          imperativeRef={imperativeRef}
        />
      );
    }

    const controlEdgeVisible = controlIndicator.mode !== "idle";
    const controlEdgePaused = controlIndicator.mode === "paused";
    const controlEdgeColor = controlEdgePaused
      ? "rgba(245, 158, 11, 0.78)"
      : "rgba(20, 184, 166, 0.62)";
    const controlEdgeGlow = controlEdgePaused
      ? [
          "inset 0 0 0 1px rgba(245, 158, 11, 0.34)",
          "inset 0 0 18px rgba(245, 158, 11, 0.18)",
        ].join(", ")
      : [
          "inset 0 0 0 1px rgba(20, 184, 166, 0.26)",
          "inset 0 0 18px rgba(20, 184, 166, 0.16)",
        ].join(", ");

    return (
      <div style={style} className="relative overflow-hidden bg-background">
        <style>
          {`@keyframes echo-browser-webview-edge-pulse {
  0% { box-shadow: inset 0 0 0 1px rgba(20, 184, 166, 0.24), inset 0 0 10px rgba(20, 184, 166, 0.10); }
  45% { box-shadow: inset 0 0 0 2px rgba(20, 184, 166, 0.72), inset 0 0 24px rgba(20, 184, 166, 0.24); }
  100% { box-shadow: inset 0 0 0 1px rgba(20, 184, 166, 0.26), inset 0 0 18px rgba(20, 184, 166, 0.16); }
}
@media (prefers-reduced-motion: reduce) {
  .echo-browser-webview-edge-light { animation: none !important; transition: none !important; }
}`}
        </style>
        <webview
          key={`wv-${tab.id}-${reloadSeed}`}
          ref={ref as unknown as React.RefObject<HTMLElement>}
          src={tab.url}
          partition="persist:echo-browser"
          data-echo-webcontents-adoption-lease={adoptionLease}
          data-echo-adopted-web-contents-id="pending"
          style={{ width: "100%", height: "100%" }}
        />
        {controlEdgeVisible && (
          <div
            key={controlIndicator.nonce}
            aria-hidden="true"
            className="echo-browser-webview-edge-light pointer-events-none absolute inset-0 z-20 opacity-100 transition-opacity duration-fast"
            style={{
              border: `1px solid ${controlEdgeColor}`,
              boxShadow: controlEdgeGlow,
              animation:
                controlIndicator.mode === "action"
                  ? "echo-browser-webview-edge-pulse 620ms ease-out 1"
                  : undefined,
            }}
          />
        )}
      </div>
    );
  },
);
