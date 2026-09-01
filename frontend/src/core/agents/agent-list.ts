import type { Agent } from "./types";

/** Dedupe an agent list by stable id, keeping the first occurrence. */
export function dedupeAgentsByName(agents: Agent[]): Agent[] {
  const seen = new Set<string>();
  return agents.filter((agent) => {
    if (seen.has(agent.name)) return false;
    seen.add(agent.name);
    return true;
  });
}

/** Collapse persona aliases such as `Eve` and `Eve / Siren`. */
export function dedupePersonaAgentsByDisplayName(agents: Agent[]): Agent[] {
  const result: Agent[] = [];
  const personaIndexByLabel = new Map<string, number>();
  for (const agent of agents) {
    const isExternalRuntime = /^(?:registry_local_|mobile_)/.test(agent.name);
    if (isExternalRuntime) {
      result.push(agent);
      continue;
    }

    const displayName = agent.display_name || agent.name;
    const label = displayName
      .split(/\s*\/\s*/)[0]
      ?.trim()
      .toLowerCase();
    if (!label) continue;

    const existingIndex = personaIndexByLabel.get(label);
    if (existingIndex === undefined) {
      personaIndexByLabel.set(label, result.length);
      result.push(agent);
      continue;
    }

    const existing = result[existingIndex];
    const existingHasAlias = (existing?.display_name || existing?.name || "")
      .trim()
      .includes("/");
    if (existingHasAlias && !displayName.includes("/")) {
      result[existingIndex] = agent;
    }
  }
  return result;
}
