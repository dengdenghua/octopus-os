import {
  MonitorIcon,
  SmartphoneIcon,
  TabletIcon,
  RotateCcwIcon,
  ExternalLinkIcon,
  CodeIcon,
  CodeXmlIcon,
  EyeIcon,
  Loader2Icon,
  SquareMousePointerIcon,
} from "lucide-react";
import {
  lazy,
  Suspense,
  useState,
  useRef,
  useEffect,
  useCallback,
} from "react";
import { cn } from "@/lib/utils";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { PreviewConsole } from "./preview-console";
import { BrowserPreviewPanel } from "./browser-preview-panel";

const CodeEditor = lazy(() =>
  import("./code-editor").then((module) => ({
    default: module.CodeEditor,
  })),
);

type PreviewDevice = "desktop" | "tablet" | "mobile";

export interface PreviewDiagnostic {
  id: string;
  level: "info" | "warning" | "error";
  source: "console" | "runtime" | "load" | "blank";
  message: string;
  stack?: string;
  timestamp: number;
}

interface LivePreviewPanelProps {
  /**
   * When provided, the iframe is pointed directly at this URL (typically a
   * blob: URL built from a just-written .html file, or in future a dev
   * server URL). Takes precedence over htmlContent/cssContent/jsContent.
   */
  previewUrl?: string | null;
  htmlContent?: string;
  cssContent?: string;
  jsContent?: string;
  filePath?: string | null;
  isLoading?: boolean;
  onRefresh?: () => void;
  onOpenExternal?: () => void;
  onDiagnosticsChange?: (diagnostics: PreviewDiagnostic[]) => void;
  /** When wired, the console panel shows an "add to chat" button per row. */
  onSendDiagnosticToChat?: (diagnostic: PreviewDiagnostic) => void;
  browserRegressionEnabled?: boolean;
  onToggleBrowserRegression?: () => void;
  /** Thread + workspace identity. When set and previewUrl is a non-blob http(s)
   * URL, the panel delegates to BrowserPreviewPanel — the single unified,
   * controllable URL-preview surface (real <webview>+CDP in Electron, screenshot
   * /iframe fallback on the web). The inline srcDoc/html mode is unaffected. */
  threadId?: string;
  workspacePath?: string | null;
  className?: string;
}

export function LivePreviewPanel({
  previewUrl,
  htmlContent,
  cssContent,
  jsContent,
  filePath,
  isLoading = false,
  onRefresh,
  onOpenExternal,
  onDiagnosticsChange,
  onSendDiagnosticToChat,
  browserRegressionEnabled = false,
  onToggleBrowserRegression,
  threadId,
  workspacePath,
  className,
}: LivePreviewPanelProps) {
  const { t } = useI18n();
  const [device, setDevice] = useState<PreviewDevice>("desktop");
  const [showCode, setShowCode] = useState(false);
  const [diagnostics, setDiagnostics] = useState<PreviewDiagnostic[]>([]);
  const [reloadNonce, setReloadNonce] = useState(0);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const deviceWidths: Record<PreviewDevice, string> = {
    desktop: "100%",
    tablet: "768px",
    mobile: "375px",
  };

  const previewContent = injectPreviewDiagnostics(`
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
          ${cssContent || ""}
          /* Reset for preview */
          body { margin: 0; padding: 16px; font-family: system-ui, -apple-system, sans-serif; }
        </style>
      </head>
      <body>
        ${htmlContent || ""}
        <script>${jsContent || ""}</script>
      </body>
    </html>
  `);

  const shouldUseInstrumentedHtml =
    Boolean(htmlContent) && (!previewUrl || previewUrl.startsWith("blob:"));
  const iframeSrcDoc = shouldUseInstrumentedHtml
    ? previewUrl?.startsWith("blob:")
      ? injectPreviewDiagnostics(htmlContent || "")
      : previewContent
    : undefined;

  useEffect(() => {
    // When a previewUrl is supplied, we let the browser load the iframe via
    // `src` (set declaratively in JSX) and skip the contentDocument.write
    // fallback — writing into it would blank out the navigated page.
    if (previewUrl || iframeSrcDoc) return;
    if (iframeRef.current && previewContent) {
      const doc = iframeRef.current.contentDocument;
      if (doc) {
        doc.open();
        doc.write(previewContent);
        doc.close();
      }
    }
  }, [previewContent, previewUrl, iframeSrcDoc]);

  const resetDiagnostics = useCallback(() => {
    setDiagnostics([]);
    onDiagnosticsChange?.([]);
  }, [onDiagnosticsChange]);

  const addDiagnostic = useCallback(
    (diagnostic: Omit<PreviewDiagnostic, "id" | "timestamp">) => {
      setDiagnostics((prev) => {
        const nextDiagnostic: PreviewDiagnostic = {
          ...diagnostic,
          id: `${Date.now()}:${prev.length}:${diagnostic.source}`,
          timestamp: Date.now(),
        };
        const next = [...prev, nextDiagnostic].slice(-20);
        onDiagnosticsChange?.(next);
        return next;
      });
    },
    [onDiagnosticsChange],
  );

  useEffect(() => {
    resetDiagnostics();
  }, [
    previewUrl,
    htmlContent,
    cssContent,
    jsContent,
    reloadNonce,
    resetDiagnostics,
  ]);

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as {
        type?: string;
        level?: PreviewDiagnostic["level"];
        source?: PreviewDiagnostic["source"];
        message?: string;
        stack?: string;
      };
      if (!data || data.type !== "echo-preview-diagnostic") return;
      addDiagnostic({
        level: data.level ?? "info",
        source: data.source ?? "runtime",
        message: data.message || "Preview diagnostic",
        stack: data.stack,
      });
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [addDiagnostic]);

  const handleIframeLoad = () => {
    window.setTimeout(() => {
      const iframe = iframeRef.current;
      if (!iframe) return;
      let doc: Document | null = null;
      try {
        doc = iframe.contentDocument;
      } catch (e) {
        swallow(e);
        return;
      }
      if (!doc?.body) return;
      const text = doc.body.innerText?.trim() ?? "";
      const hasVisualNode = Boolean(
        doc.body.querySelector(
          "canvas,img,svg,video,iframe,object,embed,input,button,textarea,select",
        ),
      );
      const bodyHeight = doc.body.getBoundingClientRect().height;
      if (!text && !hasVisualNode && bodyHeight < 32) {
        addDiagnostic({
          level: "warning",
          source: "blank",
          message: "Preview rendered with little or no visible content.",
        });
      }
    }, 350);
  };

  const handleRefresh = () => {
    resetDiagnostics();
    if (onRefresh) {
      onRefresh();
      return;
    }
    if (iframeSrcDoc) {
      setReloadNonce((value) => value + 1);
      return;
    }
    iframeRef.current?.contentWindow?.location.reload();
  };

  const isElectronEnv =
    typeof window !== "undefined" && Boolean(window.echo?.isElectron);

  const handleOpenDevTools = useCallback(() => {
    // The preview is rendered in a sandboxed iframe — same-origin scripts
    // can't reach the host's devtools API. In Electron we open native
    // DevTools detached from the iframe; in pure web mode we surface a
    // diagnostic so the user knows to use F12 / Cmd-Opt-I.
    if (!isElectronEnv) {
      addDiagnostic({
        level: "info",
        source: "runtime",
        message: t.codeMode.previewDevToolsUnavailable,
      });
      return;
    }
    // Best-effort: the iframe is hosted in the renderer's webContents,
    // not a separate <webview>, so we just open devtools on the current
    // window. Future work could promote the preview to a <webview> for
    // a per-iframe inspector.
    void window.echo?.window?.openDevTools?.();
  }, [isElectronEnv, t, addDiagnostic]);

  const combinedCode = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
${cssContent || "/* No CSS */"}
  </style>
</head>
<body>
${htmlContent || "<!-- No HTML -->"}
<script>
${jsContent || "// No JavaScript"}
</script>
</body>
</html>`;

  // Unify the two browser surfaces: a non-blob http(s) preview URL is rendered
  // through the one controllable BrowserPreviewPanel (Electron <webview>+CDP, or
  // screenshot/iframe fallback on the web) instead of a bare, uncontrollable
  // <iframe>. Inline srcDoc/html previews stay here (the diagnostics bridge has
  // no equivalent on a remote page). Requires a threadId to bind the session.
  if (
    threadId &&
    previewUrl &&
    !previewUrl.startsWith("blob:") &&
    /^https?:\/\//i.test(previewUrl)
  ) {
    return (
      <BrowserPreviewPanel
        threadId={threadId}
        workspacePath={workspacePath}
        initialUrl={previewUrl}
        className={className}
      />
    );
  }

  return (
    <div className={cn("flex flex-col h-full", className)}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-default">
        <div className="flex items-center gap-2">
          <MonitorIcon className="size-4 text-primary" />
          <span className="text-sm font-medium">{t.livePreview.title}</span>
        </div>
        <div className="flex items-center gap-1">
          {/* Device switcher */}
          <div className="flex items-center rounded-lg bg-muted/50 p-0.5 mr-2">
            <button
              onClick={() => setDevice("desktop")}
              className={cn(
                "p-1.5 rounded-md transition-colors",
                device === "desktop"
                  ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                  : "text-muted-foreground hover:text-foreground",
              )}
              title={t.livePreview.desktop}
            >
              <MonitorIcon className="size-3.5" />
            </button>
            <button
              onClick={() => setDevice("tablet")}
              className={cn(
                "p-1.5 rounded-md transition-colors",
                device === "tablet"
                  ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                  : "text-muted-foreground hover:text-foreground",
              )}
              title={t.livePreview.tablet}
            >
              <TabletIcon className="size-3.5" />
            </button>
            <button
              onClick={() => setDevice("mobile")}
              className={cn(
                "p-1.5 rounded-md transition-colors",
                device === "mobile"
                  ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                  : "text-muted-foreground hover:text-foreground",
              )}
              title={t.livePreview.mobile}
            >
              <SmartphoneIcon className="size-3.5" />
            </button>
          </div>

          {/* Actions */}
          {onToggleBrowserRegression && (
            <button
              type="button"
              aria-pressed={browserRegressionEnabled}
              onClick={onToggleBrowserRegression}
              className={cn(
                "relative p-1.5 rounded-md transition-all",
                browserRegressionEnabled
                  ? "bg-success/10 text-success hover:bg-success/15 dark:text-success"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted",
              )}
              title={
                browserRegressionEnabled
                  ? "UI regression is on. After code changes, verification runs the preview with a visible cursor."
                  : "Turn on UI regression for this preview."
              }
            >
              <SquareMousePointerIcon className="size-3.5" />
              <span
                className={cn(
                  "absolute right-1 top-1 size-1.5 rounded-full",
                  browserRegressionEnabled
                    ? "bg-success"
                    : "bg-muted-foreground/35",
                )}
              />
            </button>
          )}
          <button
            type="button"
            onClick={() => setShowCode(!showCode)}
            className={cn(
              "p-1.5 rounded-md transition-all",
              showCode
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:text-foreground hover:bg-muted",
            )}
            title={showCode ? t.livePreview.hideCode : t.livePreview.showCode}
            aria-label={showCode ? t.livePreview.hideCode : t.livePreview.showCode}
          >
            {showCode ? (
              <EyeIcon className="size-3.5" />
            ) : (
              <CodeIcon className="size-3.5" />
            )}
          </button>
          <button
            type="button"
            onClick={handleRefresh}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            title={t.livePreview.refresh}
            aria-label={t.livePreview.refresh}
          >
            <RotateCcwIcon className="size-3.5" />
          </button>
          <button
            type="button"
            onClick={handleOpenDevTools}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            title={
              isElectronEnv
                ? t.codeMode.previewDevTools
                : t.codeMode.previewDevToolsUnavailable
            }
            aria-label={
              isElectronEnv
                ? t.codeMode.previewDevTools
                : t.codeMode.previewDevToolsUnavailable
            }
          >
            <CodeXmlIcon className="size-3.5" />
          </button>
          {onOpenExternal && (
            <button
              type="button"
              onClick={onOpenExternal}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              title={t.livePreview.openExternal}
              aria-label={t.livePreview.openExternal}
            >
              <ExternalLinkIcon className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden relative">
        {isLoading ? (
          <div className="flex h-full min-h-0 flex-col items-center justify-center text-muted-foreground">
            <Loader2Icon className="size-8 animate-spin mb-2" />
            <span className="text-sm">{t.livePreview.loading}</span>
          </div>
        ) : showCode ? (
          <Suspense
            fallback={
              <div className="flex h-full min-h-0 items-center justify-center text-sm text-muted-foreground">
                {t.livePreview.loading}
              </div>
            }
          >
            <CodeEditor
              value={combinedCode}
              readonly
              filePath={filePath || "preview.html"}
              className="h-full"
            />
          </Suspense>
        ) : (
          <div className="flex h-full min-h-0 flex-col overflow-hidden bg-muted/30 p-2">
            <div className="flex h-full min-h-0 justify-center overflow-auto">
              <div
                className={cn(
                  "flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-border-default bg-background shadow-[var(--shadow-xs)] transition-all duration-slow",
                  device === "mobile" && "max-w-[375px]",
                  device === "tablet" && "max-w-[768px]",
                )}
                style={{ width: deviceWidths[device] }}
              >
                {shouldUseInstrumentedHtml ? (
                  <iframe
                    key={`srcdoc-${reloadNonce}-${previewUrl ?? "inline"}`}
                    ref={iframeRef}
                    srcDoc={iframeSrcDoc}
                    onLoad={handleIframeLoad}
                    className="h-full min-h-0 w-full flex-1"
                    sandbox="allow-scripts"
                    title={t.livePreview.title}
                  />
                ) : previewUrl ? (
                  <iframe
                    key={previewUrl}
                    ref={iframeRef}
                    src={previewUrl}
                    onLoad={handleIframeLoad}
                    className="h-full min-h-0 w-full flex-1"
                    sandbox="allow-scripts"
                    title={t.livePreview.title}
                  />
                ) : htmlContent ? (
                  <iframe
                    ref={iframeRef}
                    onLoad={handleIframeLoad}
                    className="h-full min-h-0 w-full flex-1"
                    sandbox="allow-scripts"
                    title={t.livePreview.title}
                  />
                ) : (
                  <div className="flex h-full min-h-[400px] flex-col items-center justify-center text-muted-foreground/50">
                    <MonitorIcon className="size-12 mb-3 opacity-30" />
                    <span className="text-sm">{t.livePreview.empty}</span>
                    <span className="text-xs mt-1 opacity-60">
                      {t.livePreview.emptyHint}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {!showCode && (
        <PreviewConsole
          diagnostics={diagnostics}
          onClear={resetDiagnostics}
          onSendToChat={onSendDiagnosticToChat}
        />
      )}
    </div>
  );
}

function injectPreviewDiagnostics(html: string): string {
  const bridge = `<script>
(() => {
  if (window.__echoPreviewBridgeInstalled) return;
  window.__echoPreviewBridgeInstalled = true;
  const __swallow = (e) => { try { /* noop */ } catch (_) {} };
  const __safeStr = (v, seen) => {
    if (v === null) return "null";
    if (v === undefined) return "undefined";
    try {
      if (v instanceof Error) return v.stack || v.message || String(v);
    } catch (_) {}
    try {
      const t = typeof v;
      if (t === "string") {
        const trimmed = v.trim();
        return trimmed || "(空字符串)";
      }
      if (t === "number") {
        if (Number.isNaN(v)) return "NaN";
        if (!Number.isFinite(v)) return v > 0 ? "Infinity" : "-Infinity";
        return String(v);
      }
      if (t === "bigint") return v.toString() + "n";
      if (t === "boolean") return v ? "true" : "false";
      if (t === "symbol") return v.toString();
      if (t === "function") return "function " + (v.name || "anonymous");
      if (v instanceof Element) {
        const tag = v.tagName?.toLowerCase() || "element";
        const id = v.id ? "#" + v.id : "";
        const cls = v.className && typeof v.className === "string" ? "." + v.className.trim().split(/\s+/).join(".") : "";
        return "<" + tag + id + cls + ">";
      }
      const nextSeen = seen || new WeakSet();
      if (typeof v === "object") {
        if (nextSeen.has(v)) return "[Circular]";
        nextSeen.add(v);
      }
      if (Array.isArray(v)) {
        try {
          const items = v.map((item) => __safeStr(item, nextSeen));
          return "[" + items.join(", ") + "]";
        } catch (_) {
          return "[Array(" + v.length + ")]";
        }
      }
      try {
        const s = JSON.stringify(v, (key, val) => {
          if (val === undefined) return "[undefined]";
          if (typeof val === "function") return "[Function]";
          if (typeof val === "symbol") return val.toString();
          if (val instanceof Element) return __safeStr(val, nextSeen);
          if (typeof val === "object" && val !== null) {
            if (nextSeen.has(val)) return "[Circular]";
            nextSeen.add(val);
          }
          return val;
        });
        if (s !== undefined && s !== "{}") return s;
        const str = String(v);
        if (str && str !== "[object Object]") return str;
        return "{}";
      } catch (e) {
        __swallow(e);
        try { return String(v); } catch (_) { return "?"; }
      }
    } catch (e) {
      __swallow(e);
      try { return String(v); } catch (_) { return "?"; }
    }
  };
  const send = (level, source, message, stack) => {
    try {
      let m = message;
      if (typeof m !== "string") m = __safeStr(m);
      if (!m || !m.trim()) m = "(控制台输出)";
      window.parent.postMessage({
        type: "echo-preview-diagnostic",
        level,
        source,
        message: m,
        stack: stack ? __safeStr(stack) : undefined,
      }, "*");
    } catch (e) { __swallow(e); }
  };
  const __wrapConsole = (level, sendLevel) => {
    const orig = console[level];
    if (typeof orig !== "function") return;
    console[level] = (...args) => {
      try {
        if (args.length === 0) {
          send(sendLevel, "console", "(控制台输出，无参数)");
        } else {
          const parts = new Array(args.length);
          let hasContent = false;
          for (let i = 0; i < args.length; i++) {
            const s = __safeStr(args[i]);
            parts[i] = s;
            if (s && s.trim()) hasContent = true;
          }
          const msg = hasContent ? parts.join(" ") : "(控制台输出)";
          send(sendLevel, "console", msg);
        }
      } catch (e) { __swallow(e); }
      orig.apply(console, args);
    };
  };
  __wrapConsole("error",   "error");
  __wrapConsole("warn",    "warning");
  __wrapConsole("info",    "info");
  __wrapConsole("log",     "info");
  __wrapConsole("debug",   "info");
  window.addEventListener("error", (event) => {
    const msg = event.message || event.error?.message || "Runtime error";
    send("error", "runtime", msg, event.error?.stack);
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    const msg = (reason === undefined || reason === null)
      ? "Unhandled promise rejection"
      : __safeStr(reason);
    send("error", "runtime", msg, reason?.stack);
  });
  window.addEventListener("load", () => {
    window.setTimeout(() => {
      const body = document.body;
      if (!body) return;
      const text = (body.innerText || "").trim();
      const hasVisualNode = Boolean(body.querySelector("canvas,img,svg,video,iframe,object,embed,input,button,textarea,select"));
      const bodyHeight = body.getBoundingClientRect().height;
      if (!text && !hasVisualNode && bodyHeight < 32) {
        send("warning", "blank", "Preview rendered with little or no visible content.");
      }
    }, 250);
  });
})();
</script>`;
  if (/<head(\s[^>]*)?>/i.test(html)) {
    return html.replace(/<head(\s[^>]*)?>/i, (match) => `${match}\n${bridge}`);
  }
  if (/<html(\s[^>]*)?>/i.test(html)) {
    return html.replace(
      /<html(\s[^>]*)?>/i,
      (match) => `${match}\n<head>${bridge}</head>`,
    );
  }
  return `${bridge}\n${html}`;
}
