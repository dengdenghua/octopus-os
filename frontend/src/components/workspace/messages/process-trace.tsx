import { useEffect, useMemo, useState } from "react";
import {
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  CircleIcon,
  FileTextIcon,
  GlobeIcon,
  Loader2Icon,
  MonitorIcon,
  NetworkIcon,
  PencilLineIcon,
  SquareActivityIcon,
  TerminalIcon,
  XCircleIcon,
} from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { DotProgress } from "@/components/workspace/swarm/dot-progress";
import { emitAgentWorkbenchFocus } from "../agent-workbench-events";

import {
  agentPhaseDisplayTitle,
  deriveAgentPhases,
  progressForPhases,
} from "../agent-phases";
import {
  type AgentRunState,
  agentRunAvatarAnimationClass,
  agentRunHue,
  agentRunIconClass,
  agentRunPanelClass,
  agentRunProgressBarClass,
} from "../agent-run-status";
import { LiveToolTimeline, type LiveToolEvent } from "../live-tool-timeline";
import { getProcessTraceEvents } from "../process-trace-events";
import { effectiveToolInput } from "./action-display";

import {
  type ProcessTraceMode,
  shouldOpenProcessTraceByDefault,
} from "./process-trace-visibility";

type MessageAgentRow = {
  id: string;
  name: string;
  label: string;
  status: AgentRunState;
  task: string;
  prompt?: string;
  role?: string;
  avatar?: string;
  currentTool?: string;
  eventCount: number;
  /** Failure cause for a lane that ended in error. Without it a failed lane
   * was only a red tint, leaving no way to tell a network drop from a round
   * cap from a refused route. */
  error?: string;
  summary?: string;
};

type TraceSectionKind = "thinking" | "action" | "verification";

type TraceSection = {
  kind: TraceSectionKind;
  title: string;
  summary: string;
  events: LiveToolEvent[];
  openByDefault: boolean;
};

type TraceDisplayItem =
  | { kind: "event"; event: LiveToolEvent }
  | {
      kind: "delegation-summary";
      events: LiveToolEvent[];
      target: string;
    };

export function ProcessTrace({
  events,
  hasAnswer,
  mode,
  live = false,
}: {
  events: LiveToolEvent[];
  hasAnswer?: boolean;
  mode: ProcessTraceMode;
  live?: boolean;
}) {
  const { t } = useI18n();
  const visibleEvents = useMemo(() => getProcessTraceEvents(events), [events]);
  const phaseState = useMemo(
    () => deriveAgentPhases(events, { hasAnswer }),
    [events, hasAnswer],
  );
  const parallelAgents = useMemo(
    () => deriveMessageAgentRows(events),
    [events],
  );
  const sections = useMemo(
    () =>
      buildTraceSections(
        mergeSectionEvents(visibleEvents, events),
        phaseState.currentPhase?.title ?? "",
        t,
      ),
    [events, visibleEvents, phaseState.currentPhase?.title, t],
  );
  const [open, setOpen] = useState(
    live || shouldOpenProcessTraceByDefault(visibleEvents, hasAnswer, mode),
  );
  const [rawDetailsOpen, setRawDetailsOpen] = useState(false);
  const shouldOpen = shouldOpenProcessTraceByDefault(
    visibleEvents,
    hasAnswer,
    mode,
  );
  const doneCount = useMemo(
    () => visibleEvents.filter((e) => e.status === "done").length,
    [visibleEvents],
  );
  const totalCount = visibleEvents.length;
  const progress = phaseState.currentPhase
    ? progressForPhases(phaseState.phases, phaseState.currentPhase)
    : null;
  const showAgents = parallelAgents.length > 0;
  const completedAgentCount = parallelAgents.filter(
    (agent) => agent.status === "done",
  ).length;
  const failedAgentCount = parallelAgents.filter(
    (agent) => agent.status === "error",
  ).length;
  const showProcessBody = open;
  const hasSectionCards = sections.length > 0;

  useEffect(() => {
    setOpen(shouldOpen);
  }, [shouldOpen]);

  useEffect(() => {
    if (!open) setRawDetailsOpen(false);
  }, [open]);

  return (
    <div
      className={cn(
        "px-1 py-1.5",
        live
          ? "mb-3 border-primary/35"
          : "mb-2 border-border-default text-muted-foreground",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 py-1 text-left transition-colors hover:text-foreground"
      >
        {showAgents ? (
          <NetworkIcon className="size-4 shrink-0 text-muted-foreground" />
        ) : (
          <CircleIcon className="size-4 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
          {showAgents ? t.message.agentCluster : t.message.thinkingProcess}
        </span>
        <span className="shrink-0 text-xs text-muted-foreground">
          {showAgents
            ? t.message.agentProgressSummary(
                parallelAgents.length,
                completedAgentCount,
                failedAgentCount,
              )
            : progress
              ? `${progress.current}/${progress.total}`
              : `${doneCount}/${totalCount}`}
        </span>
        <ChevronDownIcon
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            open ? "rotate-180" : "rotate-0",
          )}
        />
      </button>
      {showProcessBody && (
        <div className="mt-2 space-y-3">
          {showAgents ? (
            <AgentClusterCard
              agents={parallelAgents.slice(0, open ? 12 : 4)}
              statusLabels={{
                running: t.message.statusViewing,
                waiting: t.message.statusWaiting,
                done: t.message.statusCompleted,
                error: t.message.statusError,
                pending: t.message.statusWaiting,
              }}
            />
          ) : hasSectionCards ? (
            sections.map((section) => (
              <TraceSectionCard key={section.kind} section={section} />
            ))
          ) : (
            phaseState.phases.slice(0, open ? 7 : 3).map((phase) => (
              <div
                key={phase.id}
                className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm"
              >
                {phase.status === "running" ? (
                  <Loader2Icon className="size-3.5 shrink-0 animate-spin text-success" />
                ) : phase.status === "waiting_approval" ? (
                  <CircleIcon className="size-3.5 shrink-0 text-warning" />
                ) : phase.status === "done" ? (
                  <CheckCircle2Icon className="size-3.5 shrink-0 text-success" />
                ) : (
                  <CircleIcon className="size-3.5 shrink-0 text-muted-foreground/45" />
                )}
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate",
                    phase.status === "pending"
                      ? "text-muted-foreground"
                      : "text-foreground",
                  )}
                  title={phase.title}
                >
                  {agentPhaseDisplayTitle(phase, t.agentPhases)}
                </span>
              </div>
            ))
          )}
        </div>
      )}
      {open && visibleEvents.length > 0 && (
        <div className="mt-2 border-t border-border-subtle pt-2">
          <button
            type="button"
            onClick={() => setRawDetailsOpen((value) => !value)}
            className="flex w-full items-center gap-2 rounded-md px-1 py-1 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/35 hover:text-foreground"
            aria-expanded={rawDetailsOpen}
          >
            <ChevronDownIcon
              className={cn(
                "size-3.5 shrink-0 transition-transform",
                rawDetailsOpen ? "rotate-180" : "-rotate-90",
              )}
            />
            <span className="font-medium">{t.message.processDetails}</span>
            <span className="ml-auto tabular-nums">
              {t.message.processRecords(visibleEvents.length)}
            </span>
          </button>
          {rawDetailsOpen && (
            <div className="pt-2">
              <LiveToolTimeline
                events={visibleEvents}
                showAll
                compactDelegations
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AgentClusterCard({
  agents,
  statusLabels,
}: {
  agents: MessageAgentRow[];
  statusLabels: Record<MessageAgentRow["status"], string>;
}) {
  return (
    <div className="px-1 py-1">
      <div className="space-y-2">
        {agents.map((agent) => (
          <AgentClusterRow
            key={agent.id}
            agent={agent}
            statusLabel={statusLabels[agent.status]}
          />
        ))}
      </div>
    </div>
  );
}

function AgentClusterRow({
  agent,
  statusLabel,
}: {
  agent: MessageAgentRow;
  statusLabel: string;
}) {
  const expanded = agent.status === "error";
  const progress = agentProgress(agent);
  const progressHue = agentRunHue(agent.status);
  return (
    <div className="group/agent-row relative">
      <button
        type="button"
        onClick={() =>
          emitAgentWorkbenchFocus({
            agentId: agent.id,
            tab: "agent",
            view: "screen",
          })
        }
        aria-label={`${agent.name} · ${statusLabel}`}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm transition-colors hover:bg-muted/45"
      >
        <span
          className={cn(
            "flex size-6 shrink-0 items-center justify-center bg-transparent",
            agentRunPanelClass(agent.status),
          )}
        >
          {agent.avatar ? (
            <span className="text-base leading-none" aria-hidden="true">
              {agent.avatar}
            </span>
          ) : (
            <BotIcon
              className={cn("size-4", agentRunIconClass(agent.status))}
            />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
              {agent.label}
            </span>
            <span className="truncate font-medium text-foreground">
              {agent.name}
            </span>
            {agent.role &&
              agent.role.trim().toLowerCase() !==
                agent.name.trim().toLowerCase() && (
                <span className="hidden truncate text-xs text-muted-foreground sm:inline">
                  {agent.role}
                </span>
              )}
            <DotProgress
              progress={progress}
              hue={progressHue}
              cols={12}
              rows={2}
              className={cn(
                "ml-1 shrink-0",
                agentRunAvatarAnimationClass(agent.status),
              )}
            />
            <span
              className={cn(
                "ml-auto shrink-0 text-xs",
                agent.status === "error"
                  ? "text-destructive"
                  : agent.status === "done"
                    ? "text-success"
                    : "text-muted-foreground",
              )}
            >
              {statusLabel}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={cn(
                "min-w-0 flex-1 truncate text-xs",
                // Show the cause in place of the task once a lane fails: the
                // task was already stated at dispatch, while the reason is the
                // only thing that tells the user what to do next.
                agent.error ? "text-destructive" : "text-muted-foreground",
              )}
              title={agent.error ?? undefined}
            >
              {expanded && agent.error
                ? agent.task
                : (agent.error ?? agent.task)}
            </span>
          </div>
          {agent.error ? (
            <div
              className={cn(
                "mt-2 whitespace-pre-wrap break-words rounded-md bg-muted/35 px-3 py-2 text-xs leading-5",
                agent.error ? "text-destructive" : "text-foreground/80",
              )}
            >
              {agent.error}
            </div>
          ) : null}
        </div>
      </button>
    </div>
  );
}

function agentProgress(agent: MessageAgentRow): number {
  if (agent.status === "done" || agent.status === "error") return 1;
  if (agent.status === "pending") return 0.08;
  return Math.max(0.18, Math.min(0.92, 0.28 + agent.eventCount * 0.08));
}

function TraceSectionCard({ section }: { section: TraceSection }) {
  const [open, setOpen] = useState(section.openByDefault);
  const Icon =
    section.kind === "thinking"
      ? PencilLineIcon
      : section.kind === "action"
        ? NetworkIcon
        : SquareActivityIcon;
  const status = section.events.some((event) => event.status === "error")
    ? "error"
    : section.events.some((event) => event.status === "waiting_approval")
      ? "waiting"
      : section.events.some((event) => event.status === "running")
        ? "running"
      : "done";
  const displayItems = useMemo(
    () => compactTraceEvents(section.events),
    [section.events],
  );

  return (
    <div className="px-1 py-1.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 text-left"
      >
        {status === "running" ? (
          <Loader2Icon className="size-4 shrink-0 animate-spin text-success" />
        ) : status === "waiting" ? (
          <CircleIcon className="size-4 shrink-0 text-warning" />
        ) : status === "error" ? (
          <XCircleIcon className="size-4 shrink-0 text-destructive" />
        ) : (
          <CheckCircle2Icon className="size-4 shrink-0 text-success" />
        )}
        <Icon className="size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-foreground">
              {section.title}
            </span>
            <span className="ml-auto text-xs text-muted-foreground">
              {section.summary}
            </span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full transition-all",
                agentRunProgressBarClass(status),
              )}
              style={{ width: `${section.events.length > 0 ? 100 : 0}%` }}
            />
          </div>
        </div>
        <ChevronDownIcon
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div className="mt-2 space-y-1.5 pl-1">
          {displayItems.map((item) => (
            <TraceDisplayLine key={traceDisplayItemKey(item)} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

function traceDisplayItemKey(item: TraceDisplayItem): string {
  if (item.kind === "event") return item.event.id;
  return `delegation-summary:${item.target}`;
}

function compactTraceEvents(events: LiveToolEvent[]): TraceDisplayItem[] {
  const items: TraceDisplayItem[] = [];
  const delegationBuckets = new Map<
    string,
    Extract<TraceDisplayItem, { kind: "delegation-summary" }>
  >();

  for (const event of events) {
    if (!isDelegationEvent(event)) {
      items.push({ kind: "event", event });
      continue;
    }

    const target = delegationTarget(event);
    const existing = delegationBuckets.get(target);
    if (existing) {
      existing.events.push(event);
      continue;
    }

    const summary = { kind: "delegation-summary" as const, events: [event], target };
    delegationBuckets.set(target, summary);
    items.push(summary);
  }

  return items.flatMap((item) => {
    if (item.kind === "event" || item.events.length > 1) return [item];
    return [{ kind: "event" as const, event: item.events[0]! }];
  });
}

function isDelegationEvent(event: LiveToolEvent): boolean {
  if (event.lifecycle) return false;
  return /agent|delegate|orchestrat/i.test(event.name);
}

function delegationTarget(event: LiveToolEvent): string {
  const input = effectiveToolInput(event.input);
  return (
    firstString(input, [
      "agent_id",
      "subagent_id",
      "subagent_name",
      "role",
      "agent",
      "name",
    ]) ||
    event.subAgentRole ||
    event.agentName ||
    "other"
  );
}

function TraceDisplayLine({ item }: { item: TraceDisplayItem }) {
  if (item.kind === "event") return <TraceEventLine event={item.event} />;
  return <DelegationSummaryLine events={item.events} target={item.target} />;
}

function DelegationSummaryLine({
  events,
  target,
}: {
  events: LiveToolEvent[];
  target: string;
}) {
  const { t } = useI18n();
  const hasRunning = events.some((event) => event.status === "running");
  const hasError = events.some((event) => event.status === "error");
  const status = hasRunning ? "running" : hasError ? "error" : "done";
  const statusLabel =
    status === "running"
      ? t.message.statusViewing
      : status === "error"
        ? t.message.statusError
        : t.message.statusCompleted;

  return (
    <div className="flex items-center gap-2 py-1.5 text-xs text-muted-foreground">
      {status === "running" ? (
        <Loader2Icon className="size-3.5 shrink-0 animate-spin text-success" />
      ) : status === "error" ? (
        <XCircleIcon className="size-3.5 shrink-0 text-destructive" />
      ) : (
        <CheckCircle2Icon className="size-3.5 shrink-0 text-success" />
      )}
      <NetworkIcon className="size-3.5 shrink-0 text-chart-6" />
      <span className="min-w-0 truncate text-sm font-medium text-foreground">
        {t.messageGrouping.callTeammate} · {target}
      </span>
      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
        {events.length}× · {statusLabel}
      </span>
    </div>
  );
}

function TraceEventLine({ event }: { event: LiveToolEvent }) {
  const { t } = useI18n();
  const Icon =
    event.name === "read_file"
      ? FileTextIcon
      : event.name === "shell_command" || event.name === "exec_shell"
        ? TerminalIcon
        : event.name === "web_search"
          ? GlobeIcon
          : MonitorIcon;
  const { label, detail } = publicTraceEventLabel(event, {
    callTeammate: t.messageGrouping.callTeammate,
    executeCommand: t.messageGrouping.executeCommand,
    readFile: t.messageGrouping.readFile,
    readWebpage: t.messageGrouping.readWebpage,
    runAction: t.messageGrouping.runAction,
    searchSources: t.messageGrouping.searchSources,
    updateFile: t.messageGrouping.updateFile,
  });
  return (
    <div className="flex items-start gap-2 text-xs text-muted-foreground">
      {event.status === "running" ? (
        <Loader2Icon className="mt-0.5 size-3.5 shrink-0 animate-spin text-success" />
      ) : event.status === "waiting_approval" ? (
        <CircleIcon className="mt-0.5 size-3.5 shrink-0 text-warning" />
      ) : event.status === "error" ? (
        <XCircleIcon className="mt-0.5 size-3.5 shrink-0 text-destructive" />
      ) : (
        <CheckCircle2Icon className="mt-0.5 size-3.5 shrink-0 text-success" />
      )}
      <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-foreground">{label}</div>
        {detail && (
          <div className="truncate text-xs text-muted-foreground">{detail}</div>
        )}
      </div>
    </div>
  );
}

type TraceEventLabelBag = {
  callTeammate: string;
  executeCommand: string;
  readFile: string;
  readWebpage: string;
  runAction: string;
  searchSources: string;
  updateFile: string;
};

const DEFAULT_TRACE_LABELS: TraceEventLabelBag = {
  callTeammate: "Call teammate",
  executeCommand: "Run command",
  readFile: "Read file",
  readWebpage: "Read webpage",
  runAction: "Run operation",
  searchSources: "Search sources",
  updateFile: "Update file",
};

const TRACE_UNSAFE_TEXT_RE =
  /(?:sk-[\w-]+|bearer\s+[a-z0-9._-]+|api[_-]?key|token|secret|credential|password|passwd|id_rsa|id_ed25519|\.pem\b|\.key\b|<[/]?(?:tool|tool_call|function|thinking|thought|TextBlock|ReasoningBlock|ToolCallBlock|ToolResultBlock)\b|```|\b(?:Action|Observation|Thought|Final Answer)\s*:|\b(?:read_file|exec_shell|shell_command|run_command|todo_write|apply_patch)\b)/i;

function compactPublicTraceText(value: unknown): string {
  if (typeof value !== "string") return "";
  const text = value.replace(/\s+/g, " ").trim();
  if (!text || text.length > 220 || TRACE_UNSAFE_TEXT_RE.test(text)) return "";
  return text;
}

function safeTraceTarget(value: unknown): string {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (!text || TRACE_UNSAFE_TEXT_RE.test(text)) return "";
  if (/^https?:\/\//i.test(text)) {
    try {
      return new URL(text).hostname;
    } catch {
      return "";
    }
  }
  if (/[\\/]/.test(text)) {
    const parts = text.split(/[\\/]/).filter(Boolean);
    return parts.at(-1) ?? "";
  }
  return text.length > 80 ? `${text.slice(0, 79).trimEnd()}…` : text;
}

function firstSafeTarget(input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  for (const key of [
    "path",
    "file_path",
    "filepath",
    "filename",
    "url",
    "query",
    "target",
  ]) {
    const target = safeTraceTarget(input[key]);
    if (target) return target;
  }
  return "";
}

export function publicTraceEventLabel(
  event: LiveToolEvent,
  labels: TraceEventLabelBag = DEFAULT_TRACE_LABELS,
): { label: string; detail: string } {
  const narrative =
    compactPublicTraceText(event.thought) ||
    compactPublicTraceText(event.observation);
  if (narrative) return { label: narrative, detail: "" };

  const name = event.name.toLowerCase();
  const target = firstSafeTarget(effectiveToolInput(event.input));
  const label =
    name.includes("search") || name.includes("grep") || name.includes("glob")
      ? labels.searchSources
      : name.includes("web") ||
          name.includes("fetch") ||
          name.includes("browser")
        ? labels.readWebpage
        : name.includes("read") || name === "ls" || name === "list_cwd"
          ? labels.readFile
          : name.includes("write") ||
              name.includes("edit") ||
              name.includes("patch")
            ? labels.updateFile
            : name.includes("shell") ||
                name.includes("exec") ||
                name.includes("command")
              ? labels.executeCommand
              : name.includes("agent") || name.includes("delegate")
                ? labels.callTeammate
                : labels.runAction;
  return { label, detail: target };
}

function mergeSectionEvents(
  visibleEvents: LiveToolEvent[],
  rawEvents: LiveToolEvent[],
): LiveToolEvent[] {
  const byId = new Map(visibleEvents.map((event) => [event.id, event]));
  for (const event of rawEvents) {
    const hasPublicThinking =
      event.name === "model_reasoning" ||
      Boolean(event.thought?.trim()) ||
      Boolean(event.observation?.trim());
    if (hasPublicThinking && !byId.has(event.id)) {
      byId.set(event.id, event);
    }
  }
  return Array.from(byId.values()).sort((a, b) => a.startedAt - b.startedAt);
}

function buildTraceSections(
  events: LiveToolEvent[],
  phaseTitle: string,
  t: ReturnType<typeof useI18n>["t"],
): TraceSection[] {
  const thinking = events.filter(
    (event) =>
      Boolean(event.thought?.trim()) ||
      Boolean(event.observation?.trim()) ||
      event.name === "model_reasoning",
  );
  const action = events.filter((event) =>
    /read|write|edit|shell|exec|search|fetch|browse|web_search|call_agent|todo_write/i.test(
      event.name,
    ),
  );
  const verification = events.filter((event) =>
    /verify|check|test|validate|review|approval/i.test(event.name),
  );
  const remainder = events.filter(
    (event) =>
      !thinking.includes(event) &&
      !action.includes(event) &&
      !verification.includes(event),
  );

  const sections: TraceSection[] = [];
  if (thinking.length > 0) {
    sections.push({
      kind: "thinking",
      title: t.message.thinkingProcess,
      summary: phaseTitle || t.message.thinking,
      events: thinking,
      openByDefault: true,
    });
  }
  if (action.length > 0) {
    sections.push({
      kind: "action",
      title: t.message.execution,
      summary: t.message.actionCount(action.length),
      events: action,
      openByDefault: true,
    });
  }
  if (verification.length > 0) {
    sections.push({
      kind: "verification",
      title: t.message.verification,
      summary: t.message.checkCount(verification.length),
      events: verification,
      openByDefault: verification.some((event) => event.status === "error"),
    });
  }
  if (sections.length === 0 && remainder.length > 0) {
    sections.push({
      kind: "action",
      title: t.message.process,
      summary: t.message.processRecords(remainder.length),
      events: remainder,
      openByDefault: true,
    });
  }
  return sections;
}

function deriveMessageAgentRows(events: LiveToolEvent[]): MessageAgentRow[] {
  // Lifecycle markers often carry a runtime UUID while subsequent activity
  // carries the role/codename. Resolve every record to one stable human
  // identity so one spawned agent cannot become two cards.
  const runtimeIdToStableId = new Map<string, string>();
  for (const event of events) {
    if (!event.agentId || event.agentId === "__main__") continue;
    const stableId = event.subagentCodename || event.subAgentRole;
    if (stableId) runtimeIdToStableId.set(event.agentId, stableId);
  }
  const byId = new Map<string, MessageAgentRow>();
  for (const event of events) {
    const id =
      (event.agentId ? runtimeIdToStableId.get(event.agentId) : undefined) ??
      event.subagentCodename ??
      (event.parentToolUseId && event.subAgentRole
        ? `${event.parentToolUseId}:${event.subAgentRole}`
        : undefined) ??
      event.subAgentRole ??
      event.agentName;
    if (!id || id === "__main__") continue;
    const existing = byId.get(id);
    const status =
      event.status === "error"
        ? "error"
        : event.status === "done"
          ? "done"
          : event.status === "waiting_approval"
            ? "waiting"
            : event.status === "running"
              ? "running"
              : "pending";
    const prompt =
      firstString(effectiveToolInput(event.input), [
        "prompt",
        "task",
        "description",
        "query",
      ]) ||
      event.thought ||
      existing?.prompt ||
      "";
    const output =
      typeof event.output === "string"
        ? event.output
        : firstString(event.output as Record<string, unknown> | undefined, [
            "summary",
            "result",
            "output",
            "answer",
            "content",
          ]);
    const summary =
      output || event.observation || event.thought || existing?.summary;
    const terminalStatus = status === "done" || status === "error";
    byId.set(id, {
      id,
      name:
        event.subagentCodename ??
        event.agentName ??
        existing?.name ??
        event.subAgentRole ??
        id,
      label: existing?.label ?? String(byId.size + 1).padStart(2, "0"),
      status: terminalStatus
        ? status
        : existing?.status === "done" || existing?.status === "error"
          ? existing.status
          : status,
      task: existing?.task || prompt || event.name.replace(/[_-]+/g, " "),
      prompt: prompt || existing?.prompt,
      role: event.subAgentRole ?? existing?.role,
      avatar: event.subagentAvatar ?? existing?.avatar,
      currentTool: event.name.replace(/[_-]+/g, " "),
      eventCount: (existing?.eventCount ?? 0) + 1,
      // Keep the first cause seen: later events for the same lane (a spawn
      // echo, a trailing tool call) carry no error and would erase it.
      error: event.error ?? existing?.error,
      summary,
    });
  }
  if (byId.size > 0) return Array.from(byId.values()).slice(0, 12);

  // No real sub-agent events — don't fabricate. See parallel comment
  // in agent-workbench-panel.tsx for the rationale (was creating fake
  // swarm tiles in single-agent runs).
  return [];
}

function firstString(
  input: Record<string, unknown> | undefined,
  keys: string[],
) {
  if (!input) return "";
  for (const key of keys) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}
