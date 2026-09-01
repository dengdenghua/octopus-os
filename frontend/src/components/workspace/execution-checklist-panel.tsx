"use client";

import {
  CheckCircle2Icon,
  CircleIcon,
  Loader2Icon,
  ListChecksIcon,
} from "lucide-react";
import { useMemo } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales";
import { cn } from "@/lib/utils";

import type { LiveToolEvent } from "./live-tool-timeline";
import {
  isFileMutationToolName,
  isReadToolName,
  isSearchToolName,
  isShellToolName,
} from "./tool-name-groups";

type StepStatus = "pending" | "in_progress" | "completed";

interface ChecklistStep {
  label: string;
  detail?: string;
  status: StepStatus;
}

interface ExecutionChecklistPanelProps {
  liveToolEvents: LiveToolEvent[];
  hasAnswer?: boolean;
  isRunning?: boolean;
  className?: string;
}

function hasTool(
  events: LiveToolEvent[],
  predicate: (name: string) => boolean,
) {
  return events.some((event) => predicate(event.name));
}

function hasRunningTool(events: LiveToolEvent[]) {
  return events.some(
    (event) =>
      event.status === "running" || event.status === "waiting_approval",
  );
}

function queryText(event: LiveToolEvent): string {
  const query = event.input?.query;
  return typeof query === "string" ? query.trim() : "";
}

function classifySearchQuery(
  query: string,
  index: number,
  t: Translations,
): string {
  if (/规模|增[长長]|CAGR|market size|forecast/i.test(query)) {
    return t.executionChecklist.marketSize;
  }
  if (
    /品牌|竞争|格局|company|companies|Oura|Eight Sleep|床垫|床墊/i.test(query)
  ) {
    return t.executionChecklist.competition;
  }
  if (/技术|technology|AI|sensor|wearable|产品|product/i.test(query)) {
    return t.executionChecklist.technology;
  }
  if (/消费|需求|痛点|睡眠经济|consumer|demand|pain/i.test(query)) {
    return t.executionChecklist.consumerDemand;
  }
  return t.executionChecklist.evidenceRound(index + 1);
}

function buildToolStep(
  events: LiveToolEvent[],
  t: Translations,
): ChecklistStep | null {
  if (events.length === 0) return null;
  const running = hasRunningTool(events);
  const parts: string[] = [];
  const searchCount = events.filter((event) =>
    isSearchToolName(event.name),
  ).length;
  if (searchCount > 0) parts.push(t.executionChecklist.webSearch(searchCount));
  if (hasTool(events, isReadToolName))
    parts.push(t.executionChecklist.readContext);
  if (hasTool(events, isFileMutationToolName))
    parts.push(t.executionChecklist.writeFile);
  if (hasTool(events, isShellToolName))
    parts.push(t.executionChecklist.runCommand);
  if (parts.length === 0) {
    parts.push(t.executionChecklist.callTool(events.length));
  }

  return {
    label: parts.join("、"),
    detail: t.executionChecklist.toolCallDetail,
    status: running ? "in_progress" : "completed",
  };
}

function buildResearchIterationSteps(
  events: LiveToolEvent[],
  t: Translations,
): ChecklistStep[] {
  const searches = events
    .filter((event) => isSearchToolName(event.name))
    .sort((a, b) => a.startedAt - b.startedAt);
  if (searches.length === 0) return [];

  const steps: ChecklistStep[] = [];
  searches.forEach((event, index) => {
    const query = queryText(event);
    const settled =
      event.status !== "running" && event.status !== "waiting_approval";
    const classifiedQuery = classifySearchQuery(query, index, t);
    steps.push({
      label: t.executionChecklist.searchRound(index + 1, classifiedQuery),
      detail: query
        ? `${t.executionChecklist.queryPrefix}${query}`
        : t.executionChecklist.continueFromPrevious,
      status: settled ? "completed" : "in_progress",
    });
    if (index < searches.length - 1) {
      steps.push({
        label: t.executionChecklist.adjustKeywords(index + 1),
        detail: t.executionChecklist.adjustKeywordsDetail,
        status: settled ? "completed" : "pending",
      });
    }
  });
  return steps;
}

function buildSteps(
  events: LiveToolEvent[],
  hasAnswer: boolean,
  isRunning: boolean,
  t: Translations,
): ChecklistStep[] {
  const researchSteps = buildResearchIterationSteps(events, t);
  const toolStep = buildToolStep(events, t);
  const running = hasRunningTool(events);
  const toolsSettled = events.length === 0 || !running;
  const generating = hasAnswer && isRunning;

  const steps: ChecklistStep[] = [
    {
      label: t.executionChecklist.clarifyGoal,
      detail: t.executionChecklist.clarifyGoalDetail,
      status: "completed",
    },
  ];

  if (researchSteps.length > 0) {
    steps.push(...researchSteps);
  } else if (toolStep) {
    steps.push(toolStep);
  }

  steps.push({
    label: t.executionChecklist.analyzeAndAlign,
    detail: t.executionChecklist.analyzeAndAlignDetail,
    status: hasAnswer ? "completed" : toolsSettled ? "in_progress" : "pending",
  });

  steps.push({
    label: t.executionChecklist.generateResponse,
    detail: t.executionChecklist.generateResponseDetail,
    status:
      hasAnswer && !isRunning
        ? "completed"
        : generating
          ? "in_progress"
          : "pending",
  });

  return steps;
}

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "completed") {
    return <CheckCircle2Icon className="size-4 shrink-0 text-success" />;
  }
  if (status === "in_progress") {
    return (
      <Loader2Icon className="size-4 shrink-0 animate-spin text-info" />
    );
  }
  return <CircleIcon className="size-4 shrink-0 text-muted-foreground/50" />;
}

export function ExecutionChecklistPanel({
  liveToolEvents,
  hasAnswer = false,
  isRunning = false,
  className,
}: ExecutionChecklistPanelProps) {
  const { t } = useI18n();
  const hasExplicitTodos = liveToolEvents.some(
    (event) => event.name === "todo_write",
  );
  const steps = useMemo(
    () => buildSteps(liveToolEvents, hasAnswer, isRunning, t),
    [hasAnswer, isRunning, liveToolEvents, t],
  );

  if (hasExplicitTodos || liveToolEvents.length === 0) {
    return null;
  }

  const completed = steps.filter((step) => step.status === "completed").length;

  return (
    <div
      className={cn(
        "workspace-panel-subtle my-3 rounded-lg border border-border-default p-3",
        className,
      )}
    >
      <div className="mb-2 flex items-center justify-between">
        <div className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
          <ListChecksIcon className="size-3.5 text-chart-1" />
          {t.executionChecklist.title}
        </div>
        <div className="text-xs tabular-nums text-muted-foreground">
          {completed}/{steps.length}
        </div>
      </div>
      <div className="space-y-1">
        {steps.map((step) => (
          <div key={step.label} className="flex items-start gap-2 py-1">
            <div className="mt-0.5">
              <StepIcon status={step.status} />
            </div>
            <div className="min-w-0">
              <div
                className={cn(
                  "text-sm leading-5",
                  step.status === "completed" &&
                    "text-muted-foreground line-through",
                  step.status === "in_progress" && "font-medium",
                )}
              >
                {step.label}
              </div>
              {step.detail && (
                <div className="mt-0.5 text-xs leading-4 text-muted-foreground/80">
                  {step.detail}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
