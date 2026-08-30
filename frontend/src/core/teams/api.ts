import { swallow } from "@/core/utils/log";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { eventBus } from "@/core/events";
import type { Agent } from "@/core/agents/types";

export interface TeamParticipant {
  id: string;
  display_name: string;
  role: TeamParticipantRole;
  actor_id?: string | null;
  joined_at: string;
  last_seen_at?: string | null;
  status: string;
  // Governance (see team_rooms_router.py)
  muted?: boolean;
  speak_mode?: SpeakMode;
  twin_agent_id?: string | null;
  host_id?: string | null;
}

export interface Team {
  id: string;
  name: string;
  thread_id?: string | null;
  members: Agent[];
  leaderId: string | null;
  owner_id?: string | null;
  created_at?: string;
  updated_at?: string;
  participants?: TeamParticipant[];
  invite_token?: string | null;
  invite_role?: TeamParticipantRole;
  join_policy?: TeamJoinPolicy;
  is_project_group?: boolean;
  project_id?: string | null;
  // Turn-engine floor state
  speaker_policy?: SpeakerPolicy;
  current_speaker_id?: string | null;
  moderator_id?: string | null;
  floor_requests?: string[];
}

export type TeamParticipantRole = "owner" | "member" | "viewer";
export type TeamInviteRole = Exclude<TeamParticipantRole, "owner">;
export type TeamInviteStatus = "active" | "expired" | "exhausted" | "revoked";
export type TeamJoinPolicy = "direct_join" | "apply_then_join";
export type TeamJoinRequestStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "withdrawn"
  | "expired"
  | "cancelled";

// Who may speak in a room. ``free``/``admin_only`` are stateless; the
// trio drive the turn-engine floor. Mirrors the backend _SPEAKER_POLICIES.
export type SpeakerPolicy =
  | "free"
  | "admin_only"
  | "round_robin"
  | "roll_call"
  | "moderated";

// How a participant's turn produces text. Their OWN opt-in only.
export type SpeakMode = "manual" | "twin" | "hosted";

export interface TeamInviteRecord {
  id: string;
  team_id: string;
  role: TeamInviteRole;
  created_by?: string | null;
  created_at: string;
  expires_at?: string | null;
  max_uses?: number | null;
  use_count: number;
  status: TeamInviteStatus;
  revoked_at?: string | null;
  revoked_by?: string | null;
  last_used_at?: string | null;
}

export interface TeamInvite extends Omit<TeamInviteRecord, "id"> {
  id?: string;
  invite_id: string;
  invite_token: string;
  invite_role: TeamInviteRole;
  invite_path: string;
  invite_hash_path: string;
  join_policy?: TeamJoinPolicy;
}

export interface TeamJoinRequest {
  id: string;
  invite_id: string;
  team_id: string;
  display_name: string;
  role: TeamInviteRole;
  status: TeamJoinRequestStatus;
  created_at: string;
  updated_at: string;
  expires_at?: string | null;
  decided_at?: string | null;
  decision_reason?: string | null;
  participant_id?: string | null;
  actor_id?: string | null;
  decided_by?: string | null;
}

export interface TeamJoinPolicyInfo {
  team_id: string;
  join_policy: TeamJoinPolicy;
  is_project_group: boolean;
  project_id?: string | null;
  overridden: boolean;
}

export interface CreateTeamInviteInput {
  role?: TeamInviteRole;
  expires_in_seconds?: number;
  max_uses?: number;
}

export interface TeamInvitePreview {
  invite: {
    id: string;
    role: TeamInviteRole;
    expires_at?: string | null;
    status: TeamInviteStatus;
    remaining_uses?: number | null;
  };
  team: {
    id: string;
    name: string;
    member_count: number;
    participant_count: number;
  };
  join_policy?: TeamJoinPolicy;
  thread_id?: string | null;
}

export interface JoinTeamInviteInput {
  display_name?: string;
  participant_id?: string;
}

export interface JoinedTeamInviteResult {
  outcome: "joined";
  join_policy: TeamJoinPolicy;
  team: Team;
  participant: TeamParticipant;
  invite?: TeamInvitePreview["invite"];
  thread_id?: string | null;
}

export interface PendingTeamInviteResult {
  ok: boolean;
  created?: boolean;
  outcome: TeamJoinRequestStatus | "pending_approval";
  join_policy: TeamJoinPolicy;
  join_request: TeamJoinRequest;
  team: Pick<Team, "id" | "name"> & {
    member_count?: number;
    participant_count?: number;
  };
  thread_id?: null;
}

export type JoinTeamInviteResult =
  | JoinedTeamInviteResult
  | PendingTeamInviteResult;

export type OwnTeamJoinRequestResult =
  | (JoinedTeamInviteResult & { join_request: TeamJoinRequest })
  | {
      outcome: TeamJoinRequestStatus;
      join_policy: TeamJoinPolicy;
      join_request: TeamJoinRequest;
      participant?: TeamParticipant | null;
      team: Team | Pick<Team, "id" | "name">;
      thread_id?: string | null;
    };

export interface UpdateTeamParticipantInput {
  display_name?: string;
  role?: TeamParticipantRole;
  status?: "active" | "offline" | "removed";
  muted?: boolean;
}

export interface UpdateDelegationInput {
  speak_mode: SpeakMode;
  twin_agent_id?: string | null;
  host_id?: string | null;
}

export interface UpdateTeamParticipantResult {
  team: Team;
  participant: TeamParticipant;
}

export interface RemoveTeamParticipantResult {
  ok: boolean;
  team: Team;
  participant_id: string;
}

export interface CreateTeamInput {
  id?: string;
  name: string;
  members: Agent[];
  leaderId: string | null;
  thread_id?: string | null;
}

const BASE = () => `${getBackendBaseURL()}/api`;
const PARTICIPANT_KEY = "echo:teamParticipantId";

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function fetchTeams(): Promise<Team[]> {
  const res = await fetch(`${BASE()}/teams`, { headers: authHeaders() });
  const data = await parseJson<{ teams?: Team[] } | Team[]>(res);
  return Array.isArray(data) ? data : (data.teams ?? []);
}

export async function createTeam(input: CreateTeamInput): Promise<Team> {
  const res = await fetch(`${BASE()}/teams`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(input),
  });
  return parseJson<Team>(res);
}

export async function updateTeam(
  teamId: string,
  input: CreateTeamInput,
): Promise<Team> {
  const res = await fetch(`${BASE()}/teams/${encodeURIComponent(teamId)}`, {
    method: "PUT",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(input),
  });
  return parseJson<Team>(res);
}

export async function deleteTeam(teamId: string): Promise<void> {
  const res = await fetch(`${BASE()}/teams/${encodeURIComponent(teamId)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  await parseJson(res);
}

export async function createTeamInvite(
  teamId: string,
  input: CreateTeamInviteInput = {},
): Promise<TeamInvite> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/invites`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(input),
    },
  );
  return parseJson<TeamInvite>(res);
}

export async function listTeamInvites(
  teamId: string,
): Promise<TeamInviteRecord[]> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/invites`,
    { headers: authHeaders() },
  );
  const data = await parseJson<
    { invites?: TeamInviteRecord[] } | TeamInviteRecord[]
  >(res);
  return Array.isArray(data) ? data : (data.invites ?? []);
}

export async function revokeTeamInvite(
  teamId: string,
  inviteId: string,
): Promise<TeamInviteRecord> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/invites/${encodeURIComponent(inviteId)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  const data = await parseJson<{ invite: TeamInviteRecord }>(res);
  return data.invite;
}

export async function getTeamJoinPolicy(
  teamId: string,
): Promise<TeamJoinPolicyInfo> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/join-policy`,
    { headers: authHeaders() },
  );
  return parseJson<TeamJoinPolicyInfo>(res);
}

export async function updateTeamJoinPolicy(
  teamId: string,
  joinPolicy: TeamJoinPolicy,
): Promise<TeamJoinPolicyInfo> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/join-policy`,
    {
      method: "PATCH",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ join_policy: joinPolicy }),
    },
  );
  return parseJson<TeamJoinPolicyInfo>(res);
}

export async function listTeamJoinRequests(
  teamId: string,
  status: TeamJoinRequestStatus | "all" = "pending",
): Promise<TeamJoinRequest[]> {
  const query = status === "all" ? "" : `?status=${encodeURIComponent(status)}`;
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/join-requests${query}`,
    { headers: authHeaders() },
  );
  const data = await parseJson<{
    join_requests?: TeamJoinRequest[];
  }>(res);
  return data.join_requests ?? [];
}

export async function approveTeamJoinRequest(
  teamId: string,
  requestId: string,
): Promise<
  JoinedTeamInviteResult & {
    changed?: boolean;
    join_request: TeamJoinRequest;
  }
> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/join-requests/${encodeURIComponent(requestId)}/approve`,
    { method: "POST", headers: jsonAuthHeaders(), body: "{}" },
  );
  return parseJson(res);
}

export async function rejectTeamJoinRequest(
  teamId: string,
  requestId: string,
  reason = "",
): Promise<{ ok: boolean; changed?: boolean; join_request: TeamJoinRequest }> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/join-requests/${encodeURIComponent(requestId)}/reject`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ reason }),
    },
  );
  return parseJson(res);
}

export async function inspectTeamInvite(
  token: string,
): Promise<TeamInvitePreview> {
  const res = await fetch(
    `${BASE()}/team-invites/${encodeURIComponent(token)}`,
    { headers: authHeaders() },
  );
  const data = await parseJson<TeamInvitePreview | { team: Team }>(res);
  if ("invite" in data) return data;

  // Transitional compatibility for a backend that still returns the full
  // room. Consumers only receive the minimum preview shape either way.
  const team = data.team;
  return {
    invite: {
      id: "legacy",
      role: team.invite_role === "viewer" ? "viewer" : "member",
      status: "active",
    },
    team: {
      id: team.id,
      name: team.name,
      member_count: team.members.length,
      participant_count: team.participants?.length ?? 0,
    },
  };
}

export async function joinTeamInvite(
  token: string,
  input: JoinTeamInviteInput,
): Promise<JoinTeamInviteResult> {
  const res = await fetch(
    `${BASE()}/team-invites/${encodeURIComponent(token)}/join`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(input),
    },
  );
  return parseJson<JoinTeamInviteResult>(res);
}

export async function getOwnTeamJoinRequest(
  token: string,
): Promise<OwnTeamJoinRequestResult | null> {
  const res = await fetch(
    `${BASE()}/team-invites/${encodeURIComponent(token)}/join-request`,
    { headers: authHeaders() },
  );
  if (res.status === 404) return null;
  return parseJson<OwnTeamJoinRequestResult>(res);
}

export async function withdrawOwnTeamJoinRequest(
  token: string,
): Promise<{ ok: boolean; outcome: string; join_request: TeamJoinRequest }> {
  const res = await fetch(
    `${BASE()}/team-invites/${encodeURIComponent(token)}/join-request`,
    { method: "DELETE", headers: authHeaders() },
  );
  return parseJson(res);
}

export async function updateTeamParticipant(
  teamId: string,
  participantId: string,
  input: UpdateTeamParticipantInput,
): Promise<UpdateTeamParticipantResult> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/participants/${encodeURIComponent(participantId)}`,
    {
      method: "PATCH",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(input),
    },
  );
  return parseJson<UpdateTeamParticipantResult>(res);
}

export async function updateSpeakerPolicy(
  teamId: string,
  speakerPolicy: SpeakerPolicy,
): Promise<{ team: Team; speaker_policy: SpeakerPolicy }> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/speaker-policy`,
    {
      method: "PATCH",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ speaker_policy: speakerPolicy }),
    },
  );
  return parseJson<{ team: Team; speaker_policy: SpeakerPolicy }>(res);
}

export async function updateDelegation(
  teamId: string,
  participantId: string,
  input: UpdateDelegationInput,
): Promise<UpdateTeamParticipantResult> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/participants/${encodeURIComponent(participantId)}/delegation`,
    {
      method: "PATCH",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(input),
    },
  );
  return parseJson<UpdateTeamParticipantResult>(res);
}

export async function removeTeamParticipant(
  teamId: string,
  participantId: string,
): Promise<RemoveTeamParticipantResult> {
  const res = await fetch(
    `${BASE()}/teams/${encodeURIComponent(teamId)}/participants/${encodeURIComponent(participantId)}`,
    {
      method: "DELETE",
      headers: authHeaders(),
    },
  );
  return parseJson<RemoveTeamParticipantResult>(res);
}

export async function migrateLegacyTeamsIfNeeded(
  existing: Team[],
): Promise<Team[]> {
  if (existing.length > 0 || typeof window === "undefined") return existing;
  const raw = window.localStorage.getItem("echo:teams");
  if (!raw) return existing;
  let legacy: Team[];
  try {
    const parsed = JSON.parse(raw);
    legacy = Array.isArray(parsed) ? (parsed as Team[]) : [];
  } catch (e) {
    swallow(e);
    return existing;
  }
  const migrated: Team[] = [];
  for (const team of legacy) {
    if (
      !team?.name ||
      !Array.isArray(team.members) ||
      team.members.length === 0
    ) {
      continue;
    }
    try {
      migrated.push(
        await createTeam({
          id: team.id,
          name: team.name,
          members: team.members,
          leaderId: team.leaderId ?? team.members[0]?.name ?? null,
        }),
      );
    } catch (e) {
      swallow(e);
    }
  }
  return migrated.length > 0 ? migrated : existing;
}

export function readPreferredTeamId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const direct = window.localStorage.getItem("echo:currentTeamId");
    if (direct) return direct;
    const raw = window.localStorage.getItem("echo:currentTeam");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { id?: string };
    return parsed.id ?? null;
  } catch (e) {
    swallow(e);
    return null;
  }
}

export function writePreferredTeam(team: Team | null): void {
  if (typeof window === "undefined") return;
  try {
    if (!team) {
      window.localStorage.removeItem("echo:currentTeamId");
      window.localStorage.removeItem("echo:currentTeam");
      return;
    }
    window.localStorage.setItem("echo:currentTeamId", team.id);
    window.localStorage.setItem("echo:currentTeam", JSON.stringify(team));
  } catch (e) {
    swallow(e, "storage");
  }
}

export function dispatchTeamUpdated(team?: Team | null): void {
  if (typeof window === "undefined") return;
  eventBus.emit(
    "team:updated",
    team ? { id: team.id, name: team.name } : { id: "", name: "" },
  );
  eventBus.emit("teams:changed");
}

export function readOrCreateTeamParticipantId(): string {
  if (typeof window === "undefined") return `guest-${Date.now()}`;
  try {
    const existing = window.localStorage.getItem(PARTICIPANT_KEY);
    if (existing) return existing;
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `guest-${Date.now()}`;
    window.localStorage.setItem(PARTICIPANT_KEY, id);
    return id;
  } catch (e) {
    swallow(e);
    return `guest-${Date.now()}`;
  }
}

export function readTeamParticipantIdForTeam(team?: Team | null): string {
  if (typeof window === "undefined") return `guest-${Date.now()}`;
  try {
    const existing = window.localStorage.getItem(PARTICIPANT_KEY);
    if (
      existing &&
      team?.participants?.some(
        (p) => p.id === existing && p.status !== "removed",
      )
    ) {
      return existing;
    }

    const localOwner = team?.participants?.find(
      (p) =>
        p.status !== "removed" &&
        p.role === "owner" &&
        (p.actor_id === "local" || p.id === "owner-local"),
    );
    if (localOwner) {
      return localOwner.id;
    }
  } catch (e) {
    swallow(e);
  }
  return readOrCreateTeamParticipantId();
}
