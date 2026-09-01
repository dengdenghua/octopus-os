import {
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleIcon,
  CodeIcon,
  DatabaseIcon,
  FileCodeIcon,
  GlobeIcon,
  Loader2Icon,
  SparklesIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { useState } from "react";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export type AgentStepStatus = "pending" | "running" | "completed" | "error";

export interface AgentStep {
  id: string;
  name: string;
  description?: string;
  inputText?: string;
  outputText?: string;
  status: AgentStepStatus;
  icon?: "plan" | "code" | "review" | "deploy" | "test" | "fix" | "default";
  startTime?: number;
  endTime?: number;
  subSteps?: AgentStep[];
}

interface AgentWorkflowPanelProps {
  steps: AgentStep[];
  currentStepId?: string;
  isRunning: boolean;
  className?: string;
}

const stepIcons = {
  plan: SparklesIcon,
  code: CodeIcon,
  review: FileCodeIcon,
  deploy: GlobeIcon,
  test: BotIcon,
  fix: WrenchIcon,
  default: DatabaseIcon,
};

function StepItem({
  step,
  isLast,
  depth = 0,
}: {
  step: AgentStep;
  isLast: boolean;
  depth?: number;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(step.status === "running");
  const Icon = stepIcons[step.icon || "default"];
  const duration =
    step.endTime && step.startTime
      ? Math.round((step.endTime - step.startTime) / 1000)
      : step.startTime
        ? Math.round((Date.now() - step.startTime) / 1000)
        : null;
  const hasDetails = Boolean(step.inputText || step.outputText);

  return (
    <div className={cn("relative", depth > 0 && "ml-4")}>
      <div className="flex items-start gap-3 py-2">
        <div className="relative flex flex-col items-center">
          <div
            className={cn(
              "flex size-6 items-center justify-center rounded-full border-2 transition-colors duration-slow",
              step.status === "completed" &&
                "border-success/50 bg-success/10 text-success",
              step.status === "running" &&
                "border-chart-1 bg-chart-1/10 text-chart-1",
              step.status === "error" &&
                "border-destructive/50 bg-destructive/10 text-destructive",
              step.status === "pending" &&
                "border-muted-foreground/30 text-muted-foreground/50",
            )}
          >
            {step.status === "completed" ? (
              <CheckCircle2Icon className="size-3.5" />
            ) : step.status === "running" ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : step.status === "error" ? (
              <XCircleIcon className="size-3.5" />
            ) : (
              <CircleIcon className="size-3.5" />
            )}
          </div>
          {!isLast && (
            <div
              className={cn(
                "mt-1 h-full min-h-[20px] w-px transition-colors duration-slow",
                step.status === "completed"
                  ? "bg-success/30"
                  : "bg-border/50",
              )}
            />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Icon
                  className={cn(
                    "size-3.5 shrink-0",
                    step.status === "running" && "text-chart-1",
                    step.status === "completed" && "text-success",
                    step.status === "error" && "text-destructive",
                    step.status === "pending" && "text-muted-foreground/50",
                  )}
                />
                <span
                  className={cn(
                    "truncate text-sm font-medium",
                    step.status === "running" &&
                      "text-chart-1 dark:text-chart-1",
                    step.status === "completed" &&
                      "text-success",
                    step.status === "error" &&
                      "text-destructive",
                    step.status === "pending" && "text-muted-foreground",
                  )}
                >
                  {step.name}
                </span>
                {duration !== null && (
                  <span className="text-xs text-muted-foreground/60">
                    {duration}s
                  </span>
                )}
              </div>
              {step.description && (
                <p className="mt-0.5 break-all text-xs text-muted-foreground/75 line-clamp-2">
                  {step.description}
                </p>
              )}
              {!expanded && step.outputText && (
                <p className="mt-1 break-all text-xs text-success/80 line-clamp-2 dark:text-success/80">
                  {step.outputText}
                </p>
              )}
            </div>
            {hasDetails && (
              <button
                type="button"
                onClick={() => setExpanded((value) => !value)}
                className="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                {expanded ? (
                  <ChevronDownIcon className="size-3" />
                ) : (
                  <ChevronRightIcon className="size-3" />
                )}
                <span>
                  {expanded
                    ? t.agentWorkflow.hideDetails
                    : t.agentWorkflow.showDetails}
                </span>
              </button>
            )}
          </div>

          {expanded && hasDetails && (
            <div className="mt-2 space-y-2 rounded-md border border-border-default bg-muted/30 p-2">
              {step.inputText && (
                <div className="space-y-1">
                  <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground/70">
                    {t.agentWorkflow.input}
                  </div>
                  <pre className="whitespace-pre-wrap break-all font-mono text-xs text-muted-foreground/85">
                    {step.inputText}
                  </pre>
                </div>
              )}
              {step.outputText && (
                <div className="space-y-1">
                  <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground/70">
                    {t.agentWorkflow.result}
                  </div>
                  <pre className="whitespace-pre-wrap break-all font-mono text-xs text-success/85 dark:text-success/85">
                    {step.outputText}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {step.subSteps && step.subSteps.length > 0 && (
        <div className="mt-1">
          {step.subSteps.map((subStep, idx) => (
            <StepItem
              key={subStep.id}
              step={subStep}
              isLast={idx === (step.subSteps ?? []).length - 1}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function AgentWorkflowPanel({
  steps,
  currentStepId: _currentStepId,
  isRunning,
  className,
}: AgentWorkflowPanelProps) {
  const { t } = useI18n();
  const completedCount = steps.filter((s) => s.status === "completed").length;
  const progress = steps.length > 0 ? (completedCount / steps.length) * 100 : 0;

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-2">
          <BotIcon className="size-4 text-chart-1" />
          <span className="text-sm font-medium">{t.agentWorkflow.title}</span>
        </div>
        {isRunning && (
          <div className="flex items-center gap-1.5 text-xs text-chart-1">
            <Loader2Icon className="size-3 animate-spin" />
            <span>{t.agentWorkflow.running}</span>
          </div>
        )}
      </div>

      <div className="px-3 py-2">
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="text-muted-foreground">
            {t.agentWorkflow.progress}
          </span>
          <span className="text-muted-foreground">
            {completedCount}/{steps.length}
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full transition-colors duration-slow",
              isRunning ? "bg-chart-1" : "bg-success",
            )}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className="flex-1 overflow-auto px-3 py-2">
        {steps.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-muted-foreground/50">
            <BotIcon className="mb-2 size-8 opacity-50" />
            <span className="text-xs">{t.agentWorkflow.empty}</span>
          </div>
        ) : (
          <div className="space-y-0">
            {steps.map((step, idx) => (
              <StepItem
                key={step.id}
                step={step}
                isLast={idx === steps.length - 1}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
