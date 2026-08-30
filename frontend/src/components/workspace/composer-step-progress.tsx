import {
  AlertCircleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronUpIcon,
  CircleIcon,
  Loader2Icon,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import {
  agentPhaseDisplayTitle,
  deriveAgentPhases,
  progressForPhases,
  type AgentPhase,
} from "./agent-phases";
import type { LiveToolEvent } from "./live-tool-timeline";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

function hasExplicitTaskPlan(events: LiveToolEvent[]): boolean {
  return events.some((event) => {
    if (event.name !== "todo_write") return false;
    const items = event.input?.items ?? event.input?.todos;
    return Array.isArray(items) && items.length >= 2;
  });
}

function PhaseStatusIcon({
  phase,
  className,
}: {
  phase: AgentPhase;
  className?: string;
}) {
  if (phase.status === "running")
    return (
      <Loader2Icon
        aria-hidden="true"
        className={cn("size-4 shrink-0 animate-spin text-info dark:text-info", className)}
      />
    );
  if (phase.status === "waiting_approval")
    return <CircleIcon aria-hidden="true" className={cn("size-4 shrink-0 text-warning", className)} />;
  if (phase.status === "error")
    return <AlertCircleIcon aria-hidden="true" className={cn("size-4 shrink-0 text-destructive", className)} />;
  if (phase.status === "done")
    return <CheckCircle2Icon aria-hidden="true" className={cn("size-4 shrink-0 text-success", className)} />;
  return <CircleIcon aria-hidden="true" className={cn("size-4 shrink-0 text-info dark:text-info", className)} />;
}

export function ComposerStepProgress({
  events,
  hasAnswer,
  isLoading,
  runSettled,
  runFailed,
  paused,
  className,
}: {
  events: LiveToolEvent[];
  hasAnswer?: boolean;
  isLoading?: boolean;
  runSettled?: boolean;
  runFailed?: boolean;
  paused?: boolean;
  className?: string;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const hasPlan = useMemo(() => hasExplicitTaskPlan(events), [events]);
  const { phases, currentPhase } = useMemo(
    () =>
      deriveAgentPhases(events, {
        hasAnswer,
        runSettled,
        runFailed,
        paused,
      }),
    [events, hasAnswer, paused, runFailed, runSettled],
  );
  // A plan whose items are all done is NOT a completed task while the turn is
  // still streaming — the model may keep working (or extend the plan, as in
  // the 4/4→5-item case). A checkmark is only meaningful once the run settles,
  // so present the last finished phase as still running during streaming.
  const phaseForDisplay = useMemo(() => {
    if (!currentPhase) return null;
    if (isLoading && currentPhase.status === "done") {
      return { ...currentPhase, status: "running" as const };
    }
    return currentPhase;
  }, [currentPhase, isLoading]);

  const toggleExpanded = useCallback(() => setExpanded((value) => !value), []);

  // This indicator describes a real model-authored task plan only. Generic
  // tool activity stays in the transcript rather than being presented as an
  // invented numbered plan.
  if (
    !hasPlan ||
    !phaseForDisplay ||
    phases.length < 2 ||
    (runSettled && hasAnswer && !paused) ||
    (!isLoading && phaseForDisplay.status === "done")
  ) {
    return null;
  }

  const progress = progressForPhases(phases, phaseForDisplay);
  const label = t.agentWorkbench.stepProgress(progress.current, progress.total);
  const phaseLabels = t.agentPhases;

  return (
    <div className={cn("flex w-full flex-col items-center", className)}>
      <button
        type="button"
        onClick={toggleExpanded}
        aria-label={`${label} · ${phaseForDisplay.title}`}
        aria-expanded={expanded}
        title={phaseForDisplay.title}
        className="group inline-flex h-9 max-w-full items-center gap-1.5 rounded-full border border-border-default bg-background/95 pl-3.5 pr-2.5 text-xs font-semibold text-muted-foreground shadow-[0_8px_24px_-16px_rgba(15,23,42,0.45)] backdrop-blur-xl transition-[border-color,background-color,color,transform] hover:-translate-y-0.5 hover:border-primary/30 hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 active:translate-y-0"
      >
        <PhaseStatusIcon phase={phaseForDisplay} className="size-3.5" />
        <span className="truncate tabular-nums">{label}</span>
        {expanded ? (
          <ChevronUpIcon aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground/60" />
        ) : (
          <ChevronDownIcon aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground/60" />
        )}
      </button>

      {expanded ? (
        <ul className="mt-2.5 w-full max-w-sm space-y-0.5 rounded-lg border border-border-subtle bg-background/60 p-2.5 text-left">
          {phases.map((phase) => (
            <li
              key={phase.id}
              className="flex items-start gap-1.5 px-1 py-0.5 text-xs leading-5"
            >
              <PhaseStatusIcon phase={phase} className="mt-0.5 size-3.5" />
              <span
                className={cn(
                  "line-clamp-2 min-w-0 flex-1",
                  phase.status === "pending"
                    ? "text-muted-foreground"
                    : "text-foreground",
                )}
              >
                {agentPhaseDisplayTitle(phase, phaseLabels)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
