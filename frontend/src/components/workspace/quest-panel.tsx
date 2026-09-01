/**
 * Quest Panel — Autonomous task execution UI
 *
 * Displays the Quest Mode pipeline progress:
 *   Analyze -> Plan -> Execute -> Verify -> Report
 *
 * Supports:
 * - Phase stepper with animated transitions
 * - Plan review / approval before execution
 * - Live step-by-step execution progress
 * - Verification results with fix cycle tracking
 * - Final report card
 * - SSE streaming for real-time updates
 */

import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { openSseStream } from "@/core/streaming/sse";
import { useI18n } from "@/core/i18n/hooks";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClipboardCheckIcon,
  FileTextIcon,
  Loader2Icon,
  PlayIcon,
  RocketIcon,
  SearchIcon,
  ShieldCheckIcon,
  XCircleIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type QuestPhase =
  | "pending"
  | "analyzing"
  | "planning"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "reporting"
  | "completed"
  | "failed"
  | "cancelled";

interface QuestStep {
  step_id: string;
  order: number;
  title: string;
  description: string;
  tool_hint: string;
  status: string;
  estimated_complexity: string;
}

interface QuestStepResult {
  step_id: string;
  status: string;
  output: string;
  files_changed: string[];
  error: string | null;
  duration_ms: number;
}

interface QuestVerification {
  passed: boolean;
  tests_run: number;
  tests_passed: number;
  tests_failed: number;
  lint_errors: number;
  type_errors: number;
  requirement_checks: Array<{
    description: string;
    passed: boolean;
    details: string;
  }>;
  issues_found: string[];
  fix_attempts: number;
}

interface QuestReport {
  quest_id: string;
  requirement: string;
  status: string;
  summary: string;
  steps_completed: number;
  steps_total: number;
  files_changed: string[];
  verification: QuestVerification | null;
  duration_ms: number;
  remaining_todos: string[];
  error: string | null;
}

interface QuestPlan {
  steps: QuestStep[];
  total_estimated_complexity: string;
  strategy_notes: string;
}

interface QuestState {
  questId: string | null;
  phase: QuestPhase;
  plan: QuestPlan | null;
  stepResults: QuestStepResult[];
  currentStepIndex: number;
  verification: QuestVerification | null;
  report: QuestReport | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Phase definitions
// ---------------------------------------------------------------------------

const PHASES: {
  key: QuestPhase;
  labelKey: "analyze" | "plan" | "execute" | "verify" | "report";
  icon: React.ElementType;
}[] = [
  { key: "analyzing", labelKey: "analyze", icon: SearchIcon },
  { key: "planning", labelKey: "plan", icon: ClipboardCheckIcon },
  { key: "executing", labelKey: "execute", icon: PlayIcon },
  { key: "verifying", labelKey: "verify", icon: ShieldCheckIcon },
  { key: "reporting", labelKey: "report", icon: FileTextIcon },
];

const PHASE_ORDER: Record<string, number> = {
  pending: -1,
  analyzing: 0,
  planning: 1,
  awaiting_approval: 1.5,
  executing: 2,
  verifying: 3,
  reporting: 4,
  completed: 5,
  failed: 5,
  cancelled: 5,
};

function getPhaseIndex(phase: QuestPhase): number {
  return PHASE_ORDER[phase] ?? -1;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function startQuest(
  requirement: string,
  options?: {
    workspacePath?: string;
    modelName?: string;
    autoApprove?: boolean;
  },
): Promise<{ quest_id: string; stream_url: string }> {
  const base = getBackendBaseURL();
  const resp = await fetch(`${base}/api/quest/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      requirement,
      workspace_path: options?.workspacePath ?? "",
      model_name: options?.modelName ?? null,
      auto_approve: options?.autoApprove ?? false,
    }),
  });
  if (!resp.ok) throw new Error(`Failed to start quest: ${resp.statusText}`);
  return resp.json();
}

async function approvePlan(questId: string): Promise<void> {
  const base = getBackendBaseURL();
  const resp = await fetch(`${base}/api/quest/${questId}/approve`, {
    method: "POST",
  });
  if (!resp.ok) throw new Error(`Failed to approve plan: ${resp.statusText}`);
}

async function rejectPlan(questId: string): Promise<void> {
  const base = getBackendBaseURL();
  const resp = await fetch(`${base}/api/quest/${questId}/reject`, {
    method: "POST",
  });
  if (!resp.ok) throw new Error(`Failed to reject plan: ${resp.statusText}`);
}

async function cancelQuest(questId: string): Promise<void> {
  const base = getBackendBaseURL();
  const resp = await fetch(`${base}/api/quest/${questId}/cancel`, {
    method: "POST",
  });
  if (!resp.ok) throw new Error(`Failed to cancel quest: ${resp.statusText}`);
}

// ---------------------------------------------------------------------------
// SSE hook
// ---------------------------------------------------------------------------

function useQuestStream(questId: string | null): QuestState {
  const [state, setState] = useState<QuestState>({
    questId: null,
    phase: "pending",
    plan: null,
    stepResults: [],
    currentStepIndex: 0,
    verification: null,
    report: null,
    error: null,
  });

  useEffect(() => {
    if (!questId) return;

    setState((s) => ({ ...s, questId }));

    const base = getBackendBaseURL();
    const url = `${base}/api/quest/${questId}/stream`;

    return openSseStream({
      url,
      onEvent: (msg) => {
        if (msg.event === "complete") {
          // Quest finished — close cleanly, no reconnect.
          return true;
        }
        if (msg.event === "phase") {
          try {
            const data = JSON.parse(msg.data);
            setState((s) => ({
              ...s,
              phase: data.phase as QuestPhase,
            }));
          } catch (e) {
            swallow(e, "quest.phase");
          }
          return;
        }
        if (msg.event === "plan_ready") {
          try {
            const data = JSON.parse(msg.data);
            setState((s) => ({
              ...s,
              phase: "awaiting_approval" as QuestPhase,
              plan: data.plan as QuestPlan,
            }));
          } catch (e) {
            swallow(e, "quest.plan_ready");
          }
          return;
        }
        if (msg.event === "step_progress") {
          let data: { step_index?: number; result?: unknown };
          try {
            data = JSON.parse(msg.data);
          } catch (e) {
            swallow(e, "quest.step_progress");
            return;
          }
          setState((s) => {
            const updated = {
              ...s,
              currentStepIndex: data.step_index ?? s.currentStepIndex,
            };
            if (data.result) {
              // Avoid duplicates by step_id
              const existing = s.stepResults.find(
                (r) => r.step_id === (data.result as QuestStepResult).step_id,
              );
              if (!existing) {
                updated.stepResults = [
                  ...s.stepResults,
                  data.result as QuestStepResult,
                ];
              }
            }
            return updated;
          });
          return;
        }
        if (msg.event === "verification") {
          try {
            const data = JSON.parse(msg.data);
            setState((s) => ({
              ...s,
              verification: data.verification as QuestVerification,
            }));
          } catch (e) {
            swallow(e, "quest.verification");
          }
          return;
        }
        if (msg.event === "report") {
          try {
            const data = JSON.parse(msg.data);
            setState((s) => ({
              ...s,
              report: data.report as QuestReport,
            }));
          } catch (e) {
            swallow(e, "quest.report");
          }
          return;
        }
        if (msg.event === "error") {
          try {
            const data = JSON.parse(msg.data);
            setState((s) => ({
              ...s,
              phase: "failed" as QuestPhase,
              error: data.error ?? "Unknown error",
            }));
          } catch (e) {
            swallow(e);
            setState((s) => ({
              ...s,
              phase: "failed" as QuestPhase,
              error: "Connection lost",
            }));
          }
          // Server reported a terminal quest failure — no reconnect.
          return true;
        }
      },
      onError: () => {
        setState((s) => ({
          ...s,
          phase: "failed" as QuestPhase,
          error: "Connection lost",
        }));
      },
    });
  }, [questId]);

  return state;
}

// ---------------------------------------------------------------------------
// Phase Stepper Component
// ---------------------------------------------------------------------------

function PhaseStepper({ currentPhase }: { currentPhase: QuestPhase }) {
  const { t } = useI18n();
  const currentIdx = getPhaseIndex(currentPhase);

  return (
    <div className="flex items-center gap-1 border-b border-border-subtle bg-muted/20 px-4 py-2.5">
      {PHASES.map((phase, i) => {
        const Icon = phase.icon;
        const isActive = Math.floor(currentIdx) === i;
        const isComplete = currentIdx > i;
        const isFuture = currentIdx < i;

        return (
          <div key={phase.key} className="flex items-center">
            <div
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors duration-slow",
                isActive && "bg-primary/10 text-primary ring-1 ring-primary/30",
                isComplete && "text-success",
                isFuture && "text-muted-foreground/40",
              )}
            >
              {isActive ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : isComplete ? (
                <CheckCircle2Icon className="size-3.5" />
              ) : (
                <Icon className="size-3.5" />
              )}
              <span className={cn(i > 0 && "hidden sm:inline")}>
                {t.questMode[phase.labelKey]}
              </span>
            </div>
            {i < PHASES.length - 1 && (
              <div
                className={cn(
                  "mx-1 h-px w-4 transition-colors duration-slow",
                  isComplete
                    ? "bg-success dark:bg-success"
                    : "bg-border",
                )}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Plan Review Component
// ---------------------------------------------------------------------------

function PlanReview({
  plan,
  onApprove,
  onReject,
}: {
  plan: QuestPlan;
  onApprove: () => void;
  onReject: () => void;
}) {
  const { t } = useI18n();
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const toggleStep = (stepId: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  };

  return (
    <div className="space-y-3 px-4 py-3 animate-in fade-in duration-slow">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold">{t.questMode.executionPlan}</h4>
        <span
          className={cn(
            "rounded-lg px-2 py-0.5 text-xs font-medium",
            plan.total_estimated_complexity === "small" &&
              "bg-success/10 text-success",
            plan.total_estimated_complexity === "medium" &&
              "bg-warning/10 text-warning",
            plan.total_estimated_complexity === "large" &&
              "bg-destructive/10 text-destructive",
          )}
        >
          {plan.total_estimated_complexity} complexity
        </span>
      </div>

      {plan.strategy_notes && (
        <p className="text-muted-foreground text-xs">{plan.strategy_notes}</p>
      )}

      <div className="space-y-1">
        {plan.steps.map((step) => {
          const expanded = expandedSteps.has(step.step_id);
          return (
            <div
              key={step.step_id}
              className="rounded-lg border border-border-default bg-card text-card-foreground"
            >
              <button
                type="button"
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-accent/40"
                onClick={() => toggleStep(step.step_id)}
              >
                {expanded ? (
                  <ChevronDownIcon className="text-muted-foreground size-3" />
                ) : (
                  <ChevronRightIcon className="text-muted-foreground size-3" />
                )}
                <span className="text-muted-foreground font-mono">
                  {step.order}.
                </span>
                <span className="flex-1 font-medium">{step.title}</span>
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 text-xs",
                    step.estimated_complexity === "low" &&
                      "bg-success/10 text-success",
                    step.estimated_complexity === "medium" &&
                      "bg-warning/10 text-warning",
                    step.estimated_complexity === "high" &&
                      "bg-destructive/10 text-destructive",
                  )}
                >
                  {step.estimated_complexity}
                </span>
              </button>
              {expanded && (
                <div className="border-t px-3 py-2">
                  <p className="text-muted-foreground text-xs">
                    {step.description}
                  </p>
                  {step.tool_hint && (
                    <p className="text-muted-foreground mt-1 font-mono text-xs">
                      Tool: {step.tool_hint}
                    </p>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onReject}
          className="rounded-lg border border-border-default px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
        >
          {t.questMode.reject}
        </button>
        <button
          type="button"
          onClick={onApprove}
          className="bg-primary text-primary-foreground hover:bg-primary/90 rounded-lg px-3 py-1.5 text-xs font-medium shadow-[var(--shadow-xs)] shadow-primary/10 transition-colors"
        >
          {t.questMode.approveExecute}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Execution Progress Component
// ---------------------------------------------------------------------------

function ExecutionProgress({
  plan,
  stepResults,
  currentStepIndex,
}: {
  plan: QuestPlan;
  stepResults: QuestStepResult[];
  currentStepIndex: number;
}) {
  const { t } = useI18n();
  const resultMap = useMemo(() => {
    const map = new Map<string, QuestStepResult>();
    for (const r of stepResults) map.set(r.step_id, r);
    return map;
  }, [stepResults]);

  const completedCount = stepResults.filter(
    (r) => r.status === "completed",
  ).length;
  const progress =
    plan.steps.length > 0
      ? Math.round((completedCount / plan.steps.length) * 100)
      : 0;

  return (
    <div className="space-y-2 px-4 py-3 animate-in fade-in duration-slow">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">
          {t.questMode.execute} ({completedCount}/{plan.steps.length})
        </span>
        <span className="text-muted-foreground">{progress}%</span>
      </div>

      {/* Progress bar */}
      <div className="bg-primary/10 h-1.5 w-full overflow-hidden rounded-lg">
        <div
          className="bg-primary h-full rounded-lg transition-all duration-slow shadow-[var(--shadow-xs)] shadow-primary/20"
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="max-h-48 space-y-1 overflow-y-auto">
        {plan.steps.map((step, i) => {
          const result = resultMap.get(step.step_id);
          const isRunning = i === currentStepIndex && !result;
          const isComplete = result?.status === "completed";
          const isFailed = result?.status === "failed";

          return (
            <div
              key={step.step_id}
              className={cn(
                "flex items-start gap-2 rounded-lg px-3 py-2 text-xs transition-colors",
                isRunning && "bg-primary/5 ring-1 ring-primary/10",
              )}
            >
              {isRunning ? (
                <Loader2Icon className="text-primary mt-0.5 size-3.5 animate-spin" />
              ) : isComplete ? (
                <CheckCircle2Icon className="mt-0.5 size-3.5 text-success" />
              ) : isFailed ? (
                <XCircleIcon className="mt-0.5 size-3.5 text-destructive" />
              ) : (
                <div className="mt-0.5 size-3.5 rounded-lg border border-muted-foreground/30" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span
                    className={cn(
                      "truncate font-medium",
                      isComplete && "text-muted-foreground line-through",
                    )}
                  >
                    {step.title}
                  </span>
                  {result?.duration_ms !== undefined &&
                    result.duration_ms > 0 && (
                      <span className="text-muted-foreground shrink-0 text-xs">
                        {result.duration_ms < 1000
                          ? `${result.duration_ms}ms`
                          : `${(result.duration_ms / 1000).toFixed(1)}s`}
                      </span>
                    )}
                </div>
                {result?.output && (
                  <p className="text-muted-foreground mt-0.5 truncate">
                    {result.output}
                  </p>
                )}
                {result?.error && (
                  <p className="mt-0.5 truncate text-destructive">{result.error}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Verification Results Component
// ---------------------------------------------------------------------------

function VerificationResults({
  verification,
}: {
  verification: QuestVerification;
}) {
  const { t } = useI18n();
  return (
    <div className="space-y-2 px-4 py-3">
      <div className="flex items-center gap-2">
        {verification.passed ? (
          <CheckCircle2Icon className="size-4 text-success" />
        ) : (
          <AlertTriangleIcon className="size-4 text-warning" />
        )}
        <h4 className="text-sm font-semibold">
          {verification.passed
            ? t.questMode.verificationPassed
            : t.questMode.verificationIssues}
        </h4>
        {verification.fix_attempts > 0 && (
          <span className="text-muted-foreground text-xs">
            (fix cycle {verification.fix_attempts})
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2">
        {verification.tests_run > 0 && (
          <div className="rounded-lg border border-border-default bg-card p-2 text-center">
            <div className="text-sm font-bold">
              {verification.tests_passed}/{verification.tests_run}
            </div>
            <div className="text-muted-foreground text-xs">
              {t.questMode.tests}
            </div>
          </div>
        )}
        {verification.lint_errors > 0 && (
          <div className="rounded-lg border border-warning/30 bg-warning/5 p-2 text-center">
            <div className="text-sm font-bold text-warning">
              {verification.lint_errors}
            </div>
            <div className="text-muted-foreground text-xs">
              {t.questMode.lintErrors}
            </div>
          </div>
        )}
        {verification.type_errors > 0 && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-2 text-center">
            <div className="text-sm font-bold text-destructive">
              {verification.type_errors}
            </div>
            <div className="text-muted-foreground text-xs">
              {t.questMode.typeErrors}
            </div>
          </div>
        )}
      </div>

      {verification.requirement_checks.length > 0 && (
        <div className="space-y-1">
          <div className="text-muted-foreground text-xs font-medium uppercase">
            {t.questMode.requirementChecks}
          </div>
          {verification.requirement_checks.map((check, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs">
              {check.passed ? (
                <CheckCircle2Icon className="mt-0.5 size-3 text-success" />
              ) : (
                <XCircleIcon className="mt-0.5 size-3 text-destructive" />
              )}
              <span>{check.description}</span>
            </div>
          ))}
        </div>
      )}

      {verification.issues_found.length > 0 && (
        <div className="space-y-1">
          <div className="text-muted-foreground text-xs font-medium uppercase">
            {t.questMode.issues}
          </div>
          {verification.issues_found.map((issue, i) => (
            <div
              key={i}
              className="flex items-start gap-1.5 text-xs text-warning"
            >
              <AlertTriangleIcon className="mt-0.5 size-3" />
              <span>{issue}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Report Card Component
// ---------------------------------------------------------------------------

function ReportCard({ report }: { report: QuestReport }) {
  const { t } = useI18n();
  const isSuccess = report.status === "completed";

  return (
    <div className="space-y-3 px-4 py-3">
      <div className="flex items-center gap-2">
        {isSuccess ? (
          <CheckCircle2Icon className="size-5 text-success" />
        ) : (
          <XCircleIcon className="size-5 text-destructive" />
        )}
        <h4 className="text-sm font-semibold">
          {isSuccess ? t.questMode.questCompleted : t.questMode.questFailed}
        </h4>
        <span className="text-muted-foreground text-xs">
          {report.duration_ms < 1000
            ? `${report.duration_ms}ms`
            : `${(report.duration_ms / 1000).toFixed(1)}s`}
        </span>
      </div>

      <p className="text-sm">{report.summary}</p>

      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-lg border border-border-default bg-card p-2">
          <div className="font-bold">
            {report.steps_completed}/{report.steps_total}
          </div>
          <div className="text-muted-foreground">
            {t.questMode.stepsCompleted}
          </div>
        </div>
        <div className="rounded-lg border border-border-default bg-card p-2">
          <div className="font-bold">{report.files_changed.length}</div>
          <div className="text-muted-foreground">
            {t.questMode.filesChanged}
          </div>
        </div>
      </div>

      {report.files_changed.length > 0 && (
        <div className="space-y-1">
          <div className="text-muted-foreground text-xs font-medium uppercase">
            {t.questMode.changedFiles}
          </div>
          <div className="max-h-24 overflow-y-auto">
            {report.files_changed.map((f) => (
              <div key={f} className="font-mono text-xs">
                {f}
              </div>
            ))}
          </div>
        </div>
      )}

      {report.remaining_todos.length > 0 && (
        <div className="space-y-1">
          <div className="text-muted-foreground text-xs font-medium uppercase">
            {t.questMode.remainingTodos}
          </div>
          {report.remaining_todos.map((todo, i) => (
            <div
              key={i}
              className="text-muted-foreground flex items-start gap-1.5 text-xs"
            >
              <span className="text-warning">-</span>
              <span>{todo}</span>
            </div>
          ))}
        </div>
      )}

      {report.error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {report.error}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quest Start Form Component
// ---------------------------------------------------------------------------

function QuestStartForm({
  onStart,
}: {
  onStart: (requirement: string) => void;
}) {
  const { t } = useI18n();
  const [requirement, setRequirement] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = () => {
    const trimmed = requirement.trim();
    if (!trimmed) return;
    onStart(trimmed);
    setRequirement("");
  };

  return (
    <div className="space-y-3 px-4 py-3">
      <div className="flex items-center gap-2">
        <div className="flex size-6 items-center justify-center rounded-lg bg-primary/10">
          <RocketIcon className="text-primary size-3.5" />
        </div>
        <h4 className="text-sm font-semibold">{t.questMode.startQuest}</h4>
      </div>
      <p className="text-muted-foreground text-xs">
        {t.questMode.startDescription}
      </p>
      <textarea
        ref={textareaRef}
        value={requirement}
        onChange={(e) => setRequirement(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            handleSubmit();
          }
        }}
        placeholder="e.g. Add a dark mode toggle to the settings page with persistent preference storage..."
        className="bg-muted/30 text-foreground placeholder:text-muted-foreground/40 min-h-[80px] w-full resize-none rounded-lg border border-border-default px-3 py-2 text-sm outline-none transition-colors focus:border-primary/40 focus:ring-1 focus:ring-primary/30"
        rows={3}
      />
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground/50 text-xs">
          Ctrl+Enter to start
        </span>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!requirement.trim()}
          className={cn(
            "bg-primary text-primary-foreground flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-xs font-medium shadow-[var(--shadow-xs)] shadow-primary/10 transition-colors",
            requirement.trim()
              ? "hover:bg-primary/90"
              : "cursor-not-allowed opacity-50",
          )}
        >
          <RocketIcon className="size-3" />
          {t.questMode.startQuest}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main QuestPanel Component
// ---------------------------------------------------------------------------

interface QuestPanelProps {
  open: boolean;
  onClose: () => void;
  className?: string;
  workspacePath?: string;
}

export function QuestPanel({
  open,
  onClose,
  className,
  workspacePath,
}: QuestPanelProps) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [questId, setQuestId] = useState<string | null>(null);
  const quest = useQuestStream(questId);

  const handleStart = useCallback(
    async (requirement: string) => {
      try {
        const result = await startQuest(requirement, {
          workspacePath,
        });
        setQuestId(result.quest_id);
      } catch (err) {
        swallow(err);
        toast.error(t.questMode.startFailed);
      }
    },
    [workspacePath, t.questMode.startFailed],
  );

  const handleApprove = useCallback(async () => {
    if (!questId) return;
    try {
      await approvePlan(questId);
    } catch (err) {
      swallow(err);
      toast.error(t.questMode.approveFailed);
    }
  }, [questId, t.questMode.approveFailed]);

  const handleReject = useCallback(async () => {
    if (!questId) return;
    if (
      !(await confirm({
        title: t.questMode.rejectConfirmTitle,
        description: t.questMode.rejectConfirmDescription,
        confirmLabel: t.questMode.reject,
      }))
    )
      return;
    try {
      await rejectPlan(questId);
      setQuestId(null);
    } catch (err) {
      swallow(err);
      toast.error(t.questMode.rejectFailed);
    }
  }, [questId, confirm, t.questMode.rejectConfirmTitle, t.questMode.rejectConfirmDescription, t.questMode.reject, t.questMode.rejectFailed]);

  const handleCancel = useCallback(async () => {
    if (!questId) return;
    if (
      !(await confirm({
        title: t.questMode.cancelConfirmTitle,
        description: t.questMode.cancelConfirmDescription,
        confirmLabel: t.questMode.cancelConfirmLabel,
      }))
    )
      return;
    try {
      await cancelQuest(questId);
      setQuestId(null);
    } catch (err) {
      swallow(err);
      toast.error(t.questMode.cancelFailed);
    }
  }, [questId, confirm, t.questMode.cancelConfirmTitle, t.questMode.cancelConfirmDescription, t.questMode.cancelConfirmLabel, t.questMode.cancelFailed]);

  const handleNewQuest = useCallback(() => {
    setQuestId(null);
  }, []);

  if (!open) return null;

  const isActive =
    questId !== null &&
    quest.phase !== "completed" &&
    quest.phase !== "failed" &&
    quest.phase !== "cancelled";

  return (
    <div
      className={cn(
        "bg-popover text-popover-foreground w-96 overflow-hidden rounded-lg border border-border-default shadow-sm",
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-default px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex size-6 items-center justify-center rounded-lg bg-primary/10">
            <RocketIcon className="text-primary size-3.5" />
          </div>
          <span className="text-sm font-semibold">{t.questMode.title}</span>
          {isActive && (
            <span className="bg-primary/10 text-primary rounded-lg px-1.5 py-0.5 text-xs font-medium">
              {t.questMode.active}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {isActive && (
            <button
              type="button"
              onClick={handleCancel}
              aria-label="Cancel quest"
              className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
              title="Cancel quest"
            >
              <XCircleIcon className="size-3.5" />
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <XIcon className="size-3.5" />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="max-h-[500px] overflow-y-auto">
        {!questId ? (
          <QuestStartForm onStart={handleStart} />
        ) : (
          <>
            {/* Phase stepper */}
            <PhaseStepper currentPhase={quest.phase} />

            {/* Phase-specific content */}
            {quest.phase === "analyzing" && (
              <div className="flex items-center gap-2 px-4 py-6 animate-in fade-in duration-slow">
                <Loader2Icon className="text-primary size-4 animate-spin" />
                <span className="text-muted-foreground text-sm">
                  {t.questMode.analyzing}
                </span>
              </div>
            )}

            {quest.phase === "planning" && (
              <div className="flex items-center gap-2 px-4 py-6 animate-in fade-in duration-slow">
                <Loader2Icon className="text-primary size-4 animate-spin" />
                <span className="text-muted-foreground text-sm">
                  {t.questMode.generatingPlan}
                </span>
              </div>
            )}

            {quest.phase === "awaiting_approval" && quest.plan && (
              <PlanReview
                plan={quest.plan}
                onApprove={handleApprove}
                onReject={handleReject}
              />
            )}

            {quest.phase === "executing" && quest.plan && (
              <ExecutionProgress
                plan={quest.plan}
                stepResults={quest.stepResults}
                currentStepIndex={quest.currentStepIndex}
              />
            )}

            {quest.phase === "verifying" && (
              <div className="space-y-2 animate-in fade-in duration-slow">
                {quest.verification ? (
                  <VerificationResults verification={quest.verification} />
                ) : (
                  <div className="flex items-center gap-2 px-4 py-6">
                    <Loader2Icon className="text-primary size-4 animate-spin" />
                    <span className="text-muted-foreground text-sm">
                      {t.questMode.verifyingResults}
                    </span>
                  </div>
                )}
              </div>
            )}

            {quest.phase === "reporting" && (
              <div className="flex items-center gap-2 px-4 py-6 animate-in fade-in duration-slow">
                <Loader2Icon className="text-primary size-4 animate-spin" />
                <span className="text-muted-foreground text-sm">
                  {t.questMode.generatingReport}
                </span>
              </div>
            )}

            {(quest.phase === "completed" || quest.phase === "failed") && (
              <div className="space-y-2 animate-in fade-in duration-slow">
                {quest.verification && (
                  <VerificationResults verification={quest.verification} />
                )}
                {quest.report && <ReportCard report={quest.report} />}
                {quest.error && !quest.report && (
                  <div className="px-4 py-2">
                    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                      {quest.error}
                    </div>
                  </div>
                )}
                <div className="flex justify-center px-4 pb-3 pt-1">
                  <button
                    type="button"
                    onClick={handleNewQuest}
                    className="bg-primary text-primary-foreground hover:bg-primary/90 flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-xs font-medium shadow-[var(--shadow-xs)] shadow-primary/10 transition-colors"
                  >
                    <RocketIcon className="size-3" />
                    {t.questMode.newQuest}
                  </button>
                </div>
              </div>
            )}

            {quest.phase === "cancelled" && (
              <div className="space-y-2 px-4 py-6 text-center animate-in fade-in duration-slow">
                <div className="mx-auto flex size-12 items-center justify-center rounded-lg bg-muted/50">
                  <XCircleIcon className="text-muted-foreground size-6" />
                </div>
                <p className="text-muted-foreground text-sm">
                  {t.questMode.cancelled}
                </p>
                <button
                  type="button"
                  onClick={handleNewQuest}
                  className="bg-primary text-primary-foreground hover:bg-primary/90 rounded-lg px-4 py-1.5 text-xs font-medium shadow-[var(--shadow-xs)] shadow-primary/10 transition-colors"
                >
                  {t.questMode.startNewQuest}
                </button>
              </div>
            )}
          </>
        )}
      </div>
      {confirmDialog}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quest Button (floating trigger)
// ---------------------------------------------------------------------------

interface QuestButtonProps {
  onClick: () => void;
  isActive: boolean;
  className?: string;
}

export function QuestButton({
  onClick,
  isActive,
  className,
}: QuestButtonProps) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium shadow-[var(--shadow-xs)] transition-colors transition-shadow duration-base",
        isActive
          ? "bg-primary text-primary-foreground border-primary/60 shadow-primary/10"
          : "bg-background/80 text-muted-foreground hover:bg-muted/50 hover:text-foreground border-border-default",
        className,
      )}
    >
      <RocketIcon className="size-3.5" />
      {t.questMode.quest}
    </button>
  );
}
