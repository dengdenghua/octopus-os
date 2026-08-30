import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

export interface ArmOption {
  arm_id: string;
  display_name: string;
  description: string;
  affinity: string[];
  icon: string;
  skills: string[];
}

export interface ToolRegistry {
  arms: string[];
  extra_affinity: string[];
  private_skills: string[];
}

export interface CapabilityPermission {
  id: string;
  enabled: boolean;
  available: boolean;
  skill_names: string[];
}

export async function listArms(): Promise<ArmOption[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/arms`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to list arms: ${res.statusText}`);
  return (await res.json()) as ArmOption[];
}

export async function getAgentToolRegistry(
  agentId: string,
): Promise<ToolRegistry> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(agentId)}/tool-registry`,
    { headers: authHeaders() },
  );
  if (!res.ok)
    throw new Error(`Failed to load tool-registry: ${res.statusText}`);
  return (await res.json()) as ToolRegistry;
}

export async function saveAgentToolRegistry(
  agentId: string,
  body: ToolRegistry,
): Promise<void> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/agents/${encodeURIComponent(agentId)}/tool-registry`,
    {
      method: "PUT",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `Failed to save tool-registry: ${res.status} ${detail || res.statusText}`,
    );
  }
}

export async function listCapabilityPermissions(): Promise<
  CapabilityPermission[]
> {
  const res = await fetch(`${getBackendBaseURL()}/api/capability-permissions`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Failed to list capability permissions: ${res.statusText}`);
  }
  const body = (await res.json()) as { permissions: CapabilityPermission[] };
  return body.permissions;
}

export async function updateCapabilityPermission(
  group: string,
  enabled: boolean,
): Promise<CapabilityPermission> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/capability-permissions/${encodeURIComponent(group)}`,
    {
      method: "PUT",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ enabled }),
    },
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `Failed to update capability permission: ${res.status} ${detail || res.statusText}`,
    );
  }
  return (await res.json()) as CapabilityPermission;
}
