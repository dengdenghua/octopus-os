import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getAPIClient } from "../api";
import { authHeaders, jsonAuthHeaders } from "../auth/api";
import { getBackendBaseURL } from "../config";
import { isPrimaryPersonaAgentId } from "../agents/persona-policy";
import {
  ensureCollabRoom,
  getCoworkGroup,
  replaceCoworkRoster,
} from "../cowork/api";
import type { CoworkMode } from "../cowork/types";

export interface Project {
  id: string;
  name: string;
  goal?: string;
  status?: string;
  /** Canonical project-group thread. Project OS keeps this in sync when a
   * thread is bound; older projects may not have one until first open. */
  execution_thread_id?: string;
  // Existing sidebar-only metadata is optional for Project OS projects.
  icon?: string;
  category?: string;
  created_at?: string;
  thread_ids?: string[];
}

export interface ProjectHome {
  project: Project;
  threadId: string;
}

export interface DetachedProjectBinding {
  ok: boolean;
  thread_id: string;
  project_id: string;
  detached: boolean;
  project?: Project | null;
}

export class ProjectBindingRequestError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code?: string | null) {
    super(message);
    this.name = "ProjectBindingRequestError";
    this.status = status;
    this.code = code?.trim() || null;
  }
}

export interface ProjectInitialAgent {
  id: string;
  displayName?: string;
  description?: string;
  avatarUrl?: string | null;
  icon?: string | null;
}

export interface ProjectHomeOptions {
  /** Complete initial AI roster for a newly-created project group. Omit this
   * when merely opening an existing project so its durable roster is kept. */
  initialAgents?: readonly ProjectInitialAgent[];
}

const BASE = () => `${getBackendBaseURL()}/api/projects`;
export const DEFAULT_PROJECT_AGENT_ID = "general";

function normalizedInitialAgents(
  agents: readonly ProjectInitialAgent[] | undefined,
): ProjectInitialAgent[] | undefined {
  if (agents === undefined) return undefined;
  const seen = new Set<string>();
  const normalized: ProjectInitialAgent[] = [];
  for (const agent of agents) {
    const id = agent.id.trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    normalized.push({ ...agent, id });
  }
  if (normalized.length === 0) {
    return [{ id: DEFAULT_PROJECT_AGENT_ID, displayName: "通用助手" }];
  }
  const leaderIndex = normalized.findIndex((agent) =>
    isPrimaryPersonaAgentId(agent.id),
  );
  if (leaderIndex < 0) {
    normalized.unshift({
      id: DEFAULT_PROJECT_AGENT_ID,
      displayName: "通用助手",
    });
  } else if (leaderIndex > 0) {
    const [leader] = normalized.splice(leaderIndex, 1);
    if (leader) normalized.unshift(leader);
  }
  return normalized;
}

function rosterMode(agentCount: number): CoworkMode {
  return agentCount > 1 ? "cluster" : "chat";
}

function roomMember(agent: ProjectInitialAgent) {
  return {
    name: agent.id,
    display_name:
      agent.displayName?.trim() ||
      (agent.id === DEFAULT_PROJECT_AGENT_ID ? "通用助手" : agent.id),
    ...(agent.description?.trim()
      ? { description: agent.description.trim() }
      : {}),
    ...(agent.avatarUrl ? { avatar_url: agent.avatarUrl } : {}),
    ...(agent.icon?.trim() ? { icon: agent.icon.trim() } : {}),
  };
}

function projectGroupAgent(agent: ProjectInitialAgent) {
  return {
    id: agent.id,
    ...(agent.displayName?.trim()
      ? { display_name: agent.displayName.trim() }
      : {}),
    ...(agent.description?.trim()
      ? { description: agent.description.trim() }
      : {}),
    ...(agent.avatarUrl ? { avatar_url: agent.avatarUrl } : {}),
    ...(agent.icon?.trim() ? { icon: agent.icon.trim() } : {}),
  };
}

async function moveThreadToProject(
  threadId: string,
  projectId: string,
): Promise<void> {
  const res = await fetch(`${BASE()}/move`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ thread_id: threadId, project_id: projectId }),
  });
  if (!res.ok) {
    throw new Error(`Failed to bind project home: ${res.statusText}`);
  }
}

/** Ensure every project has one durable, directly-openable work group.
 *
 * Thread creation goes through the canonical threads API so authenticated
 * deployments still provision the server-owned workspace. The linked room is
 * the persistent human/group transcript surface; Project OS remains the source
 * of truth for milestones and tasks.
 */
export async function ensureProjectHome(
  project: Project,
  options: ProjectHomeOptions = {},
): Promise<ProjectHome> {
  const api = getAPIClient();
  let threadId = project.execution_thread_id?.trim() ?? "";
  let createdThread = false;
  const requestedAgents = normalizedInitialAgents(options.initialAgents);
  const primaryAgentId = requestedAgents?.[0]?.id ?? DEFAULT_PROJECT_AGENT_ID;

  try {
    if (threadId) {
      try {
        await api.threads.get(threadId);
      } catch {
        // A project may outlive a manually-deleted execution thread. Repair it
        // in place instead of leaving the sidebar entry permanently broken.
        threadId = "";
      }
    }
    if (!threadId) {
      const thread = await api.threads.create({
        metadata: {
          mode: "code",
          agent_name: primaryAgentId,
          project_home: true,
          project_id: project.id,
          title: project.name,
        },
        values: {
          title: project.name,
          agent_name: primaryAgentId,
          project_id: project.id,
          project_home: true,
        },
      });
      threadId = thread.thread_id;
      createdThread = true;
    }

    await moveThreadToProject(threadId, project.id);
    await api.threads.updateState(threadId, {
      metadata: {
        project_home: true,
        project_id: project.id,
        title: project.name,
      },
      values: {
        project_home: true,
        project_id: project.id,
        title: project.name,
      },
    });

    const group = await getCoworkGroup(threadId).catch(() => null);
    let roomMembers = (group?.state.roster ?? [])
      .filter(
        (member) =>
          member.kind === "agent" &&
          member.role === "participant" &&
          !member.muted,
      )
      .map((member) => roomMember({ id: member.id }));
    let mode: CoworkMode = group?.state.mode ?? "chat";

    if (requestedAgents !== undefined) {
      roomMembers = requestedAgents.map(roomMember);
      mode = rosterMode(roomMembers.length);
      await replaceCoworkRoster(threadId, {
        agent_ids: requestedAgents.map((agent) => agent.id),
        mode,
      });
    } else if (!group?.state.room_id && roomMembers.length === 0) {
      const fallbackAgent = {
        id: DEFAULT_PROJECT_AGENT_ID,
        displayName: "通用助手",
      };
      roomMembers = [roomMember(fallbackAgent)];
      mode = "chat";
      await replaceCoworkRoster(threadId, {
        agent_ids: [fallbackAgent.id],
        mode,
      });
    }

    await ensureCollabRoom(threadId, {
      id: `collab-${threadId}`,
      name: project.name,
      members: roomMembers,
      leaderId: roomMembers[0]?.name ?? primaryAgentId,
      mode,
    });

    return {
      project: { ...project, execution_thread_id: threadId },
      threadId,
    };
  } catch (error) {
    // A newly-created but unbound thread is an implementation detail of this
    // operation. Roll it back so a failed project-home creation does not leave
    // a ghost conversation in Recents. Existing project threads are preserved.
    if (createdThread && threadId) {
      await api.threads.delete(threadId).catch(() => undefined);
    }
    throw error;
  }
}

export function useProjects() {
  return useQuery<Project[]>({
    queryKey: ["projects"],
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const res = await fetch(BASE(), {
        headers: authHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Failed to load projects: ${res.statusText}`);
      }
      const data = (await res.json()) as unknown;
      if (Array.isArray(data)) return data as Project[];
      if (
        data &&
        typeof data === "object" &&
        Array.isArray((data as { projects?: unknown }).projects)
      ) {
        return (data as { projects: Project[] }).projects;
      }
      return [];
    },
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: {
      name: string;
      goal?: string;
      icon?: string;
      category?: string;
      initialAgents?: ProjectInitialAgent[];
    }) => {
      const initialAgents =
        normalizedInitialAgents(data.initialAgents ?? []) ?? [];
      const res = await fetch(`${BASE()}/group`, {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({
          name: data.name,
          goal: data.goal?.trim() || data.name,
          initial_agents: initialAgents.map(projectGroupAgent),
        }),
      });
      if (!res.ok) {
        throw new Error(`Failed to create project: ${res.statusText}`);
      }
      const state = (await res.json()) as {
        project: Project;
        thread_id: string;
      };
      const threadId = state.thread_id?.trim();
      if (!threadId) {
        throw new Error("Failed to create project: missing canonical thread");
      }
      return {
        project: { ...state.project, execution_thread_id: threadId },
        threadId,
      };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["thread-map"] });
      qc.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function usePromoteGroupToProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      name,
      goal,
    }: {
      threadId: string;
      name: string;
      goal: string;
    }) => {
      const res = await fetch(
        `${BASE()}/from-group/${encodeURIComponent(threadId)}`,
        {
          method: "POST",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({ name, goal, run: false }),
        },
      );
      if (!res.ok) {
        throw new Error(
          `Failed to create project from group: ${res.statusText}`,
        );
      }
      return (await res.json()) as { project: Project };
    },
    onSuccess: (_result, input) => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["thread-map"] });
      void qc.invalidateQueries({ queryKey: ["threads"] });
      void qc.invalidateQueries({
        queryKey: ["project", "by-thread", input.threadId],
      });
    },
  });
}

/** Remove only the Project OS binding from a work group.
 *
 * The canonical group thread, room, members, invitations, and transcript are
 * deliberately outside this mutation. Keeping the lifecycle operation in one
 * hook also makes the DELETE contract easy to adapt without leaking endpoint
 * details into the realtime page.
 */
export function useDetachProjectFromGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      expectedProjectId,
      force = false,
    }: {
      threadId: string;
      expectedProjectId: string;
      force?: boolean;
    }) => {
      const res = await fetch(
        `${BASE()}/from-group/${encodeURIComponent(threadId)}`,
        {
          method: "DELETE",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({
            force,
            expected_project_id: expectedProjectId,
          }),
        },
      );
      if (!res.ok) {
        let detail = "";
        let code: string | null = null;
        try {
          const payload = (await res.json()) as {
            detail?: unknown;
            message?: unknown;
            code?: unknown;
          };
          const rawDetail = payload.detail ?? payload.message;
          if (typeof rawDetail === "string") {
            detail = rawDetail.trim();
          } else if (rawDetail && typeof rawDetail === "object") {
            const structured = rawDetail as {
              code?: unknown;
              message?: unknown;
            };
            if (typeof structured.message === "string") {
              detail = structured.message.trim();
            }
            if (typeof structured.code === "string") {
              code = structured.code.trim();
            }
          }
          if (!code && typeof payload.code === "string") {
            code = payload.code.trim();
          }
        } catch {
          // Some deployments return an empty/plain response for errors. The
          // status remains available to render the conflict-specific message.
        }
        throw new ProjectBindingRequestError(
          detail || `Failed to detach project from group: ${res.statusText}`,
          res.status,
          code,
        );
      }
      return (await res.json()) as DetachedProjectBinding;
    },
    onSuccess: (_result, input) => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["thread-map"] });
      void qc.invalidateQueries({ queryKey: ["threads"] });
      void qc.invalidateQueries({
        queryKey: ["project", "by-thread", input.threadId],
      });
    },
  });
}

export function useEnsureProjectHome() {
  const qc = useQueryClient();
  return useMutation({
    // React Query reserves mutationFn's second argument for its own context;
    // opening an existing project must intentionally omit creation options.
    mutationFn: (project: Project) => ensureProjectHome(project),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["thread-map"] });
      void qc.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${BASE()}/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Failed to delete project: ${res.statusText}`);
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useMoveThreadToProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      projectId,
    }: {
      threadId: string;
      projectId: string;
    }) => {
      await moveThreadToProject(threadId, projectId);
      return { ok: true, thread_id: threadId, project_id: projectId };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["thread-map"] });
      qc.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useThreadMap() {
  return useQuery<Record<string, string>>({
    queryKey: ["thread-map"],
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const res = await fetch(`${BASE()}/thread-map`, {
        headers: authHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Failed to load thread map: ${res.statusText}`);
      }
      return res.json();
    },
  });
}
