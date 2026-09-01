import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { AgentProgressPill } from "./agent-progress-pill";
import type { LiveToolEvent } from "./live-tool-timeline";
import { renderWithProviders } from "@/test/harness";
import type { StreamVitals } from "@/core/realtime";

function event(partial: Partial<LiveToolEvent>): LiveToolEvent {
  return {
    id: "event-1",
    name: "read_file",
    status: "done",
    startedAt: 1000,
    iteration: 0,
    ...partial,
  };
}

function vitals(partial: Partial<StreamVitals>): StreamVitals {
  return {
    phase: "working",
    ttftMs: null,
    lastDeltaAgeMs: Infinity,
    sinceActivityMs: 0,
    elapsedMs: 0,
    maxDeltaGapMs: 0,
    stalled: false,
    ...partial,
  };
}

describe("<AgentProgressPill />", () => {
  test("does not invent a primary stage before model events arrive", () => {
    renderWithProviders(<AgentProgressPill events={[]} isLoading />);

    expect(screen.getByRole("status")).toHaveTextContent("Thinking");
  });

  test("uses measured activity once answer content is streaming", () => {
    renderWithProviders(
      <AgentProgressPill events={[]} hasAnswer hasStreamingAnswer isLoading />,
    );

    // Answer tokens are flowing — that is processing, not "thinking".
    expect(screen.getByRole("status")).toHaveTextContent("Working");
    expect(screen.getByRole("status")).not.toHaveTextContent("Thinking");
  });

  test("keeps heartbeat-only waiting distinct from model work", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[]}
        isLoading
        vitals={vitals({ phase: "waiting", elapsedMs: 9_000 })}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Thinking · 9s");
  });

  test("keeps minute-long waits readable", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[]}
        isLoading
        vitals={vitals({ phase: "waiting", elapsedMs: 104_500 })}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "First response is taking longer · 1m 44s",
    );
    expect(screen.getByRole("status")).toHaveAttribute(
      "data-stream-first-response-delayed",
      "true",
    );
  });

  test("lets a real stall override an earlier partial answer", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[]}
        hasAnswer
        hasStreamingAnswer
        isLoading
        vitals={vitals({
          phase: "slow",
          ttftMs: 840,
          sinceActivityMs: 11_000,
          elapsedMs: 14_000,
          maxDeltaGapMs: 2_400,
          stalled: true,
        })}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Still on it");
    expect(status).not.toHaveTextContent("Model");
    expect(status).toHaveAttribute("data-stream-phase", "slow");
    expect(status).toHaveAttribute("data-stream-stalled", "true");
    expect(status).toHaveAttribute("data-stream-ttft-ms", "840");
    expect(status).toHaveAttribute("data-stream-max-gap-ms", "2400");
  });

  test("shows reconnecting ahead of answer generation after a disconnect", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[]}
        hasAnswer
        hasStreamingAnswer
        isLoading
        vitals={vitals({ phase: "disconnected", stalled: true })}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Connection dropped — reconnecting",
    );
    expect(screen.getByRole("status")).toHaveAttribute(
      "data-stream-phase",
      "disconnected",
    );
  });

  test("does not render for transport-only events", () => {
    const { container } = renderWithProviders(
      <AgentProgressPill
        events={[
          event({ id: "transport-1", name: "turn_request" }),
          event({ id: "transport-2", name: "response_stream" }),
        ]}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  test("shows current progress and expands inline without a workspace shortcut", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[
          event({
            id: "read-1",
            name: "read_file",
            input: { path: "src/app.tsx" },
          }),
          event({
            id: "shell-1",
            name: "shell_command",
            status: "running",
            startedAt: 2000,
            input: { command: "npm run typecheck" },
          }),
        ]}
      />,
    );

    const pill = screen.getByRole("button", {
      name: /Current Progress 2\/2/,
    });
    expect(screen.getByText("Current Progress 2/2")).toBeInTheDocument();
    expect(screen.getByText(/Working through leads/)).toBeInTheDocument();

    fireEvent.click(pill);
    expect(pill).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.queryByRole("button", { name: "Open Workspace" }),
    ).not.toBeInTheDocument();
  });

  test("minimizes one task into a single progress bead and restores from it", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[
          event({
            id: "read-1",
            name: "read_file",
            iteration: 0,
            input: { path: "src/app.tsx" },
          }),
          event({
            id: "shell-1",
            name: "shell_command",
            status: "done",
            iteration: 1,
            startedAt: 2000,
            input: { command: "npm run typecheck" },
          }),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Minimize Progress" }));
    expect(screen.queryByText("Current Progress 2/2")).not.toBeInTheDocument();
    const bead = screen.getByRole("button", { name: "Restore Progress" });
    expect(bead).toHaveClass("bg-muted-foreground/45");
    expect(bead.childElementCount).toBe(0);

    fireEvent.click(bead);
    expect(
      screen.getByRole("button", { name: /Current Progress 2\/2/ }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  test("uses a themed breathing bead while minimized work is still running", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[
          event({
            id: "read-1",
            name: "read_file",
            iteration: 0,
            input: { path: "src/app.tsx" },
          }),
          event({
            id: "shell-1",
            name: "shell_command",
            status: "running",
            iteration: 1,
            startedAt: 2000,
            input: { command: "npm run typecheck" },
          }),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Minimize Progress" }));

    const bead = screen.getByRole("button", { name: "Restore Progress" });
    expect(bead).toHaveClass("bg-success/70");
    expect(bead.querySelector("[aria-hidden='true']")).toHaveClass(
      "animate-pulse",
      "bg-success/15",
    );
  });

  test("keeps a manually minimized plan minimized across remounts until the plan changes", () => {
    const progressScopeKey = "agent-progress-pill:minimized-plan-remount";
    const planEvents = [
      event({
        id: "todo-1",
        name: "todo_write",
        status: "running",
        input: {
          items: [
            { content: "collect material", status: "completed" },
            { content: "write report", status: "in_progress" },
          ],
        },
      }),
    ];

    const first = renderWithProviders(
      <AgentProgressPill
        progressScopeKey={progressScopeKey}
        events={planEvents}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Minimize Progress" }));
    expect(
      screen.getByRole("button", { name: "Restore Progress" }),
    ).toBeInTheDocument();
    first.unmount();

    const second = renderWithProviders(
      <AgentProgressPill
        progressScopeKey={progressScopeKey}
        events={[
          event({
            id: "todo-2",
            name: "todo_write",
            status: "running",
            input: {
              items: [
                { content: "collect material", status: "completed" },
                { content: "write report", status: "completed" },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.queryByText("Current Progress 2/2")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Restore Progress" }),
    ).toBeInTheDocument();
    second.unmount();

    renderWithProviders(
      <AgentProgressPill
        progressScopeKey={progressScopeKey}
        events={[
          event({
            id: "todo-3",
            name: "todo_write",
            status: "running",
            input: {
              items: [
                { content: "confirm scope", status: "completed" },
                { content: "run new research", status: "in_progress" },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Current Progress 2/2")).toBeInTheDocument();
  });

  test("auto-minimizes completed runs into a small progress bead", () => {
    renderWithProviders(
      <AgentProgressPill
        hasAnswer
        runSettled
        events={[
          event({
            id: "read-1",
            name: "read_file",
            iteration: 0,
            input: { path: "src/app.tsx" },
          }),
          event({
            id: "shell-1",
            name: "shell_command",
            status: "done",
            iteration: 1,
            startedAt: 2000,
            input: { command: "npm run typecheck" },
          }),
        ]}
      />,
    );

    expect(screen.queryByText("Current Progress 2/2")).not.toBeInTheDocument();
    const bead = screen.getByRole("button", { name: "Restore Progress" });
    expect(bead).toHaveAttribute("title", "Current Progress 2/2");

    fireEvent.click(bead);

    expect(
      screen.getByRole("button", { name: /Current Progress 2\/2/ }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  test("uses active todo text when todo_write drives the current step", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[
          event({
            id: "todo-1",
            name: "todo_write",
            status: "running",
            input: {
              items: [
                { content: "collect material", status: "completed" },
                {
                  content: "check and fix",
                  activeForm: "create board deck - check and fix",
                  status: "in_progress",
                },
              ],
            },
          }),
        ]}
      />,
    );

    // "create board deck - check and fix" contains "check", which routes to
    // the testing bucket. The visible label becomes the localized testing
    // title; the raw active-form text stays on the tooltip.
    expect(
      screen.getByTitle("create board deck - check and fix"),
    ).toBeInTheDocument();
  });

  test("surfaces the current tool action below the phase", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[
          event({
            id: "todo-1",
            name: "todo_write",
            status: "running",
            startedAt: 1000,
            input: {
              items: [
                { content: "inspect project", status: "completed" },
                { content: "verify changes", status: "in_progress" },
              ],
            },
          }),
          event({
            id: "shell-1",
            name: "shell_command",
            status: "running",
            startedAt: 2000,
            input: { command: "pnpm test" },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Run terminal")).toBeInTheDocument();
    expect(screen.queryByText(/pnpm test/)).not.toBeInTheDocument();
  });

  test("expanded todo plans include upcoming phases", () => {
    renderWithProviders(
      <AgentProgressPill
        events={[
          event({
            id: "todo-1",
            name: "todo_write",
            status: "running",
            input: {
              items: [
                { content: "collect context", status: "completed" },
                { content: "apply changes", status: "in_progress" },
                { content: "verify locally", status: "pending" },
                { content: "deliver final", status: "pending" },
              ],
            },
          }),
        ]}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Current Progress 2\/4/ }),
    );

    expect(screen.getByText("deliver final")).toBeInTheDocument();
  });

  test("does not mark stale pending todo steps as failed after a settled answer", () => {
    const { container } = renderWithProviders(
      <AgentProgressPill
        hasAnswer
        runSettled
        events={[
          event({
            id: "todo-1",
            name: "todo_write",
            status: "done",
            input: {
              items: [
                { content: "create plan", status: "completed" },
                { content: "run research", status: "pending" },
              ],
            },
          }),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Restore Progress" }));

    // "run research" matches the exploring bucket (research keyword), so the
    // visible label becomes the localized exploring title while the raw text
    // stays on the tooltip.
    expect(screen.getAllByTitle("run research").length).toBeGreaterThan(0);
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(container.querySelector(".text-destructive")).toBeNull();
  });

  test("keeps approval running while an interim answer exists", () => {
    renderWithProviders(
      <AgentProgressPill
        hasAnswer
        events={[
          event({
            id: "approval-1",
            name: "write_text_file",
            status: "waiting_approval",
            input: { path: "plan.md" },
          }),
        ]}
      />,
    );

    expect(
      screen.getByRole("button", { name: /Current Progress 1\/2/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Working through leads/)).toBeInTheDocument();
  });

  test("does not keep a stale approval running after the run settles", () => {
    renderWithProviders(
      <AgentProgressPill
        hasAnswer
        runSettled
        events={[
          event({
            id: "approval-1",
            name: "write_text_file",
            status: "waiting_approval",
            input: { path: "plan.md" },
          }),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Restore Progress" }));

    expect(
      screen.getByRole("button", { name: /Current Progress 2\/2/ }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/Pulling the answer together/).length,
    ).toBeGreaterThan(0);
  });
});
