export type TeamTaskStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "cancelled";

export type TaskAssigneeKind = "agent" | "participant";

export interface TaskAssignee {
  kind: TaskAssigneeKind;
  ref: string;
}

export interface TeamTaskArtifact {
  [key: string]: unknown;
}

export interface TeamTaskMetadata {
  [key: string]: unknown;
}

export interface TeamTask {
  id: string;
  room_id: string;
  title: string;
  description: string;
  sop_template: string;
  status: TeamTaskStatus;
  assignees: TaskAssignee[];
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  produced_artifacts: TeamTaskArtifact[];
  metadata: TeamTaskMetadata;
}

export interface TeamTaskProcessTimelineNode {
  id: string;
  lane: "workflow" | "agent" | "artifact" | "timeline" | string;
  kind: string;
  ts: string;
  title: string;
  status?: string;
  severity?: "info" | "medium" | "high" | string;
  summary?: string;
  data?: Record<string, unknown>;
  produced_artifacts?: TeamTaskArtifact[];
}

export interface TeamTaskProcessTimeline {
  schema: "echo.team_task_process_timeline.v1" | string;
  task_id: string;
  room_id: string;
  overview: {
    title: string;
    description?: string;
    status: TeamTaskStatus | string;
    created_by?: string | null;
    created_at?: string | null;
    started_at?: string | null;
    completed_at?: string | null;
    updated_at?: string | null;
    runner?: Record<string, unknown>;
    event_count: number;
    artifact_count: number;
    assignee_count: number;
  };
  assignees: TaskAssignee[];
  artifacts: TeamTaskArtifact[];
  timeline: TeamTaskProcessTimelineNode[];
  safety: {
    raw_messages_included: boolean;
    artifact_content_truncated: boolean;
    process_events_persisted: boolean;
    process_event_limit: number;
  };
}

export interface ListTeamTasksResponse {
  tasks: TeamTask[];
  count: number;
}

export interface TeamTaskProcessTimelineResponse {
  timeline: TeamTaskProcessTimeline;
}

export interface CreateTeamTaskInput {
  room_id: string;
  title: string;
  description?: string;
  sop_template?: string;
  assignees?: TaskAssignee[];
  metadata?: TeamTaskMetadata;
}

export interface UpdateTeamTaskInput {
  title?: string;
  description?: string;
  status?: TeamTaskStatus;
  assignees?: TaskAssignee[];
  sop_template?: string;
}

export interface DeleteTeamTaskResponse {
  ok: boolean;
  deleted: boolean;
  task_id: string;
}
