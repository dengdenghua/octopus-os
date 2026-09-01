import { act, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { LiveRunFeedbackPanel } from "./live-run-feedback-panel";
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

describe("LiveRunFeedbackPanel", () => {
  it("does not open a duplicate card for thinking-only signals", () => {
    renderWithProviders(
      <LiveRunFeedbackPanel liveToolEvents={[]} threadId="thread-1" />,
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent("echo:thinking_signal", {
          detail: {
            threadId: "thread-1",
            type: "text_delta",
            iteration: 1,
          },
        }),
      );
    });

    expect(screen.queryByText("Live Feedback")).not.toBeInTheDocument();
    expect(screen.queryByText(/Shaping the next step/)).not.toBeInTheDocument();
  });

  it("ignores meta-only todo events because TodoPanel owns that UI", () => {
    renderWithProviders(
      <LiveRunFeedbackPanel
        liveToolEvents={[
          toolEvent("todo_write", {
            input: {
              todos: [{ content: "Read files", status: "in_progress" }],
            },
          }),
        ]}
        threadId="thread-1"
      />,
    );

    expect(screen.queryByText("Live Feedback")).not.toBeInTheDocument();
    expect(screen.queryByText("Updating plan")).not.toBeInTheDocument();
  });

  it("shows one detail card when a real tool signal exists", () => {
    renderWithProviders(
      <LiveRunFeedbackPanel
        liveToolEvents={[
          toolEvent("read_file", {
            status: "running",
            input: {
              path: "/Users/example/Public/echo/echo-agent/README.md",
            },
          }),
        ]}
        threadId="thread-1"
      />,
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent("echo:thinking_signal", {
          detail: {
            threadId: "thread-1",
            type: "text_delta",
            iteration: 1,
          },
        }),
      );
    });

    expect(screen.getByText("Live Feedback")).toBeInTheDocument();
    expect(screen.getByText(/Shaping the next step/)).toBeInTheDocument();
    expect(
      screen.getByText(
        (content) =>
          content.includes("Checking") && content.includes("README.md"),
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Users\//)).not.toBeInTheDocument();
    expect(screen.queryByText(/read_file/)).not.toBeInTheDocument();
  });

  it("summarizes realtime shell feedback without leaking raw commands", () => {
    renderWithProviders(
      <LiveRunFeedbackPanel
        liveToolEvents={[
          toolEvent("exec_shell", {
            status: "running",
            input: {
              tool: "exec_shell",
              command: "exec_shell",
              inputPreview: "cat ~/.ssh/id_rsa && npm run typecheck",
            },
          }),
        ]}
        threadId="thread-1"
      />,
    );

    expect(screen.getByText("Running local check")).toBeInTheDocument();
    expect(screen.queryByText(/exec_shell/)).not.toBeInTheDocument();
    expect(screen.queryByText(/cat ~\/.ssh\/id_rsa/)).not.toBeInTheDocument();
    expect(screen.queryByText(/npm run typecheck/)).not.toBeInTheDocument();
  });

  it("shows real-time progress from react step events", () => {
    renderWithProviders(
      <LiveRunFeedbackPanel liveToolEvents={[]} threadId="thread-1" />,
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent("echo:react_step", {
          detail: {
            threadId: "thread-1",
            currentPhase: "execute",
            progressSummary: "Reading repository context",
          },
        }),
      );
    });

    expect(screen.getByText("Live Feedback")).toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.getByText("Reading repository context")).toBeInTheDocument();
  });
});
