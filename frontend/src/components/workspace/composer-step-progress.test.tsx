import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { ComposerStepProgress } from "./composer-step-progress";
import type { LiveToolEvent } from "./live-tool-timeline";
import { renderWithProviders } from "@/test/harness";

function event(partial: Partial<LiveToolEvent>): LiveToolEvent {
  return {
    id: "event-1",
    name: "todo_write",
    status: "running",
    startedAt: 1_000,
    iteration: 0,
    ...partial,
  };
}

describe("<ComposerStepProgress />", () => {
  test("shows progress from an explicit task plan and expands its details in place", () => {
    renderWithProviders(
      <ComposerStepProgress
        isLoading
        events={[
          event({
            input: {
              items: [
                { content: "Inspect the project", status: "completed" },
                { content: "Implement the change", status: "in_progress" },
                { content: "Verify the result", status: "pending" },
              ],
            },
          }),
        ]}
      />,
    );

    const button = screen.getByRole("button", { name: /Step 2 \/ 3/ });
    expect(button).toHaveTextContent("Step 2 / 3");
    expect(button).toHaveAttribute("title", "Implement the change");
    expect(button).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Inspect the project")).toBeInTheDocument();
    expect(screen.getByText("Implement the change")).toBeInTheDocument();
    expect(screen.getByText("Verify the result")).toBeInTheDocument();
  });

  test("does not turn generic tool activity into numbered steps", () => {
    const { container } = renderWithProviders(
      <ComposerStepProgress
        isLoading
        events={[
          event({
            name: "read_file",
            input: { path: "src/app.tsx" },
          }),
          event({
            id: "event-2",
            name: "shell_command",
            input: { command: "npm test" },
          }),
        ]}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  test("leaves the composer after a successful completed answer", () => {
    const { container } = renderWithProviders(
      <ComposerStepProgress
        hasAnswer
        runSettled
        events={[
          event({
            status: "done",
            input: {
              items: [
                { content: "Inspect the project", status: "completed" },
                { content: "Verify the result", status: "completed" },
              ],
            },
          }),
        ]}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  test("does not present a done plan as complete while the turn is still streaming", () => {
    renderWithProviders(
      <ComposerStepProgress
        isLoading
        events={[
          event({
            input: {
              items: [
                { content: "Inspect the project", status: "completed" },
                { content: "Verify the result", status: "completed" },
              ],
            },
          }),
        ]}
      />,
    );

    // The plan is fully done but the turn is still running: the button stays
    // visible, but the last phase renders as an in-progress spinner instead
    // of a done checkmark (a check would falsely imply the task finished).
    const button = screen.getByRole("button", { name: /Step 2 \/ 2/ });
    expect(button).toBeInTheDocument();
    expect(button.querySelector(".animate-spin")).not.toBeNull();
    expect(button.querySelector(".text-success")).toBeNull();
  });
});
