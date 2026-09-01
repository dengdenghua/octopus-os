import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test" }),
  jsonAuthHeaders: () => ({
    Authorization: "Bearer test",
    "Content-Type": "application/json",
  }),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "https://narrative.example.test",
}));

import {
  commitCanonReview,
  createContextPack,
  getNarrativeStatus,
  listContextPacks,
  listPipelineRuns,
  listReviewRequests,
} from "./api";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("narrative v2 API", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("discovers the plugin-owned MCP and packaged skills", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        status: "ready",
        version: "0.3.0",
        capabilities: ["candidate_only_mcp", "packaged_skill_assets"],
        mcp: {
          enabled: true,
          endpoint: "/api/plugins/narrative-studio/mcp",
          transport: "json-rpc-http",
          auth: "host_inherited",
          tool_policy: "candidate_only_allowlist",
          tools: ["narrative_list_projects"],
        },
        packaged_skills: [
          { name: "narrative-authoring", description: "创建候选稿" },
        ],
      }),
    );

    await expect(getNarrativeStatus()).resolves.toMatchObject({
      ready: true,
      mcp: {
        enabled: true,
        endpoint: "/api/plugins/narrative-studio/mcp",
        auth: "host_inherited",
        tool_policy: "candidate_only_allowlist",
        tools: ["narrative_list_projects"],
      },
      packaged_skills: [
        { name: "narrative-authoring", description: "创建候选稿" },
      ],
    });
  });

  it("posts a context request to the project collection", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        context_pack: {
          id: "pack-1",
          project_id: "story/1",
          max_chars: 48000,
          max_items: 48,
          total_chars: 4000,
          sources: [],
        },
      }),
    );

    await createContextPack("story/1", {
      chapter_id: "chapter/1",
      token_budget: 12000,
      max_chars: 48000,
      max_items: 48,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "https://narrative.example.test/api/plugins/narrative-studio/projects/story%2F1/context-packs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          branch_id: undefined,
          target_chapter_id: "chapter/1",
          label: "章节创作上下文",
          max_chars: 48000,
          max_items: 48,
        }),
      }),
    );
  });

  it("normalizes native character-budget context sources into token estimates", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        context_packs: [
          {
            id: "pack-1",
            max_chars: 8000,
            max_items: 12,
            total_chars: 1200,
            sources: [
              {
                ref: "entity:hero",
                kind: "entity",
                title: "主角",
                content: "人物摘要",
                char_count: 400,
                truncated: true,
              },
            ],
          },
        ],
      }),
    );

    const [pack] = await listContextPacks("story");

    expect(pack).toMatchObject({
      token_budget: 2000,
      token_count: 300,
      max_chars: 8000,
      total_chars: 1200,
    });
    expect(pack?.sources[0]).toMatchObject({
      reference: "entity:hero",
      excerpt: "人物摘要",
      tokens: 100,
      char_count: 400,
      truncated: true,
    });
  });

  it("accepts the native six-stage name and submitted status", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        runs: [
          {
            id: "run-1",
            stages: [
              { name: "outline", ordinal: 1, status: "submitted" },
              { name: "draft", ordinal: 2, status: "pending" },
            ],
          },
        ],
      }),
    );

    const [run] = await listPipelineRuns("story");

    expect(run?.stages).toEqual([
      expect.objectContaining({
        id: "outline",
        ordinal: 1,
        status: "submitted",
      }),
      expect.objectContaining({ id: "draft", ordinal: 2, status: "pending" }),
    ]);
  });

  it("normalizes native review blocking and never commits without confirm=true", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        reviews: [
          {
            id: "review-1",
            target_type: "chapter",
            target_id: "chapter-1",
            revision: 3,
            blocking: true,
            status: "open",
            resolution: null,
          },
        ],
      }),
    );

    const [review] = await listReviewRequests("story");
    expect(review).toMatchObject({
      revision: 3,
      blocking: true,
      blockers: ["存在尚未解决的阻塞项"],
      status: "open",
    });

    await expect(
      commitCanonReview("story", "review-1", {
        actor: "editor",
        rationale: "approved",
        confirm: false,
      } as never),
    ).rejects.toThrow("必须经过人工确认");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
