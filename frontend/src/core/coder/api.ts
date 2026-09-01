import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import type { components } from "@/core/api/openapi-types";
import { getBackendBaseURL } from "@/core/config";

type CodexSchemas = components["schemas"];
type CodexAccountResponseWire = CodexSchemas["CodexAccountResponse"];
type CodexAccountWire = CodexSchemas["CodexAccountWire"];
type CodexDailyUsageBucketWire = CodexSchemas["CodexDailyUsageBucket"];
type CodexLoginResponseWire = CodexSchemas["CodexLoginResponse"];
type CodexModelWire = CodexSchemas["CodexModelWire"];
type CodexModelsResponseWire = CodexSchemas["CodexModelsResponse"];
type CodexRateLimitBucketWire = CodexSchemas["CodexRateLimitBucket"];
type CodexRateLimitWindowWire = CodexSchemas["CodexRateLimitWindow"];
type CodexRateLimitsResponseWire = CodexSchemas["CodexRateLimitsResponse"];
type CodexUsageResponseWire = CodexSchemas["CodexUsageResponse"];
type CodexUsageSummaryWire = CodexSchemas["CodexUsageSummary"];

/** Product-level source name. The App Server wire value is still `chatgpt`,
 * but that bucket can be authenticated by either ChatGPT or an API key. */
export type CoderModelSource = "follow_system" | "codex_account";
export type CoderLoginType = "chatgpt" | "chatgptDeviceCode" | "apiKey";

export interface CoderAccount extends CodexAccountWire {
  email: string | null;
  plan_type: string | null;
}

export interface CoderAccountState extends Omit<
  CodexAccountResponseWire,
  "account"
> {
  account: CoderAccount | null;
  login_id: string | null;
  login_error: string | null;
}

export type CoderLoginResult = CodexLoginResponseWire;

export interface CoderModelOption extends Omit<
  CodexModelWire,
  "reasoning_efforts" | "input_modalities"
> {
  reasoning_efforts: string[];
  input_modalities: string[];
}

export interface CoderModelsResponse extends Omit<
  CodexModelsResponseWire,
  "models"
> {
  models: CoderModelOption[];
}

export type CoderRateLimitWindow = CodexRateLimitWindowWire;

export interface CoderRateLimitBucket extends Omit<
  CodexRateLimitBucketWire,
  "primary" | "secondary"
> {
  limit_name: string | null;
  primary: CoderRateLimitWindow | null;
  secondary: CoderRateLimitWindow | null;
  plan_type: string | null;
  rate_limit_reached_type: string | null;
}

export interface CoderRateLimits extends Omit<
  CodexRateLimitsResponseWire,
  "buckets"
> {
  buckets: CoderRateLimitBucket[];
  reset_credits_available: number | null;
}

export interface CoderUsage extends Omit<CodexUsageResponseWire, "summary"> {
  summary: CodexUsageSummaryWire & {
    lifetime_tokens: number | null;
    peak_daily_tokens: number | null;
    longest_running_turn_sec: number | null;
    current_streak_days: number | null;
    longest_streak_days: number | null;
  };
  daily_usage_buckets: Array<
    CodexDailyUsageBucketWire & { tokens: number | null }
  >;
}

export interface CoderApp {
  id: string;
  name: string;
  description: string;
  logo_url: string | null;
  install_url: string | null;
  is_accessible: boolean;
  is_enabled: boolean;
  selected: boolean;
}

export interface CoderAppsResponse {
  apps: CoderApp[];
}

export interface CoderModelProfile {
  source: CoderModelSource;
  selected_model: string | null;
  effective_model: string | null;
  system_model: string | null;
  reasoning_effort: string | null;
  model_source: "turn" | "role" | "system" | "codex_default";
  compatible: boolean;
  compatibility_reason: string | null;
  provider: string | null;
  proxy_required: boolean;
}

export interface UpdateCoderModelProfile {
  source: CoderModelSource;
  model?: string;
  reasoning_effort?: string | null;
}

export type CoderUpstreamUpdate = Required<
  CodexSchemas["CodexUpdateStatusResponse"]
>;

export const coderUpstreamUpdateQueryKey = [
  "coder",
  "codex",
  "upstream-update",
] as const;

async function responseError(response: Response, fallback: string) {
  const payload = (await response.json().catch(() => null)) as {
    detail?: unknown;
    message?: unknown;
  } | null;
  const detail = payload?.detail ?? payload?.message;
  return new Error(typeof detail === "string" ? detail : fallback);
}

export async function getCoderAccount(
  signal?: AbortSignal,
): Promise<CoderAccountState> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/coder/codex/account`,
    {
      headers: authHeaders(),
      signal,
    },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder account unavailable (${response.status})`,
    );
  }
  return (await response.json()) as CoderAccountState;
}

export async function startCoderLogin(
  type: CoderLoginType,
  apiKey?: string,
): Promise<CoderLoginResult> {
  const body: { type: CoderLoginType; api_key?: string } = { type };
  if (type === "apiKey" && apiKey) body.api_key = apiKey;
  const response = await fetch(`${getBackendBaseURL()}/api/coder/codex/login`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder login failed (${response.status})`,
    );
  }
  return (await response.json()) as CoderLoginResult;
}

export async function cancelCoderLogin(loginId: string): Promise<boolean> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/coder/codex/login/${encodeURIComponent(loginId)}/cancel`,
    { method: "POST", headers: jsonAuthHeaders() },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder login cancellation failed (${response.status})`,
    );
  }
  const payload = (await response.json().catch(() => null)) as {
    cancelled?: unknown;
  } | null;
  return payload?.cancelled === true;
}

export async function logoutCoderAccount(): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/coder/codex/logout`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
    },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder logout failed (${response.status})`,
    );
  }
}

export async function getCoderModels(
  signal?: AbortSignal,
): Promise<CoderModelsResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/coder/codex/models?include_hidden=false`,
    { headers: authHeaders(), signal },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder models unavailable (${response.status})`,
    );
  }
  return (await response.json()) as CoderModelsResponse;
}

export async function getCoderRateLimits(
  signal?: AbortSignal,
): Promise<CoderRateLimits> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/coder/codex/rate-limits`,
    { headers: authHeaders(), signal },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder rate limits unavailable (${response.status})`,
    );
  }
  return (await response.json()) as CoderRateLimits;
}

export async function getCoderUsage(signal?: AbortSignal): Promise<CoderUsage> {
  const response = await fetch(`${getBackendBaseURL()}/api/coder/codex/usage`, {
    headers: authHeaders(),
    signal,
  });
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder usage unavailable (${response.status})`,
    );
  }
  return (await response.json()) as CoderUsage;
}

export async function getCoderApps(
  signal?: AbortSignal,
): Promise<CoderAppsResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/coder/codex/apps`, {
    headers: authHeaders(),
    signal,
  });
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder connectors unavailable (${response.status})`,
    );
  }
  return (await response.json()) as CoderAppsResponse;
}

export async function updateCoderApps(
  appIds: string[],
): Promise<CoderAppsResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/coder/codex/apps`, {
    method: "PUT",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ app_ids: appIds }),
  });
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder connectors update failed (${response.status})`,
    );
  }
  return (await response.json()) as CoderAppsResponse;
}

export async function getCoderModelProfile(
  signal?: AbortSignal,
): Promise<CoderModelProfile> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/coder/codex/model-profile`,
    { headers: authHeaders(), signal },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder model profile unavailable (${response.status})`,
    );
  }
  return normalizeModelProfile(await response.json());
}

function getCoderMutationBaseURL(): string {
  const configured = getBackendBaseURL();
  if (configured || !import.meta.env.DEV || typeof window === "undefined") {
    return configured;
  }

  // Dev pages share localhost's HTTP/1.1 connection pool with several SSE
  // streams. Use the equivalent loopback host for small control-plane writes
  // so a model selection cannot sit behind those long-lived connections.
  const { protocol, hostname, port } = window.location;
  const alternateHost =
    hostname === "localhost"
      ? "127.0.0.1"
      : hostname === "127.0.0.1"
        ? "localhost"
        : null;
  if (!alternateHost) return configured;
  return `${protocol}//${alternateHost}${port ? `:${port}` : ""}`;
}

export async function updateCoderModelProfile(
  input: UpdateCoderModelProfile,
): Promise<CoderModelProfile> {
  const response = await fetch(
    `${getCoderMutationBaseURL()}/api/coder/codex/model-profile`,
    {
      method: "PUT",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        mode: input.source === "codex_account" ? "chatgpt" : "follow_system",
        ...(input.model ? { model: input.model } : {}),
        ...(input.reasoning_effort !== undefined
          ? { reasoning_effort: input.reasoning_effort }
          : {}),
      }),
    },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Coder model profile update failed (${response.status})`,
    );
  }
  return normalizeModelProfile(await response.json());
}

export async function getCoderUpstreamUpdate(
  signal?: AbortSignal,
): Promise<CoderUpstreamUpdate> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/coder/codex/upstream-update`,
    { headers: authHeaders(), signal },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Codex update status unavailable (${response.status})`,
    );
  }
  return (await response.json()) as CoderUpstreamUpdate;
}

export async function checkCoderUpstreamUpdate(): Promise<CoderUpstreamUpdate> {
  const response = await fetch(
    `${getCoderMutationBaseURL()}/api/coder/codex/upstream-update/check`,
    { method: "POST", headers: authHeaders() },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Codex update check failed (${response.status})`,
    );
  }
  return (await response.json()) as CoderUpstreamUpdate;
}

export async function approveCoderUpstreamUpdate(
  version: string,
): Promise<CoderUpstreamUpdate> {
  const response = await fetch(
    `${getCoderMutationBaseURL()}/api/coder/codex/upstream-update/approve`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ version }),
    },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Codex update approval failed (${response.status})`,
    );
  }
  return (await response.json()) as CoderUpstreamUpdate;
}

function normalizeModelProfile(payload: unknown): CoderModelProfile {
  const row = (payload ?? {}) as Record<string, unknown>;
  const rawModelSource = row.model_source;
  return {
    source: row.mode === "chatgpt" ? "codex_account" : "follow_system",
    selected_model:
      typeof row.selected_model === "string" ? row.selected_model : null,
    effective_model:
      typeof row.effective_model === "string" ? row.effective_model : null,
    system_model:
      typeof row.system_model === "string" ? row.system_model : null,
    reasoning_effort:
      typeof row.reasoning_effort === "string" ? row.reasoning_effort : null,
    model_source:
      rawModelSource === "turn" ||
      rawModelSource === "role" ||
      rawModelSource === "system" ||
      rawModelSource === "codex_default"
        ? rawModelSource
        : "codex_default",
    compatible: row.compatible === true,
    compatibility_reason:
      typeof row.compatibility_reason === "string"
        ? row.compatibility_reason
        : null,
    provider: typeof row.provider === "string" ? row.provider : null,
    proxy_required: row.proxy_required === true,
  };
}

/**
 * Coder's model source is owned by its principal-scoped model profile. Remove
 * ordinary conversation-picker overrides before the realtime hook can copy
 * them into top-level App Server fields. The backend repeats this check as an
 * authorization boundary; this client-side projection keeps the wire honest.
 */
export function applyCoderModelProfileBoundary<
  T extends Record<string, unknown>,
>(
  agentId: string | null | undefined,
  context: T,
  executionEngine?: "echo" | "codex",
): T {
  if (executionEngine !== "codex" && agentId !== "coder") return context;
  const next = { ...context };
  delete next.model_name;
  delete next.reasoning_effort;
  delete next.partner_model;
  return next;
}

export function coderQueryKeys(principalKey: string) {
  const principal = principalKey.trim() || "local";
  const root = ["coder", "codex", principal] as const;
  return {
    root,
    account: [...root, "account"] as const,
    models: [...root, "models"] as const,
    rateLimits: [...root, "rate-limits"] as const,
    usage: [...root, "usage"] as const,
    apps: [...root, "apps"] as const,
    profile: [...root, "model-profile"] as const,
    upstreamUpdate: coderUpstreamUpdateQueryKey,
  };
}
