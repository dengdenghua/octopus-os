import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

import type {
  CollabRoomMessageInput,
  CollabRoomMessageResponse,
  CollabRoomInput,
  CollabRoomResponse,
  CollaborationSession,
  CoworkGroupResponse,
  CoworkInviteInput,
  CoworkMessageProjectActionInput,
  CoworkMessageProjectActionResponse,
  CoworkMode,
  CoworkPresenceResponse,
  CoworkRosterInput,
  CoworkRosterResponse,
  CoworkSearchKind,
  CoworkSearchResponse,
} from "./types";

const BASE = () => `${getBackendBaseURL()}/api/cowork`;
const COLLAB_BASE = () => `${getBackendBaseURL()}/api/collab`;

async function parseJson<T>(res: Response, action: string): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `${action} failed: ${res.status}${detail ? ` ${detail}` : ` ${res.statusText}`}`,
    );
  }
  return (await res.json()) as T;
}

export async function getCoworkGroup(
  threadId: string,
): Promise<CoworkGroupResponse> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(threadId)}`, {
    headers: authHeaders(),
  });
  return parseJson<CoworkGroupResponse>(res, "Load cowork group");
}

export async function inviteCoworkMember(
  threadId: string,
  input: CoworkInviteInput,
): Promise<CoworkGroupResponse["state"]> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(threadId)}/members`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      kind: "agent",
      role: "participant",
      grant: { scope: "all", ...(input.grant ?? {}) },
      ...input,
    }),
  });
  const data = await parseJson<{
    ok: boolean;
    state: CoworkGroupResponse["state"];
  }>(res, "Invite cowork member");
  return data.state;
}

export async function removeCoworkMember(
  threadId: string,
  memberId: string,
): Promise<CoworkGroupResponse["state"]> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(threadId)}/members/${encodeURIComponent(
      memberId,
    )}`,
    {
      method: "DELETE",
      headers: authHeaders(),
    },
  );
  const data = await parseJson<{
    ok: boolean;
    state: CoworkGroupResponse["state"];
  }>(res, "Remove cowork member");
  return data.state;
}

export async function setCoworkMode(
  threadId: string,
  mode: CoworkMode,
): Promise<CoworkGroupResponse["state"]> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(threadId)}/mode`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ mode }),
  });
  const data = await parseJson<{
    ok: boolean;
    state: CoworkGroupResponse["state"];
  }>(res, "Set cowork mode");
  return data.state;
}

export async function replaceCoworkRoster(
  threadId: string,
  input: CoworkRosterInput,
): Promise<CoworkRosterResponse> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(threadId)}/roster`, {
    method: "PUT",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(input),
    // The payload is tiny and this is the user's explicit roster save. Let the
    // browser finish it when a refresh immediately follows the click.
    keepalive: true,
  });
  return parseJson<CoworkRosterResponse>(res, "Replace cowork roster");
}

export async function searchCowork(
  threadId: string,
  query: string,
  opts: { kinds?: CoworkSearchKind[]; limit?: number; untilSeq?: number } = {},
): Promise<CoworkSearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (opts.kinds?.length) params.set("kinds", opts.kinds.join(","));
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.untilSeq != null) params.set("until_seq", String(opts.untilSeq));
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(threadId)}/search?${params.toString()}`,
    { headers: authHeaders() },
  );
  return parseJson<CoworkSearchResponse>(res, "Search cowork group");
}

export async function getCoworkPresence(
  threadId: string,
): Promise<CoworkPresenceResponse> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(threadId)}/presence`,
    { headers: authHeaders() },
  );
  return parseJson<CoworkPresenceResponse>(res, "Load cowork presence");
}

export async function markCoworkRead(
  threadId: string,
  memberId: string,
  seq?: number,
): Promise<void> {
  const res = await fetch(`${BASE()}/${encodeURIComponent(threadId)}/read`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({
      member_id: memberId,
      ...(seq != null ? { seq } : {}),
    }),
  });
  await parseJson<{ ok: boolean }>(res, "Mark cowork read");
}

export async function coworkHeartbeat(
  threadId: string,
  memberId: string,
): Promise<void> {
  const res = await fetch(
    `${BASE()}/${encodeURIComponent(threadId)}/heartbeat`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ member_id: memberId }),
    },
  );
  await parseJson<{ ok: boolean }>(res, "Cowork heartbeat");
}

export async function getCollabSession(
  threadId: string,
): Promise<CollaborationSession> {
  const res = await fetch(`${COLLAB_BASE()}/${encodeURIComponent(threadId)}`, {
    headers: authHeaders(),
  });
  return parseJson<CollaborationSession>(res, "Load collaboration session");
}

export async function linkCoworkRoom(
  threadId: string,
  roomId: string,
): Promise<void> {
  const res = await fetch(
    `${COLLAB_BASE()}/${encodeURIComponent(threadId)}/link-room`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ room_id: roomId }),
    },
  );
  await parseJson<{ ok: boolean }>(res, "Link cowork room");
}

export async function ensureCollabRoom(
  threadId: string,
  input: CollabRoomInput,
): Promise<CollabRoomResponse> {
  const res = await fetch(
    `${COLLAB_BASE()}/${encodeURIComponent(threadId)}/room`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        name: input.name ?? "",
        members: input.members ?? [],
        leaderId: input.leaderId ?? null,
        mode: input.mode ?? null,
        ...(input.id ? { id: input.id } : {}),
      }),
    },
  );
  return parseJson<CollabRoomResponse>(res, "Ensure collab room");
}

export async function postCollabRoomMessage(
  threadId: string,
  input: CollabRoomMessageInput,
): Promise<CollabRoomMessageResponse> {
  const res = await fetch(
    `${COLLAB_BASE()}/${encodeURIComponent(threadId)}/room-message`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        text: input.text,
        participant_id: input.participant_id ?? "",
        display_name: input.display_name ?? "",
        ...(input.source_message_id
          ? { source_message_id: input.source_message_id }
          : {}),
        ...(input.message_type ? { message_type: input.message_type } : {}),
        ...(input.entity_refs?.length
          ? { entity_refs: input.entity_refs }
          : {}),
        ...(input.system_card ? { system_card: input.system_card } : {}),
        ...(input.metadata && Object.keys(input.metadata).length > 0
          ? { metadata: input.metadata }
          : {}),
      }),
    },
  );
  return parseJson<CollabRoomMessageResponse>(res, "Post collab room message");
}

/** Promote one timeline message into the bound Project OS project. */
export async function applyCollabRoomMessageProjectAction(
  threadId: string,
  messageSeq: number,
  input: CoworkMessageProjectActionInput,
): Promise<CoworkMessageProjectActionResponse> {
  const res = await fetch(
    `${COLLAB_BASE()}/${encodeURIComponent(threadId)}/room-messages/${encodeURIComponent(
      String(messageSeq),
    )}/project-actions`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(input),
    },
  );
  return parseJson<CoworkMessageProjectActionResponse>(
    res,
    "Apply room message project action",
  );
}
