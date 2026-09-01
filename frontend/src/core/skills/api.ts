import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

import type {
  CustomSkillContent,
  CustomSkillUpdateRequest,
  SkillInfo,
  SkillInstallRequest,
  SkillInstallResponse,
  SkillPerformance,
  SkillRollbackRequest,
  SkillUpdateRequest,
} from "./types";

export async function listSkills(): Promise<SkillInfo[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/skills`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to list skills: ${res.statusText}`);
  const data = (await res.json()) as { skills: SkillInfo[] };
  return data.skills;
}

export async function getSkill(name: string): Promise<SkillInfo> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/skills/${encodeURIComponent(name)}`,
    { headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to get skill: ${res.statusText}`);
  return (await res.json()) as SkillInfo;
}

export async function updateSkill(
  name: string,
  request: SkillUpdateRequest,
): Promise<SkillInfo> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/skills/${encodeURIComponent(name)}`,
    {
      method: "PUT",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) throw new Error(`Failed to update skill: ${res.statusText}`);
  return (await res.json()) as SkillInfo;
}

export async function enableSkill(
  skillName: string,
  enabled: boolean,
): Promise<void> {
  const endpoint = enabled ? "enable" : "disable";
  const res = await fetch(
    `${getBackendBaseURL()}/api/skills/${encodeURIComponent(skillName)}/${endpoint}`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok)
    throw new Error(
      `Failed to ${enabled ? "enable" : "disable"} skill: ${res.statusText}`,
    );
}

export async function enableMarketSkill(skillName: string): Promise<void> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/skills-market/${encodeURIComponent(skillName)}/enable`,
    { method: "POST", headers: authHeaders() },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to enable market skill: ${res.statusText}`,
    );
  }
}

export async function loadSkills(): Promise<SkillInfo[]> {
  return listSkills();
}

export async function installSkill(
  request: SkillInstallRequest,
): Promise<SkillInstallResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/skills/install`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(err.detail ?? `Failed to install skill: ${res.statusText}`);
  }
  return (await res.json()) as SkillInstallResponse;
}

export async function listCustomSkills(): Promise<SkillInfo[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/skills/custom`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to list custom skills: ${res.statusText}`);
  const data = (await res.json()) as { skills: SkillInfo[] };
  return data.skills;
}

export async function getCustomSkill(
  name: string,
): Promise<CustomSkillContent> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(name)}`,
    { headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to get custom skill: ${res.statusText}`);
  return (await res.json()) as CustomSkillContent;
}

export async function updateCustomSkill(
  name: string,
  request: CustomSkillUpdateRequest,
): Promise<CustomSkillContent> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(name)}`,
    {
      method: "PUT",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to update custom skill: ${res.statusText}`,
    );
  }
  return (await res.json()) as CustomSkillContent;
}

export async function deleteCustomSkill(
  name: string,
): Promise<{ success: boolean }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(name)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  if (!res.ok)
    throw new Error(`Failed to delete custom skill: ${res.statusText}`);
  return (await res.json()) as { success: boolean };
}

export async function rollbackCustomSkill(
  name: string,
  request: SkillRollbackRequest,
): Promise<CustomSkillContent> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(name)}/rollback`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) throw new Error(`Failed to rollback skill: ${res.statusText}`);
  return (await res.json()) as CustomSkillContent;
}

export async function getSkillPerformance(): Promise<SkillPerformance[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/skills/performance`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to get skill performance: ${res.statusText}`);
  return (await res.json()) as SkillPerformance[];
}

export async function getDecliningSkills(): Promise<SkillPerformance[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/skills/declining`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to get declining skills: ${res.statusText}`);
  return (await res.json()) as SkillPerformance[];
}
