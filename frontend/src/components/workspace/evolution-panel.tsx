import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BookOpenTextIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  HistoryIcon,
  RefreshCcwIcon,
  ShieldAlertIcon,
  SlidersHorizontalIcon,
  XIcon,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { useI18n } from "@/core/i18n/hooks";
import {
  forgetMemory,
  forgetRule,
  kickReflection,
  type EvolutionStatus,
  type ReActVariantStat,
  type ReflectionReport,
} from "@/core/observability/api";
import { cn } from "@/lib/utils";

interface EvolutionPanelProps {
  status: EvolutionStatus;
  trigger: React.ReactNode;
}

type EvolutionView = "rules" | "memories" | "history" | "react";

export function EvolutionPanel({ status, trigger }: EvolutionPanelProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [activeView, setActiveView] = useState<EvolutionView>("rules");
  const qc = useQueryClient();

  const reflectMutation = useMutation<ReflectionReport, Error>({
    mutationFn: kickReflection,
    onSuccess: (report) => {
      if (report.error) {
        toast.info(t.evolutionPanel.toastReflectSkipped(report.error));
      } else {
        const rules = report.rule_extractor?.rules ?? 0;
        const mems = report.memory?.memories ?? 0;
        toast.success(t.evolutionPanel.toastReflectSuccess(rules, mems));
      }
      // Implementation note.
      qc.invalidateQueries({ queryKey: ["evolution", "status"] });
    },
    onError: (e) => toast.error(t.evolutionPanel.toastReflectFailed(e.message)),
  });

  const rules = status.rules_count ?? 0;
  const memories = status.memories_count ?? 0;
  const totalTrajs = status.trajectories?.total ?? 0;
  const reactTrajs = status.trajectories?.react_loop ?? 0;
  const reactFails = status.trajectories?.react_loop_failures ?? 0;
  const rulesLines = status.rules_lines ?? [];
  const memoriesLines = status.memories_lines ?? [];
  const variants = status.react_variants ?? [];
  const learnedTotal = rules + memories;
  const summary =
    learnedTotal > 0
      ? t.evolutionPanel.summaryReady(learnedTotal, totalTrajs)
      : t.evolutionPanel.summaryEmpty;
  const health =
    reactFails > 0
      ? t.evolutionPanel.summaryFailures(reactFails)
      : t.evolutionPanel.summaryHealthy;

  const forgetRuleMutation = useMutation({
    mutationFn: (index: number) => forgetRule(index),
    onSuccess: () => {
      toast.success(t.evolutionPanel.toastForgetRuleSuccess);
      qc.invalidateQueries({ queryKey: ["evolution", "status"] });
    },
    onError: (e: Error) =>
      toast.error(t.evolutionPanel.toastDeleteFailed(e.message)),
  });
  const forgetMemoryMutation = useMutation({
    mutationFn: (index: number) => forgetMemory(index),
    onSuccess: () => {
      toast.success(t.evolutionPanel.toastForgetMemorySuccess);
      qc.invalidateQueries({ queryKey: ["evolution", "status"] });
    },
    onError: (e: Error) =>
      toast.error(t.evolutionPanel.toastDeleteFailed(e.message)),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="flex h-[86vh] sm:!max-w-3xl flex-col overflow-hidden p-0">
        <DialogHeader className="border-b border-border-default px-5 py-4 pr-12">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <DialogTitle className="flex items-center gap-2 text-lg">
                <BrainCircuitIcon className="size-5" />
                {t.evolutionPanel.title}
              </DialogTitle>
              <DialogDescription className="sr-only">
                {t.evolutionPanel.description}
              </DialogDescription>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">
                  {summary}
                </span>
                <span
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                    reactFails > 0
                      ? "border-warning/70 bg-warning/5 text-warning dark:border-warning/30 dark:bg-warning/10 dark:text-warning"
                      : "border-success/70 bg-success/5 text-success dark:border-success/30 dark:bg-success/10 dark:text-success",
                  )}
                >
                  <CheckCircle2Icon className="size-3" />
                  {reactFails > 0
                    ? t.evolutionPanel.statusNeedsReview
                    : t.evolutionPanel.statusNormal}
                </span>
                <span className="sr-only">{health}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={() => reflectMutation.mutate()}
              disabled={reflectMutation.isPending}
              className={cn(
                "flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 text-sm font-medium",
                "bg-foreground text-background transition-colors hover:bg-foreground/90",
                "disabled:opacity-50 disabled:cursor-not-allowed",
              )}
            >
              <RefreshCcwIcon
                className={cn(
                  "size-3.5",
                  reflectMutation.isPending && "animate-spin",
                )}
              />
              {reflectMutation.isPending
                ? t.evolutionPanel.reflectingButton
                : t.evolutionPanel.reflectButton}
            </button>
          </div>

          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <StatTab
              active={activeView === "rules"}
              description={t.evolutionPanel.statAvoidRuleHint}
              icon={ShieldAlertIcon}
              label={t.evolutionPanel.statAvoidRule}
              onClick={() => setActiveView("rules")}
              tooltip={t.evolutionPanel.statAvoidRuleTooltip}
              value={rules}
              highlight={rules > 0}
            />
            <StatTab
              active={activeView === "memories"}
              description={t.evolutionPanel.statPatternMemHint}
              icon={BookOpenTextIcon}
              label={t.evolutionPanel.statPatternMem}
              onClick={() => setActiveView("memories")}
              tooltip={t.evolutionPanel.statPatternMemTooltip}
              value={memories}
              highlight={memories > 0}
            />
            <StatTab
              active={activeView === "history"}
              description={t.evolutionPanel.statAllTrajsHint}
              icon={HistoryIcon}
              label={t.evolutionPanel.statAllTrajs}
              onClick={() => setActiveView("history")}
              tooltip={t.evolutionPanel.statAllTrajsTooltip(totalTrajs)}
              value={totalTrajs}
            />
            <StatTab
              active={activeView === "react"}
              description={t.evolutionPanel.statReactHint(reactFails)}
              icon={CheckCircle2Icon}
              label={t.evolutionPanel.statReactLabel}
              onClick={() => setActiveView("react")}
              tooltip={t.evolutionPanel.statReactTooltip(
                reactTrajs,
                reactFails,
              )}
              value={t.evolutionPanel.statReactValue(reactTrajs, reactFails)}
              highlight={reactFails > 0}
            />
          </div>
          <span className="sr-only">
            {t.evolutionPanel.statAvoidRuleDesc}
            {t.evolutionPanel.statPatternMemDesc}
            {t.evolutionPanel.statAllTrajsDesc}
            {t.evolutionPanel.statReactDesc}
            {t.evolutionPanel.reflectHint}
          </span>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {activeView === "rules" && (
            <LearningList
              title={t.evolutionPanel.learnedMitigationsTitle}
              description={t.evolutionPanel.learnedMitigationsDesc}
              emptyHint={t.evolutionPanel.noMitigationsHint}
              lines={rulesLines}
              onDelete={(i) => forgetRuleMutation.mutate(i)}
              isDeletingIndex={
                forgetRuleMutation.isPending
                  ? forgetRuleMutation.variables
                  : undefined
              }
            />
          )}
          {activeView === "memories" && (
            <LearningList
              title={t.evolutionPanel.consolidatedMemoriesTitle}
              description={t.evolutionPanel.consolidatedMemoriesDesc}
              emptyHint={t.evolutionPanel.noMemoriesHint}
              lines={memoriesLines}
              onDelete={(i) => forgetMemoryMutation.mutate(i)}
              isDeletingIndex={
                forgetMemoryMutation.isPending
                  ? forgetMemoryMutation.variables
                  : undefined
              }
            />
          )}
          {activeView === "history" && (
            <MetricDetail
              description={t.evolutionPanel.statAllTrajsDesc}
              icon={HistoryIcon}
              label={t.evolutionPanel.statAllTrajs}
              points={t.evolutionPanel.statAllTrajsPoints(
                totalTrajs,
                learnedTotal,
              )}
              summary={summary}
              value={totalTrajs}
            />
          )}
          {activeView === "react" && (
            <MetricDetail
              description={t.evolutionPanel.statReactDesc}
              icon={CheckCircle2Icon}
              label={t.evolutionPanel.statReactLabel}
              points={t.evolutionPanel.statReactPoints(reactTrajs, reactFails)}
              summary={health}
              value={t.evolutionPanel.statReactValue(reactTrajs, reactFails)}
            />
          )}
          {(activeView === "history" || activeView === "react") &&
            variants.length > 0 && (
              <details className="group rounded-lg border border-border-default bg-muted/15">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium">
                  <span className="inline-flex items-center gap-2">
                    <SlidersHorizontalIcon className="size-4 text-muted-foreground" />
                    {t.evolutionPanel.advancedTitle}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {t.evolutionPanel.linesSuffix(variants.length)}
                  </span>
                </summary>
                <div className="border-t border-border-subtle p-4">
                  <ReActVariantsTable variants={variants} />
                </div>
              </details>
            )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function StatTab({
  active,
  description,
  highlight,
  icon: Icon,
  label,
  onClick,
  tooltip,
  value,
}: {
  active: boolean;
  description: string;
  icon: typeof BrainCircuitIcon;
  label: string;
  onClick: () => void;
  tooltip: string;
  value: string | number;
  highlight?: boolean;
}) {
  const button = (
    <button
      type="button"
      aria-pressed={active}
      title={tooltip}
      onClick={onClick}
      className={cn(
        "grid min-h-16 min-w-0 grid-cols-[auto_1fr] items-center gap-x-2 gap-y-0.5 rounded-md border px-3 py-2 text-left",
        "transition-colors hover:border-border hover:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary/55 bg-primary/10 text-foreground"
          : "border-border-default bg-background",
      )}
    >
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <div
        className={cn(
          "text-base font-semibold leading-5 tabular-nums",
          highlight ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {value}
      </div>
      <div className="col-start-2 min-w-0 text-xs font-medium leading-4 text-muted-foreground">
        {label}
      </div>
      <div className="col-span-2 min-w-0 text-xs leading-4 text-muted-foreground">
        {description}
      </div>
    </button>
  );
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-64 leading-5">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}

function MetricDetail({
  description,
  icon: Icon,
  label,
  points,
  summary,
  value,
}: {
  description: string;
  icon: typeof BrainCircuitIcon;
  label: string;
  points?: string[];
  summary: string;
  value: string | number;
}) {
  return (
    <section className="rounded-lg border border-border-default bg-muted/15 p-4">
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-background">
          <Icon className="size-4 text-muted-foreground" />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-medium text-muted-foreground">
            {label}
          </div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">
            {value}
          </div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {description}
          </p>
          <p className="mt-2 text-sm leading-6 text-foreground">{summary}</p>
          {points && points.length > 0 && (
            <ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
              {points.map((point) => (
                <li key={point} className="flex gap-2">
                  <span className="mt-2 size-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

function LearningList({
  description,
  emptyHint,
  isDeletingIndex,
  lines,
  onDelete,
  title,
}: {
  description: string;
  emptyHint: string;
  isDeletingIndex: number | undefined;
  lines: string[];
  onDelete: (index: number) => void;
  title: string;
}) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const learningCopy = {
    failureGeneric: t.evolutionPanel.failureGeneric,
    failureReadBeforeWrite: t.evolutionPanel.failureReadBeforeWrite,
    failureTypeError: t.evolutionPanel.failureTypeError,
    toolFailureLesson: t.evolutionPanel.toolFailureLesson,
  };
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold tracking-normal">{title}</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            {description}
          </p>
        </div>
        <span className="mt-1 shrink-0 text-xs tabular-nums text-muted-foreground">
          {lines.length > 0 ? t.evolutionPanel.linesSuffix(lines.length) : ""}
        </span>
      </div>
      {lines.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border-default bg-muted/20 px-3 py-3 text-sm leading-6 text-muted-foreground">
          {emptyHint}
        </p>
      ) : (
        <ul className="space-y-2">
          {lines.map((line, i) => (
            <li
              key={`${i}-${line.slice(0, 32)}`}
              className={cn(
                "group flex items-start gap-3 rounded-lg border border-border-default",
                "bg-muted/15 px-3 py-2 transition-colors hover:border-border-strong",
                isDeletingIndex === i && "opacity-40",
              )}
            >
              <CheckCircle2Icon className="mt-0.5 size-4 shrink-0 text-success" />
              <div className="min-w-0 flex-1">
                <div className="break-words text-sm leading-6">
                  {friendlyLearningLine(line, learningCopy)}
                </div>
                <div className="sr-only">{t.evolutionPanel.nextRunImpact}</div>
              </div>
              <button
                type="button"
                onClick={async () => {
                  if (
                    !(await confirm({
                      title: t.evolutionPanel.forgetConfirmTitle,
                      description:
                        t.evolutionPanel.forgetConfirmDescription,
                      confirmLabel: t.evolutionPanel.forgetLineButton,
                    }))
                  )
                    return;
                  onDelete(i);
                }}
                disabled={isDeletingIndex !== undefined}
                className={cn(
                  "flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-xs",
                  "text-muted-foreground hover:bg-destructive/10 hover:text-destructive",
                  "opacity-0 transition-opacity group-hover:opacity-100",
                  "disabled:cursor-not-allowed",
                )}
                title={t.evolutionPanel.forgetLineTitle}
              >
                <XIcon className="size-3" />
                {t.evolutionPanel.forgetLineButton}
              </button>
            </li>
          ))}
        </ul>
      )}
      {confirmDialog}
    </section>
  );
}

function ReActVariantsTable({ variants }: { variants: ReActVariantStat[] }) {
  const { t } = useI18n();
  return (
    <div>
      <div className="mb-2 text-sm font-medium">
        {t.evolutionPanel.reactVariantsTitle}
      </div>
      <div className="overflow-hidden rounded-md border border-border-subtle">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-muted-foreground">
            <tr>
              <th className="px-2 py-2 text-left">
                {t.evolutionPanel.tableName}
              </th>
              <th className="px-2 py-2 text-left">
                {t.evolutionPanel.tableSetting}
              </th>
              <th className="px-2 py-2 text-right">
                {t.evolutionPanel.tableAttempts}
              </th>
              <th className="px-2 py-2 text-right">
                {t.evolutionPanel.tableSuccessRate}
              </th>
            </tr>
          </thead>
          <tbody>
            {variants.map((v) => (
              <tr key={v.name} className="border-t border-border-subtle">
                <td className="px-2 py-2 font-medium">
                  {friendlyVariantName(v.name)}
                </td>
                <td className="px-2 py-2 text-muted-foreground">
                  {t.evolutionPanel.variantSetting(
                    v.max_iterations,
                    v.temperature.toFixed(1),
                  )}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {v.assignments}
                </td>
                <td
                  className={cn(
                    "px-2 py-2 text-right tabular-nums",
                    v.assignments === 0 && "text-muted-foreground/40",
                  )}
                >
                  {v.assignments === 0
                    ? "—"
                    : `${(v.success_rate * 100).toFixed(0)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type LearningLineCopy = {
  failureGeneric: (failure: string) => string;
  failureReadBeforeWrite: string;
  failureTypeError: string;
  toolFailureLesson: (tool: string, failure: string, count: number) => string;
};

function friendlyLearningLine(line: string, copy: LearningLineCopy) {
  const cleaned = line.replace(/^\[[^\]]+\]\s*/, "").trim();
  const toolFailure = cleaned.match(
    /^When calling '([^']+)' with args=\[[^\]]*\], failure signature '([^']+)' seen (\d+) times\b/is,
  );
  if (toolFailure) {
    const [, tool, failure, count] = toolFailure;
    if (tool && failure && count) {
      return copy.toolFailureLesson(
        tool,
        friendlyFailureSignature(failure, copy),
        Number(count),
      );
    }
  }

  return cleaned
    .replace(/react_arm\/react_loop/g, "深度任务执行")
    .replace(/\bruns?\b/gi, "次验证")
    .replace(/\bsuccess\b/gi, "成功")
    .replace(/\bavg\b/gi, "平均")
    .replace(/\bsteps?\b/gi, "步")
    .replace(/\btotal\b/gi, "总成本")
    .replace(/\s*(?:Â·|·)\s*/g, " · ");
}

function friendlyFailureSignature(failure: string, copy: LearningLineCopy) {
  if (failure.includes("read_before_write_required")) {
    return copy.failureReadBeforeWrite;
  }
  if (failure.includes("TypeError")) {
    return copy.failureTypeError;
  }
  return copy.failureGeneric(failure);
}

function friendlyVariantName(name: string) {
  const lower = name.toLowerCase();
  if (lower.includes("conservative")) return "稳妥模式";
  if (lower.includes("balanced")) return "均衡模式";
  if (lower.includes("aggressive")) return "探索模式";
  return name;
}
