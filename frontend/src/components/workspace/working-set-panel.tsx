import {
  BrainIcon,
  CheckCircle2Icon,
  CircleIcon,
  EyeIcon,
  FileCodeIcon,
  Loader2Icon,
  PencilIcon,
  FolderOpenIcon,
} from "lucide-react";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export interface WorkingSetFile {
  path: string;
  last_read_at: number;
  last_modified_at: number;
  tokens_estimated: number;
  relevance: string;
}

export type ThinkingPlanStepStatus = "pending" | "in_progress" | "completed";

export interface ThinkingPlanStepSnapshot {
  title: string;
  detail?: string;
  status?: ThinkingPlanStepStatus;
}

export interface ThinkingPlanSnapshot {
  mode?: string;
  goal?: string;
  steps: ThinkingPlanStepSnapshot[];
  progress?: number;
  current_step_index?: number | null;
}

interface WorkingSetPanelProps {
  files: WorkingSetFile[];
  currentPhase: string;
  progressSummary: string;
  thinkingPlan?: ThinkingPlanSnapshot | null;
  className?: string;
}

const getPhaseConfig = (t: {
  workingSet?: { understand?: string; execute?: string; verify?: string };
}) => ({
  understand: {
    label: t.workingSet?.understand,
    color: "text-info dark:text-info",
    bg: "bg-info/10",
    icon: EyeIcon,
  },
  execute: {
    label: t.workingSet?.execute,
    color: "text-chart-1 dark:text-chart-1",
    bg: "bg-chart-1/10",
    icon: PencilIcon,
  },
  verify: {
    label: t.workingSet?.verify,
    color: "text-success",
    bg: "bg-success/10",
    icon: FileCodeIcon,
  },
});

function FileItem({ file }: { file: WorkingSetFile }) {
  const isEditing = file.relevance === "editing";
  const shortPath = file.path.split(/[\\/]/).slice(-2).join("/") || file.path;

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors",
        isEditing
          ? "bg-chart-1/5 border border-chart-1/20"
          : "bg-muted/30 border border-transparent",
      )}
    >
      {isEditing ? (
        <PencilIcon className="size-3 shrink-0 text-chart-1" />
      ) : (
        <FileCodeIcon className="size-3 shrink-0 text-muted-foreground/60" />
      )}
      <span
        className={cn(
          "truncate font-mono",
          isEditing
            ? "text-chart-1 dark:text-chart-1"
            : "text-muted-foreground",
        )}
        title={file.path}
      >
        {shortPath}
      </span>
      {file.tokens_estimated > 0 && (
        <span className="ml-auto shrink-0 text-xs text-muted-foreground/50 tabular-nums">
          ~
          {file.tokens_estimated > 1000
            ? `${Math.round(file.tokens_estimated / 1000)}k`
            : file.tokens_estimated}
        </span>
      )}
    </div>
  );
}

function ThinkingStepIcon({ status }: { status?: ThinkingPlanStepStatus }) {
  if (status === "completed") {
    return (
      <CheckCircle2Icon className="mt-0.5 size-3 shrink-0 text-success" />
    );
  }
  if (status === "in_progress") {
    return (
      <Loader2Icon className="mt-0.5 size-3 shrink-0 animate-spin text-primary" />
    );
  }
  return (
    <CircleIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground/45" />
  );
}

const TEMPLATE_THINKING_STEP_PATTERNS = [
  /frame the ask/i,
  /gather context/i,
  /reason across options/i,
  /verify/i,
  /answer/i,
  /明确(?:你|用户)?的问题目标/,
  /结合当前上下文组织回答路径/,
  /检查是否需要补充信息/,
];

function isTemplateThinkingPlan(steps: ThinkingPlanStepSnapshot[]): boolean {
  if (steps.length < 3) return false;
  const templateMatches = steps.filter((step) =>
    TEMPLATE_THINKING_STEP_PATTERNS.some((pattern) => pattern.test(step.title)),
  ).length;
  return templateMatches >= Math.min(3, steps.length);
}

function ThinkingPlanMini({ plan }: { plan: ThinkingPlanSnapshot }) {
  const steps = (Array.isArray(plan.steps) ? plan.steps : []).filter(
    (step) => typeof step.title === "string" && step.title.trim().length > 0,
  );
  if (steps.length === 0) return null;
  if (isTemplateThinkingPlan(steps)) return null;

  const completed = steps.filter((step) => step.status === "completed").length;
  const progress =
    typeof plan.progress === "number"
      ? Math.max(0, Math.min(1, plan.progress))
      : completed / Math.max(1, steps.length);
  const currentIndex =
    typeof plan.current_step_index === "number"
      ? Math.max(0, Math.min(steps.length - 1, plan.current_step_index))
      : steps.findIndex((step) => step.status === "in_progress");
  const currentStep =
    steps[
      currentIndex >= 0 ? currentIndex : Math.min(completed, steps.length - 1)
    ];

  return (
    <div className="border-b border-border-default px-3 py-2">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <BrainIcon className="size-3.5 shrink-0 text-primary" />
          <span className="truncate text-xs font-medium">
            Thinking progress
          </span>
        </div>
        <span className="shrink-0 text-xs text-muted-foreground/60 tabular-nums">
          {completed}/{steps.length}
        </span>
      </div>
      <div
        aria-label="Thinking progress"
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={Math.round(progress * 100)}
        className="h-1.5 overflow-hidden rounded-full bg-muted"
        role="progressbar"
      >
        <div
          className="h-full rounded-full bg-primary/70 transition-all duration-slow"
          style={{ width: `${Math.round(progress * 100)}%` }}
        />
      </div>
      {currentStep && (
        <div className="mt-2 rounded-md bg-muted/40 px-2 py-1.5">
          <div className="text-xs font-medium uppercase text-muted-foreground/55">
            Current
          </div>
          <div className="mt-0.5 truncate text-xs" title={currentStep.title}>
            {currentStep.title}
          </div>
          {currentStep.detail && (
            <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground/65">
              {currentStep.detail}
            </div>
          )}
        </div>
      )}
      <ol className="mt-2 space-y-1">
        {steps.map((step, index) => (
          <li
            key={`${step.title}-${index}`}
            className="flex min-w-0 gap-1.5 text-xs"
          >
            <ThinkingStepIcon status={step.status} />
            <span
              className={cn(
                "truncate",
                step.status === "completed" && "text-muted-foreground/60",
                step.status === "in_progress" && "font-medium text-foreground",
              )}
              title={step.title}
            >
              {step.title}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function WorkingSetPanel({
  files,
  currentPhase,
  progressSummary,
  thinkingPlan,
  className,
}: WorkingSetPanelProps) {
  const { t } = useI18n();
  const PHASE_CONFIG = getPhaseConfig(t);
  const phaseCfg =
    PHASE_CONFIG[currentPhase as keyof typeof PHASE_CONFIG] ??
    PHASE_CONFIG.understand;
  const PhaseIcon = phaseCfg.icon;

  const editingFiles = files.filter((f) => f.relevance === "editing");
  const readingFiles = files.filter((f) => f.relevance !== "editing");

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-2">
          <FolderOpenIcon className="size-4 text-primary" />
          <span className="text-sm font-medium">{t.workingSet?.title}</span>
        </div>
        <div
          className={cn(
            "flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium",
            phaseCfg.bg,
            phaseCfg.color,
          )}
        >
          <PhaseIcon className="size-3" />
          <span>{phaseCfg.label}</span>
        </div>
      </div>

      {progressSummary && (
        <div className="border-b border-border-default px-3 py-1.5">
          <p className="text-xs text-muted-foreground/70 line-clamp-2">
            {progressSummary}
          </p>
        </div>
      )}

      {thinkingPlan && <ThinkingPlanMini plan={thinkingPlan} />}

      <div className="flex-1 overflow-auto px-3 py-2">
        {files.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-muted-foreground/50">
            <FolderOpenIcon className="mb-2 size-8 opacity-50" />
            <span className="text-xs">{t.workingSet?.empty}</span>
          </div>
        ) : (
          <div className="space-y-3">
            {editingFiles.length > 0 && (
              <div>
                <div className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-chart-1/70 dark:text-chart-1/70">
                  <PencilIcon className="size-2.5" />
                  <span>{t.workingSet?.editing}</span>
                  <span className="text-muted-foreground/40">
                    ({editingFiles.length})
                  </span>
                </div>
                <div className="space-y-0.5">
                  {editingFiles.map((f) => (
                    <FileItem key={f.path} file={f} />
                  ))}
                </div>
              </div>
            )}
            {readingFiles.length > 0 && (
              <div>
                <div className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground/50">
                  <EyeIcon className="size-2.5" />
                  <span>{t.workingSet?.reading}</span>
                  <span className="text-muted-foreground/40">
                    ({readingFiles.length})
                  </span>
                </div>
                <div className="space-y-0.5">
                  {readingFiles.map((f) => (
                    <FileItem key={f.path} file={f} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {files.length > 0 && (
        <div className="border-t border-border-default px-3 py-1.5">
          <div className="flex items-center justify-between text-xs text-muted-foreground/50">
            <span>
              {editingFiles.length} {t.workingSet?.editing} ·{" "}
              {readingFiles.length} {t.workingSet?.reading}
            </span>
            <span>
              ~
              {Math.round(
                files.reduce((sum, f) => sum + f.tokens_estimated, 0) / 1000,
              )}
              k tokens
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
