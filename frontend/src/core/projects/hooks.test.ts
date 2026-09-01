import { createElement, type PropsWithChildren } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createThread: vi.fn(),
  deleteThread: vi.fn(),
  getThread: vi.fn(),
  updateThread: vi.fn(),
  ensureRoom: vi.fn(),
  getGroup: vi.fn(),
  replaceRoster: vi.fn(),
}));

vi.mock("../api", () => ({
  getAPIClient: () => ({
    threads: {
      create: mocks.createThread,
      delete: mocks.deleteThread,
      get: mocks.getThread,
      updateState: mocks.updateThread,
    },
  }),
}));

vi.mock("../cowork/api", () => ({
  ensureCollabRoom: mocks.ensureRoom,
  getCoworkGroup: mocks.getGroup,
  replaceCoworkRoster: mocks.replaceRoster,
}));

import {
  ensureProjectHome,
  ProjectBindingRequestError,
  useCreateProject,
  useDetachProjectFromGroup,
  usePromoteGroupToProject,
} from "./hooks";

describe("project home work group", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mocks.createThread.mockReset();
    mocks.deleteThread.mockReset().mockResolvedValue(undefined);
    mocks.getThread.mockReset().mockResolvedValue({ thread_id: "thread-home" });
    mocks.updateThread.mockReset().mockResolvedValue({});
    mocks.ensureRoom.mockReset().mockResolvedValue({ ok: true });
    mocks.getGroup.mockReset().mockResolvedValue({
      state: { roster: [], room_id: null },
    });
    mocks.replaceRoster.mockReset().mockResolvedValue({
      ok: true,
      state: { roster: [], mode: "chat" },
      events: [],
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("{}", { status: 200 }),
    );
  });

  test("reuses an existing project thread and ensures its persistent room", async () => {
    const home = await ensureProjectHome({
      id: "P1",
      name: "发布新版",
      execution_thread_id: "thread-home",
    });

    expect(mocks.createThread).not.toHaveBeenCalled();
    expect(mocks.updateThread).toHaveBeenCalledWith("thread-home", {
      metadata: {
        project_home: true,
        project_id: "P1",
        title: "发布新版",
      },
      values: {
        project_home: true,
        project_id: "P1",
        title: "发布新版",
      },
    });
    expect(mocks.ensureRoom).toHaveBeenCalledWith("thread-home", {
      id: "collab-thread-home",
      name: "发布新版",
      members: [{ name: "general", display_name: "通用助手" }],
      leaderId: "general",
      mode: "chat",
    });
    expect(mocks.replaceRoster).toHaveBeenCalledWith("thread-home", {
      agent_ids: ["general"],
      mode: "chat",
    });
    expect(home.threadId).toBe("thread-home");
  });

  test("creates and binds a canonical home thread for a new project", async () => {
    mocks.createThread.mockResolvedValue({ thread_id: "thread-new" });
    const fetchMock = vi.mocked(globalThis.fetch);

    const home = await ensureProjectHome({ id: "P2", name: "增长实验" });

    expect(mocks.createThread).toHaveBeenCalledWith({
      metadata: {
        mode: "code",
        agent_name: "general",
        project_home: true,
        project_id: "P2",
        title: "增长实验",
      },
      values: {
        title: "增长实验",
        agent_name: "general",
        project_id: "P2",
        project_home: true,
      },
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/projects/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: "thread-new", project_id: "P2" }),
    });
    expect(mocks.updateThread).toHaveBeenCalledWith("thread-new", {
      metadata: {
        project_home: true,
        project_id: "P2",
        title: "增长实验",
      },
      values: {
        project_home: true,
        project_id: "P2",
        title: "增长实验",
      },
    });
    expect(home).toEqual({
      project: {
        id: "P2",
        name: "增长实验",
        execution_thread_id: "thread-new",
      },
      threadId: "thread-new",
    });
  });

  test("preserves an existing project roster instead of adding a default agent", async () => {
    mocks.getGroup.mockResolvedValue({
      state: {
        room_id: null,
        roster: [
          {
            id: "planner",
            kind: "agent",
            role: "participant",
            muted: false,
          },
        ],
      },
    });

    await ensureProjectHome({
      id: "P-existing",
      name: "已有项目群",
      execution_thread_id: "thread-home",
    });

    expect(mocks.replaceRoster).not.toHaveBeenCalled();
    expect(mocks.ensureRoom).toHaveBeenCalledWith("thread-home", {
      id: "collab-thread-home",
      name: "已有项目群",
      members: [{ name: "planner", display_name: "planner" }],
      leaderId: "planner",
      mode: "chat",
    });
  });

  test("removes a newly-created ghost thread when binding fails", async () => {
    mocks.createThread.mockResolvedValue({ thread_id: "thread-broken" });
    vi.mocked(globalThis.fetch).mockResolvedValue(
      new Response("bind failed", { status: 500, statusText: "broken" }),
    );

    await expect(
      ensureProjectHome({ id: "P3", name: "失败项目" }),
    ).rejects.toThrow("Failed to bind project home");
    expect(mocks.deleteThread).toHaveBeenCalledWith("thread-broken");
    expect(mocks.ensureRoom).not.toHaveBeenCalled();
  });

  test("keeps a fixed persona as lead and adds selected roles to the roster", async () => {
    mocks.createThread.mockResolvedValue({ thread_id: "thread-team" });

    await ensureProjectHome(
      { id: "P-team", name: "联合发布" },
      {
        initialAgents: [
          {
            id: "planner",
            displayName: "规划师",
            description: "拆解里程碑",
            icon: "📋",
          },
          {
            id: "writer",
            displayName: "写作助手",
            avatarUrl: "/api/agents/writer/avatar",
          },
        ],
      },
    );

    expect(mocks.createThread).toHaveBeenCalledWith({
      metadata: {
        mode: "code",
        agent_name: "general",
        project_home: true,
        project_id: "P-team",
        title: "联合发布",
      },
      values: {
        title: "联合发布",
        agent_name: "general",
        project_id: "P-team",
        project_home: true,
      },
    });
    expect(mocks.replaceRoster).toHaveBeenCalledTimes(1);
    expect(mocks.replaceRoster).toHaveBeenCalledWith("thread-team", {
      agent_ids: ["general", "planner", "writer"],
      mode: "cluster",
    });
    expect(mocks.ensureRoom).toHaveBeenCalledWith("thread-team", {
      id: "collab-thread-team",
      name: "联合发布",
      members: [
        {
          name: "general",
          display_name: "通用助手",
        },
        {
          name: "planner",
          display_name: "规划师",
          description: "拆解里程碑",
          icon: "📋",
        },
        {
          name: "writer",
          display_name: "写作助手",
          avatar_url: "/api/agents/writer/avatar",
        },
      ],
      leaderId: "general",
      mode: "cluster",
    });
  });

  test("rolls back a newly-created home when the atomic initial roster fails", async () => {
    mocks.createThread.mockResolvedValue({ thread_id: "thread-roster-failed" });
    mocks.replaceRoster.mockRejectedValue(new Error("roster unavailable"));

    await expect(
      ensureProjectHome(
        { id: "P-roster-failed", name: "失败项目" },
        { initialAgents: [{ id: "planner" }] },
      ),
    ).rejects.toThrow("roster unavailable");

    expect(mocks.ensureRoom).not.toHaveBeenCalled();
    expect(mocks.deleteThread).toHaveBeenCalledWith("thread-roster-failed");
  });

  test("creates the project group through one backend request with the initial AI metadata", async () => {
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/projects/group" && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            project: { id: "P-created", name: "产品发布" },
            thread_id: "thread-created",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("{}", { status: 200 });
    });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const wrapper = ({ children }: PropsWithChildren) =>
      createElement(QueryClientProvider, { client: queryClient }, children);
    const { result } = renderHook(() => useCreateProject(), { wrapper });

    const created = await result.current.mutateAsync({
      name: "产品发布",
      initialAgents: [
        {
          id: "planner",
          displayName: " 规划师 ",
          description: " 拆解里程碑 ",
          icon: " 📋 ",
        },
        {
          id: "writer",
          displayName: "写作助手",
          avatarUrl: "/api/agents/writer/avatar",
        },
      ],
    });

    expect(created).toEqual({
      project: {
        id: "P-created",
        name: "产品发布",
        execution_thread_id: "thread-created",
      },
      threadId: "thread-created",
    });
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledWith(
      "/api/projects/group",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "产品发布",
          goal: "产品发布",
          initial_agents: [
            {
              id: "general",
              display_name: "通用助手",
            },
            {
              id: "planner",
              display_name: "规划师",
              description: "拆解里程碑",
              icon: "📋",
            },
            {
              id: "writer",
              display_name: "写作助手",
              avatar_url: "/api/agents/writer/avatar",
            },
          ],
        }),
      },
    );
    expect(mocks.createThread).not.toHaveBeenCalled();
    expect(mocks.replaceRoster).not.toHaveBeenCalled();
    expect(mocks.ensureRoom).not.toHaveBeenCalled();
  });

  test("does not run client-side compensation when project-group creation fails", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      new Response("creation failed", {
        status: 500,
        statusText: "creation failed",
      }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const wrapper = ({ children }: PropsWithChildren) =>
      createElement(QueryClientProvider, { client: queryClient }, children);
    const { result } = renderHook(() => useCreateProject(), { wrapper });

    await expect(
      result.current.mutateAsync({
        name: "失败项目",
        initialAgents: [{ id: "planner" }],
      }),
    ).rejects.toThrow("Failed to create project: creation failed");

    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledWith(
      "/api/projects/group",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "失败项目",
          goal: "失败项目",
          initial_agents: [
            { id: "general", display_name: "通用助手" },
            { id: "planner" },
          ],
        }),
      },
    );
    expect(mocks.deleteThread).not.toHaveBeenCalled();
    expect(mocks.createThread).not.toHaveBeenCalled();
  });

  test("opens project capability on the existing group without starting execution", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          project: { id: "P-bound", name: "秋季发布" },
          thread_id: "thread-existing-group",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const wrapper = ({ children }: PropsWithChildren) =>
      createElement(QueryClientProvider, { client: queryClient }, children);
    const { result } = renderHook(() => usePromoteGroupToProject(), {
      wrapper,
    });

    const promoted = await result.current.mutateAsync({
      threadId: "thread-existing-group",
      name: "秋季发布",
      goal: "九月底完成发布",
    });

    expect(promoted.project).toMatchObject({
      id: "P-bound",
      name: "秋季发布",
    });
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledWith(
      "/api/projects/from-group/thread-existing-group",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "秋季发布",
          goal: "九月底完成发布",
          run: false,
        }),
      },
    );
    expect(mocks.createThread).not.toHaveBeenCalled();
    expect(mocks.deleteThread).not.toHaveBeenCalled();
  });

  test("detaches only the project binding from the canonical group thread", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          thread_id: "thread-stays",
          project_id: "P-detached",
          detached: true,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const wrapper = ({ children }: PropsWithChildren) =>
      createElement(QueryClientProvider, { client: queryClient }, children);
    const { result } = renderHook(() => useDetachProjectFromGroup(), {
      wrapper,
    });

    const detached = await result.current.mutateAsync({
      threadId: "thread-stays",
      expectedProjectId: "P-detached",
    });

    expect(detached).toMatchObject({
      thread_id: "thread-stays",
      project_id: "P-detached",
      detached: true,
    });
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledWith(
      "/api/projects/from-group/thread-stays",
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          force: false,
          expected_project_id: "P-detached",
        }),
      },
    );
    expect(mocks.deleteThread).not.toHaveBeenCalled();
  });

  test("preserves the 409 status when an active project cannot be detached", async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "PROJECT_ACTIVE",
            message: "project is active",
            force_required: true,
          },
        }),
        {
          status: 409,
          statusText: "Conflict",
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const wrapper = ({ children }: PropsWithChildren) =>
      createElement(QueryClientProvider, { client: queryClient }, children);
    const { result } = renderHook(() => useDetachProjectFromGroup(), {
      wrapper,
    });

    const request = result.current.mutateAsync({
      threadId: "thread-active",
      expectedProjectId: "P-active",
    });

    await expect(request).rejects.toBeInstanceOf(ProjectBindingRequestError);
    await expect(request).rejects.toMatchObject({
      message: "project is active",
      status: 409,
      code: "PROJECT_ACTIVE",
    });
  });
});
