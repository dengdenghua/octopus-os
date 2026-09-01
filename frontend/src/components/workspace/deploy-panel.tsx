/**
 * Deploy Panel -- One-Click Cloud Deploy UI
 *
 * Provides:
 * - Auto-detection of project type and recommended provider
 * - Provider selection (Vercel, Docker, Static)
 * - Config preview / edit before deploy
 * - Deploy progress with log streaming
 * - Clickable deployment URL on success
 * - Deployment history list
 */

import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { copyTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CloudIcon,
  ContainerIcon,
  CopyIcon,
  ExternalLinkIcon,
  FileCodeIcon,
  GlobeIcon,
  Loader2Icon,
  PlayIcon,
  RefreshCwIcon,
  RocketIcon,
  ServerIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { RoutedWebLink } from "@/components/ui/routed-web-link";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ProviderInfo {
  id: string;
  name: string;
  description: string;
  configured: boolean;
  requires: string[];
  supports: string[];
}

interface ConfigFile {
  filename: string;
  content: string;
  description: string;
}

interface DetectionResult {
  project_type: string;
  recommended_target: string;
  alternative_targets: string[];
  framework: string;
  language: string;
  build_command: string;
  output_directory: string;
  start_command: string;
  install_command: string;
  port: number;
  confidence: number;
  notes: string[];
  config_files: ConfigFile[];
  providers: ProviderInfo[];
}

interface DeployRecord {
  id: string;
  workspace: string;
  provider: string;
  project_name: string;
  state: string;
  url: string;
  created_at: number;
  updated_at: number;
  detection: Record<string, string>;
  config_files: ConfigFile[];
  logs: string[];
  error: string;
  meta: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const BASE = () => getBackendBaseURL();

async function detectProject(workspace: string): Promise<DetectionResult> {
  const resp = await fetch(`${BASE()}/api/deploy/detect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace }),
  });
  if (!resp.ok) throw new Error(`Detection failed: ${resp.status}`);
  return resp.json();
}

async function startDeploy(params: {
  workspace: string;
  provider: string;
  project_name?: string;
  production?: boolean;
  env_vars?: Record<string, string>;
  build_command?: string;
  output_directory?: string;
  install_command?: string;
  start_command?: string;
  dockerfile_content?: string;
  port?: number;
  push_to_registry?: boolean;
}): Promise<DeployRecord> {
  const resp = await fetch(`${BASE()}/api/deploy/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!resp.ok) throw new Error(`Deploy failed: ${resp.status}`);
  return resp.json();
}

async function getDeployStatus(deployId: string): Promise<DeployRecord> {
  const resp = await fetch(`${BASE()}/api/deploy/${deployId}/status`);
  if (!resp.ok) throw new Error(`Status check failed: ${resp.status}`);
  return resp.json();
}

async function getDeployHistory(limit = 20): Promise<DeployRecord[]> {
  const resp = await fetch(`${BASE()}/api/deploy/history?limit=${limit}`);
  if (!resp.ok) return [];
  return resp.json();
}

async function generateConfig(
  workspace: string,
  target?: string,
): Promise<ConfigFile[]> {
  const resp = await fetch(`${BASE()}/api/deploy/generate-config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace, target }),
  });
  if (!resp.ok) return [];
  const data = await resp.json();
  return data.configs || [];
}

// ---------------------------------------------------------------------------
// Provider icons and labels
// ---------------------------------------------------------------------------

const PROVIDER_META: Record<
  string,
  { icon: React.ElementType; label: string; color: string }
> = {
  vercel: { icon: CloudIcon, label: "Vercel", color: "text-info" },
  docker: { icon: ContainerIcon, label: "Docker", color: "text-info" },
  static: { icon: GlobeIcon, label: "Static Preview", color: "text-success" },
};

const STATE_DISPLAY: Record<
  string,
  { label: string; color: string; icon: React.ElementType }
> = {
  pending: {
    label: "Pending",
    color: "text-muted-foreground",
    icon: Loader2Icon,
  },
  detecting: {
    label: "Detecting...",
    color: "text-warning",
    icon: Loader2Icon,
  },
  building: { label: "Building...", color: "text-info", icon: Loader2Icon },
  deploying: {
    label: "Deploying...",
    color: "text-info",
    icon: Loader2Icon,
  },
  ready: { label: "Ready", color: "text-success", icon: CheckCircle2Icon },
  error: { label: "Error", color: "text-destructive", icon: AlertTriangleIcon },
  cancelled: {
    label: "Cancelled",
    color: "text-muted-foreground",
    icon: XIcon,
  },
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ProviderCard({
  provider,
  selected,
  recommended,
  onClick,
}: {
  provider: ProviderInfo;
  selected: boolean;
  recommended: boolean;
  onClick: () => void;
}) {
  const { t } = useI18n();
  const meta = PROVIDER_META[provider.id] || {
    icon: ServerIcon,
    label: provider.name,
    color: "text-muted-foreground",
  };
  const Icon = meta.icon;

  return (
    <button
      onClick={onClick}
      disabled={!provider.configured}
      className={cn(
        "flex flex-col items-start gap-1 rounded-lg border px-3 py-2.5 text-left text-xs transition-colors duration-base",
        selected
          ? "border-primary/60 bg-primary/5 ring-1 ring-primary/30 shadow-[var(--shadow-xs)] shadow-primary/5"
          : "border-border-default hover:border-primary/40 hover:bg-accent/40",
        !provider.configured && "cursor-not-allowed opacity-50",
      )}
    >
      <div className="flex w-full items-center gap-2">
        <Icon className={cn("size-4", meta.color)} />
        <span className="font-medium">{meta.label}</span>
        {recommended && (
          <span className="ml-auto rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
            {t.deploy.recommended}
          </span>
        )}
      </div>
      <p className="text-muted-foreground text-xs leading-tight">
        {provider.description}
      </p>
      {!provider.configured && provider.requires.length > 0 && (
        <p className="text-destructive text-xs">
          {t.deploy.requires}: {provider.requires.join(", ")}
        </p>
      )}
    </button>
  );
}

function ConfigPreview({
  configs,
  onEdit,
}: {
  configs: ConfigFile[];
  onEdit?: (index: number, content: string) => void;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState<number | null>(
    configs.length > 0 ? 0 : null,
  );

  if (configs.length === 0) return null;

  return (
    <div className="space-y-1">
      <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
        {t.deploy.generatedConfigs}
      </p>
      {configs.map((cfg, i) => (
        <div
          key={cfg.filename}
          className="rounded-lg border border-border-default"
        >
          <button
            onClick={() => setExpanded(expanded === i ? null : i)}
            className="flex w-full items-center gap-2 px-2 py-1.5 text-xs transition-colors hover:bg-accent/40"
          >
            {expanded === i ? (
              <ChevronDownIcon className="size-3" />
            ) : (
              <ChevronRightIcon className="size-3" />
            )}
            <FileCodeIcon className="text-muted-foreground size-3" />
            <span className="font-medium">{cfg.filename}</span>
            <span className="text-muted-foreground ml-auto text-xs">
              {cfg.description}
            </span>
          </button>
          {expanded === i && (
            <div className="border-t">
              <textarea
                className="bg-muted/50 w-full resize-none p-2 font-mono text-xs leading-relaxed focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                rows={Math.min(cfg.content.split("\n").length, 20)}
                value={cfg.content}
                onChange={(e) => onEdit?.(i, e.target.value)}
                spellCheck={false}
              />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function DeployLogViewer({ logs }: { logs: string[] }) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  if (logs.length === 0) return null;

  return (
    <div className="bg-muted/20 max-h-40 overflow-auto rounded-lg border border-border-default p-2 font-mono text-xs leading-relaxed">
      {logs.map((line, i) => (
        <div key={i} className="text-muted-foreground whitespace-pre-wrap">
          {line}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}

function HistoryItem({ record }: { record: DeployRecord }) {
  const defaultState = {
    label: "Unknown",
    color: "text-muted-foreground",
    icon: Loader2Icon,
  };
  const defaultProv = {
    icon: ServerIcon,
    label: "Unknown",
    color: "text-muted-foreground",
  };
  const stateInfo = STATE_DISPLAY[record.state] ?? defaultState;
  const StateIcon = stateInfo.icon;
  const provMeta = PROVIDER_META[record.provider] ?? defaultProv;
  const ProvIcon = provMeta.icon;
  const timeStr = record.created_at
    ? new Date(record.created_at * 1000).toLocaleString()
    : "";

  return (
    <div className="flex items-center gap-2 rounded-lg border border-border-default px-2 py-1.5 text-xs transition-colors hover:bg-accent/30">
      <ProvIcon className={cn("size-3.5 shrink-0", provMeta.color)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1">
          <span className="truncate font-medium">
            {record.project_name || "project"}
          </span>
          <StateIcon
            className={cn(
              "size-3 shrink-0",
              stateInfo.color,
              record.state === "deploying" && "animate-spin",
            )}
          />
        </div>
        <p className="text-muted-foreground truncate text-xs">{timeStr}</p>
      </div>
      {record.url && (
        <RoutedWebLink
          href={record.url}
          openTargetSource="deployment"
          className="text-primary hover:text-primary/80 shrink-0"
          title="Open deployment"
        >
          <ExternalLinkIcon className="size-3.5" />
        </RoutedWebLink>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main DeployPanel (floating panel like Quest)
// ---------------------------------------------------------------------------

interface DeployPanelProps {
  open: boolean;
  onClose: () => void;
  workspacePath: string;
  className?: string;
}

export function DeployPanel({
  open,
  onClose,
  workspacePath,
  className,
}: DeployPanelProps) {
  const { t } = useI18n();
  // State
  const [view, setView] = useState<"detect" | "deploy" | "history">("detect");
  const [loading, setLoading] = useState(false);
  const [detection, setDetection] = useState<DetectionResult | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<string>("");
  const [configFiles, setConfigFiles] = useState<ConfigFile[]>([]);
  const [currentDeploy, setCurrentDeploy] = useState<DeployRecord | null>(null);
  const [history, setHistory] = useState<DeployRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleDetect = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await detectProject(workspacePath);
      setDetection(result);
      setSelectedProvider(result.recommended_target);
      setConfigFiles(result.config_files);
      setView("detect");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Detection failed");
    } finally {
      setLoading(false);
    }
  }, [workspacePath]);

  const loadHistory = useCallback(async () => {
    try {
      const records = await getDeployHistory(20);
      setHistory(records);
    } catch (e) {
      swallow(e);
    }
  }, []);

  // Detect on open
  useEffect(() => {
    if (!open || !workspacePath) return;
    handleDetect();
    loadHistory();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [open, workspacePath, handleDetect, loadHistory]);

  const handleProviderChange = useCallback(
    async (providerId: string) => {
      setSelectedProvider(providerId);
      try {
        const configs = await generateConfig(workspacePath, providerId);
        setConfigFiles(configs);
      } catch (e) {
        swallow(e);
      }
    },
    [workspacePath],
  );

  const handleConfigEdit = useCallback((index: number, content: string) => {
    setConfigFiles((prev) => {
      const next = [...prev];
      if (next[index]) next[index] = { ...next[index], content };
      return next;
    });
  }, []);

  const handleDeploy = useCallback(async () => {
    if (!detection || !selectedProvider) return;
    setLoading(true);
    setError(null);
    setView("deploy");

    try {
      const record = await startDeploy({
        workspace: workspacePath,
        provider: selectedProvider,
        build_command: detection.build_command || undefined,
        output_directory: detection.output_directory || undefined,
        install_command: detection.install_command || undefined,
        start_command: detection.start_command || undefined,
        port: detection.port || undefined,
        dockerfile_content:
          selectedProvider === "docker"
            ? configFiles.find((c) => c.filename === "Dockerfile")?.content
            : undefined,
      });
      setCurrentDeploy(record);

      // Poll for status if not immediately done
      if (record.state !== "ready" && record.state !== "error") {
        pollRef.current = setInterval(async () => {
          try {
            const status = await getDeployStatus(record.id);
            setCurrentDeploy(status);
            if (status.state === "ready" || status.state === "error") {
              if (pollRef.current) clearInterval(pollRef.current);
              pollRef.current = null;
              loadHistory();
            }
          } catch (e) {
            swallow(e);
          }
        }, 2000);
      } else {
        loadHistory();
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Deploy failed");
      setCurrentDeploy(null);
    } finally {
      setLoading(false);
    }
  }, [detection, selectedProvider, workspacePath, configFiles, loadHistory]);

  const handleCopyUrl = useCallback(async () => {
    if (currentDeploy?.url) {
      try {
        await copyTextToClipboard(currentDeploy.url);
      } catch {
        toast.error(t.clipboard.failedToCopyToClipboard);
      }
    }
  }, [currentDeploy, t.clipboard.failedToCopyToClipboard]);

  if (!open) return null;

  return (
    <div
      className={cn(
        "bg-popover flex max-h-[32rem] w-[26rem] flex-col overflow-hidden rounded-lg border border-border-default shadow-2xl shadow-black/5 animate-in slide-in-from-bottom-2 fade-in duration-base",
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border-default px-4 py-2.5">
        <div className="flex size-6 items-center justify-center rounded-lg bg-primary/10">
          <RocketIcon className="text-primary size-3.5" />
        </div>
        <span className="text-sm font-semibold">{t.deploy.title}</span>

        {/* Tab switcher */}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setView("detect")}
            className={cn(
              "rounded-lg px-2 py-0.5 text-xs transition-colors",
              view === "detect"
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
          >
            {t.deploy.setup}
          </button>
          <button
            onClick={() => setView("deploy")}
            className={cn(
              "rounded-lg px-2 py-0.5 text-xs transition-colors",
              view === "deploy"
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
            disabled={!currentDeploy}
          >
            {t.deploy.title}
          </button>
          <button
            onClick={() => {
              setView("history");
              loadHistory();
            }}
            className={cn(
              "rounded-lg px-2 py-0.5 text-xs transition-colors",
              view === "history"
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
          >
            {t.deploy.history}
          </button>
          <button
            onClick={onClose}
            className="ml-1 rounded-lg p-0.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <XIcon className="size-3.5" />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-auto px-4 py-3">
        {/* Error banner */}
        {error && (
          <div className="text-destructive mb-3 flex items-start gap-2 rounded-lg border border-destructive/30/60 bg-destructive/5 p-2 text-xs dark:border-destructive/60">
            <AlertTriangleIcon className="size-3.5 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Detection / Setup View */}
        {view === "detect" && (
          <div className="space-y-4">
            {loading && !detection ? (
              <div className="flex items-center gap-2 py-8 justify-center text-xs text-muted-foreground">
                <Loader2Icon className="size-4 animate-spin" />
                {t.deploy.analyzingProject}
              </div>
            ) : detection ? (
              <>
                {/* Detection summary */}
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                      {detection.framework || detection.project_type}
                    </span>
                    <span className="text-muted-foreground text-xs">
                      {detection.language}
                    </span>
                    <span className="text-muted-foreground ml-auto text-xs">
                      {Math.round(detection.confidence * 100)}% confidence
                    </span>
                  </div>
                  {detection.notes.length > 0 && (
                    <p className="text-muted-foreground text-xs leading-tight">
                      {detection.notes[0]}
                    </p>
                  )}
                </div>

                {/* Build info */}
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                  {detection.build_command && (
                    <>
                      <span className="text-muted-foreground">Build:</span>
                      <span
                        className="font-mono truncate"
                        title={detection.build_command}
                      >
                        {detection.build_command}
                      </span>
                    </>
                  )}
                  {detection.output_directory && (
                    <>
                      <span className="text-muted-foreground">Output:</span>
                      <span className="font-mono">
                        {detection.output_directory}
                      </span>
                    </>
                  )}
                  {detection.port > 0 && (
                    <>
                      <span className="text-muted-foreground">Port:</span>
                      <span className="font-mono">{detection.port}</span>
                    </>
                  )}
                </div>

                {/* Provider selection */}
                <div className="space-y-2">
                  <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
                    {t.deploy.deployTarget}
                  </p>
                  <div className="grid gap-2">
                    {detection.providers.map((p) => (
                      <ProviderCard
                        key={p.id}
                        provider={p}
                        selected={selectedProvider === p.id}
                        recommended={p.id === detection.recommended_target}
                        onClick={() => handleProviderChange(p.id)}
                      />
                    ))}
                  </div>
                </div>

                {/* Config preview */}
                <ConfigPreview
                  configs={configFiles}
                  onEdit={handleConfigEdit}
                />

                {/* Deploy button */}
                <button
                  onClick={handleDeploy}
                  disabled={loading || !selectedProvider}
                  className={cn(
                    "flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors duration-base",
                    "bg-primary text-primary-foreground hover:bg-primary/90 shadow-[var(--shadow-xs)] shadow-primary/10",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                  )}
                >
                  {loading ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <PlayIcon className="size-4" />
                  )}
                  {t.deploy.deployTo(
                    PROVIDER_META[selectedProvider]?.label || selectedProvider,
                  )}
                </button>
              </>
            ) : (
              <div className="flex flex-col items-center gap-3 py-8 text-center">
                <div className="flex size-12 items-center justify-center rounded-lg bg-muted/50">
                  <RocketIcon className="text-muted-foreground size-6" />
                </div>
                <p className="text-muted-foreground text-xs">
                  {t.deploy.clickDetectHint}
                </p>
                <button
                  onClick={handleDetect}
                  className="bg-primary text-primary-foreground hover:bg-primary/90 rounded-lg px-4 py-2 text-xs font-medium shadow-[var(--shadow-xs)] shadow-primary/10 transition-colors"
                >
                  <RefreshCwIcon className="mr-1.5 inline size-3" />
                  {t.deploy.detectProject}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Deploy Progress View */}
        {view === "deploy" && (
          <div className="space-y-4">
            {currentDeploy ? (
              <>
                {/* Status banner */}
                {(() => {
                  const fallbackState = {
                    label: "Unknown",
                    color: "text-muted-foreground",
                    icon: Loader2Icon,
                  };
                  const stateInfo =
                    STATE_DISPLAY[currentDeploy.state] ?? fallbackState;
                  const StateIcon = stateInfo.icon;
                  return (
                    <div className="flex items-center gap-3 rounded-lg border border-border-default bg-card p-3">
                      <StateIcon
                        className={cn(
                          "size-5",
                          stateInfo.color,
                          (currentDeploy.state === "deploying" ||
                            currentDeploy.state === "building") &&
                            "animate-spin",
                        )}
                      />
                      <div className="flex-1">
                        <p
                          className={cn("text-sm font-medium", stateInfo.color)}
                        >
                          {((t.deploy as Record<string, unknown>)[
                            currentDeploy.state
                          ] as string) ?? stateInfo.label}
                        </p>
                        <p className="text-muted-foreground text-xs">
                          {currentDeploy.project_name} &rarr;{" "}
                          {PROVIDER_META[currentDeploy.provider]?.label ||
                            currentDeploy.provider}
                        </p>
                      </div>
                    </div>
                  );
                })()}

                {/* Deployment URL */}
                {currentDeploy.url && (
                  <div className="flex items-center gap-2 rounded-lg border border-success/30 bg-success/5 p-3">
                    <GlobeIcon className="size-4 shrink-0 text-success" />
                    <RoutedWebLink
                      href={currentDeploy.url}
                      openTargetSource="deployment"
                      className="flex-1 truncate text-xs font-medium text-success underline decoration-green-400 dark:text-success"
                    >
                      {currentDeploy.url}
                    </RoutedWebLink>
                    <button
                      onClick={handleCopyUrl}
                      className="shrink-0 rounded p-1 text-success hover:bg-success/10 dark:hover:bg-success"
                      title="Copy URL"
                    >
                      <CopyIcon className="size-3.5" />
                    </button>
                    <RoutedWebLink
                      href={currentDeploy.url}
                      openTargetSource="deployment"
                      className="shrink-0 rounded p-1 text-success hover:bg-success/10 dark:hover:bg-success"
                      title="Open in browser"
                    >
                      <ExternalLinkIcon className="size-3.5" />
                    </RoutedWebLink>
                  </div>
                )}

                {/* Error message */}
                {currentDeploy.error && (
                  <div className="text-destructive rounded-lg border border-destructive/30/60 bg-destructive/5 p-2 text-xs dark:border-destructive/60">
                    {currentDeploy.error}
                  </div>
                )}

                {/* Build logs */}
                <DeployLogViewer logs={currentDeploy.logs} />

                {/* Meta info */}
                {Object.keys(currentDeploy.meta).length > 0 && (
                  <div className="space-y-1">
                    <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
                      {t.deploy.details}
                    </p>
                    <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
                      {Object.entries(currentDeploy.meta)
                        .filter(([, v]) => v !== "" && v !== 0 && v !== null)
                        .slice(0, 8)
                        .map(([k, v]) => (
                          <div key={k} className="contents">
                            <span className="text-muted-foreground">
                              {k.replace(/_/g, " ")}:
                            </span>
                            <span className="font-mono truncate">
                              {String(v)}
                            </span>
                          </div>
                        ))}
                    </div>
                  </div>
                )}

                {/* Deploy again */}
                {(currentDeploy.state === "ready" ||
                  currentDeploy.state === "error") && (
                  <button
                    onClick={() => {
                      setView("detect");
                      setCurrentDeploy(null);
                    }}
                    className="text-primary hover:text-primary/80 w-full text-center text-xs underline"
                  >
                    {t.deploy.deployAgain}
                  </button>
                )}
              </>
            ) : (
              <div className="flex flex-col items-center gap-3 py-8 text-center">
                <div className="flex size-12 items-center justify-center rounded-lg bg-muted/50">
                  <CloudIcon className="text-muted-foreground size-6" />
                </div>
                <p className="text-muted-foreground text-xs">
                  {t.deploy.noActiveDeployment}
                </p>
              </div>
            )}
          </div>
        )}

        {/* History View */}
        {view === "history" && (
          <div className="space-y-2">
            {history.length === 0 ? (
              <div className="flex flex-col items-center gap-3 py-8 text-center">
                <div className="flex size-12 items-center justify-center rounded-lg bg-muted/50">
                  <ServerIcon className="text-muted-foreground size-6" />
                </div>
                <p className="text-muted-foreground text-xs">
                  {t.deploy.noDeployments}
                </p>
              </div>
            ) : (
              history.map((record) => (
                <HistoryItem key={record.id} record={record} />
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Deploy Button (floating, like QuestButton)
// ---------------------------------------------------------------------------

interface DeployButtonProps {
  onClick: () => void;
  isActive: boolean;
  className?: string;
}

export function DeployButton({
  onClick,
  isActive,
  className,
}: DeployButtonProps) {
  const { t } = useI18n();
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium shadow-[var(--shadow-xs)] transition-colors duration-base",
        isActive
          ? "bg-primary text-primary-foreground border-primary/60 shadow-primary/10"
          : "bg-background/80 text-muted-foreground hover:bg-muted/50 hover:text-foreground border-border-default",
        className,
      )}
    >
      <RocketIcon className="size-3.5" />
      {t.deploy.title}
    </button>
  );
}
