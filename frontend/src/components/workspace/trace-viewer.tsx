import {
  ActivityIcon,
  AlertCircleIcon,
  ArrowLeftIcon,
  ChevronRightIcon,
  CircleDotIcon,
  ClockIcon,
  Loader2Icon,
  RefreshCwIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { swallow } from "@/core/utils/log";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types (aligned with backend TraceContext.to_dict / trace router)
// ---------------------------------------------------------------------------

interface TraceSpan {
  span_id: string;
  name: string;
  parent_span_id?: string;
  duration_ms: number | null;
  status: string; // "ok" | "error"
  attributes?: Record<string, unknown>;
  events?: Array<{ name: string; timestamp: string; [k: string]: unknown }>;
}

interface Trace {
  trace_id: string;
  thread_id: string | null;
  agent_name: string | null;
  created_at: string;
  spans: TraceSpan[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "--";
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch (e) {
    swallow(e);
    return iso;
  }
}

function statusColor(status: string): string {
  switch (status) {
    case "ok":
    case "completed":
      return "text-success";
    case "error":
    case "failed":
      return "text-destructive";
    case "running":
      return "text-info";
    default:
      return "text-muted-foreground";
  }
}

function statusBg(status: string): string {
  switch (status) {
    case "ok":
    case "completed":
      return "bg-success";
    case "error":
    case "failed":
      return "bg-destructive";
    case "running":
      return "bg-info";
    default:
      return "bg-muted-foreground/30";
  }
}

function statusBarBg(status: string): string {
  switch (status) {
    case "ok":
    case "completed":
      return "bg-success/70";
    case "error":
    case "failed":
      return "bg-destructive/70";
    case "running":
      return "bg-info/70";
    default:
      return "bg-muted-foreground/20";
  }
}

/** Build a tree from flat spans. */
interface SpanTreeNode {
  span: TraceSpan;
  children: SpanTreeNode[];
  depth: number;
}

function buildSpanTree(spans: TraceSpan[]): SpanTreeNode[] {
  const byId = new Map<string, SpanTreeNode>();
  const roots: SpanTreeNode[] = [];

  for (const span of spans) {
    byId.set(span.span_id, { span, children: [], depth: 0 });
  }

  for (const span of spans) {
    const node = byId.get(span.span_id)!;
    if (span.parent_span_id && byId.has(span.parent_span_id)) {
      const parent = byId.get(span.parent_span_id)!;
      node.depth = parent.depth + 1;
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  return roots;
}

/** Flatten tree to ordered list for rendering. */
function flattenTree(nodes: SpanTreeNode[]): SpanTreeNode[] {
  const result: SpanTreeNode[] = [];
  function walk(list: SpanTreeNode[]) {
    for (const n of list) {
      result.push(n);
      walk(n.children);
    }
  }
  walk(nodes);
  return result;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function TraceListItem({
  trace,
  onSelect,
}: {
  trace: Trace;
  onSelect: (t: Trace) => void;
}) {
  const { t } = useI18n();
  const totalMs = useMemo(
    () => trace.spans.reduce((acc, s) => acc + (s.duration_ms ?? 0), 0),
    [trace.spans],
  );

  const hasError = trace.spans.some(
    (s) => s.status === "error" || s.status === "failed",
  );

  return (
    <button
      onClick={() => onSelect(trace)}
      className="hover:bg-muted/50 flex w-full items-center gap-2 border-b px-3 py-2 text-left transition-colors"
    >
      <div
        className={cn(
          "size-1.5 shrink-0 rounded-lg",
          hasError ? "bg-destructive" : "bg-success",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="text-foreground/90 truncate font-mono text-xs">
          {trace.agent_name || trace.trace_id.slice(0, 12)}
        </div>
        <div className="text-muted-foreground flex items-center gap-2 text-xs">
          <span>
            {trace.spans.length} {t.traces.spans}
          </span>
          <span>{formatDuration(totalMs)}</span>
        </div>
      </div>
      <div className="text-muted-foreground/60 shrink-0 text-xs">
        {formatTimestamp(trace.created_at)}
      </div>
      <ChevronRightIcon className="text-muted-foreground/40 size-3 shrink-0" />
    </button>
  );
}

function WaterfallRow({
  node,
  maxDurationMs,
  isSelected,
  onSelect,
}: {
  node: SpanTreeNode;
  maxDurationMs: number;
  isSelected: boolean;
  onSelect: (span: TraceSpan) => void;
}) {
  const barWidthPct =
    maxDurationMs > 0 && node.span.duration_ms != null
      ? Math.max(2, (node.span.duration_ms / maxDurationMs) * 100)
      : 0;

  return (
    <button
      onClick={() => onSelect(node.span)}
      className={cn(
        "flex w-full items-center gap-1 py-1 pr-2 text-left transition-colors",
        isSelected ? "bg-primary/10" : "hover:bg-muted/50",
      )}
      style={{ paddingLeft: `${8 + node.depth * 12}px` }}
    >
      <CircleDotIcon
        className={cn("size-2.5 shrink-0", statusColor(node.span.status))}
      />
      <span className="text-foreground/80 min-w-0 flex-1 truncate font-mono text-xs">
        {node.span.name}
      </span>
      <div className="flex w-20 shrink-0 items-center gap-1">
        <div className="relative h-3 flex-1 overflow-hidden rounded-lg bg-muted/50">
          <div
            className={cn(
              "absolute inset-y-0 left-0 rounded-lg",
              statusBarBg(node.span.status),
            )}
            style={{ width: `${barWidthPct}%` }}
          />
        </div>
        <span className="text-muted-foreground w-9 text-right font-mono text-xs">
          {formatDuration(node.span.duration_ms)}
        </span>
      </div>
    </button>
  );
}

function SpanDetail({
  span,
  onClose,
}: {
  span: TraceSpan;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="border-t">
      <div className="flex items-center justify-between border-b px-3 py-1.5">
        <span className="text-foreground/90 truncate font-mono text-xs font-semibold">
          {span.name}
        </span>
        <button
          onClick={onClose}
          aria-label={t.common.close}
          className="text-muted-foreground hover:text-foreground shrink-0"
        >
          <XIcon className="size-3" />
        </button>
      </div>

      <div className="space-y-2 p-3">
        {/* Status / Duration */}
        <div className="flex items-center gap-3 text-xs">
          <span className="flex items-center gap-1">
            <div className={cn("size-1.5 rounded-lg", statusBg(span.status))} />
            <span className="text-muted-foreground">{span.status}</span>
          </span>
          <span className="text-muted-foreground flex items-center gap-1">
            <ClockIcon className="size-2.5" />
            {formatDuration(span.duration_ms)}
          </span>
        </div>

        {/* Attributes */}
        {span.attributes && Object.keys(span.attributes).length > 0 && (
          <div>
            <div className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wider">
              {t.traces.attributes}
            </div>
            <div className="rounded-lg border">
              <div className="max-h-40 overflow-auto p-2 text-xs">
                <pre className="text-foreground/70 whitespace-pre-wrap break-all font-mono">
                  {JSON.stringify(span.attributes, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        )}

        {/* Events */}
        {span.events && span.events.length > 0 && (
          <div>
            <div className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wider">
              {t.traces.events(span.events.length)}
            </div>
            <div className="space-y-1">
              {span.events.map((ev, i) => (
                <div key={i} className="rounded-lg border p-2 text-xs">
                  <div className="text-foreground/80 font-mono font-medium">
                    {ev.name}
                  </div>
                  {ev.timestamp && (
                    <div className="text-muted-foreground/60">
                      {formatTimestamp(ev.timestamp)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function TraceViewer({ className }: { className?: string }) {
  const { t } = useI18n();
  const [traces, setTraces] = useState<Trace[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<Trace | null>(null);
  const [selectedSpan, setSelectedSpan] = useState<TraceSpan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- Fetch recent traces ------------------------------------------------

  const fetchTraces = useCallback(async () => {
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/trace/recent?limit=20`,
        { headers: authHeaders() },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { traces: Trace[] };
      setTraces(data.traces ?? []);
      setError(null);
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : "Failed to fetch traces");
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    setLoading(true);
    void fetchTraces().finally(() => setLoading(false));
  }, [fetchTraces]);

  // Poll every 5s when on the list view
  useEffect(() => {
    if (selectedTrace) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(() => {
      void fetchTraces();
    }, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [selectedTrace, fetchTraces]);

  // ---- Fetch single trace -------------------------------------------------

  const fetchTrace = useCallback(async (traceId: string) => {
    setLoading(true);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/trace/${traceId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as {
        success: boolean;
        trace?: Trace;
        error?: string;
      };
      if (data.success && data.trace) {
        setSelectedTrace(data.trace);
        setSelectedSpan(null);
      } else {
        setError(data.error ?? "Trace not found");
      }
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : "Failed to fetch trace");
    } finally {
      setLoading(false);
    }
  }, []);

  // ---- Derived waterfall data ---------------------------------------------

  const flatSpans = useMemo(() => {
    if (!selectedTrace) return [];
    const tree = buildSpanTree(selectedTrace.spans);
    return flattenTree(tree);
  }, [selectedTrace]);

  const maxDurationMs = useMemo(() => {
    if (!selectedTrace) return 0;
    return Math.max(...selectedTrace.spans.map((s) => s.duration_ms ?? 0), 1);
  }, [selectedTrace]);

  const totalMs = useMemo(() => {
    if (!selectedTrace) return 0;
    return selectedTrace.spans.reduce(
      (acc, s) => acc + (s.duration_ms ?? 0),
      0,
    );
  }, [selectedTrace]);

  // ---- Handlers -----------------------------------------------------------

  const handleSelectTrace = useCallback(
    (trace: Trace) => {
      void fetchTrace(trace.trace_id);
    },
    [fetchTrace],
  );

  const handleBack = useCallback(() => {
    setSelectedTrace(null);
    setSelectedSpan(null);
  }, []);

  // ---- Render -------------------------------------------------------------

  return (
    <div className={cn("flex flex-col", className)}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-1.5">
          {selectedTrace && (
            <button
              onClick={handleBack}
              className="text-muted-foreground hover:text-foreground mr-1 p-1 rounded-lg hover:bg-muted/60 transition-colors"
            >
              <ArrowLeftIcon className="size-3" />
            </button>
          )}
          <div className="flex size-6 items-center justify-center rounded-lg bg-primary/10">
            <ActivityIcon className="text-primary size-3.5" />
          </div>
          <span className="text-xs font-semibold">
            {selectedTrace ? t.traces.trace : t.traces.title}
          </span>
          {selectedTrace && (
            <span className="text-muted-foreground font-mono text-xs">
              {selectedTrace.trace_id.slice(0, 8)}
            </span>
          )}
        </div>
        <button
          onClick={() => {
            if (selectedTrace) {
              void fetchTrace(selectedTrace.trace_id);
            } else {
              setLoading(true);
              void fetchTraces().finally(() => setLoading(false));
            }
          }}
          className="text-muted-foreground hover:text-foreground p-1 rounded-lg hover:bg-muted/60 transition-colors"
          title={t.traces.refresh}
        >
          {loading ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <RefreshCwIcon className="size-3" />
          )}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mx-3 mb-2 flex items-center gap-1.5 rounded-lg border border-destructive/20 bg-destructive/5 px-2 py-1 text-xs text-destructive">
          <AlertCircleIcon className="size-3 shrink-0" />
          <span className="truncate">{error}</span>
        </div>
      )}

      {/* Content */}
      {!selectedTrace ? (
        /* ---- Trace List ---- */
        <div className="flex-1 overflow-auto">
          {traces.length === 0 && !loading && (
            <div className="text-muted-foreground/50 py-8 text-center text-xs">
              {t.traces.noTraces}
            </div>
          )}
          {traces.map((trace) => (
            <TraceListItem
              key={trace.trace_id}
              trace={trace}
              onSelect={handleSelectTrace}
            />
          ))}
        </div>
      ) : (
        /* ---- Waterfall + Detail ---- */
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Trace summary bar */}
          <div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs">
            <span className="text-muted-foreground">
              {selectedTrace.spans.length} {t.traces.spans}
            </span>
            <span className="text-muted-foreground">
              {formatDuration(totalMs)} {t.traces.total}
            </span>
            {selectedTrace.agent_name && (
              <span className="text-muted-foreground truncate">
                {selectedTrace.agent_name}
              </span>
            )}
          </div>

          {/* Waterfall rows */}
          <div className="flex-1 overflow-auto">
            {flatSpans.map((node) => (
              <WaterfallRow
                key={node.span.span_id}
                node={node}
                maxDurationMs={maxDurationMs}
                isSelected={selectedSpan?.span_id === node.span.span_id}
                onSelect={setSelectedSpan}
              />
            ))}
          </div>

          {/* Selected span detail */}
          {selectedSpan && (
            <SpanDetail
              span={selectedSpan}
              onClose={() => setSelectedSpan(null)}
            />
          )}
        </div>
      )}
    </div>
  );
}
