import { CheckCircle2Icon, CircleIcon, Loader2Icon } from "lucide-react";
import type { ExecutionPlan, ExecutionPlanStep } from "@/core/threads/types";
import { cn } from "@/lib/utils";
import { normalizeExecutionPlan } from "./execution-plan-utils";

// ---------------------------------------------------------------------------
// Detection helpers
// ---------------------------------------------------------------------------

export function isTaskChecklistMessage(message: {
  additional_kwargs?: Record<string, unknown>;
}): boolean {
  return message.additional_kwargs?.element === "task_checklist";
}

export function getChecklistPlanFromMessage(message: {
  additional_kwargs?: Record<string, unknown>;
}): ExecutionPlan | null {
  const planData = message.additional_kwargs?.execution_plan;
  return normalizeExecutionPlan(planData);
}

// ---------------------------------------------------------------------------
// Step status icon
// ---------------------------------------------------------------------------

function StepIcon({ status }: { status: ExecutionPlanStep["status"] }) {
  switch (status) {
    case "completed":
      return <CheckCircle2Icon className="size-4 shrink-0 text-success" />;
    case "in_progress":
      return (
        <Loader2Icon className="size-4 shrink-0 animate-spin text-primary" />
      );
    case "skipped":
      return (
        <CircleIcon className="size-4 shrink-0 text-muted-foreground/30" />
      );
    default:
      return (
        <CircleIcon className="size-4 shrink-0 text-muted-foreground/40" />
      );
  }
}

// ---------------------------------------------------------------------------
// TaskProgressChecklist
// ---------------------------------------------------------------------------

export function TaskProgressChecklist({
  plan,
  className,
}: {
  plan: ExecutionPlan;
  className?: string;
}) {
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const completed = steps.filter((s) => s.status === "completed").length;
  const total = steps.length;

  return (
    <div className={cn("flex flex-col gap-1 py-2", className)}>
      {/* Header */}
      <div className="flex items-center justify-between px-1">
        <span className="text-sm font-medium text-foreground">
          {plan.title}
        </span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {completed}/{total}
        </span>
      </div>

      {/* Steps */}
      <ul className="flex flex-col gap-0.5">
        {steps.map((step) => (
          <li
            key={step.step_id}
            className="flex items-start gap-2 rounded-md px-1 py-1 text-sm"
          >
            <StepIcon status={step.status} />
            <span
              className={cn(
                "leading-5",
                step.status === "completed"
                  ? "text-muted-foreground/50 line-through"
                  : step.status === "in_progress"
                    ? "text-foreground"
                    : "text-muted-foreground",
              )}
            >
              {step.description}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
