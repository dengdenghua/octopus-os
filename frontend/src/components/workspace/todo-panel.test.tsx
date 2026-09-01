import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import type { LiveToolEvent } from "./live-tool-timeline";
import { TodoPanel } from "./todo-panel";

function todoEvent(
  input: Record<string, unknown>,
  startedAt = 1,
): LiveToolEvent {
  return {
    id: `todo-${startedAt}`,
    name: "todo_write",
    status: "done",
    startedAt,
    iteration: 1,
    input,
  };
}

describe("TodoPanel", () => {
  it("renders todo_write todos/text aliases from live tool events", () => {
    renderWithProviders(
      <TodoPanel
        liveToolEvents={[
          todoEvent({
            todos: [
              { text: "列目录", status: "completed" },
              {
                title: "读取 README",
                status: "in_progress",
                active_form: "正在读取 README",
              },
              { task: "汇总结论" },
            ],
          }),
        ]}
      />,
    );

    expect(screen.getByText("列目录")).toBeInTheDocument();
    expect(screen.getByText("正在读取 README")).toBeInTheDocument();
    expect(screen.getByText("汇总结论")).toBeInTheDocument();
    expect(
      screen.getByText(
        (content) => content.includes("1/3") && content.includes("33%"),
      ),
    ).toBeInTheDocument();
  });

  it("uses the latest todo_write event", () => {
    renderWithProviders(
      <TodoPanel
        liveToolEvents={[
          todoEvent({ items: [{ content: "旧计划" }] }, 1),
          todoEvent({ todos: [{ text: "新计划" }] }, 2),
        ]}
      />,
    );

    expect(screen.queryByText("旧计划")).not.toBeInTheDocument();
    expect(screen.getByText("新计划")).toBeInTheDocument();
  });

  it("does not let a later turn.phases projection override source todos", () => {
    renderWithProviders(
      <TodoPanel
        liveToolEvents={[
          todoEvent(
            {
              items: [
                { content: "已完成定位", status: "completed" },
                { content: "正在实现", status: "in_progress" },
              ],
            },
            1,
          ),
          todoEvent(
            {
              source: "turn.phases",
              items: [
                { content: "已完成定位", status: "in_progress" },
                { content: "正在实现", status: "pending" },
              ],
            },
            2,
          ),
        ]}
      />,
    );

    expect(screen.getByText("正在实现")).toBeInTheDocument();
    expect(
      screen.getByText(
        (content) => content.includes("1/2") && content.includes("50%"),
      ),
    ).toBeInTheDocument();
  });

  it("renders todo_write JSON string payloads from native tool calls", () => {
    renderWithProviders(
      <TodoPanel
        liveToolEvents={[
          todoEvent({
            todos: JSON.stringify([
              { text: "Confirm task", status: "completed" },
              {
                text: "Check constraints",
                status: "in_progress",
                activeForm: "Checking constraints",
              },
            ]),
          }),
        ]}
      />,
    );

    expect(screen.getByText("Confirm task")).toBeInTheDocument();
    expect(screen.getByText("Checking constraints")).toBeInTheDocument();
    expect(
      screen.getByText(
        (content) => content.includes("1/2") && content.includes("50%"),
      ),
    ).toBeInTheDocument();
  });

  it("returns unfinished in-progress work to pending once the turn is no longer live", () => {
    const { container } = renderWithProviders(
      <TodoPanel
        liveToolEvents={[
          todoEvent(
            {
              todos: [
                { text: "Detect issue", status: "in_progress" },
                { text: "Fix issue", status: "completed" },
              ],
            },
            1,
          ),
          {
            id: "turn-complete",
            name: "turn_completed",
            status: "done",
            startedAt: 2,
            iteration: 1,
          } as LiveToolEvent,
        ]}
      />,
    );

    expect(screen.getByText("Detect issue")).toBeInTheDocument();
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(container.querySelector(".text-destructive")).toBeNull();
  });

  it("keeps never-started pending work neutral once the turn is no longer live", () => {
    const { container } = renderWithProviders(
      <TodoPanel
        liveToolEvents={[
          todoEvent({
            todos: [
              { text: "Already done", status: "completed" },
              { text: "Never started", status: "pending" },
            ],
          }),
        ]}
      />,
    );

    expect(screen.getByText("Never started")).toBeInTheDocument();
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(container.querySelector(".text-destructive")).toBeNull();
  });
});
