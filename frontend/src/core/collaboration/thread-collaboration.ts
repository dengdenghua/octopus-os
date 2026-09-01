export interface ThreadCollaborationRosterEntry {
  agent_id: string;
  name: string;
  display_name: string;
  avatar_url?: string | null;
  icon?: string | null;
  role: "tl" | "member";
}

export interface CollaborationAgentProfile {
  name: string;
  display_name?: string | null;
  avatar_url?: string | null;
  icon?: string | null;
}

function recordFromUnknown(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function stringsFromUnknown(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function rosterFromUnknown(value: unknown): ThreadCollaborationRosterEntry[] {
  if (!Array.isArray(value)) return [];
  const roster: ThreadCollaborationRosterEntry[] = [];
  for (const item of value) {
    const record = recordFromUnknown(item);
    if (!record) continue;
    const agentId = firstString(
      record.agent_id,
      record.name,
      record.id,
      record.ref,
    );
    if (!agentId) continue;
    const role = record.role === "tl" ? "tl" : "member";
    roster.push({
      agent_id: agentId,
      name: firstString(record.name, agentId),
      display_name: firstString(record.display_name, record.name, agentId),
      avatar_url: firstString(record.avatar_url) || null,
      icon: firstString(record.icon) || null,
      role,
    });
  }
  return roster;
}

function collaborationSourcesFromThread(
  metadata?: Record<string, unknown> | null,
  values?: Record<string, unknown> | null,
): Record<string, unknown>[] {
  const sources: Record<string, unknown>[] = [];
  const meta = recordFromUnknown(metadata);
  const vals = recordFromUnknown(values);
  if (meta) {
    sources.push(meta);
    const context = recordFromUnknown(meta.context);
    if (context) sources.push(context);
  }
  if (vals) {
    sources.push(vals);
    const context = recordFromUnknown(vals.context);
    if (context) sources.push(context);
  }
  return sources;
}

export function collaborationRosterFromThread(
  metadata: Record<string, unknown> | null | undefined,
  values: Record<string, unknown> | null | undefined,
  leaderId: string,
): ThreadCollaborationRosterEntry[] {
  const byId = new Map<string, ThreadCollaborationRosterEntry>();
  const addEntry = (entry: ThreadCollaborationRosterEntry) => {
    if (!entry.agent_id || byId.has(entry.agent_id)) return;
    byId.set(entry.agent_id, entry);
  };

  const sources = collaborationSourcesFromThread(metadata, values);
  for (const source of sources) {
    for (const entry of rosterFromUnknown(source.agent_roster)) {
      addEntry(entry);
    }
  }

  const leader = leaderId.trim();
  if (byId.size === 0 && leader) {
    const taskAgentRefs = sources.flatMap((source) =>
      stringsFromUnknown(source.task_agent_refs),
    );
    const collaboratorRefs = Array.from(new Set(taskAgentRefs)).filter(
      (id) => id !== leader,
    );
    if (collaboratorRefs.length > 0) {
      addEntry({
        agent_id: leader,
        name: leader,
        display_name: leader,
        role: "tl",
      });
      for (const id of collaboratorRefs) {
        addEntry({
          agent_id: id,
          name: id,
          display_name: id,
          role: "member",
        });
      }
    }
  }

  const roster = Array.from(byId.values());
  if (roster.length === 0) return [];
  const hasLeader = roster.some((entry) => entry.role === "tl");
  if (hasLeader) return roster;
  return roster.map((entry, index) => ({
    ...entry,
    role:
      entry.agent_id === leader || (!leader && index === 0) ? "tl" : "member",
  }));
}

/**
 * Refresh persisted roster identities from the current agent catalogue.
 *
 * Older thread snapshots often contain only an emoji icon. Keep their
 * membership order and roles, but prefer current profile fields so an avatar
 * added later (or a cache-busted avatar URL) is shown immediately.
 */
export function hydrateCollaborationRoster(
  roster: ThreadCollaborationRosterEntry[],
  profiles: CollaborationAgentProfile[],
): ThreadCollaborationRosterEntry[] {
  const profileByIdentity = new Map<string, CollaborationAgentProfile>();
  for (const profile of profiles) {
    const identities = [profile.name, profile.display_name]
      .map((value) => value?.trim())
      .filter((value): value is string => Boolean(value));
    for (const identity of identities) profileByIdentity.set(identity, profile);
  }

  return roster.map((entry) => {
    const profile =
      profileByIdentity.get(entry.agent_id) ??
      profileByIdentity.get(entry.name) ??
      profileByIdentity.get(entry.display_name);
    if (!profile) return entry;
    return {
      ...entry,
      name: profile.name || entry.name,
      display_name:
        profile.display_name?.trim() || profile.name || entry.display_name,
      avatar_url: profile.avatar_url ?? entry.avatar_url ?? null,
      icon: profile.icon ?? entry.icon ?? null,
    };
  });
}
