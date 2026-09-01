/**
 * Thread-derivation logic backing the workspace sidebar: how raw
 * ``AgentThread`` records become sidebar summaries (display titles,
 * project grouping, agent rosters, run-status lights).
 *
 * Extracted from ``components/workspace/workspace-sidebar.tsx`` so the
 * derivation rules live in the threads domain layer instead of a React
 * component. Everything here is pure (no React, no DOM) and unit-testable
 * in isolation.
 */
import { emitAgentChanged } from "@/core/events";
import type { TasksListResponse } from "@/core/tasks/api";
import type { TeamTask } from "@/core/team-tasks";
import { basename } from "@/lib/path-utils";

import type { AgentThread } from "./types";

export type ThreadSummary = {
  id: string;
  title: string;
  updatedAt: string;
  mode: string;
  href: string;
  workspacePath?: string;
  /** Agent ids associated with this thread · drives the WeChat-style
   *  avatar (single big avatar OR 2×2 / 3×3 grid for team threads). */
  agents: string[];
};

/** Subset of ``AgentRunState`` (components/workspace/agent-run-status.ts)
 *  that a sidebar thread row can display. Declared as a literal union so
 *  this core module does not depend on the components layer. */
export type ThreadRunStatus = "running" | "waiting" | "pending" | "error";

export function syncThreadAgentSelection(agents: string[]) {
  if (agents.length !== 1) return;
  const agent = agents[0]?.trim();
  if (!agent) return;
  emitAgentChanged(agent, "thread");
}

export function isProjectThreadMode(mode: string): boolean {
  return mode === "code" || mode === "team";
}

export function isConversationThreadMode(mode: string): boolean {
  return (
    mode === "chat" ||
    mode === "chats" ||
    mode === "react" ||
    mode === "deep" ||
    // Legacy persisted swarm threads stay visible in the conversation list,
    // but the composer no longer exposes swarm as a selectable mode.
    mode === "swarm" ||
    mode === "agent"
  );
}

export function isGeneratedTeamProjectName(project: string): boolean {
  const value = project.trim();
  return (
    value === "Team" ||
    value.startsWith("Team · ") ||
    value === "团队" ||
    value.startsWith("团队 · ")
  );
}

export function isBareGeneratedTeamLabel(value: string): boolean {
  const text = value.trim();
  return text === "Team" || text === "团队";
}

export function threadMetadataMode(thread: {
  metadata?: Record<string, unknown>;
  values?: Record<string, unknown>;
}): string {
  const mode = thread.metadata?.["mode"] ?? thread.values?.["mode"];
  return typeof mode === "string" ? mode : "";
}

export function isGeneratedTeamThreadTitle(
  thread: {
    metadata?: Record<string, unknown>;
    values?: Record<string, unknown>;
  },
  title: string,
): boolean {
  if (!isGeneratedTeamProjectName(title)) return false;
  return (
    isBareGeneratedTeamLabel(title) ||
    threadMetadataMode(thread) === "team" ||
    isGeneratedTeamProjectName(cleanDisplayText(thread.metadata?.["project"]))
  );
}

export function withThreadSidebarMode(
  thread: AgentThread,
  mode: "code" | "team",
): AgentThread {
  if (thread.metadata?.["mode"] === mode) return thread;
  return {
    ...thread,
    metadata: {
      ...(thread.metadata ?? {}),
      mode,
    },
  };
}

export function buildThreadRunStatusByHref({
  activeTeamTasks,
  backgroundTasks,
  liveThreadRunStatusByHref,
  threadHrefById,
}: {
  activeTeamTasks: TeamTask[];
  backgroundTasks?: TasksListResponse;
  liveThreadRunStatusByHref?: Map<string, ThreadRunStatus>;
  threadHrefById: Map<string, string>;
}): Map<string, ThreadRunStatus> {
  const byHref = new Map<string, ThreadRunStatus>();
  const activeStatuses = new Set(["running", "failed", "pending"]);
  for (const task of activeTeamTasks) {
    if (!activeStatuses.has(task.status)) continue;
    const status = teamTaskRunStatus(task.status);
    if (!status) continue;
    const href = `/workspace/realtime/${encodeURIComponent(task.room_id)}`;
    byHref.set(href, mergeThreadRunStatus(byHref.get(href), status));
  }

  for (const task of backgroundTasks?.active ?? []) {
    mergeTaskStatusForThread(byHref, threadHrefById, task.thread_id, "running");
  }
  for (const task of backgroundTasks?.pending ?? []) {
    mergeTaskStatusForThread(byHref, threadHrefById, task.thread_id, "waiting");
  }
  for (const task of backgroundTasks?.paused ?? []) {
    mergeTaskStatusForThread(byHref, threadHrefById, task.thread_id, "waiting");
  }

  for (const [href, status] of liveThreadRunStatusByHref ?? []) {
    // Live state is scoped to the current objective and is newer than the
    // heterogeneous historical task projections above.  A stale failed team
    // task must not keep a newly running/resumed thread red forever.
    byHref.set(href, status);
  }

  return byHref;
}

export function teamTaskRunStatus(
  status: TeamTask["status"],
): ThreadRunStatus | null {
  if (status === "running") return "running";
  if (status === "pending") return "pending";
  if (status === "failed") return "error";
  return null;
}

export function normalizeThreadRunStatus(
  status: "running" | "waiting" | "pending" | "error" | "done" | null,
): ThreadRunStatus | null {
  if (
    status === "running" ||
    status === "waiting" ||
    status === "pending" ||
    status === "error"
  ) {
    return status;
  }
  return null;
}

function mergeTaskStatusForThread(
  byHref: Map<string, ThreadRunStatus>,
  threadHrefById: Map<string, string>,
  threadId: string,
  status: ThreadRunStatus,
) {
  const href = threadHrefById.get(threadId);
  if (!href) return;
  // PauseController/background state is the current realtime objective.
  // It supersedes older team-task projections for the same conversation.
  byHref.set(href, status);
}

export function mergeThreadRunStatus(
  current: ThreadRunStatus | undefined,
  next: ThreadRunStatus,
): ThreadRunStatus {
  const priority: Record<ThreadRunStatus, number> = {
    error: 4,
    waiting: 3,
    running: 2,
    pending: 1,
  };
  if (!current) return next;
  return priority[next] > priority[current] ? next : current;
}

export function projectNameForThread(
  thread: Pick<ThreadSummary, "mode">,
  meta: Record<string, unknown>,
  personalSpaceLabel = "Personal space",
): string {
  const explicitProject = cleanDisplayText(meta["project"]);
  const workspacePath =
    typeof meta["workspace_path"] === "string"
      ? meta["workspace_path"].trim()
      : "";
  const workspaceProject = workspacePath ? basename(workspacePath) : "";

  if (thread.mode === "team") {
    if (workspaceProject) return workspaceProject;
    if (isGeneratedTeamProjectName(explicitProject)) return personalSpaceLabel;
    return explicitProject;
  }
  if (explicitProject) return explicitProject;
  if (workspaceProject) return workspaceProject;
  return "";
}

export function summarizeThreadForSidebar(thread: AgentThread): ThreadSummary {
  const mode =
    typeof thread.metadata?.["mode"] === "string"
      ? (thread.metadata["mode"] as string)
      : "chat";
  return {
    id: thread.thread_id,
    title: deriveThreadTitle(thread),
    updatedAt: thread.updated_at,
    mode,
    href: threadHref(thread),
    workspacePath:
      typeof thread.metadata?.["workspace_path"] === "string"
        ? (thread.metadata["workspace_path"] as string)
        : undefined,
    agents: deriveThreadAgents(thread),
  };
}

export function buildConversationThreadSummaries(
  threads: AgentThread[],
): ThreadSummary[] {
  return threads
    .filter((t) => {
      const mode =
        typeof t.metadata?.["mode"] === "string"
          ? (t.metadata["mode"] as string)
          : "chat";
      return isConversationThreadMode(mode) && !t.metadata?.subagent_role;
    })
    .map(summarizeThreadForSidebar);
}

export function buildProjectThreadSummaries(
  threads: AgentThread[],
): ThreadSummary[] {
  return threads
    .filter((t) => {
      const mode =
        typeof t.metadata?.["mode"] === "string"
          ? (t.metadata["mode"] as string)
          : "chat";
      return isProjectThreadMode(mode) && !t.metadata?.subagent_role;
    })
    .map(summarizeThreadForSidebar);
}

export function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

/** Pull a list of agent ids out of thread metadata · accepts the
 *  several places the backend stashes them (single agent on solo
 *  threads, agent_roster on team threads, fallback to bare ``agent``
 *  field). */
export function deriveThreadAgents(thread: {
  metadata?: Record<string, unknown>;
  values?: Record<string, unknown>;
}): string[] {
  const meta = thread.metadata ?? {};
  const values = thread.values ?? {};
  // 1. team threads · ``agent_roster`` is an array of {agent_id, ...}
  const roster = meta["agent_roster"] ?? values["agent_roster"];
  if (Array.isArray(roster)) {
    const ids = roster
      .map((r) =>
        r &&
        typeof r === "object" &&
        typeof (r as { agent_id?: unknown }).agent_id === "string"
          ? (r as { agent_id: string }).agent_id
          : null,
      )
      .filter((x): x is string => !!x);
    if (ids.length > 0) return ids;
  }
  // 2. team_members (legacy field name · same shape)
  const members = meta["team_members"] ?? values["team_members"];
  if (Array.isArray(members)) {
    const ids = members
      .map((r) =>
        typeof r === "string"
          ? r
          : r &&
              typeof r === "object" &&
              typeof (r as { agent_id?: unknown }).agent_id === "string"
            ? (r as { agent_id: string }).agent_id
            : null,
      )
      .filter((x): x is string => !!x);
    if (ids.length > 0) return ids;
  }
  // 3. solo agent · the ``agent`` field is set on every chat/code
  //    thread by the compat router (cf. metadata.agent='coder')
  const single = firstString(
    meta["agent"],
    meta["agent_name"],
    meta["agent_id"],
    meta["lead_agent_name"],
    meta["current_agent"],
    values["current_speaker"],
    values["agent_name"],
  );
  if (single) return [single];
  return [];
}

export function cleanDisplayText(value: unknown): string {
  if (typeof value !== "string") return "";
  const s = value
    .trim()
    .replace(/[\x00-\x1F\x7F-\x9F]/g, "")
    .replace(/\s+/g, " ");
  const questionMarks = (s.match(/\?/g) ?? []).length;
  const replacementChars = (s.match(/�/g) ?? []).length;
  if (
    /^\?{3,}$/.test(s) ||
    (questionMarks >= 5 && questionMarks / s.length > 0.25) ||
    (replacementChars >= 3 && replacementChars / s.length > 0.2)
  ) {
    return "";
  }
  return s;
}

function threadTitleFromContent(content: unknown): string {
  if (typeof content === "string") return cleanDisplayText(content);
  if (!Array.isArray(content)) return "";
  return cleanDisplayText(
    content
      .map((part) => {
        if (typeof part === "string") return part;
        if (
          part &&
          typeof part === "object" &&
          (part as Record<string, unknown>).type === "text" &&
          typeof (part as Record<string, unknown>).text === "string"
        ) {
          return (part as { text: string }).text;
        }
        return "";
      })
      .filter(Boolean)
      .join(" "),
  );
}

function truncateThreadTitle(title: string): string {
  return title.length > 60 ? `${title.slice(0, 58)}...` : title;
}

/** Best-effort thread title from `values`: first user message content,
 *  truncated. Falls back to metadata.title or a short thread-id. */
export function deriveThreadTitle(thread: {
  thread_id: string;
  metadata?: Record<string, unknown>;
  values?: Record<string, unknown>;
}): string {
  const metaTitle = cleanDisplayText(thread.metadata?.["title"]);
  if (metaTitle && !isGeneratedTeamThreadTitle(thread, metaTitle)) {
    return truncateThreadTitle(metaTitle);
  }

  const projectedTitle = threadTitleFromContent(
    thread.values?.["sidebar_title_source"],
  );
  if (projectedTitle) return truncateThreadTitle(projectedTitle);

  const messages = thread.values?.["messages"];
  if (Array.isArray(messages)) {
    for (const m of messages) {
      if (
        m &&
        typeof m === "object" &&
        (m as Record<string, unknown>).type === "human"
      ) {
        const content = (m as Record<string, unknown>).content;
        const title = threadTitleFromContent(content);
        if (title) return truncateThreadTitle(title);
      }
    }
  }
  const valuesTitle = cleanDisplayText(thread.values?.["title"]);
  if (
    valuesTitle &&
    valuesTitle !== "New chat" &&
    valuesTitle !== "New task" &&
    !isGeneratedTeamThreadTitle(thread, valuesTitle)
  ) {
    return truncateThreadTitle(valuesTitle);
  }
  if (threadMetadataMode(thread) === "team") {
    return `task/${thread.thread_id.slice(0, 6)}`;
  }
  return `thread/${thread.thread_id.slice(0, 6)}`;
}

export function threadHref(thread: {
  thread_id: string;
  metadata?: Record<string, unknown>;
}) {
  return `/workspace/realtime/${encodeURIComponent(thread.thread_id)}`;
}

export function activeWorkspaceThreadIdFromPathname(
  pathname: string,
): string | null {
  const match = /^\/workspace\/(?:realtime|team)\/([^/?#]+)/.exec(pathname);
  const value = match?.[1];
  if (!value || value === "new") return null;
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function activeTeamTaskRoomId(
  pathname: string,
  thread: AgentThread | null,
): string | null {
  const routeId = activeWorkspaceThreadIdFromPathname(pathname);
  if (!routeId) return null;
  if (/^\/workspace\/team\//.test(pathname)) return routeId;

  const metadata: Record<string, unknown> = thread?.metadata ?? {};
  const values: Record<string, unknown> = thread?.values ?? {};
  if (firstString(metadata["mode"], values["mode"]) !== "team") {
    return null;
  }
  return firstString(
    metadata["team_room_id"],
    metadata["room_id"],
    values["team_room_id"],
    values["room_id"],
    thread?.thread_id,
  );
}

export function syncedSidebarPathname(
  pathname: string,
  pendingThreadPath: string | null,
): string {
  return pendingThreadPath ?? pathname;
}
