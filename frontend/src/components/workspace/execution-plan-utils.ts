import type { ExecutionPlan, ExecutionPlanStep } from "@/core/threads/types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter(
      (item): item is string =>
        typeof item === "string" && item.trim().length > 0,
    );
  }
  if (typeof value === "string" && value.trim().length > 0) {
    return [value.trim()];
  }
  return [];
}

function normalizeStep(value: unknown, index: number): ExecutionPlanStep {
  if (typeof value === "string") {
    const description = value.trim() || `Step ${index + 1}`;
    return {
      step_id: `step-${index + 1}`,
      description,
      tools_needed: [],
      estimated_duration: "medium",
      risk: "low",
      status: "pending",
    };
  }

  if (!isRecord(value)) {
    return {
      step_id: `step-${index + 1}`,
      description: `Step ${index + 1}`,
      tools_needed: [],
      estimated_duration: "medium",
      risk: "low",
      status: "pending",
    };
  }

  const stepId =
    typeof value.step_id === "string" && value.step_id.trim().length > 0
      ? value.step_id.trim()
      : typeof value.id === "string" && value.id.trim().length > 0
        ? value.id.trim()
        : `step-${index + 1}`;
  const description =
    typeof value.description === "string" && value.description.trim().length > 0
      ? value.description.trim()
      : typeof value.title === "string" && value.title.trim().length > 0
        ? value.title.trim()
        : typeof value.text === "string" && value.text.trim().length > 0
          ? value.text.trim()
          : typeof value.task === "string" && value.task.trim().length > 0
            ? value.task.trim()
            : `Step ${index + 1}`;
  const estimatedDuration =
    value.estimated_duration === "fast" ||
    value.estimated_duration === "medium" ||
    value.estimated_duration === "slow"
      ? value.estimated_duration
      : "medium";
  const risk =
    value.risk === "low" || value.risk === "medium" || value.risk === "high"
      ? value.risk
      : "low";
  const status =
    value.status === "pending" ||
    value.status === "in_progress" ||
    value.status === "completed" ||
    value.status === "skipped"
      ? value.status
      : "pending";

  return {
    step_id: stepId,
    description,
    tools_needed: stringArray(
      value.tools_needed ?? value.tools ?? value.tool_names,
    ),
    estimated_duration: estimatedDuration,
    risk,
    status,
  };
}

export function normalizeExecutionPlan(
  planData: unknown,
): ExecutionPlan | null {
  if (!isRecord(planData)) return null;

  const steps = Array.isArray(planData.steps)
    ? planData.steps.map((step, index) => normalizeStep(step, index))
    : [];

  const status =
    planData.status === "pending_review" ||
    planData.status === "approved" ||
    planData.status === "modified" ||
    planData.status === "rejected" ||
    planData.status === "executing" ||
    planData.status === "completed"
      ? planData.status
      : "pending_review";

  return {
    plan_id:
      typeof planData.plan_id === "string" && planData.plan_id.trim().length > 0
        ? planData.plan_id.trim()
        : typeof planData.id === "string" && planData.id.trim().length > 0
          ? planData.id.trim()
          : "plan",
    title:
      typeof planData.title === "string" && planData.title.trim().length > 0
        ? planData.title.trim()
        : "Execution plan",
    steps,
    estimated_actions:
      typeof planData.estimated_actions === "number" &&
      Number.isFinite(planData.estimated_actions)
        ? planData.estimated_actions
        : steps.length,
    risk_level:
      planData.risk_level === "low" ||
      planData.risk_level === "medium" ||
      planData.risk_level === "high"
        ? planData.risk_level
        : "low",
    status,
    created_at:
      typeof planData.created_at === "number" &&
      Number.isFinite(planData.created_at)
        ? planData.created_at
        : 0,
    user_message:
      typeof planData.user_message === "string" &&
      planData.user_message.trim().length > 0
        ? planData.user_message.trim()
        : undefined,
  };
}
