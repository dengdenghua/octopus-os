import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { WorkingSetPanel } from "./working-set-panel";

describe("<WorkingSetPanel /> thinking progress", () => {
  it("hides the default prompt-template thinking plan", () => {
    renderWithProviders(
      <WorkingSetPanel
        files={[]}
        currentPhase="understand"
        progressSummary=""
        thinkingPlan={{
          mode: "react",
          progress: 0.4,
          current_step_index: 2,
          steps: [
            { title: "Frame the ask", status: "completed" },
            { title: "Gather context", status: "completed" },
            {
              title: "Reason across options",
              detail: "Compare likely interpretations.",
              status: "in_progress",
            },
            { title: "Verify", status: "pending" },
            { title: "Answer", status: "pending" },
          ],
        }}
      />,
    );

    expect(screen.queryByText("Thinking progress")).not.toBeInTheDocument();
    expect(screen.queryByText("Reason across options")).not.toBeInTheDocument();
  });

  it("renders task-specific thinking plan progress", () => {
    renderWithProviders(
      <WorkingSetPanel
        files={[]}
        currentPhase="understand"
        progressSummary=""
        thinkingPlan={{
          mode: "react",
          progress: 0.4,
          current_step_index: 2,
          steps: [
            { title: "检查前端流式渲染", status: "completed" },
            { title: "定位后端固定 reasoning delta", status: "completed" },
            {
              title: "验证真实 thinking_delta 保留",
              detail: "只展示模型实际输出的公开检查点。",
              status: "in_progress",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Thinking progress")).toBeInTheDocument();
    expect(screen.getByText("2/3")).toBeInTheDocument();
    expect(
      screen.getAllByText("验证真实 thinking_delta 保留").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByRole("progressbar", { name: "Thinking progress" }),
    ).toHaveAttribute("aria-valuenow", "40");
  });

  it("ignores malformed plan steps without crashing", () => {
    renderWithProviders(
      <WorkingSetPanel
        files={[]}
        currentPhase="understand"
        progressSummary=""
        thinkingPlan={{ steps: undefined } as never}
      />,
    );

    expect(screen.queryByText("Thinking progress")).not.toBeInTheDocument();
  });
});
