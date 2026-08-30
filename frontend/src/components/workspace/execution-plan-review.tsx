/**
 * ExecutionPlanReview — Inline card for reviewing, editing, and approving
 * execution plans before the agent starts working on complex tasks.
 *
 * Renders inside the chat message list when the middleware detects a
 * complex task and generates a structured plan. Users can:
 * - Approve the plan to start execution
 * - Edit individual steps (reorder, add, remove, modify)
 * - Reject the plan and provide alternative instructions
 *
 * During execution, the card collapses but shows live step progress
 * with checkmarks, spinners, and completion percentage.
 */

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClipboardCheckIcon,
  ClockIcon,
  GripVerticalIcon,
  Loader2Icon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  ShieldCheckIcon,
  Trash2Icon,
  WrenchIcon,
  XCircleIcon,
  XIcon,
  ZapIcon,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import type { ExecutionPlan, ExecutionPlanStep } from "@/core/threads/types";
import { normalizeExecutionPlan } from "./execution-plan-utils";

// ---------------------------------------------------------------------------
// Helper types
// ---------------------------------------------------------------------------

export type PlanAction = "approve" | "modify" | "reject";

interface ExecutionPlanReviewProps {
  plan: ExecutionPlan;
  threadId: string;
  /** Callback when the user takes an action on the plan */
  onAction?: (action: PlanAction, payload?: Record<string, unknown>) => void;
  /** Whether the plan is currently being sent/processed */
  isProcessing?: boolean;
  className?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RISK_CONFIG = {
  low: {
    color:
      "bg-success/10 text-success border-success/20",
    icon: ShieldCheckIcon,
    label: "Low Risk",
  },
  medium: {
    color:
      "bg-warning/10 text-warning border-warning/20",
    icon: AlertTriangleIcon,
    label: "Medium Risk",
  },
  high: {
    color: "bg-destructive/10 text-destructive dark:text-destructive border-destructive/20",
    icon: AlertTriangleIcon,
    label: "High Risk",
  },
} as const;

const DURATION_CONFIG = {
  fast: { label: "< 5s", icon: ZapIcon },
  medium: { label: "5-30s", icon: ClockIcon },
  slow: { label: "> 30s", icon: ClockIcon },
} as const;

const STATUS_CONFIG = {
  pending: { color: "text-muted-foreground", bgColor: "bg-muted/30" },
  in_progress: { color: "text-primary", bgColor: "bg-primary/5" },
  completed: {
    color: "text-success",
    bgColor: "bg-success/5",
  },
  skipped: { color: "text-muted-foreground/50", bgColor: "bg-muted/10" },
} as const;

// ---------------------------------------------------------------------------
// Step editor component (for edit mode)
// ---------------------------------------------------------------------------

function StepEditor({
  step,
  index,
  onUpdate,
  onRemove,
}: {
  step: ExecutionPlanStep;
  index: number;
  onUpdate: (index: number, updated: Partial<ExecutionPlanStep>) => void;
  onRemove: (index: number) => void;
}) {
  const { t } = useI18n();
  const toolsNeeded = Array.isArray(step.tools_needed) ? step.tools_needed : [];
  return (
    <div className="group flex items-start gap-2 rounded-lg border border-border-default bg-background p-2.5">
      <div className="mt-1 cursor-grab text-muted-foreground/40">
        <GripVerticalIcon className="size-3.5" />
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <Input
          type="text"
          value={step.description}
          onChange={(e) => onUpdate(index, { description: e.target.value })}
          className="w-full border-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground/40 focus-visible:ring-0"
          placeholder="Step description..."
          aria-label={t.executionPlan.stepDescriptionAria}
        />
        <div className="flex flex-wrap items-center gap-1.5">
          <select
            value={step.risk}
            onChange={(e) =>
              onUpdate(index, {
                risk: e.target.value as ExecutionPlanStep["risk"],
              })
            }
            className="h-5 rounded border border-border-default bg-muted/30 px-1 text-xs outline-none"
          >
            <option value="low">{t.executionPlan.lowRisk}</option>
            <option value="medium">{t.executionPlan.mediumRisk}</option>
            <option value="high">{t.executionPlan.highRisk}</option>
          </select>
          <select
            value={step.estimated_duration}
            onChange={(e) =>
              onUpdate(index, {
                estimated_duration: e.target
                  .value as ExecutionPlanStep["estimated_duration"],
              })
            }
            className="h-5 rounded border border-border-default bg-muted/30 px-1 text-xs outline-none"
          >
            <option value="fast">{t.executionPlan.fast}</option>
            <option value="medium">{t.executionPlan.medium}</option>
            <option value="slow">{t.executionPlan.slow}</option>
          </select>
          {toolsNeeded.length > 0 && (
            <div className="flex items-center gap-0.5">
              <WrenchIcon className="size-2.5 text-muted-foreground/40" />
              <span className="text-xs text-muted-foreground/60">
                {toolsNeeded.join(", ")}
              </span>
            </div>
          )}
        </div>
      </div>
      <button
        type="button"
        aria-label={t.executionPlan.removeStepAria}
        onClick={() => onRemove(index)}
        className="mt-0.5 rounded p-0.5 text-muted-foreground/40 transition-colors hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Trash2Icon className="size-3" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single step display (read-only + live progress)
// ---------------------------------------------------------------------------

function PlanStepRow({
  step,
  index,
  isExpanded,
  onToggle,
}: {
  step: ExecutionPlanStep;
  index: number;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  const statusCfg = STATUS_CONFIG[step.status] ?? STATUS_CONFIG.pending;
  const riskCfg = RISK_CONFIG[step.risk] ?? RISK_CONFIG.low;
  const riskLabels: Record<string, string> = {
    low: t.executionPlan.lowRisk,
    medium: t.executionPlan.mediumRisk,
    high: t.executionPlan.highRisk,
  };
  const durationCfg =
    DURATION_CONFIG[step.estimated_duration] ?? DURATION_CONFIG.medium;
  const durationLabels: Record<string, string> = {
    fast: t.executionPlan.fast,
    medium: t.executionPlan.medium,
    slow: t.executionPlan.slow,
  };
  const DurationIcon = durationCfg.icon;
  const toolsNeeded = Array.isArray(step.tools_needed) ? step.tools_needed : [];

  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2 transition-colors",
        statusCfg.bgColor,
        step.status === "in_progress" &&
          "border-primary/30 shadow-[var(--shadow-xs)] shadow-primary/5",
        step.status === "completed" && "border-success/20",
        step.status === "skipped" && "border-border-subtle opacity-60",
        step.status === "pending" && "border-border-subtle",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-start gap-2.5 text-left"
      >
        {/* Status icon */}
        <div className="mt-0.5 shrink-0">
          {step.status === "completed" ? (
            <CheckCircle2Icon className="size-3.5 text-success" />
          ) : step.status === "in_progress" ? (
            <Loader2Icon className="size-3.5 animate-spin text-primary" />
          ) : step.status === "skipped" ? (
            <XIcon className="size-3.5 text-muted-foreground/40" />
          ) : (
            <div className="mt-px size-2.5 rounded-lg border-2 border-muted-foreground/30" />
          )}
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground/40 text-xs font-mono tabular-nums">
              {index + 1}
            </span>
            <span
              className={cn(
                "text-xs leading-relaxed",
                step.status === "completed" &&
                  "text-muted-foreground line-through",
                step.status === "in_progress" && "text-foreground font-medium",
                step.status === "skipped" &&
                  "text-muted-foreground/50 line-through",
                step.status === "pending" && "text-foreground/80",
              )}
            >
              {step.description}
            </span>
          </div>
        </div>

        {/* Expand icon */}
        {(toolsNeeded.length > 0 || step.risk !== "low") && (
          <div className="shrink-0">
            {isExpanded ? (
              <ChevronDownIcon className="size-3 text-muted-foreground/40" />
            ) : (
              <ChevronRightIcon className="size-3 text-muted-foreground/40" />
            )}
          </div>
        )}
      </button>

      {/* Expanded detail */}
      {isExpanded && (
        <div className="mt-2 flex flex-wrap items-center gap-2 pl-6">
          {/* Risk badge */}
          {step.risk !== "low" && (
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-lg border px-1.5 py-0.5 text-xs font-medium",
                riskCfg.color,
              )}
            >
              <riskCfg.icon className="size-2.5" />
              {riskLabels[step.risk] ?? riskCfg.label}
            </span>
          )}
          {/* Duration */}
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground/60">
            <DurationIcon className="size-2.5" />
            {durationLabels[step.estimated_duration] ?? durationCfg.label}
          </span>
          {/* Tools */}
          {toolsNeeded.length > 0 && (
            <div className="flex items-center gap-1">
              <WrenchIcon className="size-2.5 text-muted-foreground/40" />
              {toolsNeeded.map((tool) => (
                <span
                  key={tool}
                  className="rounded bg-muted/50 px-1 py-0.5 font-mono text-xs text-muted-foreground"
                >
                  {tool}
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
// Main component
// ---------------------------------------------------------------------------

export function ExecutionPlanReview({
  plan,
  threadId,
  onAction,
  isProcessing = false,
  className,
}: ExecutionPlanReviewProps) {
  const { t } = useI18n();
  const steps = useMemo(
    () => (Array.isArray(plan.steps) ? plan.steps : []),
    [plan.steps],
  );
  const [isEditing, setIsEditing] = useState(false);
  const [editableSteps, setEditableSteps] = useState<ExecutionPlanStep[]>([]);
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [actionInFlight, setActionInFlight] = useState<PlanAction | null>(null);

  const isReviewable = plan.status === "pending_review";
  const isExecuting = plan.status === "executing";
  const isCompleted = plan.status === "completed";
  const isRejected = plan.status === "rejected";
  const isApproved = plan.status === "approved";

  // Auto-collapse after approval/execution
  const shouldCollapse = isCollapsed || isCompleted;

  // Progress calculation
  const completedSteps = steps.filter(
    (s) => s.status === "completed" || s.status === "skipped",
  ).length;
  const progressPct =
    steps.length > 0 ? (completedSteps / steps.length) * 100 : 0;

  // Risk config for header
  const riskCfg = RISK_CONFIG[plan.risk_level] ?? RISK_CONFIG.low;
  const RiskIcon = riskCfg.icon;

  // Toggle step expansion
  const toggleStep = useCallback((stepId: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  }, []);

  // Edit mode handlers
  const startEditing = useCallback(() => {
    setEditableSteps(steps.map((s) => ({ ...s })));
    setIsEditing(true);
  }, [steps]);

  const cancelEditing = useCallback(() => {
    setIsEditing(false);
    setEditableSteps([]);
  }, []);

  const updateEditableStep = useCallback(
    (index: number, updates: Partial<ExecutionPlanStep>) => {
      setEditableSteps((prev) =>
        prev.map((s, i) => (i === index ? { ...s, ...updates } : s)),
      );
    },
    [],
  );

  const removeEditableStep = useCallback((index: number) => {
    setEditableSteps((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const addEditableStep = useCallback(() => {
    setEditableSteps((prev) => [
      ...prev,
      {
        step_id: `step-${prev.length + 1}`,
        description: "",
        tools_needed: [],
        estimated_duration: "medium" as const,
        risk: "low" as const,
        status: "pending" as const,
      },
    ]);
  }, []);

  // ---------------------------------------------------------------------------
  // Dispatch a plan action as a chat message.
  //
  // The ExecutionPlanMiddleware.abefore_agent inspects incoming
  // HumanMessages for ``additional_kwargs.plan_action``.  By emitting a
  // custom DOM event the parent chat page picks it up and feeds it to
  // ``sendMessage`` — exactly like the existing ``echo:regenerate``
  // pattern.  The REST API call updates the plan store optimistically so
  // the middleware sees the correct status immediately.
  // ---------------------------------------------------------------------------

  const _dispatchPlanMessage = useCallback(
    (action: string, payload: Record<string, unknown> = {}) => {
      window.dispatchEvent(
        new CustomEvent("echo:plan-action", {
          detail: {
            text:
              action === "execution_plan_approve"
                ? "Approve plan"
                : action === "execution_plan_reject"
                  ? "Reject plan"
                  : "Modify plan",
            additionalKwargs: {
              plan_action: action,
              plan_payload: { plan_id: plan.plan_id, ...payload },
              hide_from_ui: true,
            },
          },
        }),
      );
    },
    [plan.plan_id],
  );

  // Action handlers
  const handleApprove = useCallback(async () => {
    setActionInFlight("approve");
    try {
      // Optimistic API update
      await fetch(`${getBackendBaseURL()}/api/plan/${plan.plan_id}/approve`, {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({ thread_id: threadId }),
      });

      // Send message to trigger middleware continuation
      _dispatchPlanMessage("execution_plan_approve");
      onAction?.("approve", { plan_id: plan.plan_id });
      toast.success(t.executionPlan.toastApproved);
    } catch (_err) {
      toast.error(t.executionPlan.toastApproveFailed);
    } finally {
      setActionInFlight(null);
    }
  }, [
    plan.plan_id,
    threadId,
    onAction,
    _dispatchPlanMessage,
    t.executionPlan.toastApproved,
    t.executionPlan.toastApproveFailed,
  ]);

  const handleSaveEdits = useCallback(async () => {
    const validSteps = editableSteps.filter((s) => s.description.trim());
    if (validSteps.length === 0) {
      toast.error(t.executionPlan.toastMustHaveStep);
      return;
    }

    setActionInFlight("modify");
    try {
      const modifiedSteps = validSteps.map((s, i) => ({
        step_id: s.step_id || `step-${i + 1}`,
        description: s.description,
        tools_needed: s.tools_needed,
        estimated_duration: s.estimated_duration,
        risk: s.risk,
      }));

      await fetch(`${getBackendBaseURL()}/api/plan/${plan.plan_id}/modify`, {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({
          thread_id: threadId,
          modified_steps: modifiedSteps,
        }),
      });

      setIsEditing(false);
      _dispatchPlanMessage("execution_plan_modify", {
        modified_steps: modifiedSteps,
      });
      onAction?.("modify", {
        plan_id: plan.plan_id,
        modified_steps: validSteps,
      });
      toast.success(t.executionPlan.toastUpdated);
    } catch (_err) {
      toast.error(t.executionPlan.toastModifyFailed);
    } finally {
      setActionInFlight(null);
    }
  }, [
    editableSteps,
    plan.plan_id,
    threadId,
    onAction,
    _dispatchPlanMessage,
    t.executionPlan.toastMustHaveStep,
    t.executionPlan.toastUpdated,
    t.executionPlan.toastModifyFailed,
  ]);

  const handleReject = useCallback(async () => {
    setActionInFlight("reject");
    try {
      await fetch(`${getBackendBaseURL()}/api/plan/${plan.plan_id}/reject`, {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({
          thread_id: threadId,
          reason: rejectReason,
        }),
      });

      setShowRejectInput(false);
      _dispatchPlanMessage("execution_plan_reject", { reason: rejectReason });
      onAction?.("reject", { plan_id: plan.plan_id, reason: rejectReason });
      toast.info(t.executionPlan.toastRejected);
    } catch (_err) {
      toast.error(t.executionPlan.toastRejectFailed);
    } finally {
      setActionInFlight(null);
    }
  }, [
    plan.plan_id,
    threadId,
    rejectReason,
    onAction,
    _dispatchPlanMessage,
    t.executionPlan.toastRejected,
    t.executionPlan.toastRejectFailed,
  ]);

  // Determine header status text and color
  const headerStatus = useMemo(() => {
    if (isReviewable)
      return {
        text: t.executionPlan.awaitingReview,
        color: "text-warning",
      };
    if (isApproved || isExecuting)
      return { text: t.executionPlan.executing, color: "text-primary" };
    if (isCompleted)
      return {
        text: t.executionPlan.completed,
        color: "text-success",
      };
    if (isRejected)
      return {
        text: t.executionPlan.rejected,
        color: "text-destructive dark:text-destructive",
      };
    return { text: plan.status, color: "text-muted-foreground" };
  }, [
    isReviewable,
    isApproved,
    isExecuting,
    isCompleted,
    isRejected,
    plan.status,
    t,
  ]);

  return (
    <div
      className={cn(
        "w-full rounded-lg border transition-colors transition-shadow duration-slow",
        isReviewable && "border-warning/30 bg-warning/50/[0.02] shadow-[var(--shadow-xs)]",
        (isExecuting || isApproved) && "border-primary/20 bg-primary/[0.02]",
        isCompleted && "border-success/20 bg-success/50/[0.02]",
        isRejected && "border-destructive/20 bg-destructive/[0.02] opacity-75",
        className,
      )}
    >
      {/* Header */}
      <button
        type="button"
        onClick={() => setIsCollapsed(!shouldCollapse)}
        className="flex w-full items-center justify-between px-4 py-3"
      >
        <div className="flex items-center gap-2.5">
          <ClipboardCheckIcon className={cn("size-4", headerStatus.color)} />
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">{plan.title}</span>
            <span className={cn("text-xs font-medium", headerStatus.color)}>
              {headerStatus.text}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* Progress badge */}
          {(isExecuting || isCompleted) && (
            <span className="rounded-lg bg-muted px-2 py-0.5 text-xs font-medium tabular-nums">
              {completedSteps}/{steps.length}
            </span>
          )}
          {/* Risk badge */}
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-lg border px-1.5 py-0.5 text-xs font-medium",
              riskCfg.color,
            )}
          >
            <RiskIcon className="size-2.5" />
            {(
              {
                low: t.executionPlan.lowRisk,
                medium: t.executionPlan.mediumRisk,
                high: t.executionPlan.highRisk,
              } as Record<string, string>
            )[plan.risk_level] ?? riskCfg.label}
          </span>
          {/* Expand/collapse */}
          {shouldCollapse ? (
            <ChevronRightIcon className="size-4 text-muted-foreground" />
          ) : (
            <ChevronDownIcon className="size-4 text-muted-foreground" />
          )}
        </div>
      </button>

      {/* Progress bar (visible during execution) */}
      {(isExecuting || isCompleted || isApproved) && !shouldCollapse && (
        <div className="px-4 pb-2">
          <div className="h-1 overflow-hidden rounded-lg bg-muted">
            <div
              className={cn(
                "h-full rounded-lg transition-all duration-slow ease-out",
                isCompleted ? "bg-success" : "bg-primary",
              )}
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Body */}
      {!shouldCollapse && (
        <div className="space-y-3 px-4 pb-4">
          {/* Meta info */}
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <WrenchIcon className="size-2.5" />~{plan.estimated_actions}{" "}
              actions
            </span>
            <span className="inline-flex items-center gap-1">
              <ClipboardCheckIcon className="size-2.5" />
              {steps.length} steps
            </span>
          </div>

          {/* Steps list */}
          <div className="space-y-1.5">
            {isEditing ? (
              <>
                {editableSteps.map((step, i) => (
                  <StepEditor
                    key={step.step_id || i}
                    step={step}
                    index={i}
                    onUpdate={updateEditableStep}
                    onRemove={removeEditableStep}
                  />
                ))}
                <button
                  type="button"
                  onClick={addEditableStep}
                  className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-border-default py-2 text-xs text-muted-foreground hover:border-primary/30 hover:text-primary"
                >
                  <PlusIcon className="size-3" />
                  {t.executionPlan.addStep}
                </button>
              </>
            ) : (
              steps.map((step, i) => (
                <PlanStepRow
                  key={step.step_id}
                  step={step}
                  index={i}
                  isExpanded={expandedSteps.has(step.step_id)}
                  onToggle={() => toggleStep(step.step_id)}
                />
              ))
            )}
          </div>

          {/* Reject reason input */}
          {showRejectInput && (
            <div className="space-y-2">
              <textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="(Optional) Explain why or provide alternative instructions..."
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/40 focus:border-primary/30"
                rows={2}
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-7 gap-1.5 text-xs"
                  onClick={handleReject}
                  disabled={actionInFlight === "reject"}
                >
                  {actionInFlight === "reject" ? (
                    <Loader2Icon className="size-3 animate-spin" />
                  ) : (
                    <XCircleIcon className="size-3" />
                  )}
                  {t.executionPlan.confirmReject}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-xs"
                  onClick={() => {
                    setShowRejectInput(false);
                    setRejectReason("");
                  }}
                >
                  {t.executionPlan.cancel}
                </Button>
              </div>
            </div>
          )}

          {/* Action buttons — shown only for reviewable plans */}
          {isReviewable && !showRejectInput && (
            <div className="flex items-center gap-2 pt-1">
              {isEditing ? (
                <>
                  <Button
                    size="sm"
                    variant="default"
                    className="h-8 gap-1.5 text-xs"
                    onClick={handleSaveEdits}
                    disabled={actionInFlight === "modify"}
                  >
                    {actionInFlight === "modify" ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <CheckCircle2Icon className="size-3.5" />
                    )}
                    {t.executionPlan.saveAndReview}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 text-xs"
                    onClick={cancelEditing}
                  >
                    {t.executionPlan.cancel}
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    size="sm"
                    variant="default"
                    className="h-8 gap-1.5 text-xs"
                    onClick={handleApprove}
                    disabled={isProcessing || actionInFlight !== null}
                  >
                    {actionInFlight === "approve" ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <PlayIcon className="size-3.5" />
                    )}
                    {t.executionPlan.approve}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 gap-1.5 text-xs"
                    onClick={startEditing}
                    disabled={isProcessing || actionInFlight !== null}
                  >
                    <PencilIcon className="size-3.5" />
                    {t.executionPlan.editPlan}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 gap-1.5 text-xs text-muted-foreground hover:text-destructive"
                    onClick={() => setShowRejectInput(true)}
                    disabled={isProcessing || actionInFlight !== null}
                  >
                    <XCircleIcon className="size-3.5" />
                    {t.executionPlan.reject}
                  </Button>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detection helpers (used by message-list-item to decide when to render)
// ---------------------------------------------------------------------------

/**
 * Check if a message contains an execution plan.
 */
export function isExecutionPlanMessage(message: {
  additional_kwargs?: Record<string, unknown>;
}): boolean {
  return message.additional_kwargs?.element === "execution_plan";
}

/**
 * Extract execution plan data from a message's additional_kwargs.
 */
export function getExecutionPlanFromMessage(message: {
  additional_kwargs?: Record<string, unknown>;
}): ExecutionPlan | null {
  const planData = message.additional_kwargs?.execution_plan;
  return normalizeExecutionPlan(planData);
}
