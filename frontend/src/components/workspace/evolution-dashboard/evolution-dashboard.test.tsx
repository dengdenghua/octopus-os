import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import EvolutionDashboard from "./index";

const queries = vi.hoisted(() => ({
  overview: { data: null, isLoading: false, error: null, refetch: vi.fn() },
  learning: { data: null, isLoading: false, error: null, refetch: vi.fn() },
  skills: { data: null, isLoading: false, error: null, refetch: vi.fn() },
  memory: { data: null, isLoading: false, error: null, refetch: vi.fn() },
  recommendations: {
    data: null,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  },
  story: { data: null, isLoading: false, error: null, refetch: vi.fn() },
}));

vi.mock("@/core/evolution/hooks", () => ({
  useEvolutionOverview: () => queries.overview,
  useLearningCurve: () => queries.learning,
  useSkillPerformance: () => queries.skills,
  useMemoryGrowth: () => queries.memory,
  useRecommendations: () => queries.recommendations,
  useEvolutionStory: () => queries.story,
}));

vi.mock("@/components/workspace/gene-lock-badge", () => ({
  GeneLockControlCard: () => null,
}));

describe("EvolutionDashboard states", () => {
  beforeEach(() => {
    for (const query of Object.values(queries)) {
      query.data = null;
      query.isLoading = false;
      query.error = null;
      query.refetch.mockReset();
      query.refetch.mockResolvedValue(undefined);
    }
  });

  it("waits for memory growth instead of rendering partial metrics", () => {
    queries.memory.isLoading = true;
    renderWithProviders(<EvolutionDashboard />, { locale: "zh-CN" });

    expect(screen.getByRole("status")).toHaveTextContent("加载中");
    expect(screen.queryByText("这段时间它进化了什么")).toBeNull();
  });

  it("offers one-click retry for every dashboard source", async () => {
    const user = userEvent.setup();
    queries.overview.error = new Error("offline");
    renderWithProviders(<EvolutionDashboard />, { locale: "zh-CN" });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "连接进化看板 API 失败",
    );
    await user.click(screen.getByRole("button", { name: "重新加载" }));

    for (const query of Object.values(queries)) {
      expect(query.refetch).toHaveBeenCalledOnce();
    }
  });

  it("labels populated trend and skill charts", async () => {
    queries.overview.data = {
      skills: { total: 1, auto_extracted: 1 },
      memory: { total_facts: 2, categories: { rules: 1 } },
      learning_events: 3,
      improvement_score: 0.72,
    };
    queries.learning.data = [
      {
        week: "2026-W28",
        success_rate: 0.75,
        avg_duration_ms: 1200,
        skills_used: 2,
      },
      {
        week: "2026-W29",
        success_rate: 0.9,
        avg_duration_ms: 900,
        skills_used: 4,
      },
    ];
    queries.skills.data = [
      {
        name: "source-check",
        usage_count: 8,
        success_rate: 0.875,
        source: "auto",
      },
    ];
    queries.memory.data = [];
    queries.recommendations.data = [];
    queries.story.data = {
      has_real_change: true,
      observed_task_count: 2,
      durable_change_count: 1,
      rule_count: 0,
      memory_count: 0,
      skill_count: 1,
      changes: [
        {
          kind: "skill",
          title: "source-check",
          content: "Cross-check sources before answering.",
          effect: "Used on future tasks.",
        },
      ],
      observations: [],
    };

    renderWithProviders(<EvolutionDashboard />, { locale: "zh-CN" });

    expect(
      screen.getByRole("heading", {
        name: "这段时间真正学会了 1 件事",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "能力趋势" })).toBeVisible();
    expect(
      screen.getByRole("listitem", { name: "2026-W29: 90%" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", {
        name: "source-check · 成功率",
      }),
    ).toHaveAttribute("aria-valuenow", "88");
  });

  it("opens stage, metric, and recommendation details", async () => {
    const user = userEvent.setup();
    queries.overview.data = {
      skills: { total: 3, auto_extracted: 1 },
      memory: { total_facts: 2, categories: { rules: 1 } },
      learning_events: 5,
      improvement_score: 0.6,
    };
    queries.learning.data = [];
    queries.skills.data = [];
    queries.memory.data = [];
    queries.recommendations.data = [
      {
        title: "运行反思",
        description: "复盘最近任务并提取可以复用的改进规则。",
      },
    ];
    queries.story.data = {
      has_real_change: false,
      observed_task_count: 5,
      durable_change_count: 0,
      rule_count: 0,
      memory_count: 0,
      skill_count: 0,
      changes: [],
      observations: [],
    };

    renderWithProviders(<EvolutionDashboard />, { locale: "zh-CN" });

    const stage = screen.getByRole("button", { name: /观察任务/ });
    await user.click(stage);
    expect(stage).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "观察任务" })).toBeVisible();

    const metric = screen.getByRole("button", { name: /自动形成的技能/ });
    await user.click(metric);
    expect(metric).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("region", { name: "自动形成的技能" }),
    ).toBeVisible();

    const recommendation = screen.getByRole("button", { name: /运行反思/ });
    await user.click(recommendation);
    expect(recommendation).toHaveAttribute("aria-expanded", "true");
  });
});
