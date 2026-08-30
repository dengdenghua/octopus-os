"use client";

import {
  CheckCircle2Icon,
  ChevronDownIcon,
  XCircleIcon,
  Loader2Icon,
  TerminalIcon,
  FileEditIcon,
  SearchIcon,
  GlobeIcon,
  EyeIcon,
  GitBranchIcon,
  BrainCircuitIcon,
  ShieldAlertIcon,
  RefreshCwIcon,
  ListChecksIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import type { Translations } from "@/core/i18n/locales/types";
import { cn } from "@/lib/utils";

import { emitAgentWorkbenchFocus } from "./agent-workbench-events";
import {
  agentRunBadgeClass,
  agentRunStatusLightPulseClass,
} from "./agent-run-status";
import { stripToolEnvelope } from "./messages/trace-labels";
import { getProcessTraceEvents } from "./process-trace-events";
import { SwarmRunOverview } from "./swarm-run-overview";
import { isSkillToolName } from "./tool-action-kind";

type TimelineT = Pick<
  Translations,
  "liveTools" | "liveToolTimeline" | "messageGrouping" | "todoList"
>;

const TOOL_ICONS: Record<string, { icon: React.ElementType; color: string }> = {
  bash: { icon: TerminalIcon, color: "text-chart-8" },
  exec_shell: { icon: TerminalIcon, color: "text-chart-8" },
  shell_command: { icon: TerminalIcon, color: "text-chart-8" },
  write_file: { icon: FileEditIcon, color: "text-chart-6" },
  write_text_file: { icon: FileEditIcon, color: "text-chart-6" },
  create_file: { icon: FileEditIcon, color: "text-chart-6" },
  edit_code: { icon: FileEditIcon, color: "text-chart-6" },
  edit_text_file: { icon: FileEditIcon, color: "text-chart-6" },
  str_replace: { icon: FileEditIcon, color: "text-chart-6" },
  read_file: { icon: EyeIcon, color: "text-chart-2" },
  read_text_file: { icon: EyeIcon, color: "text-chart-2" },
  fetch_url: { icon: GlobeIcon, color: "text-chart-7" },
  list_cwd: { icon: SearchIcon, color: "text-chart-1" },
  glob: { icon: SearchIcon, color: "text-chart-1" },
  grep: { icon: SearchIcon, color: "text-chart-1" },
  todo_write: { icon: ListChecksIcon, color: "text-chart-1" },
  call_agent: { icon: BrainCircuitIcon, color: "text-chart-6" },
  call_agent_parallel: { icon: BrainCircuitIcon, color: "text-chart-6" },
  bb_read: { icon: BrainCircuitIcon, color: "text-chart-6" },
  bb_write: { icon: BrainCircuitIcon, color: "text-chart-6" },
  bb_keys: { icon: BrainCircuitIcon, color: "text-chart-6" },
  "deep-research-swarm": { icon: BrainCircuitIcon, color: "text-chart-1" },
  "report-writing": { icon: FileEditIcon, color: "text-chart-6" },
  docx: { icon: FileEditIcon, color: "text-chart-6" },
  web_search: { icon: GlobeIcon, color: "text-chart-7" },
  apply_skill: { icon: BrainCircuitIcon, color: "text-chart-1" },
  list_learned_skills: { icon: BrainCircuitIcon, color: "text-chart-1" },
  learn_skill_from_text: { icon: BrainCircuitIcon, color: "text-chart-1" },
  planning: { icon: BrainCircuitIcon, color: "text-chart-6" },
  agent_thought: { icon: BrainCircuitIcon, color: "text-chart-1" },
  team_swarm: { icon: BrainCircuitIcon, color: "text-chart-6" },
  team_routing: { icon: BrainCircuitIcon, color: "text-chart-6" },
  git_status: { icon: GitBranchIcon, color: "text-chart-4" },
  git_commit: { icon: GitBranchIcon, color: "text-chart-4" },
  git_diff: { icon: GitBranchIcon, color: "text-chart-4" },
  stream_recovery: { icon: RefreshCwIcon, color: "text-chart-6" },
  model_gateway: { icon: BrainCircuitIcon, color: "text-chart-6" },
  model_reasoning: { icon: BrainCircuitIcon, color: "text-chart-1" },
};

function getToolLabels(t: TimelineT): Record<string, string> {
  return {
    bash: t.liveTools.terminal,
    exec_shell: t.liveTools.terminal,
    shell_command: t.liveTools.terminal,
    write_file: t.liveTools.writeFile,
    write_text_file: t.liveTools.writeFile,
    create_file: t.liveTools.writeFile,
    edit_code: t.liveTools.editFile,
    edit_text_file: t.liveTools.editFile,
    str_replace: t.liveTools.editFile,
    read_file: t.liveTools.readFile,
    read_text_file: t.liveTools.readFile,
    fetch_url: t.liveToolTimeline.browsingPage,
    list_cwd: t.liveTools.searchFiles,
    glob: t.liveTools.searchFiles,
    grep: t.liveTools.searchContent,
    todo_write: t.todoList.title,
    call_agent: t.liveToolTimeline.callSubAgentShort,
    call_agent_parallel: t.liveToolTimeline.subtaskAggregation,
    bb_read: t.liveToolTimeline.readBlackboardShort,
    bb_write: t.liveToolTimeline.writeBlackboardShort,
    bb_keys: t.liveToolTimeline.readBlackboardDirectory,
    "deep-research-swarm": t.messageGrouping.searchSources,
    "report-writing": t.messageGrouping.runAction,
    docx: t.messageGrouping.updateFile,
    web_search: t.liveTools.webSearch,
    apply_skill: t.liveToolTimeline.invokeSkillProcess,
    list_learned_skills: t.messageGrouping.runAction,
    learn_skill_from_text: t.liveToolTimeline.invokeSkillProcess,
    planning: t.liveToolTimeline.understandTask,
    agent_thought: t.liveToolTimeline.thinking,
    team_swarm: t.liveToolTimeline.subtaskAggregation,
    team_routing: t.liveToolTimeline.focusedDelegation,
    git_status: t.liveTools.gitStatus,
    git_commit: t.liveTools.gitCommit,
    git_diff: t.liveTools.gitDiff,
    stream_recovery: t.liveTools.streamRecovery,
    model_gateway: t.liveToolTimeline.connectRuntime,
    model_reasoning: t.liveToolTimeline.thinking,
  };
}

const SENSITIVE_DETAIL_KEY_RE =
  /^(?:token|secret|api[_-]?key|password|passwd|authorization|cookie|set-cookie|private[_-]?key)$/i;
const SENSITIVE_DETAIL_TEXT_RE =
  /(?:bearer\s+|\bsk-[a-z0-9_-]+\b|\b(?:ghp|github_pat|xox[baprs])-)[^\s,;)}\]]+/gi;

function sanitizePublicDetailText(value: string): string {
  return value
    .replace(SENSITIVE_DETAIL_TEXT_RE, "[redacted]")
    .replace(
      /((?:^|[\s,{[])['\"]?(?:token|secret|api[_-]?key|password|passwd|authorization|cookie|set-cookie|private[_-]?key)['\"]?\s*[:=]\s*)([^,;}\]\n]+)/gi,
      "$1[redacted]",
    );
}

function sanitizePublicDetailValue(value: unknown, depth = 0): unknown {
  if (depth > 6) return "[redacted]";
  if (typeof value === "string") return sanitizePublicDetailText(value);
  if (Array.isArray(value)) {
    return value.map((item) => sanitizePublicDetailValue(item, depth + 1));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        SENSITIVE_DETAIL_KEY_RE.test(key)
          ? "[redacted]"
          : sanitizePublicDetailValue(item, depth + 1),
      ]),
    );
  }
  return value;
}

export interface LiveToolEvent {
  id: string;
  name: string;
  status: "running" | "done" | "error" | "waiting_approval";
  /** Durable owning turn coordinates. `iteration` is an event-local
   * execution/ordering coordinate and must not be used to infer chat turns. */
  turnId?: string;
  turnIndex?: number;
  /** Why this failed, when the source event carried a reason. Kept separate
   * from `output` so a renderer can show the cause without stringifying and
   * truncating a whole payload. Only meaningful with status "error". */
  error?: string;
  startedAt: number;
  durationMs?: number;
  finishedAt?: number;
  iteration: number;
  agentId?: string;
  agentName?: string;
  input?: Record<string, unknown>;
  output?: unknown;
  parentToolUseId?: string;
  subAgentRole?: string;
  /** Cute codename assigned at spawn time (e.g. "Spark-3a4"). Set on
   * sub-agent-attributable events so the workbench can group them
   * under one tile. */
  subagentCodename?: string;
  /** Emoji avatar derived from role. Falls back to 🐙 for unknown
   * roles. */
  subagentAvatar?: string;
  /** Authoritative role display name from the backend built-in role
   * catalog (``BUILTIN_ROLES``), e.g. "Code Reviewer". Absent for
   * free-form role labels the catalog doesn't recognise, in which case
   * the frontend falls back to its own name mapping. */
  subagentRoleDisplayName?: string;
  /** Authoritative role responsibility blurb from ``BUILTIN_ROLES``.
   * Same fallback semantics as ``subagentRoleDisplayName``. */
  subagentRoleDescription?: string;
  /** Lifecycle marker. Synthesised events carry these instead of a
   * tool name, so panels can render the spawn moment + finish stats
   * without waiting for the first real tool call. */
  lifecycle?: "spawned" | "finished";
  /** Set on lifecycle="finished" events. */
  iterationCount?: number;
  /** Set on lifecycle="finished" events. */
  filesTouched?: string[];
  thought?: string;
  observation?: string;
  /** True when name/input/output mention a report-style deliverable
   * (see core/threads/report-deliverable.ts). Precomputed at mapping
   * time so per-frame render never stringifies payloads. */
  isReportLike?: boolean;
  /** When the tool is recognized by the catalog but its group is
   * excluded by config (e.g. web_search under enable_web_skills=false),
   * this carries {group, config_flag} so the UI can render a one-click
   * "enable" prompt instead of a bare error. */
  capabilityDisabled?: { group: string; config_flag: string };
}

function workflowEvents(events: LiveToolEvent[]): LiveToolEvent[] {
  return getProcessTraceEvents(events);
}

function getVisibleEvents(events: LiveToolEvent[]): LiveToolEvent[] {
  const topLevel = workflowEvents(events).filter((e) => !e.parentToolUseId);
  const runningEvents = topLevel
    .filter(
      (event) =>
        event.status === "running" || event.status === "waiting_approval",
    )
    .sort((a, b) => a.startedAt - b.startedAt);
  const recentFinishedEvents = topLevel
    .filter((event) => event.status !== "running")
    .sort(
      (a, b) => (b.finishedAt ?? b.startedAt) - (a.finishedAt ?? a.startedAt),
    )
    .slice(0, runningEvents.length > 0 ? 2 : 4);

  return [...runningEvents, ...recentFinishedEvents];
}

function getAllVisibleEvents(events: LiveToolEvent[]): LiveToolEvent[] {
  return workflowEvents(events)
    .filter((event) => !event.parentToolUseId)
    .sort((a, b) => a.startedAt - b.startedAt);
}

function getRunningEvents(events: LiveToolEvent[]): LiveToolEvent[] {
  return workflowEvents(events)
    .filter((event) => !event.parentToolUseId && event.status === "running")
    .sort((a, b) => a.startedAt - b.startedAt);
}

function getChildren(
  events: LiveToolEvent[],
  parentId: string,
): LiveToolEvent[] {
  return events
    .filter((e) => e.parentToolUseId === parentId)
    .sort((a, b) => a.startedAt - b.startedAt);
}

export function LiveToolTimeline({
  events,
  className,
  groupByAgent,
  runningOnly = false,
  showAll = false,
  compactDelegations = false,
}: {
  events: LiveToolEvent[];
  className?: string;
  groupByAgent?: boolean;
  runningOnly?: boolean;
  showAll?: boolean;
  compactDelegations?: boolean;
}) {
  const { t } = useI18n();
  const toolLabels = useMemo(() => getToolLabels(t), [t]);
  const visibleEvents = useMemo(
    () =>
      showAll
        ? getAllVisibleEvents(events)
        : runningOnly
          ? getRunningEvents(events)
          : getVisibleEvents(events),
    [events, runningOnly, showAll],
  );
  const displayEvents = useMemo(
    () =>
      compactDelegations
        ? compactDelegationEvents(visibleEvents)
        : visibleEvents.map((event) => ({ kind: "event" as const, event })),
    [compactDelegations, visibleEvents],
  );

  if (visibleEvents.length === 0) {
    return <SwarmRunOverview events={events} className={className} />;
  }

  if (groupByAgent) {
    return (
      <div className={className}>
        <SwarmRunOverview events={events} />
        <GroupedTimeline
          events={visibleEvents}
          allEvents={events}
          toolLabels={toolLabels}
          t={t}
        />
      </div>
    );
  }

  return (
    <div className={cn("space-y-1 py-1.5", className)}>
      <SwarmRunOverview events={events} />
      {displayEvents.map((item) =>
        item.kind === "event" ? (
          <ParentWithChildren
            key={item.event.id}
            event={item.event}
            allEvents={events}
            toolLabels={toolLabels}
            t={t}
          />
        ) : (
          <DelegationSummaryRow
            key={`delegation-summary:${item.target}`}
            events={item.events}
            target={item.target}
            t={t}
          />
        ),
      )}
    </div>
  );
}

type TimelineDisplayItem =
  | { kind: "event"; event: LiveToolEvent }
  | { kind: "delegation"; events: LiveToolEvent[]; target: string };

function compactDelegationEvents(
  events: LiveToolEvent[],
): TimelineDisplayItem[] {
  const items: TimelineDisplayItem[] = [];
  const buckets = new Map<
    string,
    Extract<TimelineDisplayItem, { kind: "delegation" }>
  >();
  for (const event of events) {
    if (event.lifecycle || !/agent|delegate|orchestrat/i.test(event.name)) {
      items.push({ kind: "event", event });
      continue;
    }
    const input = event.input ?? {};
    const target =
      ["agent_id", "subagent_id", "subagent_name", "role", "agent", "name"]
        .map((key) => input[key])
        .find(
          (value): value is string =>
            typeof value === "string" && Boolean(value.trim()),
        )
        ?.trim() ||
      event.subAgentRole ||
      event.agentName ||
      "other";
    const existing = buckets.get(target);
    if (existing) {
      existing.events.push(event);
      continue;
    }
    const summary = { kind: "delegation" as const, events: [event], target };
    buckets.set(target, summary);
    items.push(summary);
  }
  return items.flatMap((item) =>
    item.kind === "delegation" && item.events.length === 1
      ? [{ kind: "event" as const, event: item.events[0]! }]
      : [item],
  );
}

function DelegationSummaryRow({
  events,
  target,
  t,
}: {
  events: LiveToolEvent[];
  target: string;
  t: TimelineT;
}) {
  const running = events.some((event) => event.status === "running");
  const error = events.some((event) => event.status === "error");
  return (
    <div className="flex items-center gap-2 py-1.5 pl-2 text-xs text-muted-foreground">
      {running ? (
        <Loader2Icon className="size-3.5 shrink-0 animate-spin text-success" />
      ) : error ? (
        <XCircleIcon className="size-3.5 shrink-0 text-destructive" />
      ) : (
        <CheckCircle2Icon className="size-3.5 shrink-0 text-success" />
      )}
      <BrainCircuitIcon className="size-3.5 shrink-0 text-chart-6" />
      <span className="min-w-0 truncate text-sm font-medium text-foreground">
        {t.liveToolTimeline.callSubAgent(target)}
      </span>
      <span className="ml-auto shrink-0 tabular-nums">{events.length}×</span>
    </div>
  );
}

function ParentWithChildren({
  event,
  allEvents,
  toolLabels,
  t,
  showAgent,
}: {
  event: LiveToolEvent;
  allEvents: LiveToolEvent[];
  toolLabels: Record<string, string>;
  t: TimelineT;
  showAgent?: boolean;
}) {
  const children = useMemo(
    () => getChildren(allEvents, event.id),
    [allEvents, event.id],
  );
  if (children.length === 0) {
    return (
      <ToolEventRow
        event={event}
        toolLabels={toolLabels}
        t={t}
        showAgent={showAgent}
      />
    );
  }
  return (
    <div className="space-y-1">
      <ToolEventRow
        event={event}
        toolLabels={toolLabels}
        t={t}
        showAgent={showAgent}
      />
      <div className="ml-6 space-y-1 border-l border-primary/20 pl-2">
        {children.map((child) => (
          <ToolEventRow
            key={`${event.id}:${child.id}`}
            event={child}
            toolLabels={toolLabels}
            t={t}
            showAgent
            nested
          />
        ))}
      </div>
    </div>
  );
}

function formatInputSummary(
  input?: Record<string, unknown>,
): string | undefined {
  if (!input) return undefined;
  const priority = [
    "command",
    "path",
    "file_path",
    "cwd",
    "pattern",
    "url",
    "query",
    "task",
    "prompt",
    "description",
  ] as const;
  for (const key of priority) {
    const v = input[key];
    if (v !== undefined && v !== null && `${v}`.trim() !== "") {
      const text = typeof v === "string" ? v : JSON.stringify(v);
      const normalized = text.replace(/\s+/g, " ").trim();
      return normalized.length > 120
        ? `${normalized.slice(0, 120)}…`
        : normalized;
    }
  }
  const entries = Object.entries(input).filter(
    ([, v]) => v !== undefined && v !== null,
  );
  if (entries.length === 0) return undefined;
  return entries
    .slice(0, 2)
    .map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}: ${text.length > 60 ? text.slice(0, 60) + "…" : text}`;
    })
    .join(" · ");
}

function formatOutputSummary(output: unknown): string | undefined {
  if (output === undefined || output === null) return undefined;
  if (typeof output === "string") {
    const normalized = stripToolEnvelope(output).replace(/\s+/g, " ").trim();
    if (!normalized) return undefined;
    return normalized.length > 140
      ? `${normalized.slice(0, 140)}…`
      : normalized;
  }
  if (typeof output === "object" && !Array.isArray(output)) {
    const record = output as Record<string, unknown>;
    for (const key of [
      "error",
      "stderr",
      "stdout",
      "result",
      "message",
      "path",
      "content",
    ]) {
      const v = record[key];
      if (v !== undefined && v !== null && `${v}`.trim() !== "") {
        const text = typeof v === "string" ? v : JSON.stringify(v);
        const normalized = text.replace(/\s+/g, " ").trim();
        return `${key}: ${normalized.length > 120 ? normalized.slice(0, 120) + "…" : normalized}`;
      }
    }
  }
  const text = JSON.stringify(output);
  return text.length > 140 ? `${text.slice(0, 140)}…` : text;
}

function formatDetailBlock(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  const sanitized = sanitizePublicDetailValue(value);
  const text =
    typeof sanitized === "string"
      ? sanitized
      : JSON.stringify(sanitized, null, 2);
  const normalized = text.trim();
  if (!normalized) return undefined;
  return normalized.length > 4000
    ? `${normalized.slice(0, 4000)}\n...`
    : normalized;
}

function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) {
    return value;
  }
  try {
    return JSON.parse(trimmed);
  } catch (e) {
    swallow(e);
    return value;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  const parsed = parseMaybeJson(value);
  if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
    return parsed as Record<string, unknown>;
  }
  return null;
}

function countItems(value: unknown, keys: string[]): number | null {
  const parsed = parseMaybeJson(value);
  if (Array.isArray(parsed)) return parsed.length;
  const record = asRecord(parsed);
  if (!record) return null;
  for (const key of keys) {
    const item = record[key];
    if (Array.isArray(item)) return item.length;
    const nested = asRecord(item);
    if (nested) {
      const nestedCount = countItems(nested, keys);
      if (nestedCount !== null) return nestedCount;
    }
  }
  return null;
}

interface SearchResultItem {
  title: string;
  url?: string;
}

function extractSearchResults(value: unknown, max = 8): SearchResultItem[] {
  const parsed = parseMaybeJson(value);
  const list = Array.isArray(parsed)
    ? parsed
    : (() => {
        const record = asRecord(parsed);
        if (!record) return [];
        for (const key of ["results", "items", "sources", "urls", "pages"]) {
          const candidate = record[key];
          if (Array.isArray(candidate)) return candidate;
        }
        return [record];
      })();
  const results: SearchResultItem[] = [];
  const seen = new Set<string>();
  for (const item of list) {
    const record = asRecord(item);
    if (!record) continue;
    const title =
      typeof record.title === "string" && record.title.trim()
        ? record.title.trim()
        : typeof record.name === "string" && record.name.trim()
          ? record.name.trim()
          : typeof record.text === "string" && record.text.trim()
            ? record.text.trim()
            : "";
    const url =
      typeof record.url === "string" && record.url.trim()
        ? record.url.trim()
        : typeof record.link === "string" && record.link.trim()
          ? record.link.trim()
          : "";
    const label = title || url;
    if (!label) continue;
    const key = url || label;
    if (seen.has(key)) continue;
    seen.add(key);
    results.push({ title: compactMiddle(label, 96), url: url || undefined });
    if (results.length >= max) break;
  }
  return results;
}

function extractSourceLabels(value: unknown, max = 4): string[] {
  const parsed = parseMaybeJson(value);
  const out: string[] = [];
  const visit = (item: unknown) => {
    if (out.length >= max) return;
    const record = asRecord(item);
    if (!record) return;
    const title = typeof record.title === "string" ? record.title.trim() : "";
    const url =
      typeof record.url === "string"
        ? record.url.trim()
        : typeof record.link === "string"
          ? record.link.trim()
          : "";
    if (url) {
      try {
        out.push(new URL(url).hostname.replace(/^www\./, ""));
        return;
      } catch (e) {
        swallow(e);
        out.push(url.slice(0, 32));
        return;
      }
    }
    if (title) out.push(title.slice(0, 32));
  };
  if (Array.isArray(parsed)) {
    parsed.forEach(visit);
    return out;
  }
  const record = asRecord(parsed);
  if (!record) return out;
  for (const key of ["results", "items", "sources", "urls", "pages"]) {
    const list = record[key];
    if (Array.isArray(list)) {
      list.forEach(visit);
      if (out.length > 0) return out;
    }
  }
  visit(record);
  return out;
}

function researchLogText(
  event: LiveToolEvent,
  t: TimelineT,
): {
  label: string;
  detail?: string;
  sources?: string[];
} | null {
  if (event.name === "web_search") {
    const query =
      typeof event.input?.query === "string" ? event.input.query.trim() : "";
    const count =
      countItems(event.output, ["results", "items", "sources"]) ??
      (typeof event.input?.max_results === "number"
        ? event.input.max_results
        : null);
    if (event.status === "running") {
      return {
        label: t.liveToolTimeline.searchingWeb,
        detail: query ? t.liveToolTimeline.searchingQuery(query) : undefined,
      };
    }
    return {
      label: t.liveToolTimeline.searchedPages(count ?? undefined),
      detail: query
        ? t.liveToolTimeline.searchResultCovering(query)
        : t.liveToolTimeline.searchResultInContext,
      sources: extractSourceLabels(event.output),
    };
  }

  if (event.name === "fetch_url") {
    const url =
      typeof event.input?.url === "string" ? event.input.url.trim() : "";
    const source = url
      ? (() => {
          try {
            return new URL(url).hostname.replace(/^www\./, "");
          } catch (e) {
            swallow(e);
            return url;
          }
        })()
      : undefined;
    return {
      label:
        event.status === "running"
          ? t.liveToolTimeline.browsingPage
          : t.liveToolTimeline.browsedOnePage,
      detail: source
        ? t.liveToolTimeline.sourceFrom(source)
        : t.liveToolTimeline.pageOpenedAndExtracted,
      sources: source ? [source] : extractSourceLabels(event.output, 1),
    };
  }

  return null;
}

function stringInput(
  input: Record<string, unknown> | undefined,
  keys: string[],
): string {
  if (!input) return "";
  for (const key of keys) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number") return String(value);
  }
  return "";
}

function compactMiddle(text: string, max = 96): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const head = Math.floor((max - 1) * 0.62);
  const tail = max - 1 - head;
  return `${clean.slice(0, head)}…${clean.slice(-tail)}`;
}

function actionStatusLabel(
  event: LiveToolEvent,
  verb: (running: boolean) => string,
  target?: string,
): string {
  const label = verb(event.status === "running");
  return target ? `${label} ${target}` : label;
}

function elapsedInputMs(input?: Record<string, unknown>): number | undefined {
  const value = input?.elapsed_ms ?? input?.elapsedMs;
  if (typeof value === "number" && Number.isFinite(value))
    return Math.max(0, value);
  if (
    typeof value === "string" &&
    value.trim() &&
    !Number.isNaN(Number(value))
  ) {
    return Math.max(0, Number(value));
  }
  return undefined;
}

function elapsedSeconds(input?: Record<string, unknown>): number {
  return Math.max(0, Math.floor((elapsedInputMs(input) ?? 0) / 1000));
}

function swarmLogText(
  event: LiveToolEvent,
  t: TimelineT,
): {
  label: string;
  detail?: string;
} | null {
  if (event.name === "call_agent_parallel") {
    const specs = Array.isArray(event.input?.specs) ? event.input.specs : [];
    const roles = specs
      .map((spec) => asRecord(spec))
      .map((spec) => {
        const role = spec?.agent_id ?? spec?.role ?? spec?.name;
        return typeof role === "string" ? role : "";
      })
      .filter(Boolean)
      .slice(0, 5);
    const count = specs.length;
    return {
      label:
        event.status === "error"
          ? t.liveToolTimeline.parallelDispatchFailed(count || undefined)
          : event.status === "running"
            ? t.liveToolTimeline.parallelDispatching(count || undefined)
            : t.liveToolTimeline.parallelTasksReturned(count || undefined),
      detail:
        event.status === "error"
          ? undefined
          : roles.length > 0
            ? t.liveToolTimeline.rolesWithNextStep(roles.join(" / "))
            : t.liveToolTimeline.subtaskAggregation,
    };
  }

  if (event.name === "call_agent") {
    const role = stringInput(event.input, ["agent_id", "role", "name"]);
    return {
      label: role
        ? t.liveToolTimeline.callSubAgent(role)
        : t.liveToolTimeline.callSubAgentShort,
      detail: t.liveToolTimeline.focusedDelegation,
    };
  }

  if (event.name === "bb_write") {
    const key = stringInput(event.input, ["key"]);
    return {
      label: key
        ? t.liveToolTimeline.writeBlackboard(compactMiddle(key, 60))
        : t.liveToolTimeline.writeBlackboardShort,
      detail: t.liveToolTimeline.saveBlackboardFinding,
    };
  }

  if (event.name === "bb_read" || event.name === "bb_keys") {
    const key = stringInput(event.input, ["key"]);
    return {
      label:
        event.name === "bb_keys"
          ? t.liveToolTimeline.readBlackboardDirectory
          : key
            ? t.liveToolTimeline.readBlackboard(compactMiddle(key, 60))
            : t.liveToolTimeline.readBlackboardShort,
      detail: t.liveToolTimeline.pullParallelResults,
    };
  }

  return null;
}

function codeLogText(
  event: LiveToolEvent,
  t: TimelineT,
): {
  label: string;
  detail?: string;
} | null {
  if (event.name === "agent_thought") {
    return {
      label: t.liveToolTimeline.thoughtDetailLabel(
        event.iteration || undefined,
      ),
      detail: event.thought ?? t.liveToolTimeline.modelPublicReasoningFragment,
    };
  }

  if (event.name === "model_reasoning") {
    const outputRecord = asRecord(event.output);
    const content =
      typeof outputRecord?.content === "string"
        ? outputRecord.content
        : typeof event.output === "string"
          ? event.output
          : "";
    return {
      label: t.liveToolTimeline.modelPublicReasoningStream,
      detail: content
        ? compactMiddle(content, 220)
        : t.liveToolTimeline.modelOutputtingReasoning,
    };
  }

  if (isSkillToolName(event.name)) {
    const skillName =
      stringInput(event.input, ["skill", "skill_name", "name"]) || event.name;
    const request = stringInput(event.input, [
      "user_request",
      "request",
      "query",
      "task",
      "prompt",
    ]);
    return {
      label: actionStatusLabel(
        event,
        t.liveToolTimeline.applyingSkill,
        compactMiddle(skillName, 80),
      ),
      detail: request
        ? compactMiddle(request, 180)
        : t.liveToolTimeline.invokeSkillProcess,
    };
  }

  if (event.name === "planning") {
    const request = stringInput(event.input, [
      "task",
      "prompt",
      "description",
      "summary",
    ]);
    return {
      label: actionStatusLabel(event, t.liveToolTimeline.planningNextStep),
      detail: request
        ? compactMiddle(request, 180)
        : t.liveToolTimeline.modelOrganizingNextStep,
    };
  }

  if (event.name === "turn_request") {
    return {
      label: t.liveToolTimeline.understandTask,
      detail: t.liveToolTimeline.readingUserRequirements,
    };
  }

  if (event.name === "stream_connection") {
    return {
      label: t.liveToolTimeline.connectRuntime,
      detail: t.liveToolTimeline.establishingCallbackChannel,
    };
  }

  if (event.name === "response_stream") {
    return {
      label: t.liveToolTimeline.renderingModelOutput,
      detail: t.liveToolTimeline.incrementalTextReceived,
    };
  }

  if (event.name === "model_gateway") {
    const seconds = elapsedSeconds(event.input);
    if (event.status === "running") {
      return {
        label: actionStatusLabel(event, t.liveToolTimeline.planningNextStep),
        detail:
          seconds > 0
            ? t.liveToolTimeline.modelOrganizingNextStepWithWait(seconds)
            : t.liveToolTimeline.modelOrganizingNextStep,
      };
    }
    if (event.status === "error") {
      return {
        label: t.liveToolTimeline.modelOutputIncomplete,
        detail: t.liveToolTimeline.providerRejected,
      };
    }
    return {
      label: t.liveToolTimeline.modelOutputReceived,
      detail: t.liveToolTimeline.modelStartedReturning,
    };
  }

  const path = stringInput(event.input, [
    "path",
    "file_path",
    "target_path",
    "cwd",
  ]);
  const command = stringInput(event.input, ["command", "cmd", "script"]);
  const pattern = stringInput(event.input, ["pattern", "query"]);
  const description = stringInput(event.input, ["description", "summary"]);
  const lineStart = stringInput(event.input, [
    "line_start",
    "start_line",
    "start",
  ]);
  const lineEnd = stringInput(event.input, ["line_end", "end_line", "end"]);
  const lineSuffix =
    lineStart && lineEnd
      ? ` (lines ${lineStart}-${lineEnd})`
      : lineStart
        ? ` (line ${lineStart})`
        : "";

  if (event.name === "read_file" || event.name === "read_text_file") {
    return {
      label: actionStatusLabel(
        event,
        t.liveToolTimeline.readingFile,
        `${compactMiddle(path || "file")}${lineSuffix}`,
      ),
      detail: path ? undefined : t.liveToolTimeline.readFileToUnderstand,
    };
  }

  if (event.name === "list_cwd") {
    return {
      label: actionStatusLabel(
        event,
        t.liveToolTimeline.browsingDirectory,
        compactMiddle(path || "."),
      ),
      detail: t.liveToolTimeline.viewDirectoryStructure,
    };
  }

  if (event.name === "glob") {
    return {
      label: actionStatusLabel(
        event,
        t.liveToolTimeline.searchingFiles,
        compactMiddle(pattern || path || "*"),
      ),
      detail: path ? t.liveToolTimeline.scopePath(path) : undefined,
    };
  }

  if (event.name === "grep") {
    return {
      label: actionStatusLabel(
        event,
        t.liveToolTimeline.searchingText,
        compactMiddle(pattern || "pattern"),
      ),
      detail: path ? t.liveToolTimeline.scopePath(path) : undefined,
    };
  }

  if (
    event.name === "bash" ||
    event.name === "exec_shell" ||
    event.name === "shell_command"
  ) {
    return {
      label: actionStatusLabel(
        event,
        t.liveToolTimeline.runningCommand,
        compactMiddle(description || command || "command"),
      ),
      detail: command && description ? command : undefined,
    };
  }

  if (
    event.name === "write_file" ||
    event.name === "write_text_file" ||
    event.name === "create_file"
  ) {
    const creating = event.name === "create_file";
    return {
      label: actionStatusLabel(
        event,
        creating
          ? t.liveToolTimeline.creatingFile
          : t.liveToolTimeline.writingFile,
        compactMiddle(path || "file"),
      ),
      detail: t.liveToolTimeline.writeFileContent,
    };
  }

  if (
    event.name === "edit_code" ||
    event.name === "edit_text_file" ||
    event.name === "str_replace"
  ) {
    return {
      label: actionStatusLabel(
        event,
        t.liveToolTimeline.editingFile,
        compactMiddle(path || "file"),
      ),
      detail: pattern
        ? t.liveToolTimeline.matchPattern(compactMiddle(pattern))
        : undefined,
    };
  }

  if (event.name === "git_status") {
    return {
      label: actionStatusLabel(event, t.liveToolTimeline.readingGitStatus),
    };
  }
  if (event.name === "git_diff") {
    return {
      label: actionStatusLabel(event, t.liveToolTimeline.readingGitDiff),
    };
  }
  if (event.name === "git_commit") {
    return {
      label: actionStatusLabel(event, t.liveToolTimeline.committingGit),
    };
  }

  return null;
}

const CONTENT_PREVIEW_KEYS = [
  "content",
  "text",
  "code",
  "body",
  "patch",
  "diff",
] as const;

function normalizePreviewText(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  const lines = trimmed.split(/\r?\n/);
  const preview = lines.slice(0, 8).join("\n");
  const tooLong = lines.length > 8 || preview.length > 900;
  return `${preview.slice(0, 900)}${tooLong ? "\n..." : ""}`;
}

function getContentPreviewFromRecord(
  record?: Record<string, unknown>,
): string | undefined {
  if (!record) return undefined;
  for (const key of CONTENT_PREVIEW_KEYS) {
    const preview = normalizePreviewText(record[key]);
    if (preview) return preview;
  }
  return undefined;
}

function getContentPreview(event: LiveToolEvent): string | undefined {
  const inputPreview = getContentPreviewFromRecord(event.input);
  if (inputPreview) return inputPreview;
  if (
    typeof event.output === "object" &&
    event.output !== null &&
    !Array.isArray(event.output)
  ) {
    return getContentPreviewFromRecord(event.output as Record<string, unknown>);
  }
  return undefined;
}

function researchAdjustmentSummary(
  event: LiveToolEvent,
  t: TimelineT,
): string | undefined {
  if (event.name !== "web_search") return undefined;
  if (event.status === "running") {
    return t.liveToolTimeline.collectingEvidence;
  }
  if (event.status === "error") {
    return t.liveToolTimeline.searchRoundFailed;
  }
  const query = typeof event.input?.query === "string" ? event.input.query : "";
  if (/规模|增[长長]|CAGR|market size|forecast/i.test(query)) {
    return t.liveToolTimeline.marketSizeLeads;
  }
  if (
    /品牌|竞争|格局|company|companies|Oura|Eight Sleep|床垫|床墊/i.test(query)
  ) {
    return t.liveToolTimeline.competitionLeads;
  }
  if (/技术|technology|AI|sensor|wearable|产品|product/i.test(query)) {
    return t.liveToolTimeline.technologyLeads;
  }
  if (/消费|需求|痛点|睡眠经济|consumer|demand|pain/i.test(query)) {
    return t.liveToolTimeline.demandLeads;
  }
  return t.liveToolTimeline.roundResultsRead;
}

function statusText(event: LiveToolEvent, t: TimelineT): string {
  switch (event.status) {
    case "running":
      return t.liveToolTimeline.statusRunning;
    case "done":
      return t.liveToolTimeline.statusDone;
    case "error":
      return t.liveToolTimeline.statusFailed;
    case "waiting_approval":
      return t.liveToolTimeline.statusWaitingApproval;
    default:
      return "";
  }
}

function statusClassName(status: LiveToolEvent["status"]): string {
  return agentRunBadgeClass(status);
}

function detailTitle(
  t: TimelineT,
  key:
    | "input"
    | "thought"
    | "publicReasoning"
    | "result"
    | "observation"
    | "preview",
): string {
  return t.liveToolTimeline.detailTitles[key];
}

function InlineSummaryRow({
  label,
  value,
  tone = "muted",
}: {
  label: string;
  value: string;
  tone?: "muted" | "result";
}) {
  return (
    <div
      className={cn(
        "mt-1 ml-5 flex min-w-0 items-start gap-2 border-l pl-2 text-xs leading-4",
        tone === "result"
          ? "border-success/25 text-success/85"
          : "border-border-default text-muted-foreground/85",
      )}
    >
      <span className="shrink-0 rounded-sm bg-muted/60 px-1.5 py-0.5 font-medium text-muted-foreground">
        {label}
      </span>
      <span className="min-w-0 break-words line-clamp-2">{value}</span>
    </div>
  );
}

function ToolEventRow({
  event,
  toolLabels,
  t,
  showAgent,
  nested,
}: {
  event: LiveToolEvent;
  toolLabels: Record<string, string>;
  t: TimelineT;
  showAgent?: boolean;
  nested?: boolean;
}) {
  // Tool names carry suffixes the icon map doesn't (grep_text→grep,
  // glob_files→glob) — fall back to the normalized base before the generic
  // icon so search/file/shell steps stay visually distinct.
  const iconCfg = TOOL_ICONS[event.name] ??
    TOOL_ICONS[event.name.toLowerCase().replace(/_text/g, "")] ??
    TOOL_ICONS[event.name.toLowerCase().replace(/_files?$/, "")] ?? {
      icon: TerminalIcon,
      color: "text-muted-foreground",
    };
  const label = toolLabels[event.name] ?? t.liveTools.genericAction;
  const Icon = iconCfg.icon;
  const modelReasoningOutput = (() => {
    if (event.name !== "model_reasoning") return undefined;
    const outputRecord = asRecord(event.output);
    if (typeof outputRecord?.content === "string") return outputRecord.content;
    return typeof event.output === "string" ? event.output : undefined;
  })();
  const isSystemWorkLogEvent =
    event.name === "turn_request" ||
    event.name === "stream_connection" ||
    event.name === "response_stream" ||
    event.name === "model_gateway" ||
    event.name === "model_reasoning";
  const inputSummary = formatInputSummary(event.input);
  const outputSummary = formatOutputSummary(event.output);
  const inputDetail = isSystemWorkLogEvent
    ? undefined
    : formatDetailBlock(event.input);
  const outputDetail =
    event.name === "model_reasoning"
      ? modelReasoningOutput
      : isSystemWorkLogEvent
        ? undefined
        : formatDetailBlock(event.output);
  const thoughtDetail = formatDetailBlock(event.thought);
  const observationDetail = formatDetailBlock(event.observation);
  const contentPreview = isSystemWorkLogEvent
    ? undefined
    : getContentPreview(event);
  const researchSummary = researchAdjustmentSummary(event, t);
  const researchLog = researchLogText(event, t);
  const searchResults =
    event.name === "web_search" && event.status !== "running"
      ? extractSearchResults(event.output)
      : [];
  const swarmLog = researchLog ? null : swarmLogText(event, t);
  const codeLog = researchLog || swarmLog ? null : codeLogText(event, t);
  const [open, setOpen] = useState(
    event.status === "running" || event.status === "error",
  );
  useEffect(() => {
    if (event.status === "running" || event.status === "error") setOpen(true);
  }, [event.id, event.status]);
  const inlineInputSummary =
    inputSummary &&
    !researchLog &&
    !isSystemWorkLogEvent &&
    inputSummary !== codeLog?.detail
      ? inputSummary
      : undefined;
  const inlineOutputSummary =
    outputSummary &&
    !researchLog &&
    !isSystemWorkLogEvent &&
    event.status !== "running"
      ? outputSummary
      : undefined;
  const hasDetails = Boolean(
    inputDetail ||
    outputDetail ||
    thoughtDetail ||
    observationDetail ||
    contentPreview,
  );

  return (
    <div
      className={cn(
        "relative transition-all duration-base",
        nested ? "py-1 pl-2 text-xs" : "py-1.5 pl-2 text-xs",
        event.status === "error" ? "text-destructive" : "text-muted-foreground",
      )}
    >
      <div className="flex items-center gap-2">
        {event.status === "running" ? (
          <Loader2Icon className="size-3.5 animate-spin text-success shrink-0" />
        ) : event.status === "waiting_approval" ? (
          <ShieldAlertIcon className="size-3.5 text-warning shrink-0 animate-pulse" />
        ) : event.status === "error" ? (
          <XCircleIcon className="size-3.5 text-destructive shrink-0" />
        ) : (
          <CheckCircle2Icon className="size-3.5 text-success shrink-0" />
        )}

        <Icon className={cn("size-3.5 shrink-0", iconCfg.color)} />
        <span className="min-w-0 truncate text-sm font-medium text-foreground">
          {researchLog?.label ?? swarmLog?.label ?? codeLog?.label ?? label}
        </span>

        {showAgent && event.agentName && (
          <span className="text-muted-foreground text-xs">
            · {event.agentName}
          </span>
        )}

        {researchLog?.sources && researchLog.sources.length > 0 && (
          <span className="flex min-w-0 items-center gap-1">
            {researchLog.sources.slice(0, 3).map((source) => (
              <span
                key={source}
                className="max-w-20 truncate rounded-full border border-border-default bg-background/80 px-1.5 py-0.5 text-xs text-muted-foreground"
                title={source}
              >
                {source}
              </span>
            ))}
          </span>
        )}

        <span className="ml-auto flex items-center gap-1">
          <span
            className={cn(
              "rounded-full px-1.5 py-0.5 text-xs font-medium",
              agentRunStatusLightPulseClass(event.status) ?? "",
              statusClassName(event.status),
            )}
          >
            {statusText(event, t)}
          </span>

          {event.status === "done" &&
            event.durationMs != null &&
            event.durationMs >= 1 && (
              <span className="text-muted-foreground text-xs">
                {event.durationMs < 1000
                  ? `${event.durationMs}ms`
                  : `${(event.durationMs / 1000).toFixed(1)}s`}
              </span>
            )}

          {hasDetails && (
            <button
              type="button"
              className="flex size-5 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
              onClick={() => setOpen((value) => !value)}
              aria-label={
                open
                  ? t.liveToolTimeline.collapseToolDetails
                  : t.liveToolTimeline.expandToolDetails
              }
              title={
                open
                  ? t.liveToolTimeline.collapseToolDetails
                  : t.liveToolTimeline.expandToolDetails
              }
            >
              <ChevronDownIcon
                className={cn(
                  "size-3.5 transition-transform",
                  open ? "rotate-180" : "rotate-0",
                )}
              />
            </button>
          )}
        </span>
      </div>

      {researchLog?.detail && (
        <div className="mt-2 ml-5 border-l border-border-default pl-3 text-sm leading-6 text-foreground/80">
          {researchLog.detail}
        </div>
      )}

      {searchResults.length > 0 && (
        <SearchResultsInline results={searchResults} t={t} />
      )}

      {swarmLog?.detail && (
        <div className="mt-2 ml-5 border-l border-chart-6/25 pl-3 text-sm leading-6 text-foreground/80">
          {swarmLog.detail}
        </div>
      )}

      {codeLog?.detail && (
        <div className="mt-1 ml-5 break-words border-l border-border-default pl-3 text-sm leading-6 text-foreground/75">
          {codeLog.detail}
        </div>
      )}

      {thoughtDetail && event.name !== "agent_thought" && (
        <div className="mt-1 ml-5 border-l border-chart-1/25 pl-2 text-xs leading-5 text-chart-1">
          <span className="font-medium">{detailTitle(t, "thought")}: </span>
          {compactMiddle(thoughtDetail, 260)}
        </div>
      )}

      {observationDetail && (
        <div className="mt-1 ml-5 border-l border-success/25 pl-2 text-xs leading-5 text-success">
          <span className="font-medium">{detailTitle(t, "observation")}: </span>
          {compactMiddle(observationDetail, 260)}
        </div>
      )}

      {inlineInputSummary && (
        <InlineSummaryRow
          label={t.liveToolTimeline.detailTitles.input}
          value={inlineInputSummary}
        />
      )}

      {inlineOutputSummary && (
        <InlineSummaryRow
          label={t.liveToolTimeline.detailTitles.result}
          value={inlineOutputSummary}
          tone="result"
        />
      )}

      {researchSummary && !researchLog && (
        <div className="mt-1 ml-5 border-l border-chart-6/25 pl-2 text-xs leading-4 text-chart-6">
          {researchSummary}
        </div>
      )}

      {open && inputDetail && (
        <div className="mt-2 ml-5 overflow-hidden border-l border-border-default pl-2">
          <div className="pb-1 text-xs font-medium text-muted-foreground">
            {detailTitle(t, "input")}
          </div>
          <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/35 px-2 py-1.5 font-mono text-xs leading-4 text-foreground/80">
            {inputDetail}
          </pre>
        </div>
      )}

      {open && thoughtDetail && (
        <div className="mt-2 ml-5 overflow-hidden border-l border-chart-1/25 pl-2">
          <div className="pb-1 text-xs font-medium text-muted-foreground">
            {detailTitle(t, "thought")}
          </div>
          <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-md bg-chart-1/5 px-2 py-1.5 font-mono text-xs leading-4 text-foreground/80">
            {thoughtDetail}
          </pre>
        </div>
      )}

      {open &&
        outputDetail &&
        (event.status !== "running" || event.name === "model_reasoning") && (
          <div className="mt-2 ml-5 overflow-hidden border-l border-success/25 pl-2">
            <div className="pb-1 text-xs font-medium text-muted-foreground">
              {event.name === "model_reasoning"
                ? detailTitle(t, "publicReasoning")
                : detailTitle(t, "result")}
            </div>
            <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-md bg-success/5 px-2 py-1.5 font-mono text-xs leading-4 text-foreground/80">
              {outputDetail}
            </pre>
          </div>
        )}

      {open && observationDetail && (
        <div className="mt-2 ml-5 overflow-hidden border-l border-success/25 pl-2">
          <div className="pb-1 text-xs font-medium text-muted-foreground">
            {detailTitle(t, "observation")}
          </div>
          <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-md bg-success/5 px-2 py-1.5 font-mono text-xs leading-4 text-foreground/80">
            {observationDetail}
          </pre>
        </div>
      )}

      {open && contentPreview && (
        <div className="mt-2 ml-5 overflow-hidden border-l border-border-default pl-2">
          <div className="pb-1 text-xs font-medium text-muted-foreground">
            {detailTitle(t, "preview")}
          </div>
          <pre className="max-h-36 overflow-hidden whitespace-pre-wrap break-words py-1 font-mono text-xs leading-4 text-foreground/80">
            {contentPreview}
          </pre>
        </div>
      )}
    </div>
  );
}

function SearchResultsInline({
  results,
  t,
}: {
  results: SearchResultItem[];
  t: TimelineT;
}) {
  const [expanded, setExpanded] = useState(false);
  const collapsedCount = 5;
  const visibleResults = expanded ? results : results.slice(0, collapsedCount);
  const hiddenCount = Math.max(0, results.length - visibleResults.length);
  return (
    <div className="mt-2 ml-5 space-y-1 border-l border-border-default pl-3">
      {visibleResults.map((result, index) => (
        <div
          key={`${result.url ?? result.title}-${index}`}
          className="flex min-w-0 items-start gap-2 text-xs leading-5 text-muted-foreground"
        >
          <span className="w-4 shrink-0 text-right font-mono text-xs text-muted-foreground/70">
            {index + 1}
          </span>
          {result.url ? (
            <RoutedWebLink
              href={result.url}
              openTargetSource="tool-search-result"
              className="min-w-0 truncate text-foreground/75 underline-offset-2 hover:text-foreground hover:underline"
              title={result.title}
            >
              {result.title}
            </RoutedWebLink>
          ) : (
            <span
              className="min-w-0 truncate text-foreground/75"
              title={result.title}
            >
              {result.title}
            </span>
          )}
        </div>
      ))}
      {hiddenCount > 0 && (
        <button
          type="button"
          className="mt-1 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
          onClick={() => setExpanded(true)}
        >
          <ChevronDownIcon className="size-3" />
          {t.liveToolTimeline.showMoreResults(hiddenCount)}
        </button>
      )}
      {expanded && results.length > collapsedCount && (
        <button
          type="button"
          className="mt-1 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
          onClick={() => setExpanded(false)}
        >
          <ChevronDownIcon className="size-3 rotate-180" />
          {t.liveToolTimeline.collapseResults}
        </button>
      )}
    </div>
  );
}

function GroupedTimeline({
  events,
  allEvents,
  toolLabels,
  t,
  className,
}: {
  events: LiveToolEvent[];
  allEvents: LiveToolEvent[];
  toolLabels: Record<string, string>;
  t: TimelineT;
  className?: string;
}) {
  const grouped = useMemo(() => {
    const map = new Map<string, { name: string; events: LiveToolEvent[] }>();
    for (const e of events) {
      const key = timelineAgentGroupId(e) ?? "__main__";
      if (!map.has(key)) {
        map.set(key, {
          name: e.subagentCodename ?? e.agentName ?? e.subAgentRole ?? key,
          events: [],
        });
      }
      map.get(key)!.events.push(e);
    }
    return Array.from(map.entries());
  }, [events]);

  return (
    <div className={cn("space-y-3 py-1.5", className)}>
      {grouped.map(([agentId, group]) => (
        <div key={agentId}>
          {agentId !== "__main__" && (
            <button
              type="button"
              onClick={() => emitAgentWorkbenchFocus({ agentId })}
              className="mb-1 flex w-full items-center gap-1.5 rounded-md px-3 py-1 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors hover:bg-muted/45 hover:text-foreground"
            >
              <span
                className={cn(
                  "size-1.5 rounded-lg",
                  group.events.some((event) => event.status === "error")
                    ? "bg-destructive"
                    : group.events.some((event) => event.status === "running")
                      ? "animate-pulse bg-primary"
                      : "bg-success",
                )}
              />
              {group.name}
            </button>
          )}
          <div className="space-y-1">
            {group.events.map((item) => (
              <ParentWithChildren
                key={item.id}
                event={item}
                allEvents={allEvents}
                toolLabels={toolLabels}
                t={t}
                showAgent={agentId === "__main__" && !!item.agentName}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function timelineAgentGroupId(event: LiveToolEvent): string | undefined {
  const lane =
    (event.agentId && event.agentId !== event.subAgentRole
      ? event.agentId
      : undefined) ??
    event.subagentCodename ??
    event.agentId ??
    event.agentName ??
    event.subAgentRole;
  if (event.parentToolUseId && lane) {
    return `${event.parentToolUseId}:${lane}`;
  }
  return lane;
}
