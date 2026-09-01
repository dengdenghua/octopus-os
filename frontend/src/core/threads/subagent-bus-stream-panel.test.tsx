import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import type { LiveToolEvent } from "@/components/workspace/live-tool-timeline";
import { SubAgentBusStreamPanel } from "./subagent-bus-stream-panel";

vi.mock("./use-subagent-bus-stream", () => ({
  useSubAgentBusStream: vi.fn(),
}));

import { useSubAgentBusStream } from "./use-subagent-bus-stream";

const mockedUse = vi.mocked(useSubAgentBusStream);

function spawnedEvent(codename: string, role: string): LiveToolEvent {
  return {
    id: `started:${codename}`,
    name: codename,
    status: "running",
    startedAt: 1720000000000,
    iteration: 0,
    lifecycle: "spawned",
    subagentCodename: codename,
    subAgentRole: role,
  };
}

function toolEnd(tool: string, role: string): LiveToolEvent {
  return {
    id: `end:${tool}`,
    name: tool,
    status: "done",
    startedAt: 1720000001000,
    finishedAt: 1720000002000,
    durationMs: 1000,
    iteration: 1,
    subAgentRole: role,
  };
}

function renderPanel(rootThreadId: string | null) {
  return renderWithProviders(
    <SubAgentBusStreamPanel rootThreadId={rootThreadId} />,
    { locale: "zh-CN" },
  );
}

describe("SubAgentBusStreamPanel", () => {
  beforeEach(() => {
    mockedUse.mockReset();
    mockedUse.mockReturnValue({ events: [], status: "idle", lastSeq: 0 });
  });

  it("shows the empty state while idle", () => {
    renderPanel("root-1");
    expect(screen.getByText("暂无子智能体活动")).toBeInTheDocument();
  });

  it("renders bus events through the timeline with a live status badge", () => {
    mockedUse.mockReturnValue({
      events: [
        spawnedEvent("exp-1", "researcher"),
        toolEnd("web_search", "researcher"),
      ],
      status: "live",
      lastSeq: 2,
    });
    renderPanel("root-1");
    expect(screen.getByText("2 条事件")).toBeInTheDocument();
    expect(screen.getAllByText("exp-1").length).toBeGreaterThan(0);
  });

  it("renders each sub-agent as its own grouped lane", () => {
    mockedUse.mockReturnValue({
      events: [
        spawnedEvent("Spark-A", "researcher"),
        spawnedEvent("Spark-B", "researcher"),
      ],
      status: "live",
      lastSeq: 2,
    });
    renderPanel("root-1");
    expect(screen.getAllByText("Spark-A").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Spark-B").length).toBeGreaterThan(0);
  });

  it("keeps same-role children as distinct lanes via their codename", () => {
    // Two parallel researchers share the role but each has its own codename —
    // they must render as two independent threads, not one merged lane.
    const a = { ...spawnedEvent("Spark-9f2", "researcher"), id: "started:a" };
    const b = { ...spawnedEvent("Spark-3aa", "researcher"), id: "started:b" };
    mockedUse.mockReturnValue({
      events: [a, b],
      status: "live",
      lastSeq: 2,
    });
    renderPanel("root-1");
    expect(screen.getAllByText("Spark-9f2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Spark-3aa").length).toBeGreaterThan(0);
  });

  it("renders the error state when the stream is failing", () => {
    mockedUse.mockReturnValue({ events: [], status: "error", lastSeq: 0 });
    renderPanel("root-1");
    expect(screen.getAllByText(/流异常/).length).toBeGreaterThan(0);
  });

  it("renders collected events while reconnecting", () => {
    mockedUse.mockReturnValue({
      events: [spawnedEvent("exp-2", "explorer")],
      status: "connecting",
      lastSeq: 1,
    });
    renderPanel("root-1");
    expect(screen.getAllByText("exp-2").length).toBeGreaterThan(0);
  });
});
