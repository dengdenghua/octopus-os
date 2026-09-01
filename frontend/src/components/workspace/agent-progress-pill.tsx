import {
  CheckCircle2Icon,
  ChevronDownIcon,
  CircleIcon,
  Loader2Icon,
  Minimize2Icon,
  WifiOffIcon,
  XCircleIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  agentPhaseDisplayTitle,
  deriveAgentPhases,
  progressForPhases,
} from "./agent-phases";
import type { LiveToolEvent } from "./live-tool-timeline";
import {
  normalizeEventsForSettledDisplay,
  pickCurrentWorkBlock,
  workBlockLabelsFromShape,
  workBlockTitle,
} from "./work-blocks";
import { useI18n } from "@/core/i18n/hooks";
import { getBackendBaseURL } from "@/core/config";
import {
  FIRST_RESPONSE_DELAY_NOTICE_MS,
  formatStreamElapsed,
  type StreamVitals,
} from "@/core/realtime";
import { cn } from "@/lib/utils";
import { agentRunBeadTone } from "./agent-run-status";

const minimizedPlanByScope = new Map<string, string>();

function planFingerprintForPhases(
  phases: Array<{ id: string; title: string }>,
) {
  if (phases.length === 0) return null;
  return phases.map((phase) => `${phase.id}:${phase.title}`).join("|");
}

function rememberMinimizedPlan(
  scopeKey: string | undefined,
  fingerprint: string | null,
) {
  if (!scopeKey || !fingerprint) return;
  minimizedPlanByScope.set(scopeKey, fingerprint);
}

function forgetMinimizedPlan(scopeKey: string | undefined) {
  if (!scopeKey) return;
  minimizedPlanByScope.delete(scopeKey);
}

// Pre-activity status-strip label. When vitals are available, phrase the
// label from the model's actual liveness (still working vs. slow) instead
// of the blind elapsed-time heuristic — the whole point of this strip is to
// tell "working" apart from "stuck". Falls back to the time heuristic when
// vitals are absent or we're merely waiting for the first token.
function fallbackStatusLabel({
  t,
  vitals,
  hasStreamingAnswer,
  runSettled,
  runFailed,
}: {
  t: ReturnType<typeof useI18n>["t"];
  vitals?: StreamVitals;
  hasStreamingAnswer?: boolean;
  runSettled?: boolean;
  runFailed?: boolean;
}): string {
  const s = t.publicThinkingStatus;
  if (vitals?.phase === "disconnected") return s.reconnecting;
  if (vitals?.phase === "slow") {
    return `${s.slowResponse}${vitals.elapsedMs >= 3_000 ? ` · ${formatStreamElapsed(vitals.elapsedMs)}` : ""}`;
  }
  if (vitals?.phase === "waiting") {
    const label =
      vitals.elapsedMs >= FIRST_RESPONSE_DELAY_NOTICE_MS
        ? s.firstResponseSlow
        : s.waitingForModel;
    return `${label}${vitals.elapsedMs >= 3_000 ? ` · ${formatStreamElapsed(vitals.elapsedMs)}` : ""}`;
  }
  if (runSettled && !runFailed) return s.thinkingCompleted;
  if (vitals && vitals.phase !== "idle") {
    // Mid-task liveness is "processing", not "thinking": the 思考中 label
    // is reserved for the pre-first-response window (handled above).
    const suffix =
      vitals.elapsedMs >= 3_000
        ? ` · ${formatStreamElapsed(vitals.elapsedMs)}`
        : "";
    return `${s.processing}${suffix}`;
  }
  if (hasStreamingAnswer) return s.processing;
  // Without wire telemetry, do not manufacture “understanding / planning /
  // analysing” stages from a timer. The only fact we know is that the first
  // observable model event has not arrived yet.
  return s.waitingForModel;
}

function StatusIcon({
  status,
}: {
  status: LiveToolEvent["status"] | "pending";
}) {
  if (status === "waiting_approval") {
    return <CircleIcon className="size-4 shrink-0 text-warning" />;
  }
  if (status === "running") {
    return (
      <Loader2Icon className="size-4 shrink-0 animate-spin text-primary" />
    );
  }
  if (status === "pending") {
    return <CircleIcon className="size-4 shrink-0 text-muted-foreground/45" />;
  }
  if (status === "error") {
    return <XCircleIcon className="size-4 shrink-0 text-destructive" />;
  }
  return <CheckCircle2Icon className="size-4 shrink-0 text-success" />;
}

function phaseWindow<T>(
  phases: T[],
  currentIndex: number,
  maxVisible: number,
): T[] {
  if (phases.length <= maxVisible) return phases;
  const safeIndex = Math.max(0, currentIndex);
  const before = Math.floor((maxVisible - 1) / 2);
  const start = Math.min(
    Math.max(0, safeIndex - before),
    Math.max(0, phases.length - maxVisible),
  );
  return phases.slice(start, start + maxVisible);
}

export function AgentProgressPill({
  events,
  hasAnswer,
  hasStreamingAnswer,
  isLoading,
  runSettled,
  runFailed,
  paused,
  className,
  progressScopeKey,
  vitals,
  workbenchVisible,
}: {
  events: LiveToolEvent[];
  hasAnswer?: boolean;
  hasStreamingAnswer?: boolean;
  isLoading?: boolean;
  runSettled?: boolean;
  runFailed?: boolean;
  paused?: boolean;
  className?: string;
  progressScopeKey?: string;
  /** Live streaming vitals. When present, the pre-activity status label is
   * driven by the model's actual liveness (working vs. slow/stalled)
   * instead of a blind elapsed-time heuristic. */
  vitals?: StreamVitals;
  /** When the right-side workbench panel is open, the pill auto-minimizes
   * into a small bead to avoid duplicating progress info that the workbench
   * already shows. The user can still expand it manually. */
  workbenchVisible?: boolean;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [minimized, setMinimized] = useState(false);
  const [enablingCapability, setEnablingCapability] = useState(false);
  const displayEvents = useMemo(
    () =>
      normalizeEventsForSettledDisplay(events, {
        hasAnswer,
        runSettled,
        runFailed,
        paused,
      }),
    [events, hasAnswer, runFailed, runSettled, paused],
  );
  const { blocks, phases, currentPhase } = useMemo(
    () =>
      deriveAgentPhases(displayEvents, {
        hasAnswer,
        runSettled,
        runFailed,
        paused,
      }),
    [displayEvents, hasAnswer, runSettled, runFailed, paused],
  );
  const autoMinimizedRunRef = useRef<string | null>(null);
  const workbenchAutoMinimizedRef = useRef(false);
  const displayPhase = currentPhase;
  const progress = displayPhase
    ? progressForPhases(phases, displayPhase)
    : { current: 0, total: 0 };
  const displayPhaseIndex = displayPhase
    ? phases.findIndex((phase) => phase.id === displayPhase.id)
    : -1;
  const currentBlock = useMemo(() => pickCurrentWorkBlock(blocks), [blocks]);
  const workBlockLabels = workBlockLabelsFromShape(t.workBlocks);
  // Detect tools that failed because their group is config-disabled (e.g.
  // web_search under enable_web_skills=false). We surface a one-click
  // "enable" prompt so the user doesn't have to find the config file.
  const capabilityDisabledInfo = useMemo(() => {
    for (const evt of displayEvents) {
      if (evt.capabilityDisabled && evt.status === "error") {
        return { toolName: evt.name, ...evt.capabilityDisabled };
      }
    }
    return null;
  }, [displayEvents]);
  const handleEnableCapability = async () => {
    if (!capabilityDisabledInfo || enablingCapability) return;
    setEnablingCapability(true);
    try {
      const resp = await fetch(
        `${getBackendBaseURL()}/api/capabilities/enable`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ group: capabilityDisabledInfo.group }),
        },
      );
      if (resp.ok) {
        window.location.reload();
      }
    } catch {
      // best-effort — user can still restart the backend manually
    } finally {
      setEnablingCapability(false);
    }
  };
  const planFingerprint = useMemo(
    () => planFingerprintForPhases(phases),
    [phases],
  );
  const running = displayPhase?.status === "running";
  const waiting =
    !displayEvents.some((event) => event.status === "running") &&
    displayEvents.some((event) => event.status === "waiting_approval");
  const autoMinimizeKey = displayPhase
    ? `${displayPhase.id}:${progress.current}/${progress.total}:${events.length}`
    : null;
  const vitalsAttributes = vitals
    ? {
        "data-stream-phase": vitals.phase,
        "data-stream-stalled": vitals.stalled ? "true" : "false",
        "data-stream-ttft-ms": vitals.ttftMs ?? undefined,
        "data-stream-max-gap-ms": vitals.maxDeltaGapMs,
        "data-stream-since-activity-ms": Number.isFinite(vitals.sinceActivityMs)
          ? Math.round(vitals.sinceActivityMs)
          : undefined,
        "data-stream-first-response-delayed":
          vitals.phase === "waiting" &&
          vitals.elapsedMs >= FIRST_RESPONSE_DELAY_NOTICE_MS
            ? "true"
            : "false",
      }
    : {};

  useEffect(() => {
    if (!progressScopeKey || !planFingerprint) return;
    const storedFingerprint = minimizedPlanByScope.get(progressScopeKey);
    if (storedFingerprint === planFingerprint) {
      setMinimized(true);
      setExpanded(false);
      return;
    }
    if (storedFingerprint) {
      forgetMinimizedPlan(progressScopeKey);
      setMinimized(false);
      setExpanded(false);
    }
  }, [planFingerprint, progressScopeKey]);

  useEffect(() => {
    if (!autoMinimizeKey || !runSettled || runFailed || paused || running) {
      autoMinimizedRunRef.current = null;
      return;
    }
    if (autoMinimizedRunRef.current === autoMinimizeKey) return;
    autoMinimizedRunRef.current = autoMinimizeKey;
    rememberMinimizedPlan(progressScopeKey, planFingerprint);
    setMinimized(true);
    setExpanded(false);
  }, [
    autoMinimizeKey,
    paused,
    planFingerprint,
    progressScopeKey,
    runFailed,
    runSettled,
    running,
  ]);

  // When the right-side workbench panel opens, collapse the pill into a bead
  // so progress info isn't duplicated in two places. Restore when it closes,
  // but only if we were the ones who minimized it (not a user action).
  useEffect(() => {
    if (workbenchVisible && !minimized) {
      workbenchAutoMinimizedRef.current = true;
      setMinimized(true);
      setExpanded(false);
    } else if (!workbenchVisible && workbenchAutoMinimizedRef.current) {
      workbenchAutoMinimizedRef.current = false;
      setMinimized(false);
    }
  }, [workbenchVisible, minimized]);

  if (!displayPhase || phases.length === 0 || blocks.length === 0) {
    if (!isLoading) return null;
    const fallbackLabel = fallbackStatusLabel({
      t,
      vitals,
      hasStreamingAnswer,
      runSettled,
      runFailed,
    });
    // "slow" is the one genuinely ambiguous state — the model may still be
    // working or the turn may be wedged. Tint it so it reads as "taking a
    // while", distinct from the calm blue of normal progress.
    const stalled = Boolean(
      vitals?.stalled ||
      (vitals?.phase === "waiting" &&
        vitals.elapsedMs >= FIRST_RESPONSE_DELAY_NOTICE_MS),
    );
    const disconnected = vitals?.phase === "disconnected";
    return (
      <div
        {...vitalsAttributes}
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className={cn(
          "relative z-20 flex min-h-9 w-full items-center gap-2 rounded-t-lg border border-b-0 border-border-default bg-background/95 px-3 py-1.5 text-sm",
          className,
        )}
      >
        {disconnected ? (
          <WifiOffIcon className="size-3.5 shrink-0 text-warning" />
        ) : (
          <Loader2Icon
            className={cn(
              "size-3.5 shrink-0 animate-spin",
              stalled ? "text-warning" : "text-primary",
            )}
          />
        )}
        <span
          className={cn(
            "min-w-0 flex-1 truncate font-medium",
            stalled ? "text-warning" : "text-foreground",
          )}
        >
          {fallbackLabel}
        </span>
      </div>
    );
  }

  const percent = Math.round((progress.current / progress.total) * 100);
  const visiblePhases = phaseWindow(phases, displayPhaseIndex, 7);
  const beadTone = agentRunBeadTone({
    paused,
    runFailed,
    status: displayPhase.status,
    waiting,
  });
  const progressLabel = `${t.agentWorkbench.currentProgress} ${progress.current}/${progress.total}`;
  const activeStatus = currentBlock
    ? currentBlock.status === "running"
      ? t.agentWorkbench.statusProcessing
      : currentBlock.status === "waiting_approval"
        ? t.agentWorkbench.waitingToContinue
        : currentBlock.status === "error"
          ? t.agentWorkbench.statusError
          : t.agentWorkbench.statusCompleted
    : displayPhase.status === "running"
      ? t.agentWorkbench.statusProcessing
      : displayPhase.status === "waiting_approval"
        ? t.agentWorkbench.waitingToContinue
        : displayPhase.status === "error"
          ? t.agentWorkbench.statusError
          : t.agentWorkbench.statusCompleted;

  if (minimized) {
    return (
      <div
        className={cn(
          "relative z-30 -mb-2 ml-3 flex w-fit max-w-full items-center rounded-full bg-transparent",
          className,
        )}
      >
        <button
          type="button"
          onClick={() => {
            forgetMinimizedPlan(progressScopeKey);
            setMinimized(false);
            setExpanded(true);
          }}
          title={progressLabel}
          aria-label={t.agentWorkbench.restoreProgress}
          className={cn(
            "relative isolate size-4 rounded-full shadow-[var(--shadow-xs)] transition-transform hover:scale-110",
            beadTone.bead,
          )}
        >
          {beadTone.halo ? (
            <span
              aria-hidden="true"
              className={cn(
                "pointer-events-none absolute -inset-1 -z-10 rounded-full",
                beadTone.halo,
              )}
            />
          ) : null}
        </button>
      </div>
    );
  }

  return (
    <div
      {...vitalsAttributes}
      className={cn("relative z-20 flex w-full flex-col", className)}
    >
      {expanded ? (
        <div className="rounded-t-lg border border-b-0 border-border-default bg-background/95 p-2">
          <div className="max-h-44 space-y-0.5 overflow-y-auto pr-1">
            {visiblePhases.map((phase) => {
              const active = phase.id === displayPhase.id;
              return (
                <div
                  key={phase.id}
                  className={cn(
                    "flex min-w-0 items-center gap-2 rounded-lg px-2 py-1.5 text-xs",
                    active
                      ? "bg-primary/8 text-foreground"
                      : "text-muted-foreground",
                  )}
                >
                  <StatusIcon status={phase.status} />
                  <span className="min-w-0 flex-1 truncate" title={phase.title}>
                    {agentPhaseDisplayTitle(phase, t.agentPhases)}
                  </span>
                  {active ? (
                    <span className="shrink-0 rounded-full bg-primary/15 px-1.5 py-0.5 text-xs font-medium tabular-nums text-primary">
                      {progress.current}/{progress.total}
                    </span>
                  ) : null}
                </div>
              );
            })}
          </div>
          {currentBlock ? (
            <div className="mt-1.5 flex min-w-0 items-center gap-2 border-t border-border-subtle pt-1.5 text-xs">
              <span
                className={cn(
                  "size-1.5 shrink-0 rounded-full",
                  currentBlock.status === "running"
                    ? "bg-primary"
                    : currentBlock.status === "error"
                      ? "bg-destructive"
                      : currentBlock.status === "waiting_approval"
                        ? "bg-warning"
                        : "bg-success",
                )}
              />
              <span className="min-w-0 flex-1 truncate text-foreground/85">
                {workBlockTitle(currentBlock, workBlockLabels)}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded-full px-1.5 py-0.5 text-xs font-medium",
                  currentBlock.status === "error"
                    ? "bg-destructive/10 text-destructive"
                    : currentBlock.status === "running"
                      ? "bg-primary/10 text-primary"
                      : currentBlock.status === "waiting_approval"
                        ? "bg-warning/10 text-warning"
                        : "bg-success/10 text-success",
                )}
              >
                {activeStatus}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
      <div
        className={cn(
          "group flex w-full items-center gap-2 border border-border-default bg-background/95 px-3 py-2 text-left transition-colors hover:bg-muted/40",
          expanded ? "border-b-0" : "rounded-t-lg border-b-0",
        )}
      >
        <button
          type="button"
          onClick={() => {
            setExpanded((value) => !value);
          }}
          aria-expanded={expanded}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <StatusIcon status={displayPhase.status} />
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2 text-sm leading-5">
              <span
                className={cn(
                  "min-w-0 flex-1 truncate",
                  running ? "text-foreground" : "text-foreground/80",
                )}
                title={displayPhase.title}
              >
                {agentPhaseDisplayTitle(displayPhase, t.agentPhases)}
              </span>
              <span className="shrink-0 rounded-full bg-muted/70 px-2 py-0.5 text-xs font-medium tabular-nums text-muted-foreground">
                {progressLabel}
              </span>
              <ChevronDownIcon
                className={cn(
                  "size-3.5 shrink-0 text-muted-foreground transition-transform",
                  expanded && "rotate-180",
                )}
              />
            </div>
            {currentBlock && currentBlock.event.name !== "todo_write" ? (
              <div className="mt-1 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                <span
                  className={cn(
                    "size-1.5 shrink-0 rounded-full",
                    currentBlock.status === "running"
                      ? "bg-primary"
                      : currentBlock.status === "error"
                        ? "bg-destructive"
                        : currentBlock.status === "waiting_approval"
                          ? "bg-warning"
                          : "bg-success",
                  )}
                />
                <span className="min-w-0 flex-1 truncate">
                  {workBlockTitle(currentBlock, workBlockLabels)}
                </span>
              </div>
            ) : null}
            {capabilityDisabledInfo ? (
              <div className="mt-1.5 flex items-center gap-1.5 rounded-md bg-warning/10 px-2 py-1 text-xs text-warning">
                <WifiOffIcon className="size-3 shrink-0" />
                <span className="min-w-0 flex-1 truncate">
                  {t.messageGrouping.capabilityDisabled(
                    capabilityDisabledInfo.toolName,
                  )}
                </span>
                <button
                  type="button"
                  onClick={handleEnableCapability}
                  disabled={enablingCapability}
                  className="shrink-0 rounded bg-warning px-1.5 py-0.5 text-xs font-medium text-white transition-colors hover:bg-warning disabled:opacity-50"
                >
                  {enablingCapability
                    ? t.messageGrouping.enablingCapability
                    : t.messageGrouping.enableCapability}
                </button>
              </div>
            ) : null}
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  "h-full rounded-full transition-all duration-slow",
                  displayPhase.status === "error"
                    ? "bg-destructive"
                    : displayPhase.status === "running"
                      ? "bg-primary"
                      : "bg-success",
                )}
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
        </button>
        <button
          type="button"
          onClick={() => {
            rememberMinimizedPlan(progressScopeKey, planFingerprint);
            setMinimized(true);
            setExpanded(false);
          }}
          title={t.agentWorkbench.minimizeProgress}
          aria-label={t.agentWorkbench.minimizeProgress}
          className="flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
        >
          <Minimize2Icon className="size-3" />
        </button>
      </div>
    </div>
  );
}
