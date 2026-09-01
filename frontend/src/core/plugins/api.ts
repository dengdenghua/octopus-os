import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

import type {
  CapabilityInfo,
  PluginMigrationReadiness,
  PluginInfo,
  PluginLifecycleHistory,
  PluginRuntimeProfile,
  PluginRegistryUpdates,
  PluginSmokeSummary,
  PluginPublisherTrustReport,
} from "./types";
import type { HubPluginInfo, DiscoveredPlugin } from "./types";

// ── Legacy API (Codex plugins) ────────────────────────────

export async function listPlugins(): Promise<PluginInfo[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/plugins`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to list plugins: ${res.statusText}`);
  return (await res.json()) as PluginInfo[];
}

export async function getPlugin(pluginId: string): Promise<PluginInfo> {
  const res = await fetch(`${getBackendBaseURL()}/api/plugins/${pluginId}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to get plugin: ${res.statusText}`);
  return (await res.json()) as PluginInfo;
}

export async function listCapabilities(
  type?: string,
): Promise<CapabilityInfo[]> {
  const params = type ? `?type=${encodeURIComponent(type)}` : "";
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/capabilities${params}`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to list capabilities: ${res.statusText}`);
  return (await res.json()) as CapabilityInfo[];
}

export async function fetchPluginSmokeSummary(): Promise<PluginSmokeSummary> {
  const res = await fetch(`${getBackendBaseURL()}/api/plugins/smoke-summary`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Failed to get plugin smoke summary: ${res.statusText}`);
  }
  return (await res.json()) as PluginSmokeSummary;
}

export async function fetchPluginMigrationReadiness(): Promise<PluginMigrationReadiness> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/migration-readiness`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok) {
    throw new Error(
      `Failed to get plugin migration readiness: ${res.statusText}`,
    );
  }
  return (await res.json()) as PluginMigrationReadiness;
}

export async function fetchPluginPublisherTrust(): Promise<PluginPublisherTrustReport> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/publisher-trust`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok) {
    throw new Error(`Failed to get publisher trust: ${res.statusText}`);
  }
  return (await res.json()) as PluginPublisherTrustReport;
}

export async function fetchPluginLifecycleHistory(): Promise<PluginLifecycleHistory> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/lifecycle/history`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok) {
    throw new Error(
      `Failed to get plugin lifecycle history: ${res.statusText}`,
    );
  }
  return (await res.json()) as PluginLifecycleHistory;
}

export async function fetchPluginRegistryUpdates(): Promise<PluginRegistryUpdates> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/registry/updates`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok) {
    throw new Error(`Failed to get plugin registry updates: ${res.statusText}`);
  }
  return (await res.json()) as PluginRegistryUpdates;
}

export async function installPluginFromRegistry(
  pluginId: string,
): Promise<{ plugin_id: string; status: string; version: string }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/registry/install`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ plugin_id: pluginId, confirm_install: true }),
    },
  );
  if (!res.ok) {
    throw new Error(`Failed to install registry plugin: ${res.statusText}`);
  }
  return res.json() as Promise<{
    plugin_id: string;
    status: string;
    version: string;
  }>;
}

export async function rotatePluginPublisherKey(input: {
  publisher_id: string;
  previous_key_id?: string;
  new_key_id: string;
  new_public_key: string;
  reason: string;
}): Promise<{ status: string; trust: PluginPublisherTrustReport }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/publisher-trust/rotate`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ ...input, confirm_rotation: true }),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to rotate publisher key: ${res.statusText}`);
  return res.json() as Promise<{
    status: string;
    trust: PluginPublisherTrustReport;
  }>;
}

export async function revokePluginPublisherKey(input: {
  publisher_id: string;
  key_id: string;
  reason: string;
}): Promise<{ status: string; trust: PluginPublisherTrustReport }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/publisher-trust/revoke`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ ...input, confirm_revocation: true }),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to revoke publisher key: ${res.statusText}`);
  return res.json() as Promise<{
    status: string;
    trust: PluginPublisherTrustReport;
  }>;
}

export async function getPluginRuntime(
  pluginId: string,
): Promise<PluginRuntimeProfile> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/plugins/${encodeURIComponent(pluginId)}/runtime`,
    { headers: authHeaders() },
  );
  if (!res.ok) {
    throw new Error(`Failed to get plugin runtime: ${res.statusText}`);
  }
  return (await res.json()) as PluginRuntimeProfile;
}

// ── PluginHub API (new pluggable module architecture) ─────

const HUB_BASE = `${getBackendBaseURL()}/api/plugin-hub`;

/** List all loaded plugins via PluginHub. */
export async function hubListPlugins(): Promise<HubPluginInfo[]> {
  const res = await fetch(`${HUB_BASE}/plugins`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to list hub plugins: ${res.statusText}`);
  return (await res.json()) as HubPluginInfo[];
}

/** Scan for unloaded plugin candidates. */
export async function hubDiscoverPlugins(): Promise<DiscoveredPlugin[]> {
  const res = await fetch(`${HUB_BASE}/plugins/discover`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to discover plugins: ${res.statusText}`);
  return (await res.json()) as DiscoveredPlugin[];
}

/** Load a discovered plugin. */
export async function hubLoadPlugin(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${HUB_BASE}/plugins/${encodeURIComponent(name)}/load`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to load plugin: ${res.statusText}`);
  return res.json() as Promise<{ ok: boolean }>;
}

/** Start a loaded plugin. */
export async function hubStartPlugin(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${HUB_BASE}/plugins/${encodeURIComponent(name)}/start`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to start plugin: ${res.statusText}`);
  return res.json() as Promise<{ ok: boolean }>;
}

/** Stop a started plugin. */
export async function hubStopPlugin(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${HUB_BASE}/plugins/${encodeURIComponent(name)}/stop`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to stop plugin: ${res.statusText}`);
  return res.json() as Promise<{ ok: boolean }>;
}

/** Unload a plugin. */
export async function hubUnloadPlugin(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${HUB_BASE}/plugins/${encodeURIComponent(name)}/unload`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to unload plugin: ${res.statusText}`);
  return res.json() as Promise<{ ok: boolean }>;
}

/** Get a plugin's configuration. */
export async function hubGetPluginConfig(
  name: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(
    `${HUB_BASE}/plugins/${encodeURIComponent(name)}/config`,
    { headers: authHeaders() },
  );
  if (!res.ok)
    throw new Error(`Failed to get plugin config: ${res.statusText}`);
  return res.json() as Promise<Record<string, unknown>>;
}

/** Update a plugin's configuration. */
export async function hubUpdatePluginConfig(
  name: string,
  config: Record<string, unknown>,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${HUB_BASE}/plugins/${encodeURIComponent(name)}/config`,
    {
      method: "PUT",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(config),
    },
  );
  if (!res.ok)
    throw new Error(`Failed to update plugin config: ${res.statusText}`);
  return res.json() as Promise<{ ok: boolean }>;
}

/** Get full details for a single plugin. */
export async function hubGetPlugin(name: string): Promise<HubPluginInfo> {
  const res = await fetch(`${HUB_BASE}/plugins/${encodeURIComponent(name)}`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to get plugin detail: ${res.statusText}`);
  return res.json() as Promise<HubPluginInfo>;
}
