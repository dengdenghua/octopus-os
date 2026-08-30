import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import NarrativePage from "./page";

const narrative = vi.hoisted(() => ({
  dispatchNarrativeStage: vi.fn(),
}));

const api = vi.hoisted(() => ({
  createBranch: vi.fn(),
  createChapter: vi.fn(),
  createContextPack: vi.fn(),
  createNarrativeProject: vi.fn(),
  createPipelineRun: vi.fn(),
  createReviewRequest: vi.fn(),
  createScene: vi.fn(),
  commitCanonReview: vi.fn(),
  getNarrativeStatus: vi.fn(),
  importEchoUniverse: vi.fn(),
  listNarrativeProjects: vi.fn(),
  loadNarrativeExtensions: vi.fn(),
  loadNarrativeWorkspace: vi.fn(),
  submitPipelineStage: vi.fn(),
  updateChapter: vi.fn(),
  updateScene: vi.fn(),
  voteReviewRequest: vi.fn(),
}));

vi.mock("./api", () => api);
vi.mock("@/core/narrative", () => narrative);

const project = {
  id: "echo-story",
  title: "陌生人的记忆",
  premise: "记忆成为证据",
  language: "zh",
  default_branch_id: "main",
  canon_policy: "candidate_only",
  canon_status: "candidate" as const,
};

const workspace = {
  project,
  counts: {
    world_packs: 1,
    branches: 1,
    chapters: 1,
    scenes: 1,
    facts: 1,
    state_changes: 1,
  },
  worldPacks: [
    {
      id: "echo-pack",
      name: "ECHO Universe",
      summary: "只读快照",
      resources: [{ relative_path: "bible/rules.md" }],
      canon_status: "candidate" as const,
    },
  ],
  branches: [
    {
      id: "main",
      name: "Main candidate branch",
      purpose: "默认候选分支",
      canon_status: "candidate" as const,
    },
  ],
  chapters: [
    {
      id: "chapter-1",
      branch_id: "main",
      ordinal: 1,
      title: "第一章",
      summary: "醒来",
      body: "候选正文",
      canon_status: "candidate" as const,
    },
  ],
  scenes: [
    {
      id: "scene-1",
      chapter_id: "chapter-1",
      branch_id: "main",
      ordinal: 1,
      title: "病房",
      goal: "确认身体归属",
      conflict: "陌生的程序记忆",
      outcome: "保留技能",
      body: "场景正文",
      canon_status: "candidate" as const,
    },
  ],
  facts: [
    {
      id: "fact-1",
      subject: "Ghost",
      predicate: "origin",
      object: "uploaded memory",
      scope: "world",
      source_refs: [],
      canon_status: "candidate" as const,
    },
  ],
  stateChanges: [
    {
      id: "change-1",
      branch_id: "main",
      chapter_id: "chapter-1",
      entity_id: "lin-qiao",
      field: "procedural_memory",
      before: "absent",
      after: "present",
      reason: "场景结果",
      canon_status: "candidate" as const,
    },
  ],
};

const contextPack = {
  id: "context-1",
  project_id: "echo-story",
  branch_id: "main",
  chapter_id: "chapter-1",
  token_budget: 12000,
  token_count: 2300,
  max_chars: 48000,
  max_items: 48,
  total_chars: 9200,
  omitted_count: 0,
  sources: [
    {
      id: "source-1",
      kind: "world_fact",
      title: "Ghost 起源",
      reference: "fact:ghost-origin",
      excerpt: "uploaded memory",
      tokens: 120,
      char_count: 480,
      truncated: false,
      included: true,
    },
  ],
};

const extensions = {
  arcs: [],
  entities: [
    {
      id: "lin-qiao",
      name: "林桥",
      kind: "character",
      description: "失去自传体记忆的人",
      attributes: {},
      source_refs: [],
    },
  ],
  relationships: [],
  foreshadows: [],
  contextPacks: [],
  pipelineRuns: [],
  reviewRequests: [],
  canonCommits: [],
  warnings: [],
};

const pipelineRun = {
  id: "run-1",
  project_id: "echo-story",
  branch_id: "main",
  chapter_id: "chapter-1",
  status: "active",
  stages: [
    {
      id: "outline",
      name: "outline",
      ordinal: 1,
      status: "submitted" as const,
      output: "第一幕从病房醒来开始。",
    },
    {
      id: "draft",
      name: "draft",
      ordinal: 2,
      status: "pending" as const,
    },
  ],
};

const agentResult = {
  success: true as const,
  output: "AI 生成的候选初稿",
  error: null,
  stage: "draft" as const,
  subagentType: "narrative-draft" as const,
  runId: "run-1",
  turnId: "turn-1",
  promptAudit: {
    promptChars: 4100,
    maxPromptChars: 64000,
    truncated: true,
    promptLimitApplied: false,
    omittedContextSources: 2,
    omittedUpstreamStages: 0,
    inputs: [],
  },
  metadata: {
    agentId: "agent-1",
    sessionId: "session-1",
    model: "gpt-narrative",
    status: "completed",
    durationSeconds: 3,
    iterationCount: 1,
    usage: {
      inputTokens: 300,
      outputTokens: 45,
      totalTokens: 345,
      costUsd: 0.01,
    },
  },
};

describe("NarrativePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getNarrativeStatus.mockResolvedValue({
      status: "ready",
      ready: true,
      version: "0.1.0",
    });
    api.listNarrativeProjects.mockResolvedValue([project]);
    api.loadNarrativeWorkspace.mockResolvedValue(workspace);
    api.loadNarrativeExtensions.mockResolvedValue(extensions);
    api.updateChapter.mockResolvedValue(workspace.chapters[0]);
    api.createContextPack.mockResolvedValue(contextPack);
    api.submitPipelineStage.mockResolvedValue({
      id: "run-1",
      project_id: "echo-story",
      branch_id: "main",
      chapter_id: "chapter-1",
      status: "active",
      stages: [],
    });
    narrative.dispatchNarrativeStage.mockResolvedValue(agentResult);
  });

  it("loads the real workspace contract and saves a candidate chapter", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    expect(await screen.findByDisplayValue("第一章")).toBeInTheDocument();
    expect(screen.getByText("ECHO Universe")).toBeInTheDocument();
    expect(screen.getAllByText("候选态").length).toBeGreaterThan(0);

    const body = screen.getByPlaceholderText("写下这一章的候选正文……");
    await user.clear(body);
    await user.type(body, "候选正文第二稿");
    await user.click(screen.getByRole("button", { name: "保存候选稿" }));

    await waitFor(() =>
      expect(api.updateChapter).toHaveBeenCalledWith(
        "echo-story",
        "chapter-1",
        expect.objectContaining({ body: "候选正文第二稿" }),
      ),
    );

    await user.click(screen.getByRole("button", { name: "正典" }));
    expect(screen.getByRole("button", { name: "提交正典审核" })).toBeEnabled();
    expect(screen.queryByText("确认提交正典")).not.toBeInTheDocument();
  });

  it("builds a traceable context pack from the chapter editor", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "构建上下文" }));

    await waitFor(() =>
      expect(api.createContextPack).toHaveBeenCalledWith(
        "echo-story",
        expect.objectContaining({
          chapter_id: "chapter-1",
          branch_id: "main",
          token_budget: 12000,
        }),
      ),
    );
    expect((await screen.findAllByText("Ghost 起源")).length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getAllByText(/2,300 \/ 12,000 tokens/).length,
    ).toBeGreaterThan(0);
  });

  it("requires an explicit second confirmation before a canon commit", async () => {
    api.loadNarrativeExtensions.mockResolvedValue({
      ...extensions,
      reviewRequests: [
        {
          id: "review-1",
          project_id: "echo-story",
          target_type: "chapter",
          target_id: "chapter-1",
          revision: 2,
          title: "第一章 · 正典审核",
          status: "resolved",
          quorum_required: 2,
          quorum_received: 2,
          blockers: [],
          blocking: false,
          approval_ratio: 1,
          votes: [],
        },
      ],
    });
    api.commitCanonReview.mockResolvedValue({
      id: "commit-1",
      review_request_id: "review-1",
      target_type: "chapter",
      target_id: "chapter-1",
      status: "committed",
      actor: "human-editor",
      rationale: "连续性与票数均已核对",
    });
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "正典" }));
    await user.click(screen.getByRole("button", { name: "人工确认提交正典" }));

    expect(api.commitCanonReview).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: "二次确认：提交正典" });
    expect(dialog).toBeInTheDocument();
    await user.type(
      screen.getByPlaceholderText("说明为什么该修订可以进入正典"),
      "连续性与票数均已核对",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "确认提交正典" }));

    await waitFor(() =>
      expect(api.commitCanonReview).toHaveBeenCalledWith(
        "echo-story",
        "review-1",
        {
          actor: "human-editor",
          rationale: "连续性与票数均已核对",
          confirm: true,
        },
      ),
    );
  });

  it("shows all six stages and submits the next real pipeline artifact", async () => {
    api.loadNarrativeExtensions.mockResolvedValue({
      ...extensions,
      pipelineRuns: [
        {
          id: "run-1",
          project_id: "echo-story",
          branch_id: "main",
          chapter_id: "chapter-1",
          status: "active",
          stages: [
            {
              id: "outline",
              name: "outline",
              ordinal: 1,
              status: "submitted",
            },
            {
              id: "draft",
              name: "draft",
              ordinal: 2,
              status: "pending",
            },
          ],
        },
      ],
    });
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "流水线" }));
    expect(await screen.findByText("1. 大纲")).toBeInTheDocument();
    expect(screen.getByText("2. 初稿")).toBeInTheDocument();
    expect(screen.getByText("6. 编辑审阅")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "提交本阶段产物" }));

    await waitFor(() =>
      expect(api.submitPipelineStage).toHaveBeenCalledWith(
        "echo-story",
        "run-1",
        "draft",
        expect.objectContaining({
          actor: "human-editor",
          output: expect.objectContaining({ target_id: "chapter-1" }),
        }),
      ),
    );
  });

  it("runs the real AI stage into an audited preview without auto-submitting", async () => {
    api.loadNarrativeExtensions.mockResolvedValue({
      ...extensions,
      contextPacks: [contextPack],
      pipelineRuns: [pipelineRun],
    });
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "流水线" }));
    await user.click(screen.getByRole("button", { name: "AI 运行当前阶段" }));

    expect(await screen.findByText("AI 生成的候选初稿")).toBeInTheDocument();
    expect(screen.getByText("模型：gpt-narrative")).toBeInTheDocument();
    expect(screen.getByText("Token：345")).toBeInTheDocument();
    expect(screen.getByText("截断审计：已触发")).toBeInTheDocument();
    expect(screen.getByText("省略来源：2")).toBeInTheDocument();
    expect(api.submitPipelineStage).not.toHaveBeenCalled();
    expect(narrative.dispatchNarrativeStage).toHaveBeenCalledWith(
      expect.objectContaining({
        project: expect.objectContaining({ id: "echo-story" }),
        run: { id: "run-1" },
        stage: "draft",
        goal: "醒来",
        contextPack: expect.objectContaining({ id: "context-1" }),
        completedUpstreamStages: [
          expect.objectContaining({
            stage: "outline",
            output: "第一幕从病房醒来开始。",
          }),
        ],
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("submits an AI candidate only after the explicit human action", async () => {
    api.loadNarrativeExtensions.mockResolvedValue({
      ...extensions,
      contextPacks: [contextPack],
      pipelineRuns: [pipelineRun],
    });
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "流水线" }));
    await user.click(screen.getByRole("button", { name: "AI 运行当前阶段" }));
    await screen.findByText("AI 生成的候选初稿");
    expect(api.submitPipelineStage).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "提交 AI 候选产物" }));

    await waitFor(() =>
      expect(api.submitPipelineStage).toHaveBeenCalledWith(
        "echo-story",
        "run-1",
        "draft",
        expect.objectContaining({
          actor: "human-editor",
          output: "AI 生成的候选初稿",
        }),
      ),
    );
  });

  it("keeps a failed AI stage visible and does not submit it", async () => {
    api.loadNarrativeExtensions.mockResolvedValue({
      ...extensions,
      contextPacks: [contextPack],
      pipelineRuns: [pipelineRun],
    });
    narrative.dispatchNarrativeStage.mockRejectedValue(
      new Error("模型暂时不可用"),
    );
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "流水线" }));
    await user.click(screen.getByRole("button", { name: "AI 运行当前阶段" }));

    expect(
      await screen.findByText("AI 阶段运行失败：模型暂时不可用"),
    ).toBeInTheDocument();
    expect(api.submitPipelineStage).not.toHaveBeenCalled();
  });

  it("turns a missing-model backend error into a user-facing setup hint", async () => {
    api.loadNarrativeExtensions.mockResolvedValue({
      ...extensions,
      contextPacks: [contextPack],
      pipelineRuns: [pipelineRun],
    });
    narrative.dispatchNarrativeStage.mockRejectedValue(
      new Error(
        "Narrative outline agent failed (400): RuntimeError: no LLM model configured — add one to data/custom_models.json (or set ANTHROPIC_API_KEY)",
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "流水线" }));
    await user.click(screen.getByRole("button", { name: "AI 运行当前阶段" }));

    expect(
      await screen.findByText(
        "AI 阶段运行失败：尚未配置可用模型，请先在设置中添加模型后再运行。",
      ),
    ).toBeInTheDocument();
    expect(api.submitPipelineStage).not.toHaveBeenCalled();
  });

  it("cancels an in-flight AI stage without creating a candidate or submission", async () => {
    api.loadNarrativeExtensions.mockResolvedValue({
      ...extensions,
      contextPacks: [contextPack],
      pipelineRuns: [pipelineRun],
    });
    narrative.dispatchNarrativeStage.mockImplementation(
      ({ signal }: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () => {
            reject(Object.assign(new Error("cancelled"), { code: "aborted" }));
          });
        }),
    );
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "流水线" }));
    await user.click(screen.getByRole("button", { name: "AI 运行当前阶段" }));
    await user.click(
      await screen.findByRole("button", { name: "取消 AI 运行" }),
    );

    expect(
      await screen.findByText("AI 运行已取消，未产生或提交任何内容。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("AI 候选预览")).not.toBeInTheDocument();
    expect(api.submitPipelineStage).not.toHaveBeenCalled();
  });

  it("does not create a review record until its confirmation is accepted", async () => {
    api.createReviewRequest.mockResolvedValue({
      id: "review-new",
      project_id: "echo-story",
      target_type: "chapter",
      target_id: "chapter-1",
      revision: 1,
      title: "第一章 · 正典审核",
      status: "open",
      quorum_required: 2,
      quorum_received: 0,
      blockers: [],
      blocking: false,
      approval_ratio: 0,
      votes: [],
    });
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    await screen.findByDisplayValue("第一章");
    await user.click(screen.getByRole("button", { name: "正典" }));
    await user.click(screen.getByRole("button", { name: "提交正典审核" }));

    expect(api.createReviewRequest).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "确认提交正典审核" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "确认提交审核" }));

    await waitFor(() =>
      expect(api.createReviewRequest).toHaveBeenCalledWith(
        "echo-story",
        expect.objectContaining({
          target_type: "chapter",
          target_id: "chapter-1",
        }),
      ),
    );
  });

  it("shows a usable project creation flow when storage is empty", async () => {
    api.listNarrativeProjects.mockResolvedValue([]);
    const user = userEvent.setup();
    renderWithProviders(<NarrativePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/narrative",
    });

    expect(await screen.findByText("建立第一个叙事项目")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "新建项目" })[0]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("项目名称")).toBeInTheDocument();
    expect(screen.getByText("创作语言")).toBeInTheDocument();
  });
});
