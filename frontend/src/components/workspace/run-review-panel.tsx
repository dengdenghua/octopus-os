import {
  ActivityIcon,
  AlertTriangleIcon,
  CheckCircle2Icon,
  ClockIcon,
  CoinsIcon,
  FileTextIcon,
  PauseIcon,
  PlayIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  WrenchIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { openSseStream } from "@/core/streaming/sse";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export interface RunReviewEvent {
  event_type?: string;
  ts?: string;
  task_id?: string;
  thread_id?: string;
  parent_tool_use_id?: string | null;
  tool_call_id?: string;
  tool_name?: string;
  sucker_id?: string;
  path?: string;
  action?: string;
  role_id?: string;
  is_error?: boolean;
  duration_ms?: number;
  tokens?: number;
  total_tokens?: number;
  usd?: number;
  cost_usd?: number;
  cost?: {
    tokens?: number;
    usd?: number;
  };
  usage?: {
    total_tokens?: number;
    tokens?: number;
  };
  [key: string]: unknown;
}

export interface RunReview {
  id: string;
  startedAt: number;
  lastAt: number;
  status: "running" | "done" | "error";
  eventCount: number;
  toolCount: number;
  approvalCount: number;
  fileCount: number;
  artifactCount: number;
  errorCount: number;
  tokens: number;
  usd: number;
  tools: Array<{ name: string; count: number }>;
  files: string[];
  risks: string[];
}

function eventTime(event: RunReviewEvent): number {
  if (event.ts) {
    const parsed = Date.parse(event.ts);
    if (Number.isFinite(parsed)) return parsed;
  }
  return Date.now();
}

function runIdForEvent(event: RunReviewEvent): string {
  const id =
    event.task_id ||
    event.thread_id ||
    event.parent_tool_use_id ||
    (typeof event.turn_id === "string" ? event.turn_id : "");
  return id || "unbound";
}

function eventKind(event: RunReviewEvent): string {
  return event.event_type ?? "event";
}

function toolName(event: RunReviewEvent): string | null {
  const name =
    event.tool_name ||
    event.sucker_id ||
    (typeof event.name === "string" ? event.name : "");
  if (name) return name;
  return /tool|step/i.test(eventKind(event)) ? eventKind(event) : null;
}

function numericValue(...values: unknown[]): number {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return 0;
}

function eventTokens(event: RunReviewEvent): number {
  return numericValue(
    event.tokens,
    event.total_tokens,
    event.cost?.tokens,
    event.usage?.total_tokens,
    event.usage?.tokens,
  );
}

function eventUsd(event: RunReviewEvent): number {
  return numericValue(event.usd, event.cost_usd, event.cost?.usd);
}

function isApprovalEvent(event: RunReviewEvent): boolean {
  return /approval|permission|requires_action/i.test(eventKind(event));
}

function isFileEvent(event: RunReviewEvent): boolean {
  return (
    Boolean(event.path) ||
    /file_op|write_file|read_file|patch/i.test(eventKind(event))
  );
}

function isArtifactEvent(event: RunReviewEvent): boolean {
  return /artifact|screenshot|browser_artifact/i.test(eventKind(event));
}

function isErrorEvent(event: RunReviewEvent): boolean {
  return (
    event.is_error === true ||
    /error|failed|immune_reject|reject/i.test(eventKind(event))
  );
}

function riskForEvent(event: RunReviewEvent): string | null {
  if (isErrorEvent(event)) return eventKind(event);
  if (isApprovalEvent(event)) return "approval_required";
  if (/budget|breaker/i.test(eventKind(event))) return eventKind(event);
  return null;
}

export function deriveRunReviews(events: RunReviewEvent[]): RunReview[] {
  const groups = new Map<string, RunReviewEvent[]>();
  for (const event of events) {
    const id = runIdForEvent(event);
    groups.set(id, [...(groups.get(id) ?? []), event]);
  }

  const reviews: RunReview[] = [];
  for (const [id, group] of groups) {
    const times = group.map(eventTime);
    const runningToolIds = new Set<string>();
    const closedToolIds = new Set<string>();
    const toolCounts = new Map<string, number>();
    const files = new Set<string>();
    const risks = new Set<string>();

    let approvalCount = 0;
    let fileCount = 0;
    let artifactCount = 0;
    let errorCount = 0;
    let tokens = 0;
    let usd = 0;

    for (const event of group) {
      const kind = eventKind(event);
      const name = toolName(event);
      if (name) toolCounts.set(name, (toolCounts.get(name) ?? 0) + 1);
      if (event.tool_call_id && /start/i.test(kind))
        runningToolIds.add(event.tool_call_id);
      if (event.tool_call_id && /end|done|finish/i.test(kind))
        closedToolIds.add(event.tool_call_id);
      if (isApprovalEvent(event)) approvalCount += 1;
      if (isFileEvent(event)) {
        fileCount += 1;
        if (event.path) files.add(event.path);
      }
      if (isArtifactEvent(event)) artifactCount += 1;
      if (isErrorEvent(event)) errorCount += 1;
      const risk = riskForEvent(event);
      if (risk) risks.add(risk);
      tokens += eventTokens(event);
      usd += eventUsd(event);
    }

    for (const id of closedToolIds) runningToolIds.delete(id);

    const status =
      errorCount > 0 ? "error" : runningToolIds.size > 0 ? "running" : "done";
    const tools = Array.from(toolCounts.entries())
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));

    reviews.push({
      id,
      startedAt: Math.min(...times),
      lastAt: Math.max(...times),
      status,
      eventCount: group.length,
      toolCount: tools.reduce((sum, item) => sum + item.count, 0),
      approvalCount,
      fileCount,
      artifactCount,
      errorCount,
      tokens,
      usd,
      tools,
      files: Array.from(files).slice(0, 6),
      risks: Array.from(risks).slice(0, 6),
    });
  }

  return reviews.sort((a, b) => b.lastAt - a.lastAt).slice(0, 20);
}

function formatTime(ms: number): string {
  try {
    return new Date(ms).toLocaleTimeString();
  } catch (e) {
    swallow(e);
    return "--";
  }
}

function formatDuration(start: number, end: number): string {
  const delta = Math.max(0, end - start);
  if (delta < 1000) return `${delta}ms`;
  if (delta < 60_000) return `${Math.round(delta / 1000)}s`;
  return `${Math.round(delta / 60_000)}m`;
}

function formatUsd(value: number): string {
  if (value <= 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

function StatusBadge({ status }: { status: RunReview["status"] }) {
  const { t } = useI18n();
  const label =
    status === "running"
      ? t.observabilityPage.runReviewStatusRunning
      : status === "error"
        ? t.observabilityPage.runReviewStatusError
        : t.observabilityPage.runReviewStatusDone;
  return (
    <Badge
      variant="outline"
      className={cn(
        "text-xs",
        status === "running" && "border-info/30 text-info",
        status === "error" && "border-destructive/30 text-destructive",
        status === "done" && "border-success/30 text-success",
      )}
    >
      {status === "running" ? (
        <RefreshCwIcon className="mr-1 size-3 animate-spin" />
      ) : status === "error" ? (
        <AlertTriangleIcon className="mr-1 size-3" />
      ) : (
        <CheckCircle2Icon className="mr-1 size-3" />
      )}
      {label}
    </Badge>
  );
}

function SummaryTile({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-border-default bg-muted/20 p-3">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </div>
      <div className="mt-1 font-mono text-xl font-semibold">{value}</div>
    </div>
  );
}

export function RunReviewPanel() {
  const { t } = useI18n();
  const [events, setEvents] = useState<RunReviewEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    return openSseStream({
      url: `${getBackendBaseURL()}/api/stream`,
      onOpen: () => setConnected(true),
      onReconnecting: () => setConnected(false),
      onEvent: (msg) => {
        if (paused) return;
        try {
          const event = JSON.parse(msg.data) as RunReviewEvent;
          setEvents((prev) => [...prev, event].slice(-600));
        } catch (e) {
          swallow(e);
        }
      },
    });
  }, [paused]);

  const reviews = useMemo(() => deriveRunReviews(events), [events]);
  const summary = useMemo(
    () => ({
      runs: reviews.length,
      running: reviews.filter((run) => run.status === "running").length,
      errors: reviews.reduce((sum, run) => sum + run.errorCount, 0),
      tokens: reviews.reduce((sum, run) => sum + run.tokens, 0),
      usd: reviews.reduce((sum, run) => sum + run.usd, 0),
    }),
    [reviews],
  );

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <ActivityIcon className="size-4 text-primary" />
                {t.observabilityPage.runReviewTitle}
              </CardTitle>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {t.observabilityPage.runReviewHint}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted-foreground">
                <span
                  className={cn(
                    "size-2 rounded-full",
                    connected ? "bg-success" : "bg-muted",
                  )}
                />
                {connected
                  ? t.observabilityPage.connected
                  : t.observabilityPage.idle}
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setPaused((value) => !value)}
              >
                {paused ? (
                  <PlayIcon className="mr-1.5 size-3" />
                ) : (
                  <PauseIcon className="mr-1.5 size-3" />
                )}
                {paused
                  ? t.observabilityPage.resume
                  : t.observabilityPage.pause}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEvents([])}>
                {t.observabilityPage.clear}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <SummaryTile
              icon={ActivityIcon}
              label={t.observabilityPage.runReviewRuns}
              value={String(summary.runs)}
            />
            <SummaryTile
              icon={RefreshCwIcon}
              label={t.observabilityPage.runReviewRunning}
              value={String(summary.running)}
            />
            <SummaryTile
              icon={AlertTriangleIcon}
              label={t.observabilityPage.runReviewErrors}
              value={String(summary.errors)}
            />
            <SummaryTile
              icon={CoinsIcon}
              label={t.observabilityPage.runReviewTokens}
              value={summary.tokens.toLocaleString()}
            />
            <SummaryTile
              icon={CoinsIcon}
              label={t.observabilityPage.runReviewCost}
              value={formatUsd(summary.usd)}
            />
          </div>
        </CardContent>
      </Card>

      {reviews.length === 0 ? (
        <Card>
          <CardContent className="py-12">
            <div className="flex flex-col items-center justify-center gap-2 text-center">
              <ActivityIcon className="size-7 text-muted-foreground" />
              <div className="text-sm font-medium">
                {t.observabilityPage.runReviewEmpty}
              </div>
              <div className="max-w-md text-xs leading-5 text-muted-foreground">
                {t.observabilityPage.runReviewEmptyHint}
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {reviews.map((run) => (
            <Card key={run.id} className="py-4">
              <CardHeader className="px-4 pb-2">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={run.status} />
                      <span className="truncate font-mono text-xs text-muted-foreground">
                        {run.id}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <ClockIcon className="size-3" />
                        {formatTime(run.startedAt)} ·{" "}
                        {formatDuration(run.startedAt, run.lastAt)}
                      </span>
                      <span>
                        {t.observabilityPage.runReviewEvents(run.eventCount)}
                      </span>
                      <span>
                        {t.observabilityPage.runReviewCostLine(
                          run.tokens,
                          run.usd,
                        )}
                      </span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Badge variant="outline" className="text-xs">
                      <WrenchIcon className="mr-1 size-3" />
                      {run.toolCount}
                    </Badge>
                    <Badge variant="outline" className="text-xs">
                      <ShieldCheckIcon className="mr-1 size-3" />
                      {run.approvalCount}
                    </Badge>
                    <Badge variant="outline" className="text-xs">
                      <FileTextIcon className="mr-1 size-3" />
                      {run.fileCount}
                    </Badge>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 px-4">
                {run.tools.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {t.observabilityPage.runReviewToolSummary}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {run.tools.slice(0, 8).map((tool) => (
                        <Badge
                          key={tool.name}
                          variant="outline"
                          className="font-mono text-xs"
                        >
                          {tool.name} · {tool.count}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

                {run.files.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {t.observabilityPage.runReviewFiles}
                    </div>
                    <div className="grid gap-1 md:grid-cols-2">
                      {run.files.map((file) => (
                        <div
                          key={file}
                          className="truncate rounded-md border border-border-default bg-muted/20 px-2 py-1 font-mono text-xs"
                          title={file}
                        >
                          {file}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {run.risks.length > 0 && (
                  <div className="rounded-lg border border-warning/25 bg-warning/5 px-3 py-2">
                    <div className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-warning">
                      <AlertTriangleIcon className="size-3" />
                      {t.observabilityPage.runReviewLearningSignals}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {run.risks.map((risk) => (
                        <Badge
                          key={risk}
                          variant="outline"
                          className="border-warning/30 text-xs text-warning"
                        >
                          {risk}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
