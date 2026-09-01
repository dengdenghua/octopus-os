import { jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

import {
  buildNarrativeStagePrompt,
  type BuildNarrativeStagePromptInput,
  type NarrativePromptAudit,
} from "./prompt";
import type { NarrativeStageName, NarrativeSubagentType } from "./stages";

const DEFAULT_STAGE_TIMEOUT_SECONDS = 300;
const MIN_STAGE_TIMEOUT_SECONDS = 30;
const MAX_STAGE_TIMEOUT_SECONDS = 900;
const MAX_ROUTING_ID_CHARS = 256;

export type NarrativeDispatchErrorCode =
  | "aborted"
  | "network"
  | "http"
  | "agent_failure"
  | "invalid_response";

export interface DispatchNarrativeStageInput extends BuildNarrativeStagePromptInput {
  turnId?: string;
  timeoutSeconds?: number;
  signal?: AbortSignal;
}

export interface NarrativeStageUsage {
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  costUsd: number | null;
}

export interface NarrativeStageExecutionMetadata {
  agentId: string | null;
  sessionId: string | null;
  model: string | null;
  status: string | null;
  durationSeconds: number | null;
  iterationCount: number | null;
  usage: NarrativeStageUsage;
}

export interface NarrativeStageDispatchResult {
  success: true;
  output: string;
  error: null;
  stage: NarrativeStageName;
  subagentType: NarrativeSubagentType;
  runId: string;
  turnId: string;
  promptAudit: NarrativePromptAudit;
  metadata: NarrativeStageExecutionMetadata;
}

export class NarrativeStageDispatchError extends Error {
  readonly code: NarrativeDispatchErrorCode;
  readonly status: number | null;
  readonly stage: NarrativeStageName;
  readonly subagentType: NarrativeSubagentType;
  readonly backendError: string | null;

  constructor(options: {
    message: string;
    code: NarrativeDispatchErrorCode;
    stage: NarrativeStageName;
    subagentType: NarrativeSubagentType;
    status?: number | null;
    backendError?: string | null;
    cause?: unknown;
  }) {
    super(options.message, { cause: options.cause });
    this.name = "NarrativeStageDispatchError";
    this.code = options.code;
    this.status = options.status ?? null;
    this.stage = options.stage;
    this.subagentType = options.subagentType;
    this.backendError = options.backendError ?? null;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function firstString(
  records: readonly (Record<string, unknown> | null)[],
  keys: readonly string[],
): string | null {
  for (const record of records) {
    if (!record) continue;
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value;
    }
  }
  return null;
}

function firstNumber(
  records: readonly (Record<string, unknown> | null)[],
  keys: readonly string[],
): number | null {
  for (const record of records) {
    if (!record) continue;
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "number" && Number.isFinite(value)) return value;
    }
  }
  return null;
}

function errorFromPayload(payload: unknown): string | null {
  if (typeof payload === "string" && payload.trim()) return payload.trim();
  const root = asRecord(payload);
  if (!root) return null;
  const direct = firstString([root], ["error", "message", "detail"]);
  if (direct) return direct;
  const detail = asRecord(root.detail);
  return firstString([detail], ["error", "message", "detail"]);
}

function boundedTimeout(value: number | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return DEFAULT_STAGE_TIMEOUT_SECONDS;
  }
  return Math.max(
    MIN_STAGE_TIMEOUT_SECONDS,
    Math.min(MAX_STAGE_TIMEOUT_SECONDS, Math.trunc(value)),
  );
}

function routingId(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} is required`);
  if (normalized.length > MAX_ROUTING_ID_CHARS) {
    throw new Error(
      `${label} exceeds the ${MAX_ROUTING_ID_CHARS}-character routing limit`,
    );
  }
  return normalized;
}

function createTurnId(runId: string, stage: NarrativeStageName): string {
  const randomPart =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `narrative:${runId.slice(0, 80)}:${stage}:${randomPart}`;
}

function isAbortFailure(error: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) return true;
  return error instanceof DOMException
    ? error.name === "AbortError"
    : asRecord(error)?.name === "AbortError";
}

async function responsePayload(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function normalizeMetadata(
  root: Record<string, unknown>,
): NarrativeStageExecutionMetadata {
  const metadata = asRecord(root.metadata);
  const usage = asRecord(root.usage);
  const tokenUsage = asRecord(root.token_usage);
  const metadataUsage = asRecord(metadata?.usage);
  const records = [root, metadata];
  const usageRecords = [usage, tokenUsage, metadataUsage, root, metadata];
  const inputTokens = firstNumber(usageRecords, [
    "input_tokens",
    "prompt_tokens",
    "inputTokens",
  ]);
  const outputTokens = firstNumber(usageRecords, [
    "output_tokens",
    "completion_tokens",
    "outputTokens",
  ]);
  return {
    agentId: firstString(records, [
      "agent_id",
      "requested_agent_id",
      "agentId",
    ]),
    sessionId: firstString(records, [
      "session_id",
      "subagent_session_id",
      "sessionId",
    ]),
    model: firstString(records, ["model", "model_name", "modelId"]),
    status: firstString(records, ["status"]),
    durationSeconds: firstNumber(records, [
      "duration_s",
      "duration_seconds",
      "durationSeconds",
    ]),
    iterationCount: firstNumber(records, [
      "iteration_count",
      "rounds_completed",
      "iterations",
    ]),
    usage: {
      inputTokens,
      outputTokens,
      totalTokens:
        firstNumber(usageRecords, ["total_tokens", "totalTokens"]) ??
        (inputTokens !== null && outputTokens !== null
          ? inputTokens + outputTokens
          : null),
      costUsd: firstNumber(usageRecords, [
        "cost_usd",
        "total_cost_usd",
        "costUsd",
      ]),
    },
  };
}

/**
 * Runs exactly one isolated Narrative Studio stage.
 *
 * This function intentionally stops at the subagent dispatch boundary. It
 * never submits the resulting output to a pipeline and never calls review,
 * publishing, or canon endpoints; the caller must explicitly present the
 * candidate to a human before any later mutation.
 */
export async function dispatchNarrativeStage(
  input: DispatchNarrativeStageInput,
): Promise<NarrativeStageDispatchResult> {
  const built = buildNarrativeStagePrompt(input);
  const { stage } = built;
  const timeoutSeconds = boundedTimeout(input.timeoutSeconds);
  const runId = routingId(input.run.id, "Narrative pipeline run id");
  const projectId = routingId(input.project.id, "Narrative project id");
  const contextPackId = routingId(
    input.contextPack.id,
    "Narrative context pack id",
  );
  const turnId = routingId(
    input.turnId?.trim() || createTurnId(runId, stage.name),
    "Narrative turn id",
  );
  const requestBody = {
    subagent_type: stage.subagentType,
    prompt: built.prompt,
    context: {
      narrative_project_id: projectId,
      narrative_context_pack_id: contextPackId,
      narrative_pipeline_stage: stage.name,
      candidate_only: true,
      prompt_chars: built.audit.promptChars,
      prompt_truncated: built.audit.truncated,
    },
    timeout_s: timeoutSeconds,
    run_id: runId,
    turn_id: turnId,
    source: "narrative_studio",
    share_history: false,
  };

  let response: Response;
  try {
    response = await fetch(`${getBackendBaseURL()}/api/subagents/dispatch`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      credentials: "include",
      body: JSON.stringify(requestBody),
      signal: input.signal,
    });
  } catch (error) {
    if (isAbortFailure(error, input.signal)) {
      throw new NarrativeStageDispatchError({
        message: `Narrative ${stage.name} stage was cancelled`,
        code: "aborted",
        stage: stage.name,
        subagentType: stage.subagentType,
        cause: error,
      });
    }
    throw new NarrativeStageDispatchError({
      message: `Could not reach the narrative agent for ${stage.name}: ${error instanceof Error ? error.message : "network request failed"}`,
      code: "network",
      stage: stage.name,
      subagentType: stage.subagentType,
      cause: error,
    });
  }

  const payload = await responsePayload(response);
  if (!response.ok) {
    const backendError =
      errorFromPayload(payload) || response.statusText || "request failed";
    throw new NarrativeStageDispatchError({
      message: `Narrative ${stage.name} agent failed (${response.status}): ${backendError}`,
      code: "http",
      status: response.status,
      backendError,
      stage: stage.name,
      subagentType: stage.subagentType,
    });
  }

  const root = asRecord(payload);
  if (!root) {
    throw new NarrativeStageDispatchError({
      message: `Narrative ${stage.name} agent returned an invalid response`,
      code: "invalid_response",
      stage: stage.name,
      subagentType: stage.subagentType,
    });
  }

  const succeeded = root.success === true || root.ok === true;
  const backendError =
    firstString([root], ["error"]) ??
    (succeeded ? null : errorFromPayload(root));
  if (!succeeded || backendError) {
    throw new NarrativeStageDispatchError({
      message: `Narrative ${stage.name} agent did not complete: ${backendError || "backend reported failure"}`,
      code: "agent_failure",
      backendError,
      stage: stage.name,
      subagentType: stage.subagentType,
    });
  }

  const output = firstString(
    [root, asRecord(root.result)],
    ["output", "content", "text", "result"],
  );
  if (!output) {
    throw new NarrativeStageDispatchError({
      message: `Narrative ${stage.name} agent completed without candidate output`,
      code: "invalid_response",
      stage: stage.name,
      subagentType: stage.subagentType,
    });
  }

  return {
    success: true,
    output,
    error: null,
    stage: stage.name,
    subagentType: stage.subagentType,
    runId,
    turnId,
    promptAudit: built.audit,
    metadata: normalizeMetadata(root),
  };
}
