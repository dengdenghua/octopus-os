import {
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleDotIcon,
  ClipboardListIcon,
  Loader2Icon,
  XIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { type Message, hasToolCalls } from "@/core/api/types";
import { useI18n } from "@/core/i18n/hooks";
import { taskPlanItemId } from "@/core/todos/task-plan";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PlanStep {
  id: string;
  description: string;
  status: "pending" | "in_progress" | "completed" | "failed";
  detail?: string;
  toolCalls?: string[];
}

// ---------------------------------------------------------------------------
// Extract plan from messages
// ---------------------------------------------------------------------------

function extractPlanFromMessages(messages: Message[]): PlanStep[] {
  const steps: PlanStep[] = [];
  let stepIdx = 0;

  for (const msg of messages) {
    if (msg.type !== "ai") continue;

    const content =
      typeof msg.content === "string"
        ? msg.content
        : Array.isArray(msg.content)
          ? msg.content
              .map((p: string | { type?: string; text?: string }) =>
                typeof p === "string" ? p : (p?.text ?? ""),
              )
              .join("")
          : "";

    // Detect numbered plan steps: "1. Do X", "2. Do Y", "Step 1:", etc.
    const planLines = content.match(
      /(?:^|\n)\s*(?:\d+[\.\)]\s+|Step \d+[:\s]+|[-•]\s+)(.+)/g,
    );
    if (planLines && planLines.length >= 2) {
      for (const line of planLines) {
        const cleaned = line
          .replace(/^\s*(?:\d+[\.\)]\s+|Step \d+[:\s]+|[-•]\s+)/, "")
          .trim();
        if (cleaned.length > 5 && cleaned.length < 200) {
          stepIdx++;
          steps.push({
            id: `step-${stepIdx}`,
            description: cleaned,
            status: "pending",
          });
        }
      }
    }

    // Detect tool calls as progress indicators
    if (hasToolCalls(msg)) {
      for (const tc of msg.tool_calls) {
        const name = tc.name ?? "";
        // Try to match tool call to a plan step
        const matchIdx = steps.findIndex(
          (s) =>
            s.status === "pending" &&
            (s.description.toLowerCase().includes(name) ||
              name.includes("write") ||
              name.includes("bash") ||
              name.includes("read")),
        );
        if (matchIdx >= 0) {
          steps[matchIdx]!.status = "in_progress";
          steps[matchIdx]!.toolCalls = steps[matchIdx]!.toolCalls ?? [];
          steps[matchIdx]!.toolCalls!.push(name);
        }
      }
    }
  }

  // Simple heuristic: mark steps before the first active one as completed
  let lastActiveIdx = steps.findIndex(
    (s) => s.status === "in_progress" || s.status === "pending",
  );
  if (lastActiveIdx === -1) lastActiveIdx = steps.length;
  for (let i = 0; i < lastActiveIdx; i++) {
    if (steps[i]!.status === "pending") {
      steps[i]!.status = "completed";
    }
  }

  // Also extract from TodoList values if available
  return steps;
}

function extractTodosAsSteps(
  todos:
    | Array<{
        id?: string;
        content: string;
        status: string;
        activeForm?: string;
      }>
    | undefined,
  labels?: { completed: string; inProgress: string; pending: string },
): PlanStep[] {
  if (!todos || todos.length === 0) return [];
  // Human-readable status label used as the fallback `detail` so every
  // item is expandable — the row shows the status chip when the model
  // didn't supply an `activeForm` narration. Without this, the PlanPanel
  // suppresses the expand chevron for minimal {content, status} todos
  // (which is what the backend write_todos middleware emits today).
  const statusLabel = (s: string) =>
    s === "completed"
      ? (labels?.completed ?? "completed")
      : s === "in_progress"
        ? (labels?.inProgress ?? "in_progress")
        : (labels?.pending ?? "pending");
  const occurrences = new Map<string, number>();
  return todos.map((todo) => {
    const occurrence = occurrences.get(todo.content) ?? 0;
    occurrences.set(todo.content, occurrence + 1);
    return {
      id: `todo-${taskPlanItemId(todo as unknown as Record<string, unknown>, occurrence)}`,
      description: todo.content,
      status:
        todo.status === "completed"
          ? "completed"
          : todo.status === "in_progress"
            ? "in_progress"
            : "pending",
      detail:
        todo.activeForm || `${statusLabel(todo.status)} · ${todo.content}`,
    };
  });
}

/**
 * Compute combined plan steps (todos first, fallback to extracting numbered
 * items from assistant messages). Shared between PlanPanel (which uses it
 * internally) and PlanButton's visibility gate.
 */
export function computePlanSteps(
  messages: Message[],
  todos?: Array<{
    id?: string;
    content: string;
    status: string;
    activeForm?: string;
  }>,
  labels?: { completed: string; inProgress: string; pending: string },
): PlanStep[] {
  const todoSteps = extractTodosAsSteps(todos, labels);
  if (todoSteps.length > 0) return todoSteps;
  return extractPlanFromMessages(messages);
}

// ---------------------------------------------------------------------------
// Plan Step Component
// ---------------------------------------------------------------------------

function PlanStepItem({
  step,
  index,
  expanded,
  onToggle,
}: {
  step: PlanStep;
  index: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2 transition-colors",
        step.status === "completed" &&
          "border-success/30/60 bg-success/5 dark:border-success/30",
        step.status === "in_progress" &&
          "border-primary/30 bg-primary/5 shadow-[var(--shadow-xs)] shadow-primary/5",
        step.status === "failed" &&
          "border-destructive/30/60 bg-destructive/5 dark:border-destructive/30",
        step.status === "pending" &&
          "border-border-default bg-muted/20 hover:bg-muted/30",
      )}
    >
      <button
        onClick={onToggle}
        className="flex w-full items-start gap-2 text-left"
      >
        {/* Status icon */}
        <div className="mt-0.5 shrink-0">
          {step.status === "completed" ? (
            <CheckCircle2Icon className="size-3.5 text-success" />
          ) : step.status === "in_progress" ? (
            <Loader2Icon className="size-3.5 animate-spin text-primary" />
          ) : step.status === "failed" ? (
            <XIcon className="size-3.5 text-destructive" />
          ) : (
            <CircleDotIcon className="size-3.5 text-muted-foreground/40" />
          )}
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-muted-foreground/50 text-xs font-mono">
              {index + 1}
            </span>
            <span
              className={cn(
                "text-xs",
                step.status === "completed" &&
                  "text-muted-foreground line-through",
                step.status === "in_progress" && "text-foreground font-medium",
                step.status === "pending" && "text-muted-foreground",
              )}
            >
              {step.description}
            </span>
          </div>
        </div>

        {/* Expand arrow */}
        {(step.detail || step.toolCalls) &&
          (expanded ? (
            <ChevronDownIcon className="size-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRightIcon className="size-3 shrink-0 text-muted-foreground" />
          ))}
      </button>

      {/* Detail */}
      {expanded && (step.detail || step.toolCalls) && (
        <div className="mt-1.5 pl-6 text-xs text-muted-foreground">
          {step.detail && <div>{step.detail}</div>}
          {step.toolCalls && step.toolCalls.length > 0 && (
            <div className="mt-0.5 flex flex-wrap gap-1">
              {step.toolCalls.map((tc, i) => (
                <span
                  key={i}
                  className="rounded bg-muted/70 px-1 py-0.5 font-mono text-xs"
                >
                  {tc}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Plan Panel (floating button + panel)
// ---------------------------------------------------------------------------

export function PlanButton({
  onClick,
  stepCount,
  completedCount,
  isActive,
  className,
}: {
  onClick: () => void;
  stepCount: number;
  completedCount: number;
  isActive: boolean;
  className?: string;
}) {
  const { t } = useI18n();
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium shadow-[var(--shadow-xs)] transition-colors transition-shadow duration-base",
        isActive
          ? "border-primary/60 bg-primary/10 text-primary shadow-primary/10"
          : "border-border-default bg-background/80 text-muted-foreground hover:text-foreground hover:border-foreground/20 hover:bg-muted/50",
        className,
      )}
    >
      <ClipboardListIcon className="size-3.5" />
      <span>{t.planPanel.title}</span>
      {stepCount > 0 && (
        <span className="rounded-lg bg-muted/80 px-1.5 py-0.5 text-xs tabular-nums">
          {completedCount}/{stepCount}
        </span>
      )}
    </button>
  );
}

export function PlanPanel({
  messages,
  todos,
  open,
  onClose,
  className,
}: {
  messages: Message[];
  todos?: Array<{
    id?: string;
    content: string;
    status: string;
    activeForm?: string;
  }>;
  open: boolean;
  onClose: () => void;
  className?: string;
}) {
  const { t } = useI18n();
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  // Extract plan steps from messages and/or todos
  const steps = useMemo(
    () =>
      computePlanSteps(messages, todos, {
        completed: t.planPanel.completed,
        inProgress: t.planPanel.inProgress,
        pending: t.planPanel.pending,
      }),
    [
      messages,
      todos,
      t.planPanel.completed,
      t.planPanel.inProgress,
      t.planPanel.pending,
    ],
  );

  const completedCount = steps.filter((s) => s.status === "completed").length;
  const progressPct =
    steps.length > 0 ? (completedCount / steps.length) * 100 : 0;

  const toggleStep = (id: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (!open) return null;

  return (
    <div
      className={cn(
        "w-80 rounded-lg border border-border-default bg-popover shadow-xl shadow-black/5",
        "animate-in slide-in-from-bottom-2 fade-in duration-base",
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-default px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex size-6 items-center justify-center rounded-lg bg-primary/10">
            <ClipboardListIcon className="text-primary size-3.5" />
          </div>
          <span className="text-sm font-semibold">{t.planPanel.title}</span>
          <span className="text-muted-foreground text-xs">
            {t.planPanel.steps(completedCount, steps.length)}
          </span>
        </div>
        <button
          onClick={onClose}
          className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        >
          <XIcon className="size-3.5" />
        </button>
      </div>

      {/* Progress bar */}
      <div className="px-4 pt-2">
        <div className="h-1 overflow-hidden rounded-lg bg-muted">
          <div
            className="bg-primary h-full rounded-lg transition-[width] duration-slow"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* Steps */}
      <div className="max-h-80 space-y-1.5 overflow-y-auto px-4 py-3">
        {steps.length === 0 ? (
          <div className="workspace-panel-subtle py-6 text-center rounded-lg">
            <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-lg bg-primary/8">
              <ClipboardListIcon className="size-5 text-muted-foreground/40" />
            </div>
            <p className="text-muted-foreground/50 text-xs">
              No plan detected yet.
            </p>
            <p className="text-muted-foreground/40 text-xs">
              The plan will appear when the agent starts working.
            </p>
          </div>
        ) : (
          steps.map((step, i) => (
            <PlanStepItem
              key={step.id}
              step={step}
              index={i}
              expanded={expandedSteps.has(step.id)}
              onToggle={() => toggleStep(step.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}
