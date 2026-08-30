import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ExecutionChecklistPanel } from "./execution-checklist-panel";
import type { LiveToolEvent } from "./live-tool-timeline";

function toolEvent(
  name: string,
  overrides: Partial<LiveToolEvent> = {},
): LiveToolEvent {
  return {
    id: `${name}-1`,
    name,
    status: "done",
    startedAt: 1,
    iteration: 1,
    ...overrides,
  };
}

describe("ExecutionChecklistPanel", () => {
  it("does not render a generic checklist for text-only streaming", () => {
    const { container } = renderWithProviders(
      <ExecutionChecklistPanel liveToolEvents={[]} hasAnswer isRunning />,
    );

    expect(container.firstChild).toBeNull();
  });

  it("renders when real tool work exists", () => {
    renderWithProviders(
      <ExecutionChecklistPanel
        liveToolEvents={[
          toolEvent("read_file", { input: { path: "README.md" } }),
        ]}
        hasAnswer
      />,
    );

    expect(screen.getByText("Progress Checklist")).toBeInTheDocument();
    expect(screen.getByText("Read context")).toBeInTheDocument();
  });

  it("recognizes realtime edit and shell tool names", () => {
    renderWithProviders(
      <ExecutionChecklistPanel
        liveToolEvents={[
          toolEvent("edit_text_file", { input: { path: "src/app.ts" } }),
          toolEvent("run_command", { input: { command: "npm test" } }),
        ]}
        hasAnswer
      />,
    );

    expect(
      screen.getByText(
        (content) =>
          content.includes("Write/modify file") &&
          content.includes("Run checks"),
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Run command/)).not.toBeInTheDocument();
    expect(screen.queryByText(/npm test/)).not.toBeInTheDocument();
  });

  it("lets TodoPanel own explicit todo_write events", () => {
    const { container } = renderWithProviders(
      <ExecutionChecklistPanel
        liveToolEvents={[
          toolEvent("todo_write", {
            input: { todos: [{ content: "Read files" }] },
          }),
        ]}
        hasAnswer
      />,
    );

    expect(container.firstChild).toBeNull();
  });
});
