import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { appendStreamTelemetry } from "@/core/realtime";
import { renderWithProviders } from "@/test/harness";

import { StreamTelemetryPanel } from "./stream-telemetry-panel";

describe("<StreamTelemetryPanel />", () => {
  beforeEach(() => localStorage.clear());

  it("renders saved metrics and clears them", async () => {
    appendStreamTelemetry({
      id: "thread-1:turn-1",
      threadId: "thread-1",
      turnId: "turn-1",
      startedAt: 1_000,
      completedAt: 5_000,
      durationMs: 4_000,
      ttftMs: 500,
      maxDeltaGapMs: 800,
      stalledAtEnd: false,
      outcome: "completed",
    });

    renderWithProviders(<StreamTelemetryPanel />);

    expect(screen.getByText("Streaming response metrics")).toBeInTheDocument();
    expect(screen.getAllByText("500 ms")).toHaveLength(3);
    expect(screen.getAllByText("800 ms")).toHaveLength(2);
    expect(screen.getByText("4.0 s")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Clear streaming metrics" }),
    );
    expect(
      screen.getByText(
        "Complete a realtime turn to see first-token latency and pauses.",
      ),
    ).toBeInTheDocument();
  });
});
