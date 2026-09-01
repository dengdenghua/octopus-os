import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  applyCollabRoomMessageProjectAction,
  ensureCollabRoom,
  getCollabSession,
  getCoworkGroup,
  getCoworkPresence,
  inviteCoworkMember,
  linkCoworkRoom,
  postCollabRoomMessage,
  removeCoworkMember,
  replaceCoworkRoster,
  searchCowork,
  setCoworkMode,
} from "./api";
import type {
  CollabRoomInput,
  CollabRoomMessageInput,
  CollaborationSession,
  CoworkInviteInput,
  CoworkMessageProjectActionInput,
  CoworkMessageProjectActionResponse,
  CoworkMode,
  CoworkRosterInput,
  CoworkState,
  CoworkSearchKind,
} from "./types";

const COWORK_KEY = ["cowork"] as const;

export const coworkQueryKeys = {
  all: COWORK_KEY,
  group: (threadId?: string | null) =>
    [...COWORK_KEY, "group", threadId ?? "none"] as const,
  presence: (threadId?: string | null) =>
    [...COWORK_KEY, "presence", threadId ?? "none"] as const,
  search: (
    threadId?: string | null,
    query?: string,
    kinds?: CoworkSearchKind[],
  ) =>
    [
      ...COWORK_KEY,
      "search",
      threadId ?? "none",
      query ?? "",
      (kinds ?? []).join(","),
    ] as const,
  session: (threadId?: string | null) =>
    [...COWORK_KEY, "session", threadId ?? "none"] as const,
};

export function useCoworkGroup(threadId?: string | null) {
  return useQuery({
    queryKey: coworkQueryKeys.group(threadId),
    queryFn: () => getCoworkGroup(threadId!),
    enabled: Boolean(threadId && threadId !== "new"),
    staleTime: 1500,
  });
}

export function useInviteCoworkMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      threadId,
      input,
    }: {
      threadId: string;
      input: CoworkInviteInput;
    }) => inviteCoworkMember(threadId, input),
    onSuccess: (_state, { threadId }) => {
      void qc.invalidateQueries({ queryKey: coworkQueryKeys.group(threadId) });
      void qc.invalidateQueries({
        queryKey: coworkQueryKeys.session(threadId),
      });
    },
  });
}

export function useRemoveCoworkMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      threadId,
      memberId,
    }: {
      threadId: string;
      memberId: string;
    }) => removeCoworkMember(threadId, memberId),
    onSuccess: (_state, { threadId }) => {
      void qc.invalidateQueries({ queryKey: coworkQueryKeys.group(threadId) });
      void qc.invalidateQueries({
        queryKey: coworkQueryKeys.session(threadId),
      });
    },
  });
}

export function useSetCoworkMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, mode }: { threadId: string; mode: CoworkMode }) =>
      setCoworkMode(threadId, mode),
    onSuccess: (_state, { threadId }) => {
      void qc.invalidateQueries({ queryKey: coworkQueryKeys.group(threadId) });
      void qc.invalidateQueries({
        queryKey: coworkQueryKeys.session(threadId),
      });
    },
  });
}

export function useReplaceCoworkRoster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      threadId,
      input,
    }: {
      threadId: string;
      input: CoworkRosterInput;
    }) => replaceCoworkRoster(threadId, input),
    onSuccess: (response, { threadId }) => {
      qc.setQueryData<{ state: CoworkState }>(
        coworkQueryKeys.group(threadId),
        (group) => (group ? { ...group, state: response.state } : group),
      );
      void qc.invalidateQueries({ queryKey: coworkQueryKeys.group(threadId) });
      void qc.invalidateQueries({
        queryKey: coworkQueryKeys.session(threadId),
      });
    },
  });
}

export function useCoworkPresence(
  threadId?: string | null,
  opts: { enabled?: boolean; refetchInterval?: number } = {},
) {
  const enabled =
    (opts.enabled ?? true) && Boolean(threadId && threadId !== "new");
  return useQuery({
    queryKey: coworkQueryKeys.presence(threadId),
    queryFn: () => getCoworkPresence(threadId!),
    enabled,
    refetchInterval: enabled ? (opts.refetchInterval ?? 15000) : false,
    staleTime: 5000,
  });
}

export function useCoworkSearch(
  threadId?: string | null,
  query?: string,
  opts: { kinds?: CoworkSearchKind[]; limit?: number } = {},
) {
  const trimmed = (query ?? "").trim();
  return useQuery({
    queryKey: coworkQueryKeys.search(threadId, trimmed, opts.kinds),
    queryFn: () =>
      searchCowork(threadId!, trimmed, {
        kinds: opts.kinds,
        limit: opts.limit,
      }),
    enabled: Boolean(threadId && threadId !== "new") && trimmed.length > 0,
    staleTime: 2000,
  });
}

export type CollabSessionQueryOptions = {
  /** Skip even the initial session lookup. */
  enabled?: boolean;
  /** Keep an explicitly visible collaboration surface live before a room exists. */
  live?: boolean;
  refetchInterval?: number;
};

export function collabSessionRefetchInterval(
  session: CollaborationSession | undefined,
  opts: CollabSessionQueryOptions = {},
): number | false {
  if (opts.enabled === false) return false;
  const isCollaborativeSession = Boolean(
    session?.room_id ||
    (session && (session.roster.length > 1 || session.mode !== "chat")),
  );
  return opts.live || isCollaborativeSession
    ? (opts.refetchInterval ?? 5_000)
    : false;
}

export function useCollabSession(
  threadId?: string | null,
  opts: CollabSessionQueryOptions = {},
) {
  const enabled =
    (opts.enabled ?? true) && Boolean(threadId && threadId !== "new");
  return useQuery({
    queryKey: coworkQueryKeys.session(threadId),
    queryFn: () => getCollabSession(threadId!),
    enabled,
    // Team Room messages can be written by another browser, a human member,
    // or a Project OS action. Keep the central group timeline moving even
    // when the agent stream itself is idle.
    // Direct chats need one lookup for persisted collaboration metadata, but
    // they should not keep waking the backend forever. A linked room, a
    // multi-member session, or an explicitly visible live surface keeps the
    // five-second refresh behavior.
    refetchInterval: (query) =>
      enabled ? collabSessionRefetchInterval(query.state.data, opts) : false,
    refetchIntervalInBackground: false,
    staleTime: 2000,
  });
}

export function useLinkCoworkRoom() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, roomId }: { threadId: string; roomId: string }) =>
      linkCoworkRoom(threadId, roomId),
    onSuccess: (_void, { threadId }) => {
      void qc.invalidateQueries({
        queryKey: coworkQueryKeys.session(threadId),
      });
      void qc.invalidateQueries({ queryKey: coworkQueryKeys.group(threadId) });
    },
  });
}

export function useEnsureCollabRoom() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      threadId,
      input,
    }: {
      threadId: string;
      input: CollabRoomInput;
    }) => ensureCollabRoom(threadId, input),
    onSuccess: (_data, { threadId }) => {
      void qc.invalidateQueries({
        queryKey: coworkQueryKeys.session(threadId),
      });
      void qc.invalidateQueries({ queryKey: coworkQueryKeys.group(threadId) });
    },
  });
}

export function usePostCollabRoomMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      threadId,
      input,
    }: {
      threadId: string;
      input: CollabRoomMessageInput;
    }) => postCollabRoomMessage(threadId, input),
    onSuccess: (_data, { threadId }) => {
      void qc.invalidateQueries({
        queryKey: coworkQueryKeys.session(threadId),
      });
      void qc.invalidateQueries({
        predicate: (query) => {
          const key = query.queryKey;
          return (
            Array.isArray(key) &&
            key[0] === COWORK_KEY[0] &&
            key[1] === "search" &&
            key[2] === threadId
          );
        },
      });
    },
  });
}

/** Merge the authoritative write response into the cached timeline immediately.
 * A background refetch still follows so Project OS and room projections converge. */
export function mergeCoworkProjectActionIntoSession(
  session: CollaborationSession | undefined,
  response: CoworkMessageProjectActionResponse,
): CollaborationSession | undefined {
  if (!session) return session;
  const replacements = [
    response.source_message,
    response.system_card_message ?? undefined,
  ].filter((message) => message != null);
  if (replacements.length === 0) return session;

  const bySeq = new Map(
    session.room_messages.map((message) => [message.seq, message]),
  );
  for (const message of replacements) bySeq.set(message.seq, message);
  return {
    ...session,
    room_messages: Array.from(bySeq.values()).sort((a, b) => a.seq - b.seq),
  };
}

export function useApplyCollabRoomMessageProjectAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      threadId,
      messageSeq,
      input,
    }: {
      threadId: string;
      messageSeq: number;
      input: CoworkMessageProjectActionInput;
    }) => applyCollabRoomMessageProjectAction(threadId, messageSeq, input),
    onSuccess: (response, { threadId }) => {
      qc.setQueryData<CollaborationSession>(
        coworkQueryKeys.session(threadId),
        (session) => mergeCoworkProjectActionIntoSession(session, response),
      );
      void qc.invalidateQueries({
        queryKey: coworkQueryKeys.session(threadId),
      });
      void qc.invalidateQueries({
        predicate: (query) => {
          const key = query.queryKey;
          return (
            Array.isArray(key) &&
            key[0] === COWORK_KEY[0] &&
            key[1] === "search" &&
            key[2] === threadId
          );
        },
      });
    },
  });
}
