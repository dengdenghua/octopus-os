import { describe, expect, test } from "vitest";

import { __testing } from "./workspace-sidebar";

describe("workspace sidebar route activation", () => {
  test("projects thread searches down to sidebar-only state", () => {
    expect(__testing.SIDEBAR_THREAD_QUERY_PARAMS).toEqual({
      limit: 30,
      sortBy: "updated_at",
      sortOrder: "desc",
      select: [
        "thread_id",
        "updated_at",
        "metadata",
        "values.title",
        "values.sidebar_title_source",
        "values.current_speaker",
        "values.agent_name",
        "values.agent_roster",
        "values.team_members",
        "values.mode",
        "values.workspace_path",
        "values.workspace_id",
        "values.team_room_id",
        "values.room_id",
      ],
    });
    expect(__testing.SIDEBAR_THREAD_QUERY_PARAMS.select).not.toContain(
      "values",
    );
    expect(__testing.SIDEBAR_THREAD_QUERY_PARAMS.select).not.toContain(
      "values.messages",
    );
    expect(__testing.SIDEBAR_THREAD_QUERY_PARAMS.select).not.toContain(
      "values.artifacts",
    );
  });

  test("uses a pending server route while a new realtime turn stays mounted", () => {
    expect(
      __testing.syncedSidebarPathname(
        "/workspace/realtime/new",
        "/workspace/realtime/thread-1",
      ),
    ).toBe("/workspace/realtime/thread-1");
    expect(
      __testing.syncedSidebarPathname("/workspace/realtime/new", null),
    ).toBe("/workspace/realtime/new");
  });

  test("leaves primary nav inactive on non-realtime agent subpaths", () => {
    const pathname = "/workspace/agents/general/threads/thread-1";

    expect(
      __testing.isNavRouteActive(pathname, "/workspace/realtime/new"),
    ).toBe(false);
    expect(__testing.isNavRouteActive(pathname, "/workspace/agents")).toBe(
      true,
    );
  });

  test("keeps primary chat active only on realtime entry routes", () => {
    expect(
      __testing.isNavRouteActive(
        "/workspace/realtime/new",
        "/workspace/realtime/new",
      ),
    ).toBe(true);
    expect(
      __testing.isNavRouteActive(
        "/workspace/agents/general/threads/new",
        "/workspace/realtime/new",
      ),
    ).toBe(false);
    expect(
      __testing.isNavRouteActive(
        "/workspace/realtime/thread-1",
        "/workspace/realtime/new",
      ),
    ).toBe(false);
  });

  test("keeps the Hub active on normal agent routes", () => {
    expect(
      __testing.isNavRouteActive("/workspace/agents", "/workspace/agents"),
    ).toBe(true);
    expect(
      __testing.isNavRouteActive("/workspace/agents/new", "/workspace/agents"),
    ).toBe(true);
  });

  test("keeps Hub in the surface selected by the sidebar entry", () => {
    expect(
      __testing.isCompanySurfaceActive("/workspace/agents", "?surface=chat"),
    ).toBe(false);
    expect(
      __testing.isCompanySurfaceActive("/workspace/agents", "?surface=company"),
    ).toBe(true);
    expect(__testing.isCompanySurfaceActive("/workspace/agents", "")).toBe(
      true,
    );
  });
});

describe("workspace sidebar project grouping", () => {
  const makeThread = (
    threadId: string,
    mode: string,
    metadata: Record<string, unknown> = {},
  ) =>
    ({
      thread_id: threadId,
      title: threadId,
      updated_at: "2026-06-29T00:00:00Z",
      metadata: {
        mode,
        ...metadata,
      },
      values: {},
    }) as never;

  test("treats team threads as project threads", () => {
    expect(__testing.isProjectThreadMode("team")).toBe(true);
    expect(__testing.isProjectThreadMode("code")).toBe(true);
    expect(__testing.isProjectThreadMode("chat")).toBe(false);
  });

  test("distinguishes local-folder projects from milestone-only projects", () => {
    expect(
      __testing.projectHasBoundFolder([
        {
          id: "local",
          title: "echo-agent",
          updatedAt: "2026-08-24T00:00:00Z",
          mode: "code",
          href: "/workspace/realtime/local",
          workspacePath: "/Users/example/Public/echo-agent",
          agents: [],
        },
      ]),
    ).toBe(true);
    expect(
      __testing.projectHasBoundFolder([
        {
          id: "milestone",
          title: "PM 演示",
          updatedAt: "2026-08-24T00:00:00Z",
          mode: "team",
          href: "/workspace/realtime/milestone",
          agents: [],
        },
      ]),
    ).toBe(false);
  });

  test("queries team tasks only for actual team rooms", () => {
    const ordinaryThread = makeThread("chat-1", "chat");
    const teamThread = makeThread("team-1", "team", {
      team_room_id: "room-1",
    });

    expect(
      __testing.activeTeamTaskRoomId(
        "/workspace/realtime/chat-1",
        ordinaryThread,
      ),
    ).toBeNull();
    expect(
      __testing.activeTeamTaskRoomId("/workspace/realtime/team-1", teamThread),
    ).toBe("room-1");
    expect(__testing.activeTeamTaskRoomId("/workspace/team/room-2", null)).toBe(
      "room-2",
    );
  });

  test("keeps team history out of chat recents and under projects", () => {
    const threads = [
      makeThread("chat-1", "chat"),
      makeThread("team-1", "team", {
        workspace_path: "/Users/example/Public/echo/echo-agent",
      }),
      makeThread("code-1", "code", {
        workspace_path: "/Users/example/Public/echo/echo-agent",
      }),
    ];

    expect(
      __testing.buildConversationThreadSummaries(threads).map((t) => t.id),
    ).toEqual(["chat-1"]);
    const projectThreads = __testing.buildProjectThreadSummaries(threads);
    expect(projectThreads.map((t) => t.id)).toEqual(["team-1", "code-1"]);
    expect(projectThreads.find((t) => t.id === "team-1")?.href).toBe(
      "/workspace/realtime/team-1",
    );
  });

  test("coerces dedicated team query results before building sidebar history", () => {
    const rawTeamThread = {
      thread_id: "team-legacy",
      title: "team-legacy",
      updated_at: "2026-06-29T00:00:00Z",
      metadata: {
        project: "团队",
        title: "团队",
      },
      values: {},
    } as never;
    const coerced = __testing.withThreadSidebarMode(rawTeamThread, "team");

    expect(coerced.metadata.mode).toBe("team");
    expect(__testing.buildConversationThreadSummaries([coerced])).toEqual([]);

    const [projectThread] = __testing.buildProjectThreadSummaries([coerced]);
    expect(projectThread?.href).toBe("/workspace/realtime/team-legacy");
    expect(projectThread?.title).toBe("task/team-l");
  });

  test("routes agent chat history into the unified realtime workspace", () => {
    const threads = [
      makeThread("agent-1", "agent", {
        agent: "local codex",
      }),
      makeThread("deep-1", "deep", {
        agent_name: "researcher",
      }),
    ];

    expect(
      __testing.buildConversationThreadSummaries(threads).map((t) => t.href),
    ).toEqual(["/workspace/realtime/agent-1", "/workspace/realtime/deep-1"]);
  });

  test("derives transient thread mode from the route prefix", () => {
    expect(
      __testing.transientThreadModeFromHref("/workspace/team/room-1"),
    ).toBe("team");
    expect(
      __testing.transientThreadModeFromHref("/workspace/realtime/thread-1"),
    ).toBe("chat");
  });

  test("keeps the current task inside its natural project list and moves it to the top", () => {
    const threads = [
      {
        id: "newer",
        href: "/workspace/realtime/newer",
      },
      {
        id: "current",
        href: "/workspace/realtime/current",
      },
      {
        id: "older",
        href: "/workspace/realtime/older",
      },
    ] as never;

    expect(
      __testing
        .prioritizeActiveThread(threads, "/workspace/realtime/current")
        .map((thread) => thread.id),
    ).toEqual(["current", "newer", "older"]);
  });

  test("keeps project history compact until the user expands it", () => {
    const threads = Array.from({ length: 9 }, (_, index) => ({
      id: `thread-${index + 1}`,
    }));

    expect(
      __testing
        .projectThreadsForPreview(threads, false)
        .map((thread) => thread.id),
    ).toEqual([
      "thread-1",
      "thread-2",
      "thread-3",
      "thread-4",
      "thread-5",
      "thread-6",
    ]);
    expect(__testing.projectThreadsForPreview(threads, true)).toHaveLength(9);
  });

  test("restores a durable project-group entry from thread-map after reload", () => {
    const index = __testing.buildProjectOsSidebarIndex(
      [
        {
          id: "P-legacy",
          name: "PM 演示",
          execution_thread_id: "",
          created_at: "2026-08-20T12:56:43Z",
        },
        {
          id: "P-empty",
          name: "刚创建的项目",
        },
      ],
      { "thread-home": "P-legacy" },
      [],
    );

    expect(index.projectNames).toEqual(["PM 演示", "刚创建的项目"]);
    expect(index.projectNameByThreadId.get("thread-home")).toBe("PM 演示");
    expect(index.threads).toEqual([
      {
        id: "thread-home",
        title: "PM 演示",
        updatedAt: "2026-08-20T12:56:43Z",
        mode: "code",
        href: "/workspace/realtime/thread-home",
        agents: [],
      },
    ]);
  });

  test("pins the canonical project group ahead of the compact thread preview", () => {
    const existingThreads = Array.from({ length: 7 }, (_, index) => ({
      id: index === 6 ? "thread-home" : `thread-${index}`,
      title: index === 6 ? "首轮消息覆盖的标题" : `任务 ${index}`,
      updatedAt: "2026-08-20T12:56:43Z",
      mode: "code",
      href: `/workspace/realtime/${index === 6 ? "thread-home" : `thread-${index}`}`,
      agents: [],
    }));

    const index = __testing.buildProjectOsSidebarIndex(
      [
        {
          id: "P1",
          name: "增长实验",
          execution_thread_id: "thread-home",
        },
      ],
      { "thread-home": "P1" },
      existingThreads,
    );

    expect(index.threads).toHaveLength(existingThreads.length);
    expect(index.threads[0]).toMatchObject({
      id: "thread-home",
      title: "增长实验",
    });
    expect(
      __testing.projectThreadsForPreview(index.threads, false)[0]?.id,
    ).toBe("thread-home");
  });

  test("groups team history by workspace folder before generated team label", () => {
    expect(
      __testing.projectNameForThread(
        { mode: "team" },
        {
          project: "Team · Eve",
          workspace_path: "/Users/example/Public/echo/echo-agent",
        },
      ),
    ).toBe("echo-agent");
  });

  test("groups team history without a folder under personal space", () => {
    expect(
      __testing.projectNameForThread(
        { mode: "team" },
        {
          project: "Team",
          workspace_path: "",
        },
        "个人空间",
      ),
    ).toBe("个人空间");
  });

  test("folds localized generated team labels into personal space", () => {
    expect(
      __testing.projectNameForThread(
        { mode: "team" },
        {
          project: "团队",
          workspace_path: "",
        },
        "个人空间",
      ),
    ).toBe("个人空间");
    expect(
      __testing.projectNameForThread(
        { mode: "team" },
        {
          project: "团队 · 协作",
          workspace_path: "",
        },
        "个人空间",
      ),
    ).toBe("个人空间");
  });

  test("does not render generated team labels as history titles", () => {
    const [withPrompt, withoutPrompt] = __testing.buildProjectThreadSummaries([
      {
        thread_id: "team-prompt",
        title: "team-prompt",
        updated_at: "2026-06-29T00:00:00Z",
        metadata: {
          mode: "team",
          title: "团队",
        },
        values: {
          messages: [
            {
              type: "human",
              content: "帮我做一个产品调研",
            },
          ],
        },
      } as never,
      {
        thread_id: "team-empty",
        title: "team-empty",
        updated_at: "2026-06-29T00:00:00Z",
        metadata: {
          mode: "team",
          title: "Team · Eve",
        },
        values: {},
      } as never,
    ]);

    expect(withPrompt?.title).toBe("帮我做一个产品调研");
    expect(withoutPrompt?.title).toBe("task/team-e");
  });

  test("hides generated team labels when old records only carry a team project", () => {
    const summary = __testing.summarizeThreadForSidebar({
      thread_id: "legacy-team-title",
      title: "legacy-team-title",
      updated_at: "2026-06-29T00:00:00Z",
      metadata: {
        project: "团队",
        title: "团队",
      },
      values: {},
    } as never);

    expect(summary.title).toBe("thread/legacy");
  });

  test("keeps the first-user-message title from compact sidebar projections", () => {
    const summary = __testing.summarizeThreadForSidebar({
      thread_id: "legacy-compact-title",
      updated_at: "2026-06-29T00:00:00Z",
      metadata: { mode: "chat" },
      values: {
        title: "New chat",
        sidebar_title_source: "来自旧会话的可读标题",
      },
    } as never);

    expect(summary.title).toBe("来自旧会话的可读标题");
  });

  test("hides bare legacy team labels even when mode metadata was lost", () => {
    const [summary] = __testing.buildConversationThreadSummaries([
      {
        thread_id: "bare-team-label",
        title: "bare-team-label",
        updated_at: "2026-06-29T00:00:00Z",
        metadata: {
          title: "团队",
        },
        values: {},
      } as never,
    ]);

    expect(summary?.title).toBe("thread/bare-t");
  });

  test("keeps explicit project labels for code threads", () => {
    expect(
      __testing.projectNameForThread(
        { mode: "code" },
        {
          project: "总项目",
          workspace_path: "/Users/example/Public/echo/echo-agent",
        },
      ),
    ).toBe("总项目");
  });

  test("keeps project header actions focused on executable commands", () => {
    const actions = __testing.buildProjectSectionActions({
      groupingEnabled: true,
      newProjectLabel: "添加工作区/项目",
      onNewProject: () => undefined,
    });

    expect(actions.map((action) => action.label)).toEqual(["添加工作区/项目"]);
    expect(
      actions.some((action) => /排序|分组|Sort|group/i.test(action.label)),
    ).toBe(false);
    expect(
      __testing.buildProjectSectionActions({
        groupingEnabled: false,
        newProjectLabel: "添加工作区/项目",
        onNewProject: () => undefined,
      }),
    ).toEqual([]);
  });

  test("keeps chat header actions to the new-task command", () => {
    const actions = __testing.buildChatsSectionActions({
      sectionLabel: "对话",
      actionLabel: "新建任务",
      onNewChat: () => undefined,
    });

    expect(actions.map((action) => action.label)).toEqual(["新建任务"]);
    expect(actions[0]?.ariaLabel).toBe("对话 · 新建任务");
    expect(actions.some((action) => /排序|Sort/i.test(action.label))).toBe(
      false,
    );
  });
});

describe("workspace sidebar thread status lights", () => {
  test("maps paused and pending background tasks onto conversation history", () => {
    const href = "/workspace/realtime/thread-1";
    const statusByHref = __testing.buildThreadRunStatusByHref({
      activeTeamTasks: [],
      backgroundTasks: {
        active: [],
        paused: [
          {
            agent_id: "general",
            note: "",
            reason: "user_request",
            requested_at: 1,
            requested_by: "user",
            task_id: "task-1",
            thread_id: "thread-1",
          },
        ],
        pending: [],
      },
      liveThreadRunStatusByHref: new Map(),
      threadHrefById: new Map([["thread-1", href]]),
    });

    expect(statusByHref.get(href)).toBe("waiting");
  });

  test("maps active team task lights onto unified realtime links", () => {
    const statusByHref = __testing.buildThreadRunStatusByHref({
      activeTeamTasks: [
        {
          id: "task-1",
          room_id: "room-1",
          title: "Run SOP",
          description: "",
          sop_template: "",
          status: "running",
          assignees: [],
          created_at: "2026-06-29T00:00:00Z",
          updated_at: "2026-06-29T00:00:00Z",
          produced_artifacts: [],
          metadata: {},
        },
      ],
      backgroundTasks: undefined,
      liveThreadRunStatusByHref: new Map(),
      threadHrefById: new Map(),
    });

    expect(statusByHref.get("/workspace/realtime/room-1")).toBe("running");
    expect(statusByHref.has("/workspace/team/room-1")).toBe(false);
  });

  test("maps failed team work to an error status", () => {
    const statusByHref = __testing.buildThreadRunStatusByHref({
      activeTeamTasks: [
        {
          id: "task-failed",
          room_id: "room-failed",
          title: "Failed SOP",
          description: "",
          sop_template: "",
          status: "failed",
          assignees: [],
          created_at: "2026-06-29T00:00:00Z",
          updated_at: "2026-06-29T00:00:00Z",
          produced_artifacts: [],
          metadata: {},
        },
      ],
      backgroundTasks: undefined,
      liveThreadRunStatusByHref: new Map(),
      threadHrefById: new Map(),
    });

    expect(statusByHref.get("/workspace/realtime/room-failed")).toBe("error");
  });

  test("merges live workbench status over active background task status", () => {
    const href = "/workspace/realtime/thread-2";
    const statusByHref = __testing.buildThreadRunStatusByHref({
      activeTeamTasks: [],
      backgroundTasks: {
        active: [
          {
            agent_id: "general",
            current_iteration: 2,
            max_iterations: 12,
            max_tokens: 100_000,
            max_usd: 0,
            cost_usd: 0,
            started_at: 1,
            task_id: "task-2",
            thread_id: "thread-2",
            tokens_spent: 1200,
          },
        ],
        paused: [],
        pending: [],
      },
      liveThreadRunStatusByHref: new Map([[href, "waiting"]]),
      threadHrefById: new Map([["thread-2", href]]),
    });

    expect(statusByHref.get(href)).toBe("waiting");
  });

  test("keeps waiting ahead of running so colors match the workbench pause state", () => {
    expect(__testing.mergeThreadRunStatus("running", "waiting")).toBe(
      "waiting",
    );
  });
});
