import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

export interface Capabilities {
  browser_automation: boolean;
  desktop_automation: boolean;
}

export interface SaveCapabilitiesResponse {
  ok: boolean;
  capabilities: Capabilities;
  restart_required: boolean;
  message: string;
  registry?: {
    registered: string[];
    removed: string[];
  };
}

export interface RestartBackendResponse {
  ok: boolean;
  reason?: string;
}

export async function getCapabilities(): Promise<Capabilities> {
  const res = await fetch(`${getBackendBaseURL()}/api/settings/capabilities`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to load capabilities: ${res.statusText}`);
  return (await res.json()) as Capabilities;
}

export async function saveCapabilities(
  body: Capabilities,
): Promise<SaveCapabilitiesResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/settings/capabilities`, {
    method: "PUT",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `Failed to save capabilities: ${res.status} ${detail || res.statusText}`,
    );
  }
  return (await res.json()) as SaveCapabilitiesResponse;
}

/* Implementation note. */
export async function restartBackend(): Promise<RestartBackendResponse> {
  // Implementation note.
  if (typeof window === "undefined" || !window.echo?.isElectron) {
    return { ok: false, reason: "not in electron environment" };
  }
  try {
    const result = await window.echo.backend?.restart?.();
    return result || { ok: false, reason: "ipc not available" };
  } catch (e) {
    swallow(e);
    return { ok: false, reason: e instanceof Error ? e.message : String(e) };
  }
}
