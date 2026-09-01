import { swallow } from "@/core/utils/log";
import { getBackendBaseURL, getControlPlaneBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

import type { Agent, CreateAgentRequest, UpdateAgentRequest } from "./types";

const BACKEND_UNAVAILABLE_STATUSES = new Set([502, 503, 504]);
const AGENT_LIST_TIMEOUT_MS = 5_000;
// Only the system-level admin persona is hidden from the agent list;
// desktop_operator (Raven) is a first-class user-facing CUA persona
// since #22 (CUA productization).
const HIDDEN_AGENT_IDS = new Set(["admin"]);

export class AgentNameCheckError extends Error {
  constructor(
    message: string,
    public readonly reason: "backend_unreachable" | "request_failed",
  ) {
    super(message);
    this.name = "AgentNameCheckError";
  }
}

export async function listAgents(opts?: {
  signal?: AbortSignal;
}): Promise<Agent[]> {
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort(opts?.signal?.reason);
  if (opts?.signal?.aborted) abortFromCaller();
  else opts?.signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), AGENT_LIST_TIMEOUT_MS);

  try {
    const res = await fetch(
      `${getControlPlaneBaseURL()}/api/agents?include_visuals=false`,
      {
        cache: "no-store",
        headers: authHeaders(),
        signal: controller.signal,
      },
    );
    if (!res.ok) throw new Error(`Failed to load agents: ${res.statusText}`);
    const data = (await res.json()) as Agent[] | { agents?: Agent[] };
    const agents = Array.isArray(data) ? data : (data.agents ?? []);
    return agents.filter((agent) => !HIDDEN_AGENT_IDS.has(agent.name));
  } finally {
    clearTimeout(timeout);
    opts?.signal?.removeEventListener("abort", abortFromCaller);
  }
}

export async function getAgent(
  name: string,
  opts?: { signal?: AbortSignal },
): Promise<Agent> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}?v=${Date.now()}`,
    {
      cache: "no-store",
      headers: authHeaders(),
      signal: opts?.signal,
    },
  );
  if (!res.ok) throw new Error(`Agent '${name}' not found`);
  return (await res.json()) as Agent;
}

export async function createAgent(request: CreateAgentRequest): Promise<Agent> {
  const res = await fetch(`${getBackendBaseURL()}/api/agents`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(err.detail ?? `Failed to create agent: ${res.statusText}`);
  }
  return (await res.json()) as Agent;
}

export async function updateAgent(
  name: string,
  request: UpdateAgentRequest,
): Promise<Agent> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}`,
    {
      method: "PUT",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(err.detail ?? `Failed to update agent: ${res.statusText}`);
  }
  return (await res.json()) as Agent;
}

export async function generateAgentVisuals(
  name: string,
  request: {
    provider?: string;
    style_prompt?: string;
    reference_images?: string[];
  } = {},
): Promise<{
  agent_id: string;
  provider: string;
  avatar_url?: string | null;
  visual_urls: Record<string, string>;
}> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}/visuals/generate`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to generate visuals: ${res.statusText}`,
    );
  }
  return (await res.json()) as {
    agent_id: string;
    provider: string;
    avatar_url?: string | null;
    visual_urls: Record<string, string>;
  };
}

export async function deleteAgent(name: string): Promise<void> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(name)}`,
    {
      method: "DELETE",
      headers: authHeaders(),
    },
  );
  if (!res.ok) throw new Error(`Failed to delete agent: ${res.statusText}`);
}

export async function checkAgentName(
  name: string,
): Promise<{ available: boolean; name: string }> {
  let res: Response;
  try {
    res = await fetch(
      `${getBackendBaseURL()}/api/agents/check?name=${encodeURIComponent(name)}`,
      { headers: authHeaders() },
    );
  } catch (e) {
    swallow(e);
    throw new AgentNameCheckError(
      "Could not reach the Echo backend.",
      "backend_unreachable",
    );
  }

  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    if (BACKEND_UNAVAILABLE_STATUSES.has(res.status)) {
      throw new AgentNameCheckError(
        "Could not reach the Echo backend.",
        "backend_unreachable",
      );
    }
    throw new AgentNameCheckError(
      err.detail ?? `Failed to check agent name: ${res.statusText}`,
      "request_failed",
    );
  }
  return (await res.json()) as { available: boolean; name: string };
}
