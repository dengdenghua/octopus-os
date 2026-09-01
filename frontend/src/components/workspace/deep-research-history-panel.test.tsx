import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import type * as ResearchApi from "@/core/research/api";

import { DeepResearchHistoryPanel } from "./deep-research-history-panel";

const listDeepResearchJobsMock = vi.fn();

vi.mock("@/core/research/api", async () => {
  const actual = await vi.importActual<typeof ResearchApi>(
    "@/core/research/api",
  );
  return {
    ...actual,
    listDeepResearchJobs: () => listDeepResearchJobsMock(),
  };
});

describe("<DeepResearchHistoryPanel />", () => {
  it("loads history and selects a saved research job", async () => {
    const job = {
      job_id: "research_1",
      thread_id: "t1",
      lead_agent_name: "general",
      topic: "NAS market research",
      status: "completed",
      depth: "deep",
      locale: "zh-CN",
      created_at: "2026-04-27T10:00:00.000Z",
      completed_at: "2026-04-27T10:05:00.000Z",
      materials: [
        {
          id: "m1",
          kind: "url",
          title: "Synology",
          url: "https://www.synology.com/",
        },
      ],
      sources: [],
      evidence: [
        {
          id: "e1",
          title: "Synology",
          quote_or_summary: "",
          claim: "",
          stance: "context",
          confidence: 0.6,
        },
      ],
      roles: [
        {
          id: "r1",
          name: "Role",
          subagent_name: "virtual-research-test",
          focus: "",
          deliverable: "",
          search_angles: [],
        },
      ],
      steps: [],
      max_searches: 100,
      final_report_format: "markdown",
      final_report: "# Report",
      dispatch_batch_id: "batch_1",
      memory_entry: null,
      memory_written_at: null,
      memory_path: null,
    };
    listDeepResearchJobsMock.mockResolvedValue([job]);
    const onSelect = vi.fn();

    renderWithProviders(
      <DeepResearchHistoryPanel onSelect={onSelect} activeJobId={null} />,
    );

    await screen.findByText("NAS market research");
    expect(screen.getByText("1 saved runs")).toBeInTheDocument();
    expect(screen.getByText("materials")).toBeInTheDocument();
    expect(screen.getByText("evidence")).toBeInTheDocument();
    expect(screen.getByText("roles")).toBeInTheDocument();

    fireEvent.click(screen.getByText("NAS market research"));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(job));
  });
});
