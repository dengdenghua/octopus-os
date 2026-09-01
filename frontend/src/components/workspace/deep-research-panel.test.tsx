import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import type * as ParallelApi from "@/core/parallel-agents/api";
import type * as ResearchApi from "@/core/research/api";
import type { BatchStreamCallbacks } from "@/core/parallel-agents/api";

import {
  DeepResearchPanel,
  routeDecisionFromPayload,
} from "./deep-research-panel";

const fetchBatchMock = vi.fn();
const streamBatchMock = vi.fn();
const fetchDeepResearchJobMock = vi.fn();
let streamCallbacks: BatchStreamCallbacks | null = null;

vi.mock("@/core/parallel-agents/api", async () => {
  const actual = await vi.importActual<typeof ParallelApi>(
    "@/core/parallel-agents/api",
  );
  return {
    ...actual,
    fetchBatch: (...args: unknown[]) => fetchBatchMock(...args),
    streamBatch: (...args: unknown[]) => streamBatchMock(...args),
    cancelTask: vi.fn(),
  };
});

vi.mock("@/core/research/api", async () => {
  const actual = await vi.importActual<typeof ResearchApi>(
    "@/core/research/api",
  );
  return {
    ...actual,
    fetchDeepResearchJob: (...args: unknown[]) =>
      fetchDeepResearchJobMock(...args),
  };
});

describe("<DeepResearchPanel /> route decisions", () => {
  beforeEach(() => {
    fetchBatchMock.mockReset();
    streamBatchMock.mockReset();
    fetchDeepResearchJobMock.mockReset();
    streamCallbacks = null;
  });

  it("renders deep research subagent route decisions from batch events", async () => {
    fetchBatchMock.mockResolvedValue({
      batch_id: "batch_1",
      status: "failed",
      total_tasks: 1,
      completed_tasks: 0,
      failed_tasks: 1,
      cancelled_tasks: 0,
      created_at: "2026-06-19T00:00:00Z",
      completed_at: "2026-06-19T00:00:01Z",
      results: [
        {
          task_id: "step_1",
          batch_id: "batch_1",
          description: "Compare vendors",
          status: "failed",
          result: null,
          error: "subagent_route_blocked: retired role",
          started_at: "2026-06-19T00:00:00Z",
          completed_at: "2026-06-19T00:00:00Z",
          duration_seconds: 0,
          subagent_name: "virtual-research-competitor-analyst",
          work_contract: null,
        },
      ],
      aggregated_content: null,
      aggregation_strategy: "research_synthesis",
      conflicts: [],
      event_log: [
        {
          type: "task_update",
          batch_id: "batch_1",
          task_id: "step_1",
          lane: "agent",
          status: "failed",
          subagent_name: "virtual-research-competitor-analyst",
          phase: "subagent_route_blocked",
          payload: {
            subagent_route_decision: {
              schema: "echo.subagent_route_decision.v1",
              role: "virtual-research-competitor-analyst",
              action: "block",
              reason:
                "virtual-research-competitor-analyst is a retire_candidate and this task is high-risk",
              risk_level: "high",
              verdict: "retire_candidate",
              score: 0.1,
              confidence: 0.6,
              evidence_item_ids: ["review_1"],
            },
          },
        },
      ],
    });
    streamBatchMock.mockReturnValue(() => undefined);

    renderWithProviders(<DeepResearchPanel job={researchJob()} />);

    await waitFor(() => expect(fetchBatchMock).toHaveBeenCalledWith("batch_1"));
    expect(await screen.findByText("Route blocked")).toBeInTheDocument();
    expect(
      screen.getByText(
        "virtual-research-competitor-analyst is a retire_candidate and this task is high-risk",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("retire_candidate · high")).toBeInTheDocument();
  });

  it("parses only the route-decision payload schema", () => {
    expect(routeDecisionFromPayload({})).toBeNull();
    expect(
      routeDecisionFromPayload({
        subagent_route_decision: {
          schema: "echo.subagent_route_decision.v1",
          role: "researcher",
          action: "allow_with_warning",
          reason: "under watch",
          risk_level: "low",
          verdict: "watch",
          score: 0.55,
          confidence: 0.4,
          evidence_item_ids: ["item_1"],
        },
      })?.action,
    ).toBe("allow_with_warning");
  });

  it("renders persisted route decisions when event logs are unavailable", async () => {
    fetchBatchMock.mockResolvedValue({
      batch_id: "batch_1",
      status: "failed",
      total_tasks: 1,
      completed_tasks: 0,
      failed_tasks: 1,
      cancelled_tasks: 0,
      created_at: "2026-06-19T00:00:00Z",
      completed_at: "2026-06-19T00:00:01Z",
      results: [],
      aggregated_content: null,
      aggregation_strategy: "research_synthesis",
      conflicts: [],
      event_log: [],
    });
    streamBatchMock.mockReturnValue(() => undefined);

    renderWithProviders(
      <DeepResearchPanel
        job={researchJob({
          route_decision: {
            schema: "echo.subagent_route_decision.v1",
            role: "virtual-research-competitor-analyst",
            action: "block",
            reason: "persisted route decision",
            risk_level: "high",
            verdict: "retire_candidate",
            score: 0.1,
            confidence: 0.6,
            evidence_item_ids: [],
          },
        })}
      />,
    );

    expect(await screen.findByText("Route blocked")).toBeInTheDocument();
    expect(screen.getByText("persisted route decision")).toBeInTheDocument();
  });

  it("renders cancelled batch completion as an error state, not a green done event", async () => {
    fetchBatchMock.mockResolvedValue({
      batch_id: "batch_1",
      status: "cancelled",
      total_tasks: 2,
      completed_tasks: 1,
      failed_tasks: 0,
      cancelled_tasks: 1,
      created_at: "2026-06-19T00:00:00Z",
      completed_at: "2026-06-19T00:00:02Z",
      results: [
        {
          task_id: "step_1",
          batch_id: "batch_1",
          description: "Compare vendors",
          status: "cancelled",
          result: null,
          error: "user_cancelled",
          started_at: null,
          completed_at: "2026-06-19T00:00:02Z",
          duration_seconds: null,
          subagent_name: "virtual-research-competitor-analyst",
          work_contract: null,
        },
      ],
      aggregated_content: null,
      aggregation_strategy: "research_synthesis",
      conflicts: [],
      event_log: [],
    });
    fetchDeepResearchJobMock.mockResolvedValue(null);
    streamBatchMock.mockImplementation((_batchId, callbacks) => {
      streamCallbacks = callbacks;
      return () => undefined;
    });

    renderWithProviders(<DeepResearchPanel job={researchJob()} />);
    await waitFor(() => expect(fetchBatchMock).toHaveBeenCalledWith("batch_1"));

    streamCallbacks?.onBatchComplete?.({
      type: "batch_complete",
      batch_id: "batch_1",
      status: "cancelled",
      payload: {
        total_tasks: 2,
        completed_tasks: 1,
        failed_tasks: 0,
        cancelled_tasks: 1,
      },
    });

    expect(await screen.findByText("Batch cancelled")).toBeInTheDocument();
    expect(screen.getByText(/1\/2 completed/)).toBeInTheDocument();
    expect(screen.getByText(/0 failed · 1 cancelled/)).toBeInTheDocument();
    expect(screen.getByText("user_cancelled")).toBeInTheDocument();
  });
});

function researchJob(
  stepPatch: Partial<ResearchApi.ResearchStep> = {},
): ResearchApi.ResearchJob {
  return {
    job_id: "research_1",
    thread_id: "thread_1",
    lead_agent_name: "general",
    topic: "NAS market research",
    status: "running",
    depth: "standard",
    locale: "zh-CN",
    created_at: "2026-06-19T00:00:00Z",
    materials: [],
    sources: [],
    evidence: [],
    prefetch_logs: [],
    roles: [
      {
        id: "competitor-analyst",
        name: "Competitor Analyst",
        subagent_name: "virtual-research-competitor-analyst",
        focus: "Competitor positioning",
        deliverable: "Competitor findings",
        search_angles: [],
      },
    ],
    steps: [
      {
        id: "step_1",
        title: "Competitor Analysis",
        role_id: "competitor-analyst",
        status: "pending",
        source_ids: [],
        expected_searches: 4,
        prompt: "Compare vendors",
        ...stepPatch,
      },
    ],
    max_searches: 120,
    dispatch_batch_id: "batch_1",
    final_report_format: "markdown",
    final_report: null,
    completed_at: null,
    memory_entry: null,
    memory_written_at: null,
    memory_path: null,
  };
}
