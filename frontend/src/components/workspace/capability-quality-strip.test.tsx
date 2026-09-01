import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { CapabilityQualityStrip } from "./capability-quality-strip";

const api = vi.hoisted(() => ({
  E2E_SURPASS_TARGET_SCORE: 95,
  fetchAgentCompetitorScorecard: vi.fn(),
  fetchBrowserDesktopQuality: vi.fn(),
}));

vi.mock("@/core/agent-trace/api", () => api);

describe("<CapabilityQualityStrip />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchAgentCompetitorScorecard.mockResolvedValue({
      schema: "echo.agent_competitor_scorecard.v1",
      target_score: 95,
      competitors: ["codex", "echo"],
      overall: { codex: 97, echo: 97 },
      ranking: [],
      verdict: "competitive",
      evidence_adjusted_overall: { codex: 97, echo: 97 },
      evidence_adjusted_ranking: [],
      evidence_adjusted_verdict: "competitive",
      evidence_layers: {
        schema: "echo.agent_score_evidence_layers.v1",
        architecture: {
          status: "estimated",
          echo_score: 97,
          codex_score: 97,
          source: "current_combined_architecture_baseline",
        },
        static_certification: {
          status: "certified",
          ready: true,
          passed: 17,
          total: 17,
        },
        behavioral_head_to_head: {
          status: "not_certified",
          ready: false,
          verdict: "missing_behavioral_evidence",
          echo_pass_pow_k: 0,
          codex_pass_pow_k: 0,
        },
      },
      dimensions: [],
      echo_below_target: [],
      echo_strengths: [],
      next_focus: [],
      ecosystem_readiness: {
        schema: "echo.ecosystem_readiness.v1",
        score: 0.94,
        passed: 8,
        total: 8,
        missing_count: 0,
        topics: [],
        next_actions: [],
      },
    });
    api.fetchBrowserDesktopQuality.mockResolvedValue({
      schema: "echo.browser_desktop_quality.v1",
      score: 1,
      passed: 5,
      total: 5,
      ready: true,
      checks: [],
      replay_trends: {
        schema: "echo.browser_desktop_replay_trends.v1",
        total: 36,
        pending_count: 0,
        reviewed_count: 36,
        promoted_count: 2,
        rejected_count: 34,
        review_rate: 1,
        stale_source_artifact_count: 0,
        by_status: {},
        by_candidate_kind: {},
        latest: [],
        next_actions: [],
      },
      next_actions: [],
    });
  });

  it("shows shared scorecard and browser quality on non-code surfaces", async () => {
    renderWithProviders(
      <CapabilityQualityStrip surface="browser" includeBrowserDesktop />,
    );

    expect(await screen.findByText("浏览器与桌面能力")).toBeInTheDocument();
    expect(api.fetchAgentCompetitorScorecard).toHaveBeenCalledWith(95);
    expect(await screen.findByText("Overall")).toBeInTheDocument();
    expect(await screen.findByText("Evidence")).toBeInTheDocument();
    expect(await screen.findByText("Behavior")).toBeInTheDocument();
    expect(await screen.findByText("pending")).toBeInTheDocument();
    expect(await screen.findByText("Eco")).toBeInTheDocument();
    expect(await screen.findByText("Browser")).toBeInTheDocument();
    expect(await screen.findByText("100 · stale 0")).toBeInTheDocument();
  });

  it("keeps the surface usable when quality APIs fail", async () => {
    api.fetchAgentCompetitorScorecard.mockRejectedValueOnce(
      new Error("scorecard unavailable"),
    );

    renderWithProviders(<CapabilityQualityStrip surface="knowledge" />);

    expect(await screen.findByText("知识与记忆能力")).toBeInTheDocument();
    expect(await screen.findByText("暂不可用")).toBeInTheDocument();
  });
});
