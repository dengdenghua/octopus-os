/* Implementation note. */
import {
  BrainCircuitIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleCheckBigIcon,
  CircleDotIcon,
  ClockIcon,
  CpuIcon,
  FlagIcon,
  ListTreeIcon,
  LoaderCircleIcon,
  SearchIcon,
  RefreshCwIcon,
  WrenchIcon,
  ZapIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { ErrorState, LoadingState, StatusBadge } from "@/components/ui/state";
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { authHeaders } from "@/core/auth/api";
import { useI18n } from "@/core/i18n/hooks";
import { stripTraceLabelPrefixes } from "./messages/trace-labels";

/* ── types ─────────────────────────────────────────── */

interface TimelineEvent {
  event_type: string;
  ts: string;
  task_id: string;
  arm_id?: string | null;
  skill_name?: string;
  strategy?: string;
  thought?: string;
  action?: string;
  observation?: string;
  iteration?: number;
  final_answer?: string;
  error?: string;
  tokens_in?: number;
  tokens_out?: number;
  usd?: number;
  latency_ms?: number;
  model?: string;
  provider?: string;
  job_id?: string;
  kind?: string;
  label?: string;
  status?: string;
  detail?: string;
  run_id?: string;
  name?: string;
  description?: string;
  text?: string;
  agent_seq?: number;
  agent_label?: string;
  stop_reason?: string;
  agents_started?: number;
}

interface TimelineResponse {
  task_ids: string[];
  timelines: Record<string, TimelineEvent[]>;
}

/* ── event icon/color mapping ──────────────────────── */

const EVENT_STYLE: Record<string, { icon: React.ReactNode; color: string }> = {
  step: { icon: <WrenchIcon className="size-3.5" />, color: "bg-info" },
  trajectory: {
    icon: <BrainCircuitIcon className="size-3.5" />,
    color: "bg-chart-1",
  },
  react_checkpoint: {
    icon: <CpuIcon className="size-3.5" />,
    color: "bg-warning",
  },
  immune: { icon: <ZapIcon className="size-3.5" />, color: "bg-destructive" },
  budget_squirt: {
    icon: <ClockIcon className="size-3.5" />,
    color: "bg-chart-7",
  },
  reflex_hit: {
    icon: <ZapIcon className="size-3.5" />,
    color: "bg-success",
  },
  "workflow/start": {
    icon: <FlagIcon className="size-3.5" />,
    color: "bg-chart-4",
  },
  "workflow/progress": {
    icon: <ListTreeIcon className="size-3.5" />,
    color: "bg-info",
  },
  "workflow/end": {
    icon: <CircleCheckBigIcon className="size-3.5" />,
    color: "bg-success",
  },
  "job/change": {
    icon: <LoaderCircleIcon className="size-3.5" />,
    color: "bg-chart-5",
  },
};

function eventStyle(type: string) {
  return (
    EVENT_STYLE[type] ?? {
      icon: <CircleDotIcon className="size-3.5" />,
      color: "bg-muted-foreground",
    }
  );
}

function eventSummary(ev: TimelineEvent): string | null {
  switch (ev.event_type) {
    case "job/change":
      return ev.label
        ? `${ev.label}${ev.detail ? ` — ${ev.detail}` : ""}`
        : `${ev.kind ?? "job"} · ${ev.status ?? ""}`;
    case "workflow/start":
      return ev.description || ev.name || null;
    case "workflow/progress":
      if (ev.kind === "agent_start") {
        return `agent ${ev.agent_seq ?? ""} ${ev.agent_label ?? ""} started`;
      }
      if (ev.kind === "agent_end") {
        return `agent ${ev.agent_seq ?? ""} ${ev.agent_label ?? ""}: ${
          ev.text ?? ""
        }`;
      }
      return ev.text || (ev.kind ? `${ev.kind} ${ev.name ?? ""}` : null);
    case "workflow/end":
      return `${ev.stop_reason ?? "ended"} · ${ev.agents_started ?? 0} agents`;
    default:
      return null;
  }
}

function jobStatusTone(status: string): "success" | "error" | "paused" | "running" {
  switch (status) {
    case "completed":
      return "success";
    case "failed":
    case "killed":
      return "error";
    case "stopping":
      return "paused";
    default:
      return "running";
  }
}

/* ── main component ────────────────────────────────── */

export function ExecutionTimeline() {
  const { t } = useI18n();
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(
        `${getBackendBaseURL()}/api/journal/timeline?limit=30`,
        {
          headers: authHeaders(),
        },
      );
      if (!r.ok) throw new Error(`${r.status}: ${r.statusText}`);
      setData(await r.json());
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : String(e));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (tid: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(tid)) {
        next.delete(tid);
      } else {
        next.add(tid);
      }
      return next;
    });
  };

  const taskIds = (data?.task_ids ?? []).filter(
    (tid) => !filter || tid.toLowerCase().includes(filter.toLowerCase()),
  );

  if (loading) {
    return <LoadingState title={t.executionTimeline.loading} />;
  }
  if (error) {
    return (
      <ErrorState
        title={t.executionTimeline.loadFailed}
        detail={error}
        actionLabel={t.executionTimeline.refresh}
        onAction={() => void load()}
      />
    );
  }
  if (!data) {
    return (
      <Empty className="min-h-[var(--panel-height-sm)]">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ClockIcon />
          </EmptyMedia>
          <EmptyTitle>{t.executionTimeline.empty}</EmptyTitle>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-8 rounded-lg pl-8 text-xs"
            placeholder={t.executionTimeline.searchPlaceholder}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            void load();
          }}
        >
          <RefreshCwIcon className="size-3.5" />
          {t.executionTimeline.refresh}
        </Button>
      </div>

      {taskIds.length === 0 ? (
        <Empty className="min-h-[var(--panel-height-sm)]">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <SearchIcon />
            </EmptyMedia>
            <EmptyTitle>
              {filter
                ? t.executionTimeline.noMatches
                : t.executionTimeline.empty}
            </EmptyTitle>
            {filter ? (
              <EmptyDescription>
                {t.executionTimeline.noMatchesDescription}
              </EmptyDescription>
            ) : null}
          </EmptyHeader>
        </Empty>
      ) : (
        taskIds.map((tid) => {
          const events = data.timelines[tid] ?? [];
          const isOpen = expanded.has(tid);
          const first = events[0];
          const last = events[events.length - 1];
          const strategy = events.find((e) => e.strategy)?.strategy;
          const totalTokens = events.reduce(
            (s, e) => s + (e.tokens_in ?? 0) + (e.tokens_out ?? 0),
            0,
          );
          const totalUsd = events.reduce((s, e) => s + (e.usd ?? 0), 0);

          return (
            <div
              key={tid}
              className="rounded-lg border border-border-default bg-background/60 overflow-hidden"
            >
              <button
                className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/30"
                onClick={() => toggle(tid)}
              >
                {isOpen ? (
                  <ChevronDownIcon className="size-4 text-muted-foreground" />
                ) : (
                  <ChevronRightIcon className="size-4 text-muted-foreground" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium font-mono">
                      {tid === "_no_task"
                        ? t.executionTimeline.noTask
                        : tid.slice(0, 12)}
                    </span>
                    {strategy && (
                      <StatusBadge tone="paused" className="h-5 text-xs">
                        {strategy}
                      </StatusBadge>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {events.length} {t.executionTimeline.events}
                    {totalTokens > 0 &&
                      ` · ${totalTokens.toLocaleString()} tokens`}
                    {totalUsd > 0 && ` · $${totalUsd.toFixed(4)}`}
                    {first && ` · ${new Date(first.ts).toLocaleTimeString()}`}
                    {last &&
                      first &&
                      last.ts !== first.ts &&
                      ` → ${new Date(last.ts).toLocaleTimeString()}`}
                  </div>
                </div>
              </button>

              {isOpen && (
                <div className="border-t border-border-subtle px-4 py-3">
                  <div className="relative ml-3 border-l-2 border-border-subtle pl-6 space-y-4">
                    {events.map((ev, i) => {
                      const style = eventStyle(ev.event_type);
                      return (
                        <div key={i} className="relative">
                          <div
                            className={`absolute -left-[31px] top-0.5 flex size-5 items-center justify-center rounded-full text-white ${style.color}`}
                          >
                            {style.icon}
                          </div>
                          <div className="text-xs">
                            <div className="flex items-center gap-2">
                              <span className="font-medium">
                                {ev.event_type}
                              </span>
                              {ev.skill_name && (
                                <span className="rounded bg-info/15 px-1.5 py-0.5 text-xs text-info">
                                  {ev.skill_name}
                                </span>
                              )}
                              {ev.iteration != null && (
                                <span className="text-xs text-muted-foreground">
                                  iter {ev.iteration}
                                </span>
                              )}
                              <span className="ml-auto text-xs text-muted-foreground">
                                {new Date(ev.ts).toLocaleTimeString()}
                              </span>
                            </div>
                            {(() => {
                              const summary = eventSummary(ev);
                              return summary ? (
                                <div className="mt-1 flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5 text-xs text-muted-foreground">
                                  {ev.event_type === "job/change" &&
                                    ev.status && (
                                      <StatusBadge
                                        tone={jobStatusTone(ev.status)}
                                        className="h-5 text-xs"
                                      >
                                        {ev.status}
                                      </StatusBadge>
                                    )}
                                  <span className="min-w-0 truncate">
                                    {summary}
                                  </span>
                                </div>
                              ) : null;
                            })()}
                            {ev.thought && (
                              <div className="mt-1 rounded-lg bg-muted/40 px-2.5 py-1.5 text-xs text-muted-foreground">
                                {stripTraceLabelPrefixes(ev.thought).slice(
                                  0,
                                  200,
                                )}
                              </div>
                            )}
                            {ev.action && (
                              <div className="mt-1 rounded-lg bg-info/10 px-2.5 py-1.5 text-xs font-mono text-info">
                                {stripTraceLabelPrefixes(ev.action).slice(
                                  0,
                                  150,
                                )}
                              </div>
                            )}
                            {ev.observation && (
                              <div className="mt-1 max-h-24 overflow-y-auto rounded-lg bg-muted/30 px-2.5 py-1.5 text-xs text-muted-foreground">
                                {stripTraceLabelPrefixes(ev.observation).slice(
                                  0,
                                  300,
                                )}
                              </div>
                            )}
                            {ev.final_answer && (
                              <div className="mt-1 rounded-lg bg-success/10 px-2.5 py-1.5 text-xs text-success">
                                {stripTraceLabelPrefixes(ev.final_answer).slice(
                                  0,
                                  200,
                                )}
                              </div>
                            )}
                            {ev.error && (
                              <div className="mt-1 rounded-lg bg-destructive/10 px-2.5 py-1.5 text-xs text-destructive">
                                {ev.error.slice(0, 200)}
                              </div>
                            )}
                            {(ev.tokens_in || ev.tokens_out || ev.model) && (
                              <div className="mt-1 flex gap-3 text-xs text-muted-foreground">
                                {ev.model && (
                                  <span>
                                    {ev.provider}/{ev.model}
                                  </span>
                                )}
                                {ev.tokens_in != null && (
                                  <span>{ev.tokens_in} in</span>
                                )}
                                {ev.tokens_out != null && (
                                  <span>{ev.tokens_out} out</span>
                                )}
                                {ev.latency_ms != null && (
                                  <span>{ev.latency_ms.toFixed(0)}ms</span>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
