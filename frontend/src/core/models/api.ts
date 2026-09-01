import { getBackendBaseURL } from "../config";
import { authHeaders } from "@/core/auth/api";

import type { Model } from "./types";

export async function loadModels(): Promise<Model[]> {
  // Use the LLM catalog endpoint, not /api/models — the latter aliases
  // the OpenAI-compat gateway which exposes *skills* (e.g.
  // "echo-agent/list_cwd") as "models" for external clients that
  // want to call a skill via model= routing. For the in-app
  // ModelPicker we want real LLM options (Echo Mix + configured custom models).
  const res = await fetch(`${getBackendBaseURL()}/api/llm-models`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load models: ${res.status} ${res.statusText}`);
  const { models } = (await res.json()) as { models: Model[] };
  return models ?? [];
}
