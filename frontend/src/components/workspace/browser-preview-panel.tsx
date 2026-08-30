import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  ChevronDownIcon,
  GlobeIcon,
  ImageIcon,
  Loader2Icon,
  Maximize2Icon,
  MoreHorizontalIcon,
  MousePointerClickIcon,
  MonitorIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
  ServerIcon,
  SmartphoneIcon,
  SquareIcon,
  TabletIcon,
  TypeIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { toast } from "sonner";

import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import {
  createEchoBrowserSessionIdentity,
  type EchoBrowserSessionIdentity,
} from "@/core/browser/api";
import {
  detectLocalServices,
  type DetectedLocalService,
} from "@/core/browser/local-services";
import { useI18n } from "@/core/i18n/hooks";
import { BROWSER_WORKSPACE_ROUTE } from "@/core/workspace/sidebar-routing";
import { cn } from "@/lib/utils";
import {
  AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
  AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME,
  AUTOMATION_CAPSULE_SURFACE_CLASS_NAME,
} from "@/components/ui/automation-capsule";
import {
  BROWSER_OPEN_URL_REQUEST_KEY,
  type BrowserOpenUrlRequest,
  type BrowserTab,
} from "@/components/browser/browser-store";
import {
  WebviewTab,
  type WebviewTabHandle,
} from "@/components/browser/webview-tab";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BrowserSession {
  session_id: string;
  project_id?: string;
  profile_id?: string;
  profile_dir?: string;
  automation_mode?: string;
  uses_system_mouse?: boolean;
  desktop_lease_required?: boolean;
  is_launched: boolean;
  created_at: number;
  last_activity: number;
  action_count: number;
  headless?: boolean;
  viewport_width?: number;
  viewport_height?: number;
  mode?: string;
  runtime?: string;
  has_page?: boolean;
  healthy?: boolean;
  current_url?: string;
  current_title?: string;
}

interface ActionLogEntry {
  action: string;
  detail: string;
  status?: string;
  error?: string;
  metadata?: Record<string, unknown>;
  timestamp: number;
}

interface PageInfo {
  url: string;
  title: string;
}

async function dataUrlToFile(
  dataUrl: string,
  filename: string,
  fallbackMime = "image/png",
): Promise<File> {
  const response = await fetch(dataUrl);
  const blob = await response.blob();
  return new File([blob], filename, {
    type: blob.type || fallbackMime,
  });
}

async function renderAnnotatedScreenshot(
  screenshot: string,
  points: Array<{ x: number; y: number }>,
  note: string,
): Promise<string> {
  if (typeof document === "undefined" || !screenshot) return screenshot;
  const image = new Image();
  image.src = `data:image/png;base64,${screenshot}`;
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error("Failed to render annotation"));
  });
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const context = canvas.getContext("2d");
  if (!context) return `data:image/png;base64,${screenshot}`;
  context.drawImage(image, 0, 0);
  const radius = Math.max(18, Math.round(canvas.width / 80));
  context.font = `600 ${Math.max(16, radius * 0.85)}px sans-serif`;
  points.forEach((point, index) => {
    context.beginPath();
    context.arc(point.x, point.y, radius, 0, Math.PI * 2);
    context.fillStyle = "rgba(124, 58, 237, 0.18)";
    context.fill();
    context.lineWidth = Math.max(3, Math.round(radius / 6));
    context.strokeStyle = "#7c3aed";
    context.stroke();
    context.fillStyle = "#5b21b6";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(String(index + 1), point.x, point.y);
  });
  if (note.trim()) {
    const padding = Math.max(12, Math.round(canvas.width / 120));
    const maxWidth = canvas.width - padding * 2;
    const metrics = context.measureText(note.trim());
    const boxWidth = Math.min(
      maxWidth,
      Math.max(180, metrics.width + padding * 2),
    );
    const boxHeight = Math.max(34, radius * 1.8);
    context.fillStyle = "rgba(255, 255, 255, 0.94)";
    context.strokeStyle = "rgba(124, 58, 237, 0.45)";
    context.lineWidth = 2;
    context.beginPath();
    context.roundRect(padding, padding, boxWidth, boxHeight, 8);
    context.fill();
    context.stroke();
    context.fillStyle = "#3b0764";
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillText(
      note.trim().slice(0, 160),
      padding * 1.5,
      padding + boxHeight / 2,
    );
  }
  return canvas.toDataURL("image/png");
}

interface BrowserSessionHealth {
  schema: "echo.browser_session_health.v1";
  exists: boolean;
  healthy: boolean;
  score: number;
  issues: string[];
  session: BrowserSession;
  recent_actions: ActionLogEntry[];
  replay_ready: boolean;
  stale_seconds?: number;
}

const DEVICE_PREVIEW_PRESETS = {
  desktop: {
    label: "Desktop",
    width: 1440,
    height: 900,
    Icon: MonitorIcon,
  },
  laptop: {
    label: "Laptop",
    width: 1024,
    height: 768,
    Icon: MonitorIcon,
  },
  fourK: {
    label: "4K",
    width: 3840,
    height: 2160,
    Icon: MonitorIcon,
  },
  ipadAir: {
    label: "iPad Air",
    width: 768,
    height: 1024,
    Icon: TabletIcon,
  },
  ipadMini: {
    label: "iPad mini",
    width: 744,
    height: 1133,
    Icon: TabletIcon,
  },
  surfacePro7: {
    label: "Surface Pro",
    width: 912,
    height: 1368,
    Icon: TabletIcon,
  },
  surfaceDuo: {
    label: "Surface Duo",
    width: 540,
    height: 720,
    Icon: SmartphoneIcon,
  },
  iphone15ProMax: {
    label: "iPhone 15 PM",
    width: 430,
    height: 932,
    Icon: SmartphoneIcon,
  },
  pixel8: {
    label: "Pixel 8",
    width: 412,
    height: 915,
    Icon: SmartphoneIcon,
  },
  iphone15Pro: {
    label: "iPhone 15 Pro",
    width: 390,
    height: 844,
    Icon: SmartphoneIcon,
  },
  galaxyS24Ultra: {
    label: "Galaxy S24",
    width: 412,
    height: 915,
    Icon: SmartphoneIcon,
  },
  iphoneSE: {
    label: "iPhone SE",
    width: 375,
    height: 667,
    Icon: SmartphoneIcon,
  },
} as const;

type DevicePreviewPreset = keyof typeof DEVICE_PREVIEW_PRESETS;
type PreviewSurfaceMode = "screenshot" | "live";

interface BrowserSemanticSnapshot {
  role?: string;
  name?: string;
  url?: string;
  text?: string;
  truncated?: boolean;
  nodes?: Array<{
    tag: string;
    role: string;
    name: string;
    text: string;
    selector: string;
  }>;
}

interface ScreenshotPointerPoint {
  x: number;
  y: number;
  mode: "click" | "double";
  timestamp: number;
}

function presetForViewport(
  width?: number,
  height?: number,
): DevicePreviewPreset | null {
  if (!width || !height) return null;
  const match = (
    Object.keys(DEVICE_PREVIEW_PRESETS) as DevicePreviewPreset[]
  ).find((preset) => {
    const target = DEVICE_PREVIEW_PRESETS[preset];
    return target.width === width && target.height === height;
  });
  return match ?? null;
}

function normalizePreviewUrl(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) return "";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (
    /^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|::1)(:\d+)?(\/|$)/i.test(
      trimmed,
    )
  ) {
    return `http://${trimmed}`;
  }
  return "";
}

function browserTabDeviceForPreset(
  preset: DevicePreviewPreset,
): BrowserTab["device"] {
  if (preset === "desktop" || preset === "laptop" || preset === "fourK") {
    return "desktop";
  }
  return DEVICE_PREVIEW_PRESETS[preset].width <= 540 ? "mobile" : "tablet";
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const browserApi = {
  async sessionStatus(
    sessionId: string,
  ): Promise<{ exists: boolean; session: BrowserSession }> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/browser/session/status?session_id=${encodeURIComponent(sessionId)}`,
      { headers: authHeaders() },
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(data.detail || "Failed to get browser session status");
    }
    return res.json();
  },

  async ensure(
    identity: EchoBrowserSessionIdentity,
  ): Promise<{ status: string; session: BrowserSession }> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/browser/session/ensure`,
      {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({
          session_id: identity.sessionId,
          project_id: identity.projectId,
          profile_id: identity.profileId,
          headless: true,
        }),
      },
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(data.detail || "Failed to start browser session");
    }
    return res.json();
  },

  async setViewport(
    identity: EchoBrowserSessionIdentity,
    width: number,
    height: number,
  ): Promise<{ ok: boolean; session: BrowserSession }> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/browser/session/viewport`,
      {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({
          session_id: identity.sessionId,
          project_id: identity.projectId,
          profile_id: identity.profileId,
          width,
          height,
          headless: true,
        }),
      },
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(data.detail || "Failed to update browser viewport");
    }
    return res.json();
  },

  async reset(identity: EchoBrowserSessionIdentity): Promise<void> {
    await fetch(`${getBackendBaseURL()}/api/browser/session/reset`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        session_id: identity.sessionId,
        project_id: identity.projectId,
        profile_id: identity.profileId,
      }),
    });
  },

  async navigate(
    identity: EchoBrowserSessionIdentity,
    url: string,
  ): Promise<PageInfo> {
    const res = await fetch(`${getBackendBaseURL()}/api/browser/navigate`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        session_id: identity.sessionId,
        project_id: identity.projectId,
        profile_id: identity.profileId,
        url,
      }),
    });
    if (!res.ok) throw new Error("Navigation failed");
    return res.json();
  },

  async action(
    identity: EchoBrowserSessionIdentity,
    action: string,
    params: Record<string, unknown> = {},
  ): Promise<Record<string, unknown>> {
    const res = await fetch(`${getBackendBaseURL()}/api/browser/action`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        session_id: identity.sessionId,
        project_id: identity.projectId,
        profile_id: identity.profileId,
        action,
        ...params,
      }),
    });
    if (!res.ok) throw new Error("Action failed");
    return res.json();
  },

  async semanticSnapshot(
    identity: EchoBrowserSessionIdentity,
  ): Promise<BrowserSemanticSnapshot> {
    const res = await fetch(`${getBackendBaseURL()}/api/browser/action`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        session_id: identity.sessionId,
        project_id: identity.projectId,
        profile_id: identity.profileId,
        action: "aria",
      }),
    });
    if (!res.ok) throw new Error("Semantic snapshot failed");
    const data = await res.json();
    return (data.nodes ?? {}) as BrowserSemanticSnapshot;
  },

  async queueReplayCase(sessionId: string): Promise<{ ok: boolean }> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/browser/session/replay-case/queue`,
      {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({
          session_id: sessionId,
          reason: "agent preview handoff",
          priority: "normal",
        }),
      },
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(data.detail || "Failed to queue replay case");
    }
    return res.json();
  },

  async screenshotBase64(
    sessionId: string,
  ): Promise<{ base64: string; width: number; height: number }> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/browser/screenshot/base64?session_id=${encodeURIComponent(sessionId)}`,
      { headers: authHeaders() },
    );
    if (!res.ok) throw new Error("Screenshot failed");
    return res.json();
  },

  async pageInfo(sessionId: string): Promise<PageInfo> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/browser/page-info?session_id=${encodeURIComponent(sessionId)}`,
      { headers: authHeaders() },
    );
    if (!res.ok) throw new Error("Failed to get page info");
    return res.json();
  },

  async actionLog(
    sessionId: string,
    limit = 50,
  ): Promise<{ actions: ActionLogEntry[] }> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/browser/action-log?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`,
      { headers: authHeaders() },
    );
    if (!res.ok) return { actions: [] };
    return res.json();
  },

  async sessionHealth(sessionId: string): Promise<BrowserSessionHealth | null> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/browser/session/health?session_id=${encodeURIComponent(sessionId)}`,
      { headers: authHeaders() },
    );
    if (!res.ok) return null;
    return res.json();
  },
};

// ---------------------------------------------------------------------------
// Local port detection
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Action icon helper
// ---------------------------------------------------------------------------

function ActionIcon({ action }: { action: string }) {
  switch (action) {
    case "navigate":
      return <GlobeIcon className="size-3 text-info" />;
    case "click":
    case "click_at":
    case "double_click_at":
      return <MousePointerClickIcon className="size-3 text-chart-7" />;
    case "type":
      return <TypeIcon className="size-3 text-success" />;
    case "screenshot":
      return <ImageIcon className="size-3 text-chart-1" />;
    case "viewport":
      return <MonitorIcon className="size-3 text-info" />;
    case "scroll":
      return <ArrowLeftIcon className="size-3 text-muted-foreground" />;
    default:
      return <PlayIcon className="size-3 text-muted-foreground" />;
  }
}

function actionStatusLabel(
  entry: ActionLogEntry,
  t: { actionPending: string; actionSuccess: string; actionFailed: string },
): string {
  if (!entry.status) return t.actionPending;
  if (entry.status === "ok") return t.actionSuccess;
  if (entry.status === "error") return t.actionFailed;
  return entry.status;
}

function actionStatusClass(entry: ActionLogEntry): string {
  if (entry.status === "ok") return "bg-success/10 text-success";
  if (entry.status && entry.status !== "ok") {
    return "bg-destructive/10 text-destructive";
  }
  return "bg-muted text-muted-foreground";
}

function actionCoordinateLabel(entry: ActionLogEntry): string | null {
  const point = actionCoordinatePoint(entry);
  return point ? `${point.x}, ${point.y}` : null;
}

function actionCoordinatePoint(
  entry: ActionLogEntry,
): Pick<ScreenshotPointerPoint, "x" | "y"> | null {
  const x = entry.metadata?.x;
  const y = entry.metadata?.y;
  if (typeof x === "number" && typeof y === "number") {
    return { x: Math.round(x), y: Math.round(y) };
  }
  const match = entry.detail.match(/x[=:]\s*(\d+).*y[=:]\s*(\d+)/i);
  return match ? { x: Number(match[1]), y: Number(match[2]) } : null;
}

function actionDetailLabel(
  entry: ActionLogEntry,
  t: { coordinateLabel: (coord: string) => string; noDetail: string },
): string {
  if (entry.error) return entry.error;
  if (entry.detail) return entry.detail;
  const coord = actionCoordinateLabel(entry);
  return coord ? t.coordinateLabel(coord) : t.noDetail;
}

function actionEntryKey(entry: ActionLogEntry, absoluteIndex: number): string {
  return `${entry.timestamp}-${entry.action}-${absoluteIndex}`;
}

interface BrowserPreviewToolbarProps {
  urlInput: string;
  onUrlInputChange: (value: string) => void;
  onNavigate: () => void;
  onBack: () => void;
  onForward: () => void;
  onReload: () => void;
  onOpenFullBrowser: () => void;
  onEndSession: () => void;
  canLivePreview: boolean;
  surfaceMode: PreviewSurfaceMode;
  onSurfaceModeChange: (mode: PreviewSurfaceMode) => void;
  devicePreview: DevicePreviewPreset;
  viewportChanging: boolean;
  onDevicePreviewChange: (preset: DevicePreviewPreset) => void;
  onAttachScreenshot: () => void;
  autoRefresh: boolean;
  onAutoRefreshChange: (enabled: boolean) => void;
  sessionHealthy: boolean;
  runtimeLabel: string;
}

/**
 * The preview's persistent navigation row. Contextual screenshot actions stay
 * on the canvas so this toolbar keeps one stable browser-like hierarchy.
 */
export function BrowserPreviewToolbar({
  urlInput,
  onUrlInputChange,
  onNavigate,
  onBack,
  onForward,
  onReload,
  onOpenFullBrowser,
  onEndSession,
  canLivePreview,
  surfaceMode,
  onSurfaceModeChange,
  devicePreview,
  viewportChanging,
  onDevicePreviewChange,
  onAttachScreenshot,
  autoRefresh,
  onAutoRefreshChange,
  sessionHealthy,
  runtimeLabel,
}: BrowserPreviewToolbarProps) {
  const { t } = useI18n();
  const bp = t.browserPreviewPanel;
  const switchToLive = surfaceMode !== "live";

  return (
    <div
      role="toolbar"
      aria-label={t.browser.browserAutomation}
      className="@container/browser-preview-toolbar flex shrink-0 items-center gap-1.5 border-b border-border-default bg-background px-2 py-1.5"
    >
      <div className="flex shrink-0 items-center rounded-lg border border-border-subtle bg-muted/35 p-0.5">
        <button
          type="button"
          onClick={onBack}
          className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
          title={t.browser.back}
          aria-label={t.browser.back}
        >
          <ArrowLeftIcon className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={onForward}
          className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
          title={t.browser.forward}
          aria-label={t.browser.forward}
        >
          <ArrowRightIcon className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={onReload}
          className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
          title={t.browser.reload}
          aria-label={t.browser.reload}
        >
          <RefreshCwIcon className="size-3.5" />
        </button>
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          onNavigate();
        }}
        className="flex min-w-0 flex-1 items-center"
      >
        <div className="relative flex h-8 min-w-0 flex-1 items-center rounded-lg border border-border-default bg-muted/35 transition-colors focus-within:border-ring focus-within:bg-background">
          <GlobeIcon className="absolute left-2.5 size-3.5 text-muted-foreground" />
          <input
            type="text"
            value={urlInput}
            onChange={(event) => onUrlInputChange(event.target.value)}
            placeholder={t.browser.urlPlaceholder}
            aria-label={t.browser.urlPlaceholder}
            className="h-full w-full bg-transparent pr-2 pl-8 text-xs outline-none"
          />
        </div>
      </form>

      <button
        type="button"
        onClick={onOpenFullBrowser}
        className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-border-default bg-background px-2 text-xs font-medium text-foreground transition-colors hover:bg-muted/55"
        title={bp.continueInFullBrowser}
        aria-label={bp.continueInFullBrowser}
      >
        <Maximize2Icon className="size-3.5" />
        <span className="hidden @min-[520px]/browser-preview-toolbar:inline">
          {bp.takeoverButton}
        </span>
      </button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted/55 hover:text-foreground"
            title={t.common.more}
            aria-label={t.common.more}
          >
            <MoreHorizontalIcon className="size-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuItem
            disabled={!canLivePreview}
            onSelect={() =>
              onSurfaceModeChange(switchToLive ? "live" : "screenshot")
            }
          >
            {switchToLive ? (
              <MonitorIcon className="size-3.5" />
            ) : (
              <ImageIcon className="size-3.5" />
            )}
            <span className="grid min-w-0 gap-0.5">
              <span className="font-medium">
                {switchToLive ? bp.switchToLivePreview : bp.switchToScreenshot}
              </span>
              <span className="whitespace-normal text-xs leading-snug text-muted-foreground">
                {switchToLive
                  ? bp.switchToLivePreviewDescription
                  : bp.switchToScreenshotDescription}
              </span>
            </span>
          </DropdownMenuItem>
          <div className="px-2 py-1.5">
            <label className="mb-1 block text-xs text-muted-foreground">
              {bp.selectDevicePreset}
            </label>
            <select
              value={devicePreview}
              onChange={(event) =>
                onDevicePreviewChange(event.target.value as DevicePreviewPreset)
              }
              disabled={viewportChanging}
              className="h-8 w-full rounded-md border border-border-default bg-background px-2 text-xs font-medium text-foreground outline-none"
              aria-label={bp.selectDevicePreset}
            >
              {(
                Object.keys(DEVICE_PREVIEW_PRESETS) as DevicePreviewPreset[]
              ).map((preset) => {
                const device = DEVICE_PREVIEW_PRESETS[preset];
                return (
                  <option key={preset} value={preset}>
                    {preset === "desktop" ? bp.desktopLabel : device.label}
                  </option>
                );
              })}
            </select>
          </div>
          <DropdownMenuItem onSelect={onAttachScreenshot}>
            <ImageIcon className="size-3.5" />
            {bp.attachScreenshotToComposer}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => onAutoRefreshChange(!autoRefresh)}>
            {autoRefresh ? (
              <SquareIcon className="size-3.5" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}
            {autoRefresh
              ? t.browser.stopAutoRefresh
              : t.browser.startAutoRefresh}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <div
            role="status"
            aria-label={
              sessionHealthy ? bp.sessionHealthyLabel : bp.sessionAttentionLabel
            }
            className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground"
          >
            <span
              className={cn(
                "size-1.5 shrink-0 rounded-full",
                sessionHealthy ? "bg-success" : "bg-destructive",
              )}
            />
            <span className="min-w-0 flex-1 truncate">
              {sessionHealthy
                ? bp.sessionHealthyLabel
                : bp.sessionAttentionLabel}
            </span>
            <span className="shrink-0 font-mono text-xs">{runtimeLabel}</span>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={onEndSession}>
            <XIcon className="size-3.5" />
            {bp.endSession}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BrowserPreviewPanel
// ---------------------------------------------------------------------------

interface BrowserPreviewPanelProps {
  threadId: string;
  workspacePath?: string | null;
  /** Seed the panel with a URL to load on mount — lets the live-preview panel
   * delegate its URL mode here (and the agent regression preview point at it)
   * so there is one controllable URL-preview surface. Additive: when unset the
   * panel behaves exactly as before. */
  initialUrl?: string;
  className?: string;
}

export function BrowserPreviewPanel({
  threadId,
  workspacePath,
  initialUrl,
  className,
}: BrowserPreviewPanelProps) {
  const { t } = useI18n();
  const bp = t.browserPreviewPanel;
  const [session, setSession] = useState<BrowserSession | null>(null);
  const [pageInfo, setPageInfo] = useState<PageInfo>({ url: "", title: "" });
  const [screenshot, setScreenshot] = useState<string>("");
  const [screenshotSize, setScreenshotSize] = useState({ width: 0, height: 0 });
  const [actionLog, setActionLog] = useState<ActionLogEntry[]>([]);
  const [selectedActionKey, setSelectedActionKey] = useState<string | null>(
    null,
  );
  const [sessionHealth, setSessionHealth] =
    useState<BrowserSessionHealth | null>(null);
  const [actionLogExpanded, setActionLogExpanded] = useState(false);
  const [urlInput, setUrlInput] = useState(initialUrl ?? "");
  const [loading, setLoading] = useState(true);
  const [viewportChanging, setViewportChanging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [detectedServices, setDetectedServices] = useState<
    DetectedLocalService[]
  >([]);
  const [localServicesExpanded, setLocalServicesExpanded] = useState(false);
  const [scanningPorts, setScanningPorts] = useState(false);
  const [semanticSnapshot, setSemanticSnapshot] =
    useState<BrowserSemanticSnapshot | null>(null);
  const [annotationMode, setAnnotationMode] = useState(false);
  const [annotationText, setAnnotationText] = useState("");
  const [annotationPoints, setAnnotationPoints] = useState<
    Array<{ x: number; y: number }>
  >([]);
  const [pointerMode] = useState<"click" | "double">("click");
  const [hoverPoint, setHoverPoint] = useState<ScreenshotPointerPoint | null>(
    null,
  );
  const [clickMarker, setClickMarker] = useState<ScreenshotPointerPoint | null>(
    null,
  );
  const [surfaceMode, setSurfaceMode] =
    useState<PreviewSurfaceMode>("screenshot");
  const [liveFrameLoaded, setLiveFrameLoaded] = useState(false);
  const [devicePreview, setDevicePreview] =
    useState<DevicePreviewPreset>("desktop");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const clickMarkerTimeoutRef = useRef<number | null>(null);

  const sessionIdentity = useMemo(
    () => createEchoBrowserSessionIdentity({ threadId, workspacePath }),
    [threadId, workspacePath],
  );
  const sessionId = sessionIdentity.sessionId;
  const liveWebviewRef = useRef<WebviewTabHandle | null>(null);
  const [liveTab, setLiveTab] = useState<BrowserTab>(() => ({
    id: `agent-live-${Date.now().toString(36)}`,
    url: "about:blank",
    title: bp.livePreviewTitle,
    isLoading: false,
    device: "desktop",
  }));

  const applySessionSnapshot = useCallback((next: BrowserSession | null) => {
    setSession(next);
    if (!next) return;
    const matchedPreset = presetForViewport(
      next.viewport_width,
      next.viewport_height,
    );
    if (matchedPreset) {
      setDevicePreview(matchedPreset);
    }
    const snapshotUrl = next.current_url || "";
    const snapshotTitle = next.current_title || "";
    if (snapshotUrl || snapshotTitle) {
      setPageInfo({ url: snapshotUrl, title: snapshotTitle });
      setUrlInput(snapshotUrl);
    }
  }, []);

  const refreshSessionStatus = useCallback(async () => {
    const data = await browserApi.sessionStatus(sessionId);
    applySessionSnapshot(data.exists ? data.session : null);
    return data;
  }, [applySessionSnapshot, sessionId]);

  const refreshBrowserArtifacts = useCallback(async () => {
    const [shotResult, logResult, healthResult] = await Promise.allSettled([
      browserApi.screenshotBase64(sessionId),
      browserApi.actionLog(sessionId),
      browserApi.sessionHealth(sessionId),
    ]);

    if (shotResult.status === "fulfilled") {
      setScreenshot(shotResult.value.base64);
      setScreenshotSize({
        width: shotResult.value.width,
        height: shotResult.value.height,
      });
    } else {
      swallow(shotResult.reason);
    }

    if (logResult.status === "fulfilled") {
      setActionLog(logResult.value.actions);
    } else {
      swallow(logResult.reason);
    }

    if (healthResult.status === "fulfilled") {
      setSessionHealth(healthResult.value);
    } else {
      swallow(healthResult.reason);
    }
  }, [sessionId]);

  // Start or reconnect the browser as soon as the panel is opened.
  useEffect(() => {
    let cancelled = false;

    async function ensureSession() {
      setLoading(true);
      setError(null);
      try {
        const status = await browserApi.sessionStatus(sessionId);
        if (cancelled) return;

        if (status.exists) {
          applySessionSnapshot(status.session);
        } else {
          const data = await browserApi.ensure(sessionIdentity);
          if (cancelled) return;
          applySessionSnapshot(data.session);
        }

        await refreshBrowserArtifacts();
      } catch (err) {
        swallow(err);
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to launch browser",
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void ensureSession();

    return () => {
      cancelled = true;
    };
  }, [
    applySessionSnapshot,
    refreshBrowserArtifacts,
    sessionId,
    sessionIdentity,
  ]);

  // Scan for local services on mount
  useEffect(() => {
    setScanningPorts(true);
    detectLocalServices()
      .then(setDetectedServices)
      .catch(() => setDetectedServices([]))
      .finally(() => setScanningPorts(false));
  }, []);

  const handleRescanPorts = useCallback(() => {
    setScanningPorts(true);
    detectLocalServices()
      .then(setDetectedServices)
      .catch(() => setDetectedServices([]))
      .finally(() => setScanningPorts(false));
  }, []);

  const handleQuickNavigate = useCallback(
    async (url: string) => {
      setUrlInput(url);
      setLoading(true);
      setError(null);
      try {
        if (!session) {
          const data = await browserApi.ensure(sessionIdentity);
          applySessionSnapshot(data.session);
        }
        const info = await browserApi.navigate(sessionIdentity, url);
        setPageInfo(info);
        setUrlInput(info.url);
        const shotData = await browserApi.screenshotBase64(sessionId);
        setScreenshot(shotData.base64);
        setScreenshotSize({ width: shotData.width, height: shotData.height });
        const [logData, healthData] = await Promise.all([
          browserApi.actionLog(sessionId),
          browserApi.sessionHealth(sessionId),
        ]);
        setActionLog(logData.actions);
        setSessionHealth(healthData);
        await refreshSessionStatus();
      } catch (err) {
        swallow(err);
        setError(err instanceof Error ? err.message : "Navigation failed");
      } finally {
        setLoading(false);
      }
    },
    [
      applySessionSnapshot,
      refreshSessionStatus,
      session,
      sessionId,
      sessionIdentity,
    ],
  );

  // Auto-refresh screenshot
  useEffect(() => {
    if (autoRefresh && session) {
      intervalRef.current = setInterval(async () => {
        try {
          const [shotData, info, logData, healthData] = await Promise.all([
            browserApi.screenshotBase64(sessionId),
            browserApi.pageInfo(sessionId),
            browserApi.actionLog(sessionId),
            browserApi.sessionHealth(sessionId),
          ]);
          setScreenshot(shotData.base64);
          setScreenshotSize({ width: shotData.width, height: shotData.height });
          setPageInfo(info);
          setActionLog(logData.actions);
          setSessionHealth(healthData);
          void refreshSessionStatus().catch((e) => {
            swallow(e);
          });
        } catch (e) {
          swallow(e);
        }
      }, 2000);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [autoRefresh, refreshSessionStatus, session, sessionId]);

  // Scroll action log to bottom on update
  useEffect(() => {
    if (!actionLogExpanded) return;
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [actionLog.length, actionLogExpanded]);

  useEffect(() => {
    setLiveFrameLoaded(false);
  }, [pageInfo.url, urlInput]);

  useEffect(
    () => () => {
      if (clickMarkerTimeoutRef.current) {
        clearTimeout(clickMarkerTimeoutRef.current);
      }
    },
    [],
  );

  const handleLaunch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await browserApi.ensure(sessionIdentity);
      applySessionSnapshot(data.session);
      await refreshBrowserArtifacts();
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : "Failed to launch browser");
    } finally {
      setLoading(false);
    }
  }, [applySessionSnapshot, refreshBrowserArtifacts, sessionIdentity]);

  const handleNavigate = useCallback(async () => {
    if (!urlInput.trim()) return;
    setLoading(true);
    setError(null);
    try {
      // Ensure session exists
      if (!session) {
        const data = await browserApi.ensure(sessionIdentity);
        applySessionSnapshot(data.session);
      }
      let url = urlInput.trim();
      if (!url.startsWith("http://") && !url.startsWith("https://")) {
        url = "https://" + url;
      }
      const info = await browserApi.navigate(sessionIdentity, url);
      setPageInfo(info);
      setUrlInput(info.url);
      // Refresh screenshot
      const shotData = await browserApi.screenshotBase64(sessionId);
      setScreenshot(shotData.base64);
      setScreenshotSize({ width: shotData.width, height: shotData.height });
      // Refresh log
      const [logData, healthData] = await Promise.all([
        browserApi.actionLog(sessionId),
        browserApi.sessionHealth(sessionId),
      ]);
      setActionLog(logData.actions);
      setSessionHealth(healthData);
      await refreshSessionStatus();
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : "Navigation failed");
    } finally {
      setLoading(false);
    }
  }, [
    applySessionSnapshot,
    refreshSessionStatus,
    urlInput,
    session,
    sessionId,
    sessionIdentity,
  ]);

  const handleRetry = useCallback(() => {
    if (urlInput.trim()) {
      void handleNavigate();
    } else {
      void handleLaunch();
    }
  }, [handleLaunch, handleNavigate, urlInput]);

  const handleRefreshScreenshot = useCallback(async () => {
    if (!session) return;
    try {
      const [shotData, info, logData, healthData] = await Promise.all([
        browserApi.screenshotBase64(sessionId),
        browserApi.pageInfo(sessionId),
        browserApi.actionLog(sessionId),
        browserApi.sessionHealth(sessionId),
      ]);
      setScreenshot(shotData.base64);
      setScreenshotSize({ width: shotData.width, height: shotData.height });
      setPageInfo(info);
      setActionLog(logData.actions);
      setSessionHealth(healthData);
      await refreshSessionStatus();
    } catch (e) {
      swallow(e);
    }
  }, [refreshSessionStatus, session, sessionId]);

  const handleAttachScreenshotToComposer = useCallback(async () => {
    try {
      let screenshotDataUrl = screenshot
        ? `data:image/png;base64,${screenshot}`
        : "";
      if (!screenshotDataUrl && session) {
        const shotData = await browserApi.screenshotBase64(sessionId);
        setScreenshot(shotData.base64);
        setScreenshotSize({ width: shotData.width, height: shotData.height });
        screenshotDataUrl = `data:image/png;base64,${shotData.base64}`;
      }
      if (!screenshotDataUrl) {
        toast.error(bp.noReadableText);
        return;
      }
      const host = (() => {
        try {
          return pageInfo.url ? new URL(pageInfo.url).hostname : "browser";
        } catch {
          return "browser";
        }
      })();
      const file = await dataUrlToFile(
        screenshotDataUrl,
        `browser-shot-${host}-${Date.now()}.png`,
      );
      window.dispatchEvent(
        new CustomEvent("echo:inject-composer-images", {
          detail: {
            threadId,
            images: [file],
            sourceLabel: bp.attachScreenshotSource,
          },
        }),
      );
      toast.success(bp.attachScreenshotSuccess);
    } catch (error) {
      swallow(error);
      toast.error(bp.attachScreenshotFailed);
    }
  }, [bp, pageInfo.url, screenshot, session, sessionId, threadId]);

  const handleAnnotateScreenshot = useCallback(
    (event: ReactMouseEvent<HTMLImageElement>) => {
      if (
        !annotationMode ||
        screenshotSize.width <= 0 ||
        screenshotSize.height <= 0
      ) {
        return;
      }
      const rect = event.currentTarget.getBoundingClientRect();
      setAnnotationPoints((points) => [
        ...points,
        {
          x: Math.round(
            ((event.clientX - rect.left) / Math.max(1, rect.width)) *
              screenshotSize.width,
          ),
          y: Math.round(
            ((event.clientY - rect.top) / Math.max(1, rect.height)) *
              screenshotSize.height,
          ),
        },
      ]);
    },
    [annotationMode, screenshotSize.height, screenshotSize.width],
  );

  const handleSendAnnotation = useCallback(async () => {
    if (!screenshot) return;
    let currentSemantic = semanticSnapshot;
    if (session) {
      try {
        currentSemantic = await browserApi.semanticSnapshot(sessionIdentity);
        setSemanticSnapshot(currentSemantic);
      } catch {
        // The screenshot is still useful when the page has no readable DOM.
      }
    }
    const annotatedScreenshot = await renderAnnotatedScreenshot(
      screenshot,
      annotationPoints,
      annotationText,
    );
    const image = await dataUrlToFile(
      annotatedScreenshot,
      `browser-annotation-${Date.now()}.png`,
    );
    const domLines = (currentSemantic?.nodes ?? [])
      .filter((node) => node.name || node.text)
      .slice(0, 40)
      .map(
        (node) =>
          `- <${node.tag}>${node.role ? ` role=${node.role}` : ""}${node.name ? ` name="${node.name}"` : ""}${node.text ? ` text="${node.text}"` : ""}${node.selector ? ` selector="${node.selector}"` : ""}`,
      );
    const points = annotationPoints.length
      ? annotationPoints.map((point) => `(${point.x}, ${point.y})`).join(", ")
      : "未标注坐标";
    const context = [
      "[浏览器页面标注]",
      `页面：${pageInfo.title || "未命名页面"}`,
      `URL：${pageInfo.url || urlInput || "未知"}`,
      `标注坐标：${points}`,
      annotationText.trim() ? `用户备注：${annotationText.trim()}` : "",
      "",
      "[页面 DOM 摘要]",
      ...(domLines.length ? domLines : ["暂无可用 DOM 摘要"]),
    ]
      .filter(Boolean)
      .join("\n");
    window.dispatchEvent(
      new CustomEvent("echo:inject-composer-images", {
        detail: {
          threadId,
          images: image ? [image] : [],
          sourceLabel: "浏览器标注",
          text: context,
        },
      }),
    );
    setAnnotationMode(false);
    setAnnotationText("");
    setAnnotationPoints([]);
  }, [
    annotationPoints,
    annotationText,
    pageInfo.title,
    pageInfo.url,
    screenshot,
    session,
    semanticSnapshot,
    sessionIdentity,
    threadId,
    urlInput,
  ]);

  const handleBack = useCallback(async () => {
    if (!session) return;
    try {
      const data = await browserApi.action(sessionIdentity, "back");
      if (data.url)
        setPageInfo({
          url: data.url as string,
          title: (data.title as string) || "",
        });
      setUrlInput((data.url as string) || "");
      await handleRefreshScreenshot();
    } catch (e) {
      swallow(e);
    }
  }, [session, sessionIdentity, handleRefreshScreenshot]);

  const handleForward = useCallback(async () => {
    if (!session) return;
    try {
      const data = await browserApi.action(sessionIdentity, "forward");
      if (data.url)
        setPageInfo({
          url: data.url as string,
          title: (data.title as string) || "",
        });
      setUrlInput((data.url as string) || "");
      await handleRefreshScreenshot();
    } catch (e) {
      swallow(e);
    }
  }, [session, sessionIdentity, handleRefreshScreenshot]);

  const handleReload = useCallback(async () => {
    if (!session) return;
    try {
      const data = await browserApi.action(sessionIdentity, "reload");
      if (data.url)
        setPageInfo({
          url: data.url as string,
          title: (data.title as string) || "",
        });
      await handleRefreshScreenshot();
    } catch (e) {
      swallow(e);
    }
  }, [session, sessionIdentity, handleRefreshScreenshot]);

  const handleDevicePreviewChange = useCallback(
    async (preset: DevicePreviewPreset) => {
      const target = DEVICE_PREVIEW_PRESETS[preset];
      setDevicePreview(preset);
      setViewportChanging(true);
      setError(null);
      try {
        const data = await browserApi.setViewport(
          sessionIdentity,
          target.width,
          target.height,
        );
        applySessionSnapshot(data.session);
        await refreshBrowserArtifacts();
      } catch (err) {
        swallow(err);
        setError(
          err instanceof Error ? err.message : "Failed to update viewport",
        );
      } finally {
        setViewportChanging(false);
      }
    },
    [applySessionSnapshot, refreshBrowserArtifacts, sessionIdentity],
  );

  const handleScreenshotPointer = useCallback(
    async (event: ReactMouseEvent<HTMLImageElement>) => {
      if (!session || screenshotSize.width <= 0 || screenshotSize.height <= 0) {
        return;
      }
      const rect = event.currentTarget.getBoundingClientRect();
      const x = Math.round(
        ((event.clientX - rect.left) / Math.max(1, rect.width)) *
          screenshotSize.width,
      );
      const y = Math.round(
        ((event.clientY - rect.top) / Math.max(1, rect.height)) *
          screenshotSize.height,
      );
      setClickMarker({
        x,
        y,
        mode: pointerMode,
        timestamp: Date.now(),
      });
      if (clickMarkerTimeoutRef.current) {
        clearTimeout(clickMarkerTimeoutRef.current);
      }
      clickMarkerTimeoutRef.current = window.setTimeout(
        () => setClickMarker(null),
        1800,
      );
      setLoading(true);
      setError(null);
      try {
        await browserApi.action(
          sessionIdentity,
          pointerMode === "double" ? "double_click_at" : "click_at",
          { x, y },
        );
        await handleRefreshScreenshot();
      } catch (err) {
        swallow(err);
        setError(err instanceof Error ? err.message : "Browser click failed");
      } finally {
        setLoading(false);
      }
    },
    [
      handleRefreshScreenshot,
      pointerMode,
      screenshotSize.height,
      screenshotSize.width,
      session,
      sessionIdentity,
    ],
  );

  const handleScreenshotHover = useCallback(
    (event: ReactMouseEvent<HTMLImageElement>) => {
      if (screenshotSize.width <= 0 || screenshotSize.height <= 0) return;
      const rect = event.currentTarget.getBoundingClientRect();
      const x = Math.round(
        ((event.clientX - rect.left) / Math.max(1, rect.width)) *
          screenshotSize.width,
      );
      const y = Math.round(
        ((event.clientY - rect.top) / Math.max(1, rect.height)) *
          screenshotSize.height,
      );
      setHoverPoint({ x, y, mode: pointerMode, timestamp: Date.now() });
    },
    [pointerMode, screenshotSize.height, screenshotSize.width],
  );

  const focusActionEntry = useCallback(
    (entry: ActionLogEntry, absoluteIndex: number) => {
      setSelectedActionKey(
        `${entry.timestamp}-${entry.action}-${absoluteIndex}`,
      );
      setActionLogExpanded(true);
      const point = actionCoordinatePoint(entry);
      if (!point) return;
      setClickMarker({
        ...point,
        mode: entry.action === "double_click_at" ? "double" : "click",
        timestamp: Date.now(),
      });
      if (clickMarkerTimeoutRef.current) {
        clearTimeout(clickMarkerTimeoutRef.current);
      }
      clickMarkerTimeoutRef.current = window.setTimeout(
        () => setClickMarker(null),
        2600,
      );
    },
    [],
  );

  const handleClose = useCallback(async () => {
    try {
      await browserApi.reset(sessionIdentity);
      setSession(null);
      setScreenshot("");
      setPageInfo({ url: "", title: "" });
      setActionLog([]);
      setSelectedActionKey(null);
      setSessionHealth(null);
      setAutoRefresh(false);
    } catch (e) {
      swallow(e);
    }
  }, [sessionIdentity]);

  const openInFullBrowser = useCallback(() => {
    const target = pageInfo.url || urlInput.trim();
    if (target) {
      try {
        const request: BrowserOpenUrlRequest = {
          url: target,
          title: pageInfo.title || target,
          device: browserTabDeviceForPreset(devicePreview),
          source: "agent-preview",
          sessionId,
        };
        localStorage.setItem(
          BROWSER_OPEN_URL_REQUEST_KEY,
          JSON.stringify(request),
        );
      } catch (e) {
        swallow(e);
      }
    }
    window.location.hash = BROWSER_WORKSPACE_ROUTE;
  }, [devicePreview, pageInfo.title, pageInfo.url, sessionId, urlInput]);

  const runtimeLabel = session?.runtime || session?.mode || "mock";
  const sessionHealthy = (sessionHealth?.healthy ?? session?.healthy) !== false;
  const sessionIssues = sessionHealth?.issues ?? [];
  const visibleActions = actionLog.slice(-20);
  const visibleActionStartIndex = actionLog.length - visibleActions.length;
  const actionFailureCount = actionLog.filter(
    (entry) => entry.status && entry.status !== "ok",
  ).length;
  const actionCoordinateCount = actionLog.filter((entry) =>
    Boolean(actionCoordinatePoint(entry)),
  ).length;
  const selectedAction =
    selectedActionKey == null
      ? null
      : (actionLog
          .map((entry, index) => ({ entry, index }))
          .find(
            ({ entry, index }) =>
              actionEntryKey(entry, index) === selectedActionKey,
          ) ?? null);
  const activeDevicePreview = DEVICE_PREVIEW_PRESETS[devicePreview];
  const deviceFrameKind =
    devicePreview === "desktop"
      ? "desktop"
      : activeDevicePreview.width <= 540
        ? "phone"
        : "tablet";
  const currentViewportLabel = `${activeDevicePreview.width}x${activeDevicePreview.height}`;
  const livePreviewUrl = normalizePreviewUrl(pageInfo.url || urlInput);
  const canLivePreview = Boolean(livePreviewUrl);
  const effectiveSurfaceMode =
    surfaceMode === "live" && canLivePreview ? "live" : "screenshot";
  const electronLiveSurface =
    typeof window !== "undefined" && Boolean(window.echo?.isElectron);

  useEffect(() => {
    if (!livePreviewUrl) return;
    setLiveTab((prev) => ({
      ...prev,
      url: livePreviewUrl,
      title: pageInfo.title || livePreviewUrl,
      isLoading: false,
      device: browserTabDeviceForPreset(devicePreview),
    }));
  }, [devicePreview, livePreviewUrl, pageInfo.title]);

  // Seed-and-load an externally supplied URL (initialUrl). Fires once per
  // distinct URL: ensures the session and navigates, so a caller that delegates
  // its preview here (live-preview-panel URL mode, agent regression preview)
  // loads that page in this one controllable surface. Inert when initialUrl is
  // unset, so existing callers are unaffected.
  const initialUrlLoadedRef = useRef<string | null>(null);
  useEffect(() => {
    const target = (initialUrl ?? "").trim();
    if (!target || initialUrlLoadedRef.current === target) return;
    initialUrlLoadedRef.current = target;
    let url = target;
    if (!/^https?:\/\//i.test(url) && !url.startsWith("about:")) {
      url = "https://" + url;
    }
    setUrlInput(url);
    void (async () => {
      try {
        const data = await browserApi.ensure(sessionIdentity);
        applySessionSnapshot(data.session);
        const info = await browserApi.navigate(sessionIdentity, url);
        setPageInfo(info);
        setUrlInput(info.url);
      } catch (err) {
        swallow(err);
      }
    })();
  }, [initialUrl, sessionIdentity, applySessionSnapshot]);

  // ----- Render ------------------------------------------------------------

  // Starting state
  if (!session) {
    return (
      <div
        className={cn(
          "relative flex h-full flex-col items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_top,color-mix(in_oklch,var(--primary)_10%,transparent),transparent_38%),linear-gradient(180deg,var(--background),color-mix(in_oklch,var(--muted)_42%,transparent))] p-6 text-center",
          className,
        )}
      >
        <div className="absolute inset-x-8 top-6 h-px bg-gradient-to-r from-transparent via-border/70 to-transparent" />
        <div className="grid size-14 place-items-center rounded-lg border border-border-default bg-background/82 shadow-[var(--shadow-xs)] backdrop-blur">
          {loading ? (
            <Loader2Icon className="size-8 animate-spin text-primary" />
          ) : (
            <GlobeIcon className="size-8 text-muted-foreground/60" />
          )}
        </div>
        <div className="max-w-sm">
          <h3 className="text-sm font-semibold text-foreground">
            {t.browser.browserAutomation}
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {loading
              ? t.browser.launchingBrowser
              : t.browser.browserAutomationDesc}
          </p>
        </div>
        {error && <p className="text-destructive text-xs">{error}</p>}
        {!loading && (
          <button
            onClick={handleLaunch}
            disabled={loading}
            className="inline-flex h-8 items-center gap-2 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground shadow-[var(--shadow-xs)] transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {loading ? (
              <Loader2Icon className="size-3 animate-spin" />
            ) : (
              <PlayIcon className="size-3" />
            )}
            {t.browser.launchBrowser}
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex h-full flex-col overflow-hidden bg-background",
        className,
      )}
    >
      <BrowserPreviewToolbar
        urlInput={urlInput}
        onUrlInputChange={setUrlInput}
        onNavigate={() => void handleNavigate()}
        onBack={() => void handleBack()}
        onForward={() => void handleForward()}
        onReload={() => void handleReload()}
        onOpenFullBrowser={openInFullBrowser}
        onEndSession={() => void handleClose()}
        canLivePreview={canLivePreview}
        surfaceMode={effectiveSurfaceMode}
        onSurfaceModeChange={setSurfaceMode}
        devicePreview={devicePreview}
        viewportChanging={viewportChanging}
        onDevicePreviewChange={(preset) =>
          void handleDevicePreviewChange(preset)
        }
        onAttachScreenshot={() => void handleAttachScreenshotToComposer()}
        autoRefresh={autoRefresh}
        onAutoRefreshChange={setAutoRefresh}
        sessionHealthy={sessionHealthy}
        runtimeLabel={runtimeLabel}
      />

      {!sessionHealthy && sessionIssues.length > 0 && (
        <div className="flex h-8 shrink-0 items-center gap-2 border-b border-destructive/20 bg-destructive/8 px-2 text-xs text-destructive">
          <span className="relative flex size-4 shrink-0 items-center justify-center rounded-full bg-destructive/10">
            <span className="size-1.5 rounded-full bg-destructive" />
          </span>
          <span className="min-w-0 flex-1 truncate">
            {bp.sessionNeedsAttention(sessionIssues.join(" · "))}
          </span>
          <button
            type="button"
            onClick={() => void handleLaunch()}
            className="h-5 shrink-0 rounded border border-destructive/25 px-1.5 text-xs font-medium transition-colors hover:bg-destructive/10"
          >
            {bp.reconnectButton}
          </button>
        </div>
      )}

      {error && (
        <div className="flex shrink-0 items-center gap-2 border-b border-destructive/25 bg-destructive/8 px-2 py-1.5 text-xs">
          <span className="min-w-0 flex-1 truncate text-destructive">
            {error}
          </span>
          <button
            type="button"
            onClick={handleRetry}
            className="h-6 shrink-0 rounded-md border border-destructive/25 px-2 font-medium text-destructive transition-colors hover:bg-destructive/10"
          >
            重试
          </button>
        </div>
      )}

      {/* Screenshot area */}
      <div className="relative flex-1 overflow-auto bg-[radial-gradient(circle_at_top,color-mix(in_oklch,var(--primary)_8%,transparent),transparent_34%),linear-gradient(180deg,color-mix(in_oklch,var(--muted)_34%,transparent),var(--background))] [&::-webkit-scrollbar]:size-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border/80 [&::-webkit-scrollbar-track]:bg-transparent">
        {screenshot && effectiveSurfaceMode === "screenshot" && (
          <div
            className={cn(
              AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME,
              "absolute top-3 right-3 z-40",
            )}
          >
            <button
              type="button"
              aria-pressed={annotationMode}
              onClick={() => {
                setAnnotationMode((value) => !value);
                setAnnotationPoints([]);
              }}
              className={cn(
                AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
                AUTOMATION_CAPSULE_SURFACE_CLASS_NAME,
                "flex h-8 items-center gap-1.5 px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
                annotationMode && "text-primary ring-primary/30",
              )}
              title={bp.annotateScreenshot}
              aria-label={bp.annotateScreenshot}
            >
              <PencilIcon className="size-3.5" />
              <span>{bp.annotationButton}</span>
            </button>
          </div>
        )}
        {annotationMode &&
          screenshot &&
          effectiveSurfaceMode === "screenshot" && (
            <div
              className={cn(
                AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME,
                "absolute inset-x-3 top-14 z-40 flex justify-center",
              )}
            >
              <div
                className={cn(
                  AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
                  AUTOMATION_CAPSULE_SURFACE_CLASS_NAME,
                  "flex w-full max-w-2xl items-center gap-2 p-2",
                )}
              >
                <PencilIcon className="size-3.5 shrink-0 text-primary" />
                <input
                  value={annotationText}
                  onChange={(event) => setAnnotationText(event.target.value)}
                  placeholder={bp.annotationPlaceholder}
                  aria-label={bp.annotationInputLabel}
                  className="h-7 min-w-0 flex-1 bg-transparent text-xs outline-none"
                />
                <button
                  type="button"
                  onClick={() => void handleSendAnnotation()}
                  className="flex h-7 shrink-0 items-center gap-1 rounded-md bg-primary px-2 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                >
                  <CheckIcon className="size-3.5" />
                  {bp.sendAnnotation}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAnnotationMode(false);
                    setAnnotationPoints([]);
                  }}
                  className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={bp.cancelAnnotation}
                >
                  <XIcon className="size-3.5" />
                </button>
              </div>
            </div>
          )}
        {effectiveSurfaceMode === "live" ? (
          <div className="flex min-h-full items-center justify-center p-3">
            <div
              className={cn(
                "relative overflow-hidden bg-background ring-1 ring-black/5 shadow-[0_18px_48px_rgba(15,23,42,0.18)]",
                devicePreview === "desktop"
                  ? "w-full rounded-lg border border-border-default"
                  : "border-[5px] border-foreground/80",
                deviceFrameKind === "tablet" &&
                  "max-h-full rounded-4xl shadow-[0_22px_64px_rgba(15,23,42,0.24)]",
                deviceFrameKind === "phone" &&
                  "max-h-full rounded-4xl shadow-[0_24px_70px_rgba(15,23,42,0.28)]",
              )}
              style={{
                aspectRatio:
                  devicePreview === "desktop"
                    ? undefined
                    : `${activeDevicePreview.width} / ${activeDevicePreview.height}`,
                maxWidth:
                  devicePreview === "desktop"
                    ? undefined
                    : deviceFrameKind === "tablet"
                      ? 420
                      : 240,
                width: devicePreview === "desktop" ? "100%" : "70%",
                minHeight: devicePreview === "desktop" ? "100%" : undefined,
              }}
            >
              {!electronLiveSurface && !liveFrameLoaded && (
                <div className="absolute inset-0 z-10 grid place-items-center bg-background/70">
                  <div className="flex items-center gap-2 rounded-full border bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-[var(--shadow-xs)]">
                    <Loader2Icon className="size-3.5 animate-spin text-primary" />
                    {bp.loadingLivePage}
                  </div>
                </div>
              )}
              {electronLiveSurface ? (
                <WebviewTab
                  key={liveTab.id}
                  tab={liveTab}
                  active
                  onPatch={(patch) =>
                    setLiveTab((prev) => ({ ...prev, ...patch }))
                  }
                  ref={liveWebviewRef}
                />
              ) : (
                <iframe
                  key={`${livePreviewUrl}-${currentViewportLabel}`}
                  src={livePreviewUrl}
                  title={
                    pageInfo.title || livePreviewUrl || "Live browser preview"
                  }
                  className="size-full border-0 bg-background"
                  onLoad={() => setLiveFrameLoaded(true)}
                  allow="clipboard-read; clipboard-write; fullscreen"
                  referrerPolicy="no-referrer-when-downgrade"
                />
              )}
            </div>
          </div>
        ) : screenshot ? (
          <div className="flex min-h-full items-center justify-center p-3">
            <div
              className={cn(
                "relative overflow-hidden bg-background ring-1 ring-black/5 shadow-[0_18px_48px_rgba(15,23,42,0.18)]",
                devicePreview === "desktop"
                  ? "w-full rounded-lg border border-border-default"
                  : "border-[5px] border-foreground/80",
                deviceFrameKind === "tablet" &&
                  "max-h-full rounded-4xl shadow-[0_22px_64px_rgba(15,23,42,0.24)]",
                deviceFrameKind === "phone" &&
                  "max-h-full rounded-4xl shadow-[0_24px_70px_rgba(15,23,42,0.28)]",
              )}
              style={{
                aspectRatio:
                  devicePreview === "desktop"
                    ? undefined
                    : `${activeDevicePreview.width} / ${activeDevicePreview.height}`,
                maxWidth:
                  devicePreview === "desktop"
                    ? undefined
                    : deviceFrameKind === "tablet"
                      ? 420
                      : 240,
                width: devicePreview === "desktop" ? "100%" : "70%",
              }}
            >
              <img
                src={`data:image/png;base64,${screenshot}`}
                alt={t.browser.browserAutomation}
                className={cn(
                  "size-full",
                  devicePreview === "desktop"
                    ? "object-contain"
                    : "object-cover object-left-top",
                )}
                onClick={(event) => {
                  if (annotationMode) {
                    handleAnnotateScreenshot(event);
                  } else {
                    void handleScreenshotPointer(event);
                  }
                }}
                onError={() => setScreenshot("")}
                onMouseMove={handleScreenshotHover}
                onMouseLeave={() => setHoverPoint(null)}
                title={bp.screenshotClickTitle(
                  pointerMode === "double" ? bp.doubleClickMode : bp.clickMode,
                  currentViewportLabel,
                )}
                style={{ cursor: "crosshair" }}
              />
              {annotationPoints.map((point, index) => (
                <div
                  key={`${point.x}-${point.y}-${index}`}
                  className="pointer-events-none absolute z-30 grid size-6 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 border-primary bg-primary/20 text-xs font-bold text-primary shadow-[var(--shadow-xs)]"
                  style={{
                    left: `${(point.x / screenshotSize.width) * 100}%`,
                    top: `${(point.y / screenshotSize.height) * 100}%`,
                  }}
                >
                  {index + 1}
                </div>
              ))}
              {hoverPoint && screenshotSize.width > 0 && (
                <div className="pointer-events-none absolute bottom-2 left-2 z-30 rounded-full border border-border-default bg-background/88 px-2 py-1 font-mono text-xs text-muted-foreground shadow-[var(--shadow-xs)] backdrop-blur">
                  x {hoverPoint.x} · y {hoverPoint.y}
                </div>
              )}
              {clickMarker && screenshotSize.width > 0 && (
                <div
                  className="pointer-events-none absolute z-30 grid size-9 -translate-x-1/2 -translate-y-1/2 place-items-center"
                  style={{
                    left: `${(clickMarker.x / screenshotSize.width) * 100}%`,
                    top: `${(clickMarker.y / screenshotSize.height) * 100}%`,
                  }}
                >
                  <span className="absolute size-9 rounded-full border border-primary/40 bg-primary/10 animate-ping" />
                  <span className="grid size-5 place-items-center rounded-full border border-primary/55 bg-background/90 text-micro font-bold text-primary shadow-[var(--shadow-xs)]">
                    {clickMarker.mode === "double" ? "2x" : "1x"}
                  </span>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center p-5">
            {/* 本地服务快速入口 */}
            {detectedServices.length > 0 ? (
              <div className="w-full max-w-sm space-y-3 rounded-lg border border-border-default bg-background/82 p-3 shadow-[var(--shadow-xs)] backdrop-blur">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    aria-expanded={localServicesExpanded}
                    onClick={() => setLocalServicesExpanded((value) => !value)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <div className="grid size-7 place-items-center rounded-lg bg-primary/10 text-primary">
                      <ServerIcon className="size-3.5" />
                    </div>
                    <span className="text-xs font-semibold text-foreground">
                      {bp.localServices}
                    </span>
                    <ChevronDownIcon
                      className={cn(
                        "ml-auto size-3.5 text-muted-foreground transition-transform",
                        !localServicesExpanded && "-rotate-90",
                      )}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={handleRescanPorts}
                    disabled={scanningPorts}
                    className="flex h-6 shrink-0 items-center gap-1 rounded-md border border-border-default px-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground disabled:opacity-50"
                  >
                    {scanningPorts ? (
                      <Loader2Icon className="size-3 animate-spin" />
                    ) : (
                      <RefreshCwIcon className="size-3" />
                    )}
                    {bp.scanButton}
                  </button>
                </div>
                {localServicesExpanded && (
                  <div className="space-y-1.5">
                    {detectedServices.map((svc) => (
                      <button
                        key={svc.port}
                        onClick={() => handleQuickNavigate(svc.url)}
                        className="group flex w-full items-center gap-3 rounded-lg border border-border-default bg-muted/25 px-3 py-2 text-left shadow-[var(--shadow-xs)] transition-colors hover:border-primary/25 hover:bg-primary/5"
                      >
                        <div
                          className={cn(
                            "flex size-8 shrink-0 items-center justify-center rounded-md text-xs font-bold transition-transform group-hover:scale-[1.03]",
                            svc.type === "frontend"
                              ? "bg-info/10 text-info"
                              : svc.type === "backend"
                                ? "bg-success/10 text-success"
                                : "bg-muted text-muted-foreground",
                          )}
                        >
                          {svc.port}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium text-foreground">
                            {svc.name}
                          </div>
                          <div className="truncate text-xs text-muted-foreground">
                            localhost:{svc.port}
                          </div>
                        </div>
                        <span
                          className={cn(
                            "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
                            svc.type === "frontend"
                              ? "bg-info/10 text-info"
                              : svc.type === "backend"
                                ? "bg-success/10 text-success"
                                : "bg-muted text-muted-foreground",
                          )}
                        >
                          {svc.type === "frontend"
                            ? bp.serviceTypeFrontend
                            : svc.type === "backend"
                              ? bp.serviceTypeBackend
                              : bp.serviceTypeOther}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="max-w-sm rounded-lg border border-border-default bg-background/82 p-5 text-center shadow-[var(--shadow-xs)] backdrop-blur">
                <div className="mx-auto grid size-10 place-items-center rounded-lg bg-muted/60">
                  <ImageIcon className="size-5 text-muted-foreground/45" />
                </div>
                <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                  {t.browser.navigateHint}
                </p>
                <button
                  onClick={handleRescanPorts}
                  disabled={scanningPorts}
                  className="mx-auto mt-3 flex h-8 items-center gap-1.5 rounded-md border border-border-default px-3 text-xs text-muted-foreground shadow-[var(--shadow-xs)] transition-colors hover:bg-muted/50 hover:text-foreground disabled:opacity-50"
                >
                  {scanningPorts ? (
                    <Loader2Icon className="size-3 animate-spin" />
                  ) : (
                    <RefreshCwIcon className="size-3" />
                  )}
                  {bp.scanLocalServices}
                </button>
              </div>
            )}
          </div>
        )}
        {(loading || viewportChanging) && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/60">
            <Loader2Icon className="text-primary size-6 animate-spin" />
          </div>
        )}
        {screenshotSize.width > 0 && (
          <span className="text-muted-foreground/50 absolute right-1 bottom-1 text-xs">
            {screenshotSize.width}x{screenshotSize.height} · {pointerMode}
          </span>
        )}
      </div>

      {/* Action Log */}
      <div className="shrink-0 border-t">
        <button
          type="button"
          onClick={() => setActionLogExpanded((value) => !value)}
          aria-expanded={actionLogExpanded}
          className="flex w-full items-center justify-between gap-2 px-2 py-1 text-left transition-colors hover:bg-muted/45"
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <ChevronDownIcon
              className={cn(
                "text-muted-foreground/70 size-3 shrink-0 transition-transform",
                !actionLogExpanded && "-rotate-90",
              )}
            />
            <span className="text-muted-foreground truncate text-xs font-medium uppercase tracking-wide">
              {t.browser.actionLog}
            </span>
          </span>
          <span className="text-muted-foreground/60 shrink-0 text-xs">
            {t.browser.actions(actionLog.length)}
            {actionFailureCount > 0
              ? ` · ${bp.failureCount(actionFailureCount)}`
              : ""}
            {actionCoordinateCount > 0
              ? ` · ${bp.coordinateCount(actionCoordinateCount)}`
              : ""}
          </span>
        </button>
        {selectedAction && (
          <div className="border-t border-border-subtle bg-primary/5 px-2 py-1.5 text-xs">
            <div className="flex min-w-0 items-center gap-1.5">
              <ActionIcon action={selectedAction.entry.action} />
              <span className="shrink-0 font-semibold text-foreground/85">
                {bp.selectedAction(selectedAction.entry.action)}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded-full px-1.5 py-0.5 text-xs font-medium",
                  actionStatusClass(selectedAction.entry),
                )}
              >
                {actionStatusLabel(selectedAction.entry, bp)}
              </span>
              {actionCoordinateLabel(selectedAction.entry) && (
                <button
                  type="button"
                  onClick={() =>
                    focusActionEntry(selectedAction.entry, selectedAction.index)
                  }
                  className="shrink-0 rounded-full border border-primary/25 bg-background/80 px-1.5 py-0.5 font-mono text-xs text-primary transition-colors hover:bg-primary/10"
                  title={bp.locateActionTitle}
                >
                  {actionCoordinateLabel(selectedAction.entry)}
                </button>
              )}
              <button
                type="button"
                onClick={() => setSelectedActionKey(null)}
                className="ml-auto grid size-5 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title={bp.deselectTitle}
                aria-label={bp.deselectTitle}
              >
                <XIcon className="size-3" />
              </button>
            </div>
            <div className="mt-1 truncate text-muted-foreground">
              {actionDetailLabel(selectedAction.entry, bp)}
            </div>
          </div>
        )}
        {actionLogExpanded && (
          <div className="max-h-52 overflow-auto px-2 pb-2 [&::-webkit-scrollbar]:size-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border/80 [&::-webkit-scrollbar-track]:bg-transparent">
            {actionLog.length === 0 ? (
              <p className="text-muted-foreground/50 py-2 text-center text-xs">
                {t.browser.noActions}
              </p>
            ) : (
              <div className="space-y-1">
                {visibleActions.map((entry, i) => {
                  const absoluteIndex = visibleActionStartIndex + i;
                  const entryKey = actionEntryKey(entry, absoluteIndex);
                  const coordinateLabel = actionCoordinateLabel(entry);
                  const failed = Boolean(entry.status && entry.status !== "ok");
                  return (
                    <button
                      key={entryKey}
                      type="button"
                      onClick={() => focusActionEntry(entry, absoluteIndex)}
                      className={cn(
                        "group flex w-full items-start gap-2 rounded-lg border border-border-subtle bg-muted/22 px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted/38",
                        failed && "border-destructive/25 bg-destructive/8",
                        selectedActionKey === entryKey &&
                          "border-primary/35 bg-primary/10",
                      )}
                    >
                      <div className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-md bg-background/75">
                        <ActionIcon action={entry.action} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <span className="truncate font-semibold text-foreground/85">
                            {entry.action}
                          </span>
                          <span
                            className={cn(
                              "shrink-0 rounded-full px-1.5 py-0.5 text-xs font-medium",
                              actionStatusClass(entry),
                            )}
                          >
                            {actionStatusLabel(entry, bp)}
                          </span>
                          {coordinateLabel && (
                            <span className="shrink-0 rounded-full bg-background/75 px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                              {coordinateLabel}
                            </span>
                          )}
                          <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground/50">
                            {new Date(
                              entry.timestamp * 1000,
                            ).toLocaleTimeString([], {
                              hour: "2-digit",
                              minute: "2-digit",
                              second: "2-digit",
                            })}
                          </span>
                        </div>
                        <div
                          className={cn(
                            "mt-0.5 truncate text-muted-foreground",
                            failed && "text-destructive/85",
                          )}
                        >
                          {actionDetailLabel(entry, bp)}
                        </div>
                      </div>
                    </button>
                  );
                })}
                <div ref={logEndRef} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
