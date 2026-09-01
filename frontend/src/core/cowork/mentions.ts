import type {
  CollaborationSession,
  CoworkMemberKind,
  CoworkRoomParticipant,
} from "./types";

/** Structural match for MentionAutocomplete's member input without coupling core
 * collaboration data to the workspace component tree. */
export interface CoworkMentionMember {
  name: string;
  display_name?: string | null;
  icon?: string | null;
  description?: string | null;
  avatar_url?: string | null;
  /** Value inserted after `@`; agents use `agent:<stable id>`. */
  mention_value?: string | null;
  member_id: string;
  kind: CoworkMemberKind;
}

export interface CoworkMentionProfile {
  name: string;
  display_name?: string | null;
  icon?: string | null;
  description?: string | null;
  avatar_url?: string | null;
}

function value(record: CoworkRoomParticipant, key: string): string {
  const candidate = record[key];
  return typeof candidate === "string" ? candidate.trim() : "";
}

function profileFor(
  id: string,
  profiles: CoworkMentionProfile[],
): CoworkMentionProfile | undefined {
  return profiles.find(
    (profile) => profile.name === id || profile.display_name === id,
  );
}

/** Combine the canonical cowork roster and linked-room participant projection
 * into the member list consumed by the shared chat @ autocomplete. */
export function coworkSessionToMentionMembers(
  session: CollaborationSession | null | undefined,
  profiles: CoworkMentionProfile[] = [],
): CoworkMentionMember[] {
  if (!session) return [];
  const members = new Map<string, CoworkMentionMember>();

  const add = (
    id: string,
    kind: CoworkMemberKind,
    participant?: CoworkRoomParticipant,
  ) => {
    const safeId = id.trim();
    if (!safeId) return;
    const profile = profileFor(safeId, profiles);
    const previous = members.get(safeId);
    const displayName =
      (participant && value(participant, "display_name")) ||
      profile?.display_name?.trim() ||
      (participant && value(participant, "name")) ||
      profile?.name ||
      previous?.display_name?.trim() ||
      safeId;
    const participantIcon = participant ? value(participant, "icon") : "";
    const participantDescription = participant
      ? value(participant, "description")
      : "";
    const participantAvatar = participant
      ? value(participant, "avatar_url")
      : "";
    members.set(safeId, {
      name: profile?.name || previous?.name || safeId,
      display_name: displayName,
      icon: profile?.icon ?? (participantIcon || previous?.icon),
      description:
        profile?.description ??
        (participantDescription || previous?.description),
      avatar_url:
        profile?.avatar_url ?? (participantAvatar || previous?.avatar_url),
      mention_value: kind === "agent" ? `agent:${safeId}` : displayName,
      member_id: safeId,
      kind,
    });
  };

  for (const participant of session.room_participants ?? []) {
    const id =
      value(participant, "id") ||
      value(participant, "participant_id") ||
      value(participant, "name");
    add(id, participant.kind === "agent" ? "agent" : "human", participant);
  }
  for (const member of session.roster ?? []) {
    if (member.muted) continue;
    add(member.id, member.kind);
  }
  return Array.from(members.values());
}

/** Read stable agent ids back out of a composed room message. */
export function extractCoworkAgentMentions(text: string): string[] {
  const mentions: string[] = [];
  const seen = new Set<string>();
  for (const match of text.matchAll(/@agent:([\w.-]+)/g)) {
    const id = match[1];
    if (id && !seen.has(id)) {
      seen.add(id);
      mentions.push(id);
    }
  }
  return mentions;
}
