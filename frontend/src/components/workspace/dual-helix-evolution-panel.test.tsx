import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { getCoderUpstreamUpdate } from "@/core/coder/api";
import {
  getAgentBenchmarkReport,
  getCodexGapReport,
  getDualHelixEvidence,
  getDualHelixShadowStatus,
  getEvolutionCandidates,
} from "@/core/evolution/api";
import { renderWithProviders } from "@/test/harness";

import { DualHelixEvolutionPanel } from "./dual-helix-evolution-panel";

vi.mock("@/core/evolution/api", () => ({
  getCodexGapReport: vi.fn(async () => ({
    ok: true,
    schema: "echo.codex_gap_report.v1",
    parity_score: 0.9,
    advantage_score: 0.8,
    combined_score: 0.85,
    verdict: "differentiated",
    capabilities: [
      {
        id: "loop",
        area: "codex_parity",
        title: "Execution loop",
        why: "baseline",
        score: 0.7,
        target_score: 0.9,
        status: "gap",
        next_actions: ["Promote verified repair policy"],
      },
    ],
  })),
  getAgentBenchmarkReport: vi.fn(async () => ({
    ok: true,
    schema: "echo.agent_benchmark.v1",
    score: 0.75,
    passed: 9,
    total: 12,
    ready: false,
    cases: [],
  })),
  getDualHelixEvidence: vi.fn(async () => ({
    ok: true,
    schema: "echo.dual_helix_evidence.v1",
    paired_count: 3,
    unpaired_count: 1,
    echo_wins: 2,
    codex_wins: 1,
    ties: 0,
    echo_win_rate: 0.667,
    evidence_quality: "controlled_same_task",
    controlled: {
      ok: true,
      schema: "echo.evolution.pair_evidence.v1",
      generated_at: "2026-08-25T00:00:00Z",
      trial_count: 6,
      paired_count: 3,
      pairable_key_count: 3,
      unpaired_key_count: 0,
      echo_wins: 2,
      codex_wins: 1,
      ties: 0,
      excluded: {
        infrastructure_failed: 0,
        incomplete: 0,
        hard_gate_failed: 0,
        duplicate_engine_trial: 0,
      },
      primary_metric: "quality",
      pairs: [],
    },
    strands: {
      echo: { samples: 4, successes: 3, success_rate: 0.75 },
      codex: { samples: 3, successes: 2, success_rate: 0.667 },
    },
    pairs: [],
  })),
  getDualHelixShadowStatus: vi.fn(async () => ({
    ok: true,
    enabled: false,
    isolation: "bounded_snapshot_read_only",
    runs: [],
  })),
  setDualHelixShadowEnabled: vi.fn(async (enabled: boolean) => ({
    ok: true,
    enabled,
    runs: [],
  })),
  registerCandidateCanary: vi.fn(async () => ({})),
  rollbackEvolutionCandidate: vi.fn(async () => ({})),
  getEvolutionCandidates: vi.fn(async () => ({
    ok: true,
    schema: "echo.evolution.candidate_list.v1",
    total: 3,
    by_status: { validated: 1, canary: 1, shadow: 1 },
    by_gene_type: { prompt: 1, skill: 1, routing: 1 },
    candidates: [
      {
        candidate_id: "cand_prompt",
        gene_type: "prompt",
        scope: "planner.prompt:general",
        proposer: "gepa",
        status: "validated",
        role_id: "general",
        task_domain: "planner",
        risk_level: "medium",
        hard_gate_passed: true,
        hard_gate_results: { native_replay: true },
        metric_vector: { quality: 0.8 },
        experiment_ids: [],
        metadata: {},
        deployment_key: "cand_prompt:general:planner:default",
        runtime_consumer_ready: true,
        created_at: "2026-08-25T00:00:00Z",
        updated_at: "2026-08-25T00:00:00Z",
      },
      {
        candidate_id: "cand_skill",
        gene_type: "skill",
        scope: "skill.governed-flow",
        proposer: "skill_forge",
        status: "canary",
        role_id: "general",
        task_domain: "workflow",
        risk_level: "medium",
        hard_gate_passed: true,
        hard_gate_results: { replay: true },
        metric_vector: { quality: 0.9 },
        experiment_ids: ["exp-1"],
        metadata: {},
        deployment_key: "cand_skill:general:workflow:default",
        runtime_consumer_ready: true,
        created_at: "2026-08-25T00:00:00Z",
        updated_at: "2026-08-25T00:00:00Z",
        canary: {
          skill_name: "candidate.cand_skill.test",
          phase: "canary_25",
          sample_count: 12,
          success_count: 11,
          failure_count: 1,
          current_rate: 0.916,
        },
      },
      {
        candidate_id: "cand_routing",
        gene_type: "routing",
        scope: "router.coding",
        proposer: "experiment",
        status: "shadow",
        role_id: "coder",
        task_domain: "coding",
        risk_level: "medium",
        hard_gate_passed: true,
        hard_gate_results: { replay: true },
        metric_vector: { quality: 0.85 },
        experiment_ids: ["exp-2"],
        metadata: {},
        deployment_key: "cand_routing:coder:coding:default",
        runtime_consumer_ready: false,
        created_at: "2026-08-25T00:00:00Z",
        updated_at: "2026-08-25T00:00:00Z",
      },
    ],
  })),
}));

vi.mock("@/core/coder/api", () => ({
  coderUpstreamUpdateQueryKey: ["coder", "upstream"],
  getCoderUpstreamUpdate: vi.fn(async () => ({
    current_version: "0.149.0",
    latest_version: "0.150.0",
    update_available: true,
  })),
}));

vi.mock("@/core/evolution/hooks", () => ({
  useLedger: () => ({
    error: null,
    isFetching: false,
    refetch: vi.fn(async () => ({})),
    data: {
      total: 3,
      records: [
        {
          id: "one",
          description: "codex verifier failure",
          proposer: "realtime_cerebrum",
          status: "proposed",
        },
        {
          id: "two",
          description:
            'react_failed:{"additionalDetails":null,"message":"unexpected internal failure"}',
          proposer: "codex",
          status: "proposed",
        },
        {
          id: "three",
          description: "turn_success | goal=整理本周反馈",
          proposer: "echo",
          status: "accepted",
        },
      ],
    },
  }),
  useCanary: () => ({ data: { active_count: 1, canaries: [] } }),
}));

describe("DualHelixEvolutionPanel", () => {
  it("renders both engine strands and real evolution evidence", async () => {
    renderWithProviders(<DualHelixEvolutionPanel />, { locale: "zh-CN" });

    expect(
      await screen.findByRole("heading", { name: "双引擎螺旋进化" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Echo Native")).toBeInTheDocument();
    expect(screen.getByText("OpenAI Codex")).toBeInTheDocument();
    expect(await screen.findByText("9/12")).toBeInTheDocument();
    expect(
      await screen.findByText(/3 对受控任务已完成同题实验/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Promote verified repair policy"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Codex v0\.150\.0 待审核/)).toBeInTheDocument();
    expect(screen.getByText("保护模式已关闭")).toBeInTheDocument();
    expect(
      screen.getByText(/开启开关本身不会调用模型或产生费用/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("任务执行失败 · 内部错误详情已收起"),
    ).toBeInTheDocument();
    expect(screen.getByText("任务完成 · 整理本周反馈")).toBeInTheDocument();
    expect(screen.queryByText(/additionalDetails/)).not.toBeInTheDocument();
  });

  it("separates real comparisons, shadow reviews, and ledger evidence", async () => {
    renderWithProviders(<DualHelixEvolutionPanel view="evidence" />, {
      locale: "zh-CN",
    });

    expect(
      await screen.findByRole("heading", { name: "双引擎实验证据" }),
    ).toBeInTheDocument();
    expect(screen.getByText("同任务双引擎对照")).toBeInTheDocument();
    expect(screen.getByText("影子复核记录")).toBeInTheDocument();
    expect(screen.getByText("进化账本")).toBeInTheDocument();
  });

  it("does not present unavailable overview evidence as healthy zero values", async () => {
    const failure = new TypeError("Failed to fetch");
    vi.mocked(getCodexGapReport).mockRejectedValueOnce(failure);
    vi.mocked(getAgentBenchmarkReport).mockRejectedValueOnce(failure);
    vi.mocked(getDualHelixEvidence).mockRejectedValueOnce(failure);
    vi.mocked(getDualHelixShadowStatus).mockRejectedValueOnce(failure);
    vi.mocked(getEvolutionCandidates).mockRejectedValueOnce(failure);
    vi.mocked(getCoderUpstreamUpdate).mockRejectedValueOnce(failure);

    renderWithProviders(<DualHelixEvolutionPanel />, { locale: "zh-CN" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "部分进化证据暂时无法加载",
    );
    expect(screen.getByText("保护状态暂不可用")).toBeInTheDocument();
    expect(
      screen.getByText("能力基线暂时无法加载，恢复后将继续生成候选。"),
    ).toBeInTheDocument();
    expect(screen.getByText(/受控实验数据暂不可用/)).toBeInTheDocument();
    expect(screen.getByText(/Codex 上游状态暂不可用/)).toBeInTheDocument();
    expect(
      screen.queryByText(/当前能力基线没有未达标项/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Codex 上游已同步")).not.toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
  });

  it("renders typed candidates as a flat control list", async () => {
    renderWithProviders(<DualHelixEvolutionPanel view="candidates" />, {
      locale: "zh-CN",
    });

    expect(
      await screen.findByText("planner.prompt:general"),
    ).toBeInTheDocument();
    expect(screen.getByText(/已验证/)).toBeInTheDocument();
    expect(screen.getAllByText(/门禁通过/)).toHaveLength(3);
    expect(screen.getByText(/25% · 12 次 · 92%/)).toBeInTheDocument();
    expect(screen.getByText("待接入运行时")).toBeInTheDocument();
  });

  it("separates a candidate load failure from the empty state and offers retry", async () => {
    const user = userEvent.setup();
    vi.mocked(getEvolutionCandidates).mockRejectedValueOnce(
      new TypeError("Failed to fetch"),
    );

    renderWithProviders(<DualHelixEvolutionPanel view="candidates" />, {
      locale: "zh-CN",
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "候选谱系暂时无法加载",
    );
    expect(screen.queryByText(/还没有候选/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(
      await screen.findByText("planner.prompt:general"),
    ).toBeInTheDocument();
  });
});
