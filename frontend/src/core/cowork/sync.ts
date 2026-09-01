import type { CoworkMode, CoworkState } from "./types";

export interface CoworkSelectionSyncPlan {
  desiredAgentIds: string[];
  inviteAgentIds: string[];
  removeAgentIds: string[];
  mode: CoworkMode;
  shouldSetMode: boolean;
  hasWork: boolean;
  signature: string;
}

function uniqueCleanIds(ids: string[], exclude = ""): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of ids) {
    const clean = id.trim();
    if (!clean || clean === exclude || seen.has(clean)) continue;
    seen.add(clean);
    out.push(clean);
  }
  return out;
}

function currentParticipantAgentIds(state?: CoworkState | null): string[] {
  return (
    state?.roster
      .filter(
        (member) => member.kind === "agent" && member.role === "participant",
      )
      .map((member) => member.id)
      .filter(Boolean) ?? []
  );
}

export function buildCoworkSelectionSyncPlan({
  leaderId,
  collaboratorIds,
  mode,
  current,
  keepLeader = false,
}: {
  leaderId: string;
  collaboratorIds: string[];
  mode: CoworkMode;
  current?: CoworkState | null;
  /** Project groups remain real groups even with only their lead agent. */
  keepLeader?: boolean;
}): CoworkSelectionSyncPlan {
  const leader = leaderId.trim();
  const collaborators = uniqueCleanIds(collaboratorIds, leader);
  const nextMode: CoworkMode = collaborators.length > 0 ? mode : "chat";
  const currentIds = currentParticipantAgentIds(current);
  const currentSet = new Set(currentIds);
  const desiredSet = new Set<string>();

  if (leader && (keepLeader || collaborators.length > 0)) {
    desiredSet.add(leader);
  }
  for (const id of collaborators) desiredSet.add(id);

  const desiredAgentIds = Array.from(desiredSet);
  const inviteAgentIds = desiredAgentIds.filter((id) => !currentSet.has(id));
  const removeAgentIds =
    current === undefined || current === null
      ? []
      : currentIds.filter((id) => !desiredSet.has(id));
  const shouldSetMode =
    current === undefined || current === null
      ? collaborators.length > 0 || keepLeader
      : current.mode !== nextMode;
  const hasWork =
    inviteAgentIds.length > 0 || removeAgentIds.length > 0 || shouldSetMode;

  return {
    desiredAgentIds,
    inviteAgentIds,
    removeAgentIds,
    mode: nextMode,
    shouldSetMode,
    hasWork,
    signature: [
      `leader=${leader}`,
      `keepLeader=${keepLeader ? "1" : "0"}`,
      `mode=${nextMode}`,
      `desired=${desiredAgentIds.join(",")}`,
      `current=${current ? currentIds.join(",") : "unknown"}`,
      `currentMode=${current?.mode ?? "unknown"}`,
    ].join("|"),
  };
}
