import type {
  AgentTraceReplayGate,
  AgentTraceReviewQueueItem,
  AgentTraceTaskRecoveryQueue,
  ReplayEvidenceHint,
} from "@/core/agent-trace/api";
import { AgentTraceRequestError } from "@/core/agent-trace/api";
import type { ReplayGateOverridePrompt } from "./shared";

export function formatOperatorCopy(
  copy: (source: string) => string,
  source: string,
  values: Record<string, string | number>,
) {
  return Object.entries(values).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
    copy(source),
  );
}

export function shortId(id: string | number) {
  const text = String(id);
  return text.length > 16 ? `${text.slice(0, 16)}...` : text;
}

export function countRecovery(
  queue: AgentTraceTaskRecoveryQueue,
  needle: string,
) {
  return queue.items.filter((item) => item.recommended_action.includes(needle))
    .length;
}

export function taskRecoverySteps(
  item: AgentTraceTaskRecoveryQueue["items"][number],
) {
  const raw = item.steps?.length ? item.steps : item.recovery_plan?.steps;
  if (!Array.isArray(raw)) return [];
  return raw.map((step) => step.trim()).filter(Boolean);
}

export function taskRecoveryActionLabel(action: string) {
  switch (action) {
    case "takeover_and_resume":
      return "take over + resume";
    case "takeover_for_approval":
      return "take over approval";
    case "resume_from_checkpoint":
      return "resume checkpoint";
    case "restart":
      return "restart";
    case "resume_paused_task":
      return "resume paused";
    case "takeover":
      return "take over";
    case "dispatch":
      return "dispatch";
    case "await_operator_approval":
      return "operator approval";
    case "approval_policy_denied":
      return "approval denied";
    case "capability_policy_denied":
      return "capability denied";
    case "monitor":
      return "monitor";
    default:
      return action.replaceAll("_", " ");
  }
}

export function taskRecoveryHint(action: string) {
  switch (action) {
    case "resume_from_checkpoint":
      return "Open the loop run and resume from checkpoint";
    case "restart":
      return "Restart the task from the latest safe state";
    case "await_operator_approval":
      return "Resolve the pending approval request";
    case "approval_policy_denied":
    case "capability_policy_denied":
      return "Review policy before retrying";
    case "dispatch":
      return "Worker dispatch is pending";
    default:
      return taskRecoveryActionLabel(action);
  }
}

export function competitorLabel(id: string) {
  if (id === "claude_code") return "Claude";
  if (id === "echo") return "EchoAI";
  if (id === "codex") return "Codex";
  if (id === "openclaw") return "OpenClaw";
  if (id === "hermes") return "Hermes";
  if (id === "cursor") return "Cursor";
  return id;
}

export function scorecardGapQueueItemForDimension(
  items: AgentTraceReviewQueueItem[],
  dimensionId: string,
) {
  return (
    items.find((item) => {
      const metadata = item.metadata ?? {};
      return (
        metadata.dimension_id === dimensionId ||
        item.candidate_kind === `scorecard_gap:${dimensionId}` ||
        (item.tags ?? []).includes(dimensionId)
      );
    }) ?? null
  );
}

export function formatScore(score: unknown) {
  return typeof score === "number" ? score.toFixed(2) : "--";
}

export function formatApplyResult(result: {
  applied: number;
  skipped: number;
  failed: number;
  replay_gate?: AgentTraceReplayGate;
  override_replay_gate?: boolean;
}) {
  const gate = result.replay_gate;
  return `Applied ${result.applied}, skipped ${result.skipped}, failed ${result.failed}${
    gate ? ` · gate ${gate.passed ? "passed" : "blocked"}` : ""
  }${result.override_replay_gate ? " · override" : ""}`;
}

export function readRequestErrorMessage(err: unknown): string {
  if (!(err instanceof AgentTraceRequestError)) {
    return err instanceof Error ? err.message : String(err);
  }
  const detail = err.detail;
  if (typeof detail === "string") return detail;
  if (!detail || typeof detail !== "object") return err.message;
  const raw = detail as {
    detail?: unknown;
    error?: unknown;
    message?: unknown;
  };
  if (typeof raw.detail === "string") return raw.detail;
  if (typeof raw.error === "string") return raw.error;
  if (typeof raw.message === "string") return raw.message;
  return err.message;
}

export function replayEvidenceFromError(
  err: unknown,
): ReplayEvidenceHint | null {
  if (!(err instanceof AgentTraceRequestError)) return null;
  const detail = err.detail;
  if (!detail || typeof detail !== "object") return null;
  const raw = detail as {
    replay_evidence?: unknown;
    detail?: unknown;
  };
  const candidate =
    raw.replay_evidence ??
    (raw.detail && typeof raw.detail === "object"
      ? (raw.detail as { replay_evidence?: unknown }).replay_evidence
      : null);
  if (!candidate || typeof candidate !== "object") return null;
  const evidence = candidate as ReplayEvidenceHint;
  if (!evidence.case_id && !evidence.fingerprint && !evidence.queue_url) {
    return null;
  }
  return evidence;
}

export function replayGateBlockFromError(
  err: unknown,
): ReplayGateOverridePrompt | null {
  if (!(err instanceof AgentTraceRequestError) || err.status !== 409)
    return null;
  const detail = err.detail;
  if (!detail || typeof detail !== "object") return null;
  const raw = detail as {
    message?: unknown;
    replay_gate?: unknown;
  };
  if (!raw.replay_gate || typeof raw.replay_gate !== "object") return null;
  return {
    gate: raw.replay_gate as AgentTraceReplayGate,
    message:
      typeof raw.message === "string"
        ? raw.message
        : "replay gate did not pass",
  };
}
