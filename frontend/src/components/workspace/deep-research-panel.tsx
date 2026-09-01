import {
  CheckCircle2Icon,
  CircleAlertIcon,
  CircleIcon,
  ClipboardIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FileTextIcon,
  GlobeIcon,
  Loader2Icon,
  SearchIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  SquareIcon,
  TelescopeIcon,
  UsersIcon,
  XIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { toast } from "sonner";

import { copyTextToClipboard } from "@/core/clipboard";
import {
  fetchDeepResearchJob,
  type ResearchJob,
  type ResearchPrefetchLog,
  type ResearchStep,
} from "@/core/research/api";
import {
  cancelTask,
  fetchBatch,
  streamBatch,
  type BatchResult,
  type BatchStreamEvent,
  type SubagentRouteDecision,
  type TaskResult,
} from "@/core/parallel-agents/api";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface DeepResearchPanelProps {
  job: ResearchJob;
  loading?: boolean;
  error?: string | null;
  onClose?: () => void;
}

export function DeepResearchPanel({
  job,
  loading,
  error,
  onClose,
}: DeepResearchPanelProps) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [currentJob, setCurrentJob] = useState(job);
  const [batch, setBatch] = useState<BatchResult | null>(null);
  const [liveEvents, setLiveEvents] = useState<BatchStreamEvent[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [canceling, setCanceling] = useState(false);
  const [copied, setCopied] = useState(false);
  const roleCount = currentJob.roles.length;
  const sourceCount = currentJob.sources.filter((s) => s.enabled).length;
  const materialCount = currentJob.materials.length;
  const prefetchLogs = currentJob.prefetch_logs ?? [];
  const prefetchEvidenceCount = prefetchLogs.reduce(
    (total, item) => total + item.evidence_count,
    0,
  );
  const activeSteps = currentJob.steps.filter((s) => s.role_id !== "synthesis");
  const synthesisStep = currentJob.steps.find((s) => s.role_id === "synthesis");
  const cancellableTasks = useMemo(
    () =>
      (batch?.results ?? []).filter(
        (task) => task.status === "pending" || task.status === "running",
      ),
    [batch],
  );
  const canCancel = cancellableTasks.length > 0 && !canceling;

  const batchByTaskId = useMemo(() => {
    const map = new Map<string, TaskResult>();
    for (const result of batch?.results ?? []) {
      map.set(result.task_id, result);
    }
    return map;
  }, [batch]);

  const routeDecisionByTaskId = useMemo(() => {
    const map = new Map<string, SubagentRouteDecision>();
    for (const event of [...(batch?.event_log ?? []), ...liveEvents]) {
      if (!event.task_id) continue;
      const decision = routeDecisionFromPayload(event.payload);
      if (decision) map.set(event.task_id, decision);
    }
    return map;
  }, [batch?.event_log, liveEvents]);

  const totalTasks = batch?.total_tasks ?? activeSteps.length;
  const completedTasks = batch?.completed_tasks ?? 0;
  const settledTasks =
    completedTasks + (batch?.failed_tasks ?? 0) + (batch?.cancelled_tasks ?? 0);
  const progressPct =
    totalTasks > 0
      ? Math.max(
          8,
          Math.min(100, Math.round((settledTasks / totalTasks) * 100)),
        )
      : currentJob.status === "planned"
        ? 18
        : 42;

  const refreshBatch = useCallback(async () => {
    if (!currentJob.dispatch_batch_id) return;
    const next = await fetchBatch(currentJob.dispatch_batch_id);
    if (next) setBatch(next);
  }, [currentJob.dispatch_batch_id]);

  const refreshJob = useCallback(async () => {
    const next = await fetchDeepResearchJob(currentJob.job_id);
    if (next) setCurrentJob(next);
  }, [currentJob.job_id]);

  const handleCancel = useCallback(async () => {
    if (!cancellableTasks.length || canceling) return;
    if (
      !(await confirm({
        title: t.deepResearchPanel.cancelRunConfirmTitle,
        description: t.deepResearchPanel.cancelRunConfirmDescription(
          cancellableTasks.length,
        ),
        confirmLabel: t.deepResearchPanel.cancelRunConfirmLabel,
      }))
    )
      return;
    setCanceling(true);
    try {
      await Promise.all(
        cancellableTasks.map((task) => cancelTask(task.task_id)),
      );
      await refreshBatch();
      await refreshJob();
    } finally {
      setCanceling(false);
    }
  }, [cancellableTasks, canceling, confirm, refreshBatch, refreshJob, t]);

  const handleCopyReport = useCallback(async () => {
    if (!currentJob.final_report) return;
    try {
      await copyTextToClipboard(currentJob.final_report);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(t.deepResearchPanel.copyReportFailedToast);
    }
  }, [currentJob.final_report, t]);

  const handleDownloadReport = useCallback(() => {
    if (!currentJob.final_report) return;
    const blob = new Blob([currentJob.final_report], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${safeFilename(currentJob.topic || "cowork")}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }, [currentJob.final_report, currentJob.topic]);

  useEffect(() => {
    setCurrentJob(job);
    setBatch(null);
    setLiveEvents([]);
    setStreamError(null);
  }, [job]);

  useEffect(() => {
    void refreshBatch();
  }, [refreshBatch]);

  useEffect(() => {
    if (!currentJob.dispatch_batch_id) return;
    const stop = streamBatch(
      currentJob.dispatch_batch_id,
      {
        onTaskUpdate: (event) => {
          setLiveEvents((prev) => appendBatchEvent(prev, event));
          void refreshBatch();
        },
        onBatchComplete: (event) => {
          setLiveEvents((prev) => appendBatchEvent(prev, event));
          void refreshBatch();
          void refreshJob();
        },
        onError: (err) => setStreamError(err.message),
      },
      { maxRetries: 2, baseDelay: 1200 },
    );
    return stop;
  }, [currentJob.dispatch_batch_id, refreshBatch, refreshJob]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <TelescopeIcon className="size-4 text-primary" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">
              {t.deepResearchPanel.title}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {currentJob.topic}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {currentJob.dispatch_batch_id && !currentJob.final_report && (
            <button
              type="button"
              onClick={handleCancel}
              disabled={!canCancel}
              className={cn(
                "flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors",
                "hover:bg-muted/60 hover:text-foreground",
                "disabled:cursor-not-allowed disabled:opacity-40",
              )}
              title={t.deepResearchPanel.cancelAgentRunTitle}
            >
              {canceling ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <SquareIcon className="size-3.5" fill="currentColor" />
              )}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            aria-label={t.common.close}
            className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <XIcon className="size-3.5" />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && (
          <div className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </div>
        )}
        {streamError && (
          <div className="mb-3 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            {streamError}
          </div>
        )}

        <div className="grid grid-cols-3 gap-2">
          <Metric
            icon={<UsersIcon className="size-3.5" />}
            label={t.deepResearchPanel.metricRoles}
            value={roleCount}
          />
          <Metric
            icon={<GlobeIcon className="size-3.5" />}
            label={t.deepResearchPanel.metricSources}
            value={sourceCount}
          />
          <Metric
            icon={<FileTextIcon className="size-3.5" />}
            label={t.deepResearchPanel.metricMaterials}
            value={materialCount}
          />
        </div>

        <div className="mt-3 rounded-lg border border-border-default bg-muted/20 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="text-xs font-medium">
              {t.deepResearchPanel.agentBudget}
            </div>
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <SearchIcon className="size-3" />
              {t.deepResearchPanel.searchesCount(currentJob.max_searches)}
            </div>
          </div>
          {batch && (
            <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
              <span>{batch.status}</span>
              <span>
                {t.deepResearchPanel.batchProgress(
                  batch.completed_tasks,
                  batch.total_tasks,
                )}
                {(batch.failed_tasks > 0 || batch.cancelled_tasks > 0) &&
                  ` · ${t.deepResearchPanel.batchFailedCancelled(
                    batch.failed_tasks,
                    batch.cancelled_tasks,
                  )}`}
              </span>
            </div>
          )}
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full bg-foreground transition-all",
                (loading || batch?.status === "running") && "animate-pulse",
              )}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <div className="mt-2 truncate text-xs text-muted-foreground">
            {currentJob.dispatch_batch_id
              ? t.deepResearchPanel.batchIdLabel(currentJob.dispatch_batch_id)
              : currentJob.status}
          </div>
        </div>

        {liveEvents.length > 0 && (
          <div className="mt-4 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs font-medium">
                {t.deepResearchPanel.liveAgentStream}
              </div>
              <div className="text-xs text-muted-foreground">
                {t.deepResearchPanel.eventsCount(liveEvents.length)}
              </div>
            </div>
            <div className="space-y-1.5">
              {liveEvents.slice(-12).map((event, index) => (
                <LiveResearchEventRow
                  key={`${event.type}:${event.task_id ?? "batch"}:${event.phase ?? event.status}:${index}`}
                  event={event}
                />
              ))}
            </div>
          </div>
        )}

        {prefetchLogs.length > 0 && (
          <div className="mt-4 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs font-medium">
                {t.deepResearchPanel.prefetch}
              </div>
              <div className="text-xs text-muted-foreground">
                {t.deepResearchPanel.prefetchStats(
                  prefetchLogs.length,
                  prefetchEvidenceCount,
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              {prefetchLogs.slice(0, 8).map((item) => (
                <PrefetchLogRow key={item.id} item={item} />
              ))}
            </div>
          </div>
        )}

        <div className="mt-4 space-y-2">
          <div className="text-xs font-medium">
            {t.deepResearchPanel.executionSteps}
          </div>
          {activeSteps.map((step, index) => (
            <StepRow
              key={step.id}
              step={step}
              index={index}
              task={batchByTaskId.get(step.id)}
              routeDecision={
                routeDecisionByTaskId.get(step.id) ??
                step.route_decision ??
                undefined
              }
              running={loading && index === 0}
            />
          ))}
          {synthesisStep && (
            <StepRow
              step={synthesisStep}
              index={activeSteps.length}
              synthesis
            />
          )}
        </div>

        <div className="mt-4 space-y-2">
          <div className="text-xs font-medium">
            {t.deepResearchPanel.searchSources}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {currentJob.sources
              .filter((s) => s.enabled)
              .map((source) => (
                <span
                  key={source.id}
                  title={[
                    source.query_hint,
                    ...(source.query_templates ?? []),
                    ...(source.site_filters ?? []),
                  ]
                    .filter(Boolean)
                    .join(" | ")}
                  className="inline-flex items-center gap-1 rounded-md border border-border-default bg-background px-2 py-1 text-xs text-muted-foreground"
                >
                  <span>{source.label}</span>
                  <span className="text-xs text-muted-foreground/60">
                    {source.provider ?? source.kind}
                  </span>
                </span>
              ))}
          </div>
        </div>

        {currentJob.final_report ? (
          <>
            {currentJob.evidence.length > 0 && (
              <div className="mt-4 space-y-2">
                <div className="text-xs font-medium">
                  {t.deepResearchPanel.evidence}
                </div>
                <div className="space-y-1.5">
                  {currentJob.evidence.slice(0, 12).map((item) => (
                    <div
                      key={item.id}
                      className="rounded-lg border border-border-default bg-background/70 p-2"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 text-xs font-medium">
                          {item.url ? (
                            <RoutedWebLink
                              href={item.url}
                              openTargetSource="research-evidence"
                              className="inline-flex min-w-0 items-center gap-1 text-primary hover:underline"
                            >
                              <span className="truncate">
                                {item.title || item.url}
                              </span>
                              <ExternalLinkIcon className="size-3 shrink-0" />
                            </RoutedWebLink>
                          ) : (
                            <span>
                              {item.title ||
                                item.source_kind ||
                                t.deepResearchPanel.evidence}
                            </span>
                          )}
                        </div>
                        <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                          {item.stance} {Math.round(item.confidence * 100)}%
                        </span>
                      </div>
                      <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                        {item.claim || item.quote_or_summary}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-medium">
                  {t.deepResearchPanel.finalReport}
                </div>
                <div className="flex items-center gap-1.5">
                  {currentJob.memory_written_at && (
                    <span className="rounded-md bg-success/10 px-2 py-0.5 text-xs text-success">
                      {t.deepResearchPanel.savedToLeadMemory}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={handleCopyReport}
                    aria-label={
                      copied
                        ? t.deepResearchPanel.copied
                        : t.deepResearchPanel.copyMarkdown
                    }
                    className="flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                    title={
                      copied
                        ? t.deepResearchPanel.copied
                        : t.deepResearchPanel.copyMarkdown
                    }
                  >
                    {copied ? (
                      <CheckCircle2Icon className="size-3.5 text-success" />
                    ) : (
                      <ClipboardIcon className="size-3.5" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={handleDownloadReport}
                    aria-label={t.deepResearchPanel.downloadMarkdown}
                    className="flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                    title={t.deepResearchPanel.downloadMarkdown}
                  >
                    <DownloadIcon className="size-3.5" />
                  </button>
                </div>
              </div>
              <div className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs leading-relaxed">
                {currentJob.final_report}
              </div>
            </div>
          </>
        ) : batch?.aggregated_content ? (
          <div className="mt-4 space-y-2">
            <div className="text-xs font-medium">
              {t.deepResearchPanel.stageSummary}
            </div>
            <div className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-lg border border-border-default bg-background/70 p-3 text-xs leading-relaxed text-muted-foreground">
              {batch.aggregated_content}
            </div>
          </div>
        ) : null}
      </div>
      {confirmDialog}
    </div>
  );
}

function PrefetchLogRow({ item }: { item: ResearchPrefetchLog }) {
  const { t } = useI18n();
  const subject = item.query || item.url || item.source_label || item.provider;
  const statusClass =
    item.status === "failed"
      ? "border-destructive/30 bg-destructive/10 text-destructive"
      : item.status === "skipped"
        ? "border-border-default bg-muted/30 text-muted-foreground"
        : "border-success/25 bg-success/10 text-success";

  return (
    <div className="rounded-lg border border-border-default bg-background/70 p-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="shrink-0 rounded-md border border-border-default bg-muted/30 px-1.5 py-0.5 text-xs uppercase text-muted-foreground">
              {item.action}
            </span>
            <span className="truncate text-xs font-medium">{subject}</span>
            {item.url && (
              <RoutedWebLink
                href={item.url}
                openTargetSource="research-prefetch"
                className="shrink-0 text-primary hover:opacity-80"
                title={t.deepResearchPanel.openUrl}
              >
                <ExternalLinkIcon className="size-3" />
              </RoutedWebLink>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
            <span>{item.source_label || item.source_kind}</span>
            <span>{item.provider}</span>
            <span>{t.deepResearchPanel.hitsCount(item.result_count)}</span>
            <span>
              {t.deepResearchPanel.evidenceCount(item.evidence_count)}
            </span>
          </div>
        </div>
        <span
          className={cn(
            "shrink-0 rounded-md border px-1.5 py-0.5 text-xs",
            statusClass,
          )}
        >
          {item.status}
        </span>
      </div>
      {item.error && (
        <div className="mt-1 line-clamp-2 text-xs text-destructive">
          {item.error}
        </div>
      )}
    </div>
  );
}

function appendBatchEvent(
  events: BatchStreamEvent[],
  event: BatchStreamEvent,
): BatchStreamEvent[] {
  const next = [...events, event];
  return next.length > 80 ? next.slice(-80) : next;
}

export function routeDecisionFromPayload(
  payload: BatchStreamEvent["payload"],
): SubagentRouteDecision | null {
  const raw = payload?.subagent_route_decision;
  if (!raw || typeof raw !== "object") return null;
  const decision = raw as Record<string, unknown>;
  if (decision.schema !== "echo.subagent_route_decision.v1") return null;
  return {
    schema: "echo.subagent_route_decision.v1",
    role: stringValue(decision.role),
    action: stringValue(decision.action) || "allow",
    reason: stringValue(decision.reason),
    risk_level: stringValue(decision.risk_level) || "low",
    verdict: stringValue(decision.verdict) || "unknown",
    score: numberOrNull(decision.score),
    confidence:
      typeof decision.confidence === "number" ? decision.confidence : 0,
    evidence_item_ids: Array.isArray(decision.evidence_item_ids)
      ? decision.evidence_item_ids.map(stringValue).filter(Boolean)
      : [],
  };
}

function LiveResearchEventRow({ event }: { event: BatchStreamEvent }) {
  const { t } = useI18n();
  const isDone =
    event.status === "completed" ||
    event.status === "failed" ||
    event.status === "cancelled" ||
    event.status === "timed_out" ||
    event.status === "partial" ||
    event.type === "batch_complete";
  const isError =
    event.status === "failed" ||
    event.status === "cancelled" ||
    event.status === "timed_out" ||
    Boolean(event.error);
  const routeDecision = routeDecisionFromPayload(event.payload);
  const fallbackStatus =
    event.type === "batch_complete"
      ? t.deepResearchPanel.statusComplete
      : t.deepResearchPanel.statusUpdated;
  const title =
    event.message ||
    (event.type === "batch_complete"
      ? t.deepResearchPanel.batchEventTitle(event.status ?? fallbackStatus)
      : t.deepResearchPanel.subagentEventTitle(
          event.subagent_name ?? t.deepResearchPanel.subagentFallback,
          event.status ?? fallbackStatus,
        ));
  const detail = event.result_preview || event.error || event.description;

  return (
    <div
      className={cn(
        "rounded-lg border p-2 text-xs",
        isError
          ? "border-destructive/30 bg-destructive/10"
          : isDone
            ? "border-success/25 bg-success/10"
            : "border-primary/25 bg-primary/10",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5 font-medium">
            {!isDone && !isError && (
              <Loader2Icon className="size-3 animate-spin" />
            )}
            {isDone && !isError && (
              <CheckCircle2Icon className="size-3 text-success" />
            )}
            {isError && <CircleAlertIcon className="size-3 text-destructive" />}
            <span className="truncate">{title}</span>
          </div>
          {detail && (
            <div className="mt-1 line-clamp-3 break-words text-xs text-muted-foreground">
              {detail}
            </div>
          )}
          {routeDecision && (
            <RouteDecisionSummary decision={routeDecision} className="mt-1" />
          )}
        </div>
        <span className="shrink-0 rounded-md bg-background/70 px-1.5 py-0.5 text-xs text-muted-foreground">
          {event.phase ?? event.status ?? event.type}
        </span>
      </div>
    </div>
  );
}

function RouteDecisionSummary({
  decision,
  className,
}: {
  decision: SubagentRouteDecision;
  className?: string;
}) {
  const { t } = useI18n();
  const blocked = decision.action === "block";
  const warning = decision.action === "allow_with_warning";
  const label = blocked
    ? t.deepResearchPanel.routeBlocked
    : warning
      ? t.deepResearchPanel.routeWarning
      : t.deepResearchPanel.routeAllowed;
  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs",
            blocked
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : warning
                ? "border-warning/30 bg-warning/10 text-warning"
                : "border-success/25 bg-success/10 text-success",
          )}
        >
          {blocked || warning ? (
            <ShieldAlertIcon className="size-2.5" />
          ) : (
            <ShieldCheckIcon className="size-2.5" />
          )}
          {label}
        </span>
        <span className="text-xs text-muted-foreground">
          {decision.verdict} · {decision.risk_level}
        </span>
      </div>
      {decision.reason && (
        <div className="line-clamp-2 break-words text-xs text-muted-foreground">
          {decision.reason}
        </div>
      )}
    </div>
  );
}

function Metric({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-lg border border-border-default bg-background/60 px-2.5 py-2">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold leading-none">{value}</div>
    </div>
  );
}

function safeFilename(value: string): string {
  const cleaned = value
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .slice(0, 80)
    .replace(/^-|-$/g, "");
  return cleaned || "deep-research";
}

function StepRow({
  step,
  index,
  task,
  routeDecision,
  running,
  synthesis,
}: {
  step: ResearchStep;
  index: number;
  task?: TaskResult;
  routeDecision?: SubagentRouteDecision;
  running?: boolean;
  synthesis?: boolean;
}) {
  const { t } = useI18n();
  const status = task?.status ?? step.status;
  const isRunning = running || status === "running";
  const isCompleted = status === "completed";
  const isFailed =
    status === "failed" || status === "timed_out" || status === "cancelled";

  return (
    <div className="flex gap-2 rounded-lg border border-border-default bg-background/60 p-2.5">
      <div className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
        {isRunning ? (
          <Loader2Icon className="size-3 animate-spin" />
        ) : isCompleted ? (
          <CheckCircle2Icon className="size-3 text-success" />
        ) : isFailed ? (
          <CircleAlertIcon className="size-3 text-destructive" />
        ) : status === "pending" ? (
          <CircleIcon className="size-2.5 text-muted-foreground" />
        ) : (
          index + 1
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="line-clamp-2 text-xs font-medium">{step.title}</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
          <span>
            {synthesis ? t.deepResearchPanel.synthesisRoleLabel : step.role_id}
          </span>
          <span>{status}</span>
          {step.expected_searches > 0 && (
            <span>
              {t.deepResearchPanel.searchesCount(step.expected_searches)}
            </span>
          )}
          {task?.duration_seconds != null && (
            <span>{task.duration_seconds.toFixed(1)}s</span>
          )}
        </div>
        {task?.error && (
          <div className="mt-1 line-clamp-2 text-xs text-destructive">
            {task.error}
          </div>
        )}
        {routeDecision && (
          <RouteDecisionSummary decision={routeDecision} className="mt-1" />
        )}
        {task?.result && (
          <div className="mt-1 line-clamp-3 text-xs text-muted-foreground">
            {task.result}
          </div>
        )}
      </div>
    </div>
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
