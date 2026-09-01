import { taskWorkspaceRoute } from "@/core/router/task-workspace-route";
import { primaryPersonaAgentIdOrDefault } from "@/core/agents/persona-policy";

export type TaskCollaboratorMode = "chat" | "cluster" | "swarm";

export interface TaskCollaboratorPreset {
  leaderId?: string | null;
  collaboratorIds?: string[];
  mode?: TaskCollaboratorMode;
  label?: string;
  openPicker?: boolean;
}

export const TASK_COLLABORATOR_PRESET_EVENT =
  "echo:task-collaborator-preset";

const STORAGE_KEY = "echo:task-collaborator-preset";

function normalizeIds(ids: string[] | undefined): string[] {
  const seen = new Set<string>();
  const next: string[] = [];
  for (const id of ids ?? []) {
    const clean = id.trim();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    next.push(clean);
  }
  return next;
}

export function normalizeTaskCollaboratorPreset(
  preset: TaskCollaboratorPreset,
): TaskCollaboratorPreset {
  const mode =
    preset.mode === "swarm" || preset.mode === "chat" ? preset.mode : "cluster";
  const requestedLeaderId = preset.leaderId?.trim() || null;
  return {
    leaderId: requestedLeaderId
      ? primaryPersonaAgentIdOrDefault(requestedLeaderId)
      : null,
    collaboratorIds: normalizeIds(preset.collaboratorIds),
    mode,
    label: preset.label?.trim() || undefined,
    openPicker: Boolean(preset.openPicker),
  };
}

export function writeTaskCollaboratorPreset(
  preset: TaskCollaboratorPreset,
): void {
  if (typeof window === "undefined") return;
  const normalized = normalizeTaskCollaboratorPreset(preset);
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // Best-effort handoff; the live event below still covers same-page use.
  }
  window.dispatchEvent(
    new CustomEvent(TASK_COLLABORATOR_PRESET_EVENT, { detail: normalized }),
  );
}

export function consumeTaskCollaboratorPreset(): TaskCollaboratorPreset | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    window.sessionStorage.removeItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as TaskCollaboratorPreset;
    return normalizeTaskCollaboratorPreset(parsed);
  } catch {
    return null;
  }
}

export function taskCollaboratorRouteForLeader(
  leaderId?: string | null,
): string {
  return taskWorkspaceRoute({
    agentId: leaderId ? primaryPersonaAgentIdOrDefault(leaderId) : null,
  });
}
