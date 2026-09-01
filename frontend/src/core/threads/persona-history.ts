import { isPrimaryPersonaAgentId } from "@/core/agents/persona-policy";

import type { AgentThread } from "./types";

export function threadOwnerAgentId(thread: AgentThread): string {
  const metadata = (thread.metadata ?? {}) as Record<string, unknown>;
  const values = (thread.values ?? {}) as Record<string, unknown>;
  const candidates = [
    metadata.agent,
    metadata.agent_name,
    metadata.agent_id,
    metadata.lead_agent_name,
    metadata.current_agent,
    values.current_speaker,
    values.agent_name,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }
  return "";
}

/**
 * Fixed personas keep their own lanes. Legacy expert/CLI/device-owned tasks
 * belong to the shared history because those actors no longer own identities.
 */
export function threadVisibleInPersonaHistory(
  thread: AgentThread,
  personaId: string,
): boolean {
  const ownerId = threadOwnerAgentId(thread);
  if (!ownerId) return personaId === "general";
  if (ownerId === "echo") return false;
  return ownerId === personaId || !isPrimaryPersonaAgentId(ownerId);
}
