import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { swallow } from "@/core/utils/log";
import { getToken } from "@/core/auth/api";
import { getBackendWebSocketBaseURL } from "@/core/config";
import { readOrCreateTeamParticipantId } from "@/core/teams";
import type { SpeakerPolicy } from "@/core/teams";
import { eventBus } from "@/core/events";
import { teamTaskQueryKeys } from "@/core/team-tasks/hooks";
import type { TeamTask } from "@/core/team-tasks/types";

export interface User {
  id: string;
  name: string;
  avatar?: string;
  color: string;
}

export interface AnnotationAuthor {
  display_name: string;
  avatar_color: string;
}

export interface AnnotationReply {
  reply_id: string;
  author: AnnotationAuthor | null;
  body: string;
  created_at: number;
}

export interface Annotation {
  annotation_id: string;
  message_id: string;
  author: AnnotationAuthor | null;
  body: string;
  created_at: number;
  resolved: boolean;
  replies: AnnotationReply[];
}

export interface RoomMessage {
  message_id: string;
  thread_id?: string;
  participant_id: string;
  display_name: string;
  text: string;
  created_at: string;
}

export interface TeamTaskProgressEvent {
  id: string;
  team_id: string;
  room_id: string;
  task_id: string;
  task: TeamTask | null;
  event: string;
  role?: string | null;
  role_status?: string | null;
  completed_roles?: number;
  total_roles?: number;
  progress?: number;
  server_time: string;
  error?: string | null;
  runner_event?: Record<string, unknown> | null;
}

export interface FloorState {
  speakerPolicy: SpeakerPolicy;
  currentSpeakerId: string | null;
  moderatorId: string | null;
  floorRequests: string[];
}

const DEFAULT_FLOOR: FloorState = {
  speakerPolicy: "free",
  currentSpeakerId: null,
  moderatorId: null,
  floorRequests: [],
};

interface CollabContextType {
  users: User[];
  currentUser: User | null;
  isConnected: boolean;
  roomMessages: RoomMessage[];
  taskEvents: TeamTaskProgressEvent[];
  floor: FloorState;
  join: (user: User) => void;
  leave: () => void;
  updateCursor: (position: { x: number; y: number }) => void;
  sendRoomMessage: (text: string, onBehalfOf?: string) => void;
  raiseHand: () => void;
  yieldFloor: () => void;
  grantFloor: (targetId: string | null) => void;
  notifyThreadUpdate: (reason?: string) => void;
  annotations: Annotation[];
  addAnnotation: (messageId: string, body: string) => Promise<void>;
  resolveAnnotation: (annotationId: string) => Promise<void>;
  unresolveAnnotation: (annotationId: string) => Promise<void>;
  deleteAnnotation: (annotationId: string) => Promise<void>;
  replyToAnnotation: (annotationId: string, body: string) => Promise<void>;
}

interface CollabProviderProps {
  children: ReactNode;
  teamId?: string | null;
  threadId?: string | null;
  participantId?: string | null;
  displayName?: string | null;
  avatar?: string | null;
  autoConnect?: boolean;
}

const CollabContext = createContext<CollabContextType | null>(null);

export function CollabProvider({
  children,
  teamId,
  threadId,
  participantId,
  displayName,
  avatar,
  autoConnect = true,
}: CollabProviderProps) {
  const resolvedParticipantId = useMemo(
    () => participantId || readOrCreateTeamParticipantId(),
    [participantId],
  );
  const resolvedDisplayName = displayName?.trim() || "You";
  const normalizedThreadId = useMemo(() => {
    const value = threadId?.trim();
    return value && value !== "new" ? value : "";
  }, [threadId]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const shouldReconnectRef = useRef(true);
  const queryClient = useQueryClient();
  const [users, setUsers] = useState<User[]>([]);
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [roomMessages, setRoomMessages] = useState<RoomMessage[]>([]);
  const [taskEvents, setTaskEvents] = useState<TeamTaskProgressEvent[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [floor, setFloor] = useState<FloorState>(DEFAULT_FLOOR);

  const localUser = useMemo<User>(
    () => ({
      id: resolvedParticipantId,
      name: resolvedDisplayName,
      avatar: avatar ?? undefined,
      color: colorFor(resolvedParticipantId),
    }),
    [avatar, resolvedDisplayName, resolvedParticipantId],
  );

  useEffect(() => {
    setTaskEvents([]);
  }, [teamId]);

  const notifyRemoved = useCallback(
    (_reason = "removed") => {
      if (!teamId || typeof window === "undefined") return;
      eventBus.emit("team:removed", { teamId });
    },
    [teamId],
  );

  const closeSocket = useCallback(() => {
    shouldReconnectRef.current = false;
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    wsRef.current?.close();
    wsRef.current = null;
    setIsConnected(false);
  }, []);

  useEffect(() => {
    if (!teamId || !autoConnect || typeof window === "undefined") {
      setCurrentUser(localUser);
      setUsers(localUser ? [localUser] : []);
      return;
    }

    shouldReconnectRef.current = true;
    let disposed = false;

    const connect = () => {
      if (disposed || !shouldReconnectRef.current) return;
      const wsBase = getBackendWebSocketBaseURL();
      const params = new URLSearchParams({
        participant_id: resolvedParticipantId,
        display_name: resolvedDisplayName,
      });
      if (normalizedThreadId) {
        params.set("thread_id", normalizedThreadId);
      }
      const token = getToken();
      if (token) {
        params.set("token", token);
      }
      const socket = new WebSocket(
        `${wsBase}/api/teams/${encodeURIComponent(teamId)}/ws?${params.toString()}`,
      );
      wsRef.current = socket;

      socket.onopen = () => {
        if (disposed) return;
        setIsConnected(true);
        setCurrentUser(localUser);
      };
      socket.onmessage = (event) => {
        if (disposed) return;
        const msg = parseMessage(event.data);
        if (!msg) return;
        if (msg.type === "error") {
          const message = String(msg.message ?? "");
          if (message.includes("participant removed")) {
            shouldReconnectRef.current = false;
            notifyRemoved("removed");
            socket.close(4403);
          } else if (
            msg.code === "speech_denied" ||
            msg.code === "delegation_denied" ||
            msg.code === "not_moderator"
          ) {
            // The room rejected this send (muted / not your turn / not the
            // moderator / not authorized to speak for someone). Surface it.
            toast.error(message || "You can't speak right now");
          }
        } else if (msg.type === "floor") {
          setFloor(floorFrom(msg));
        } else if (msg.type === "ready" && msg.participant) {
          setCurrentUser(participantToUser(msg.participant, avatar));
          if (isRecord(msg.team)) setFloor(floorFrom(msg.team));
        } else if (msg.type === "presence" && Array.isArray(msg.participants)) {
          setUsers(msg.participants.map((p) => participantToUser(p)));
        } else if (msg.type === "message" && typeof msg.text === "string") {
          const incomingThreadId =
            typeof msg.thread_id === "string" ? msg.thread_id : "";
          if (normalizedThreadId && incomingThreadId !== normalizedThreadId) {
            return;
          }
          const text = msg.text;
          setRoomMessages((prev) => [
            ...prev.slice(-49),
            {
              message_id: String(msg.message_id ?? `room-msg-${Date.now()}`),
              thread_id: incomingThreadId || undefined,
              participant_id: String(msg.participant_id ?? ""),
              display_name: String(msg.display_name ?? "Guest"),
              text,
              created_at: String(msg.created_at ?? new Date().toISOString()),
            },
          ]);
        } else if (msg.type === "thread:update") {
          const incomingThreadId =
            typeof msg.thread_id === "string" ? msg.thread_id : "";
          if (normalizedThreadId && incomingThreadId !== normalizedThreadId) {
            return;
          }
          const senderId = String(msg.participant_id ?? "");
          if (senderId === resolvedParticipantId) return;
          window.dispatchEvent(
            new CustomEvent("echo:team-thread-update", {
              detail: {
                teamId,
                threadId: incomingThreadId,
                participantId: senderId,
                reason: String(msg.reason ?? "updated"),
              },
            }),
          );
        } else if (msg.type === "team:update" && msg.team) {
          eventBus.emit("team:room-updated", { roomId: teamId ?? "" });
          if (isRecord(msg.team)) setFloor(floorFrom(msg.team));
          if (teamHasRemovedParticipant(msg.team, resolvedParticipantId)) {
            shouldReconnectRef.current = false;
            notifyRemoved("removed");
            socket.close(4403);
          }
        } else if (msg.type === "task:progress") {
          const taskEvent = parseTaskProgressEvent(msg, teamId);
          if (!taskEvent) return;
          setTaskEvents((prev) => appendTaskProgressEvent(prev, taskEvent));
          if (taskEvent.event === "task_deleted") {
            queryClient.setQueryData<TeamTask[]>(
              teamTaskQueryKeys.byRoom(taskEvent.room_id),
              (current) => removeTaskFromList(current, taskEvent.task_id),
            );
            void queryClient.invalidateQueries({
              queryKey: teamTaskQueryKeys.byRoom(taskEvent.room_id),
            });
          } else if (taskEvent.task) {
            queryClient.setQueryData<TeamTask[]>(
              teamTaskQueryKeys.byRoom(taskEvent.room_id),
              (current) => mergeTaskIntoList(current, taskEvent.task!),
            );
            void queryClient.invalidateQueries({
              queryKey: teamTaskQueryKeys.byRoom(taskEvent.room_id),
            });
          }
        }
      };
      socket.onclose = (event) => {
        if (disposed) return;
        setIsConnected(false);
        if (event.code === 4403) {
          shouldReconnectRef.current = false;
          notifyRemoved("removed");
          return;
        }
        if (shouldReconnectRef.current) {
          reconnectTimerRef.current = window.setTimeout(connect, 1500);
        }
      };
      socket.onerror = () => {
        setIsConnected(false);
      };
    };

    connect();

    return () => {
      disposed = true;
      closeSocket();
    };
  }, [
    autoConnect,
    avatar,
    closeSocket,
    localUser,
    normalizedThreadId,
    notifyRemoved,
    queryClient,
    resolvedDisplayName,
    resolvedParticipantId,
    teamId,
  ]);

  useEffect(() => {
    if (!isConnected) return;
    const timer = window.setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "presence:ping" }));
      }
    }, 15000);
    return () => window.clearInterval(timer);
  }, [isConnected]);

  const join = useCallback((user: User) => {
    setCurrentUser(user);
    setUsers((prev) => [user, ...prev.filter((u) => u.id !== user.id)]);
    setIsConnected(true);
  }, []);

  const leave = useCallback(() => {
    closeSocket();
    setUsers((prev) => prev.filter((u) => u.id !== currentUser?.id));
    setCurrentUser(null);
  }, [closeSocket, currentUser?.id]);

  const sendJson = useCallback((payload: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  }, []);

  const updateCursor = useCallback(
    (position: { x: number; y: number }) => {
      sendJson({
        type: "cursor",
        position,
        ...(normalizedThreadId ? { thread_id: normalizedThreadId } : {}),
      });
    },
    [normalizedThreadId, sendJson],
  );

  const sendRoomMessage = useCallback(
    (text: string, onBehalfOf?: string) => {
      const clean = text.trim();
      if (!clean) return;
      sendJson({
        type: "message",
        text: clean,
        ...(onBehalfOf ? { on_behalf_of: onBehalfOf } : {}),
        ...(normalizedThreadId ? { thread_id: normalizedThreadId } : {}),
      });
    },
    [normalizedThreadId, sendJson],
  );

  const raiseHand = useCallback(() => {
    sendJson({ type: "floor:request" });
  }, [sendJson]);

  const yieldFloor = useCallback(() => {
    sendJson({ type: "floor:yield" });
  }, [sendJson]);

  const grantFloor = useCallback(
    (targetId: string | null) => {
      sendJson({ type: "floor:grant", target: targetId ?? "" });
    },
    [sendJson],
  );

  const notifyThreadUpdate = useCallback(
    (reason = "updated") => {
      if (!normalizedThreadId) return;
      sendJson({
        type: "thread:update",
        thread_id: normalizedThreadId,
        reason,
      });
    },
    [normalizedThreadId, sendJson],
  );

  const author = useMemo<AnnotationAuthor | null>(
    () =>
      currentUser
        ? { display_name: currentUser.name, avatar_color: currentUser.color }
        : null,
    [currentUser],
  );

  const addAnnotation = useCallback(
    async (messageId: string, body: string) => {
      setAnnotations((prev) => [
        ...prev,
        {
          annotation_id: `ann-${Date.now()}-${Math.random().toString(16).slice(2)}`,
          message_id: messageId,
          author,
          body,
          created_at: Math.floor(Date.now() / 1000),
          resolved: false,
          replies: [],
        },
      ]);
    },
    [author],
  );

  const resolveAnnotation = useCallback(async (annotationId: string) => {
    setAnnotations((prev) =>
      prev.map((item) =>
        item.annotation_id === annotationId
          ? { ...item, resolved: true }
          : item,
      ),
    );
  }, []);

  const unresolveAnnotation = useCallback(async (annotationId: string) => {
    setAnnotations((prev) =>
      prev.map((item) =>
        item.annotation_id === annotationId
          ? { ...item, resolved: false }
          : item,
      ),
    );
  }, []);

  const deleteAnnotation = useCallback(async (annotationId: string) => {
    setAnnotations((prev) =>
      prev.filter((item) => item.annotation_id !== annotationId),
    );
  }, []);

  const replyToAnnotation = useCallback(
    async (annotationId: string, body: string) => {
      setAnnotations((prev) =>
        prev.map((item) =>
          item.annotation_id === annotationId
            ? {
                ...item,
                replies: [
                  ...item.replies,
                  {
                    reply_id: `reply-${Date.now()}-${Math.random().toString(16).slice(2)}`,
                    author,
                    body,
                    created_at: Math.floor(Date.now() / 1000),
                  },
                ],
              }
            : item,
        ),
      );
    },
    [author],
  );

  return (
    <CollabContext.Provider
      value={{
        users,
        currentUser,
        isConnected,
        roomMessages,
        taskEvents,
        floor,
        raiseHand,
        yieldFloor,
        grantFloor,
        join,
        leave,
        updateCursor,
        sendRoomMessage,
        notifyThreadUpdate,
        annotations,
        addAnnotation,
        resolveAnnotation,
        unresolveAnnotation,
        deleteAnnotation,
        replyToAnnotation,
      }}
    >
      {children}
    </CollabContext.Provider>
  );
}

export function useCollab() {
  const context = useContext(CollabContext);
  if (!context) {
    throw new Error("useCollab must be used within CollabProvider");
  }
  return context;
}

export function useOptionalCollab() {
  return useContext(CollabContext);
}

function parseMessage(data: unknown): Record<string, unknown> | null {
  if (typeof data !== "string") return null;
  try {
    const parsed = JSON.parse(data);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : null;
  } catch (e) {
    swallow(e);
    return null;
  }
}

function participantToUser(participant: unknown, avatar?: string | null): User {
  const item =
    participant && typeof participant === "object"
      ? (participant as Record<string, unknown>)
      : {};
  const id = String(item.id ?? `guest-${Date.now()}`);
  const name = String(item.display_name ?? "Guest");
  return {
    id,
    name,
    avatar: avatar ?? undefined,
    color: colorFor(id),
  };
}

function teamHasRemovedParticipant(
  team: unknown,
  participantId: string,
): boolean {
  const item =
    team && typeof team === "object" ? (team as Record<string, unknown>) : {};
  const participants = Array.isArray(item.participants)
    ? item.participants
    : [];
  return participants.some((participant) => {
    if (!participant || typeof participant !== "object") return false;
    const p = participant as Record<string, unknown>;
    return p.id === participantId && p.status === "removed";
  });
}

function parseTaskProgressEvent(
  msg: Record<string, unknown>,
  expectedTeamId?: string | null,
): TeamTaskProgressEvent | null {
  const task = isRecord(msg.task) ? (msg.task as unknown as TeamTask) : null;
  const taskId = stringOr(msg.task_id) || task?.id || "";
  const roomId =
    stringOr(msg.room_id) || stringOr(msg.team_id) || task?.room_id || "";
  if (!taskId || !roomId) return null;
  // Require an explicit expectedTeamId and force a strict match. A
  // permissive fallback (when expectedTeamId is falsy) used to let
  // global subscribers swallow task:progress for arbitrary rooms,
  // which corrupted React Query cache and could clear UI for tasks
  // the user owns. WS messages MUST be scoped to the current team.
  if (!expectedTeamId || roomId !== expectedTeamId) return null;
  const event = stringOr(msg.event) || "progress";
  const serverTime = stringOr(msg.server_time) || new Date().toISOString();
  return {
    id: [
      taskId,
      event,
      serverTime,
      stringOr(msg.role),
      stringOr(msg.completed_roles),
    ].join(":"),
    team_id: stringOr(msg.team_id) || roomId,
    room_id: roomId,
    task_id: taskId,
    task,
    event,
    role: stringOr(msg.role) || null,
    role_status: stringOr(msg.role_status) || null,
    completed_roles: numberOr(msg.completed_roles),
    total_roles: numberOr(msg.total_roles),
    progress: numberOr(msg.progress),
    server_time: serverTime,
    error: stringOr(msg.error) || null,
    runner_event: isRecord(msg.runner_event) ? msg.runner_event : null,
  };
}

function appendTaskProgressEvent(
  prev: TeamTaskProgressEvent[],
  event: TeamTaskProgressEvent,
) {
  const filtered = prev.filter((item) => item.id !== event.id);
  return [...filtered, event].slice(-80);
}

function mergeTaskIntoList(
  current: TeamTask[] | undefined,
  task: TeamTask,
): TeamTask[] {
  const existing = current ?? [];
  const next = existing.some((item) => item.id === task.id)
    ? existing.map((item) => (item.id === task.id ? task : item))
    : [task, ...existing];
  return [...next].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

function removeTaskFromList(
  current: TeamTask[] | undefined,
  taskId: string,
): TeamTask[] {
  return (current ?? []).filter((item) => item.id !== taskId);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

const FLOOR_POLICIES = new Set<SpeakerPolicy>([
  "free",
  "admin_only",
  "round_robin",
  "roll_call",
  "moderated",
]);

// Extract floor state from either a dedicated ``floor`` event or a team
// object (which carries the same fields). Tolerant of missing keys.
function floorFrom(source: unknown): FloorState {
  const item = isRecord(source) ? source : {};
  const policy = String(item.speaker_policy ?? "free") as SpeakerPolicy;
  return {
    speakerPolicy: FLOOR_POLICIES.has(policy) ? policy : "free",
    currentSpeakerId:
      typeof item.current_speaker_id === "string"
        ? item.current_speaker_id
        : null,
    moderatorId:
      typeof item.moderator_id === "string" ? item.moderator_id : null,
    floorRequests: Array.isArray(item.floor_requests)
      ? item.floor_requests.filter((x): x is string => typeof x === "string")
      : [],
  };
}

function stringOr(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberOr(value: unknown): number | undefined {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function colorFor(id: string): string {
  const palette = [
    "#2563eb",
    "#059669",
    "#dc2626",
    "#7c3aed",
    "#ca8a04",
    "#0891b2",
    "#be185d",
    "#4f46e5",
  ];
  let hash = 0;
  for (const char of id) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return palette[hash % palette.length] ?? palette[0]!;
}
