export type CoworkMemberKind = "agent" | "human";
export type CoworkMemberRole = "participant" | "observer";
export type CoworkMode = "chat" | "cluster" | "swarm" | "project";
export type CoworkGrantScope = "all" | "from_join" | "range" | "summary";

export interface CoworkContextGrant {
  scope: CoworkGrantScope;
  from_msg?: number | null;
  to_msg?: number | null;
}

export interface CoworkMember {
  id: string;
  kind: CoworkMemberKind;
  role: CoworkMemberRole;
  joined_at_message?: number | null;
  grant: CoworkContextGrant;
  muted?: boolean;
  invited_by?: string;
}

export interface CoworkState {
  roster: CoworkMember[];
  mode: CoworkMode;
  event_count: number;
  is_one_to_one: boolean;
  room_id?: string | null;
}

export interface CoworkEvent {
  action: "invite" | "leave" | "mute" | "unmute" | "mode" | "room_link";
  actor: string;
  target_id: string;
  target_kind: CoworkMemberKind;
  role: CoworkMemberRole;
  grant: CoworkContextGrant;
  mode?: CoworkMode | null;
  at_message?: number | null;
  ts: string;
  seq: number;
}

export interface CoworkGroupResponse {
  thread_id: string;
  state: CoworkState;
  blackboard: Record<string, unknown>;
  events: CoworkEvent[];
  responders: string[];
}

export interface CoworkInviteInput {
  target_id: string;
  kind?: CoworkMemberKind;
  role?: CoworkMemberRole;
  grant?: Partial<CoworkContextGrant>;
  at_message?: number | null;
}

export interface CoworkModeInput {
  mode: CoworkMode;
}

export interface CoworkRosterInput {
  agent_ids: string[];
  mode: CoworkMode;
}

export interface CoworkRosterResponse {
  ok: boolean;
  state: CoworkState;
  events: CoworkEvent[];
}

export interface CollabRoomMessageInput {
  text: string;
  participant_id?: string;
  display_name?: string;
  /** Stable producer-side id. Retries with the same id are idempotent. */
  source_message_id?: string;
  message_type?: CoworkRoomMessageType;
  entity_refs?: CoworkRoomEntityRef[];
  system_card?: CoworkRoomSystemCard | null;
  metadata?: Record<string, unknown>;
}

export interface CollabRoomMessageResponse {
  ok: boolean;
  room_id: string;
  seq: number;
  message?: CoworkRoomMessage | null;
}

export type CoworkRoomMessageType = "message" | "system_card";

/** A typed pointer from the room timeline into the canonical Project OS. */
export interface CoworkRoomEntityRef {
  kind: string;
  id: string;
  project_id?: string;
  milestone_id?: string;
  task_id?: string;
  label?: string;
  [key: string]: unknown;
}

export type CoworkMessageProjectAction =
  | "link_milestone"
  | "create_item"
  | "record_decision"
  | "publish_artifact";

export interface CoworkRoomProjectActionReceipt {
  id: string;
  action: CoworkMessageProjectAction;
  project_id: string;
  target: CoworkRoomEntityRef;
  event_id?: string;
  applied_at?: string;
  [key: string]: unknown;
}

/** Presentation payload written by Project OS after a message action. */
export interface CoworkRoomSystemCard {
  schema?: string;
  type: CoworkMessageProjectAction | string;
  title: string;
  summary?: string;
  status?: string;
  project_id?: string;
  target?: CoworkRoomEntityRef;
  source_message_seq?: number;
  [key: string]: unknown;
}

export interface CoworkRoomMessageMetadata {
  schema?: string;
  source_message_id?: string;
  message_type?: CoworkRoomMessageType;
  entity_refs?: CoworkRoomEntityRef[];
  system_card?: CoworkRoomSystemCard | null;
  project_actions?: CoworkRoomProjectActionReceipt[];
  [key: string]: unknown;
}

/** Canonical message returned by GET /api/collab/{thread_id}. */
export interface CoworkRoomMessage {
  session_id?: string;
  seq: number;
  room_id?: string;
  participant_id?: string;
  display_name?: string;
  text: string;
  ts?: string;
  metadata?: CoworkRoomMessageMetadata;
}

export type CoworkProjectTaskType =
  | "design"
  | "code"
  | "research"
  | "analysis"
  | "review";
export type CoworkProjectPriority = "P0" | "P1" | "P2" | "P3";

export interface CoworkMessageProjectActionInput {
  action: CoworkMessageProjectAction;
  action_id?: string;
  project_id?: string;
  milestone_id?: string;
  item_id?: string;
  title?: string;
  description?: string;
  task_type?: CoworkProjectTaskType;
  priority?: CoworkProjectPriority;
  estimate?: number;
  due_at?: string;
  acceptance_criteria?: string[];
  assigned_role?: string;
  assigned_agent?: string;
  depends_on?: string[];
  decision?: string;
  rationale?: string;
  artifact?: Record<string, unknown>;
}

export interface CoworkMessageProjectActionResponse {
  ok: boolean;
  replayed: boolean;
  created: boolean;
  action_id: string;
  action: CoworkMessageProjectAction;
  project_id: string;
  milestone_id?: string;
  target: CoworkRoomEntityRef;
  receipt: CoworkRoomProjectActionReceipt;
  event?: Record<string, unknown>;
  task?: Record<string, unknown>;
  source_message: CoworkRoomMessage;
  system_card_message?: CoworkRoomMessage | null;
}

/** Room-member projection used by the timeline and @ autocomplete adapter. */
export interface CoworkRoomParticipant {
  id?: string;
  participant_id?: string;
  name?: string;
  display_name?: string;
  kind?: CoworkMemberKind;
  avatar_url?: string | null;
  icon?: string | null;
  description?: string | null;
  [key: string]: unknown;
}

export interface CollabRoomInput {
  id?: string | null;
  name?: string;
  members?: Array<Record<string, unknown>>;
  leaderId?: string | null;
  mode?: CoworkMode | null;
}

export interface CollabRoomResponse {
  ok: boolean;
  created: boolean;
  room: Record<string, unknown>;
  session: CollaborationSession;
}

export type CoworkSearchKind =
  | "blackboard"
  | "task"
  | "event"
  | "room_message"
  | "room_task";

export interface CoworkSearchHit {
  kind: CoworkSearchKind;
  title: string;
  snippet: string;
  score: number;
  actor: string;
  ts: string | null;
  ref: Record<string, unknown>;
}

export interface CoworkSearchResponse {
  thread_id: string;
  query: string;
  hits: CoworkSearchHit[];
}

export interface CoworkMemberPresence {
  member_id: string;
  last_read: number;
  last_seen_at: string | null;
  online: boolean;
  unread: number;
}

export interface CoworkPresenceResponse {
  thread_id: string;
  members: CoworkMemberPresence[];
}

/** Unified collaboration session — one view over the cowork thread (canonical)
 * plus a linked Team Room's transcript + participants. Mirrors the backend
 * CollaborationSession (GET /api/collab/{thread_id}). */
export interface CollaborationSession {
  session_id: string;
  room_id: string | null;
  mode: CoworkMode;
  roster: CoworkMember[];
  blackboard: Record<string, unknown>;
  tasks: Record<string, unknown>[];
  presence: CoworkMemberPresence[];
  room_messages: CoworkRoomMessage[];
  room_participants: CoworkRoomParticipant[];
  room_tasks: Record<string, unknown>[];
}
