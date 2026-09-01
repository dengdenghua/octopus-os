import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { AgentWorkbenchPanel } from "./agent-workbench-panel";

describe("AgentWorkbenchPanel tool-effect focus", () => {
  it("uses the right rail for the selected receipt and can return to execution", () => {
    renderWithProviders(
      <AgentWorkbenchPanel
        focusedEffectKey="effect:risky-write"
        focusedEventNonce={1}
        events={[
          {
            id: "call-risk",
            name: "write_file",
            status: "error",
            startedAt: 1,
            iteration: 1,
            input: { path: "result.txt" },
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText("外部动作核对")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回执行详情" }));
    expect(screen.queryByText("外部动作核对")).not.toBeInTheDocument();
    expect(screen.getByText("概要")).toBeInTheDocument();
  });

  it("does not duplicate transcript-location controls in the workbench header", () => {
    renderWithProviders(
      <AgentWorkbenchPanel
        focusedEventId="thinking-7"
        focusedEventKind="thinking"
        focusedEventView="summary"
        focusedEventNonce={1}
        focusedProcessEvent={{
          kind: "thinking",
          summary: "已确认时间线顺序",
          detail: "公开进展留在主线，右侧只看细节。",
          status: "done",
        }}
        events={[]}
      />,
      { locale: "zh-CN" },
    );

    expect(
      screen.queryByRole("button", { name: "定位到主对话" }),
    ).not.toBeInTheDocument();
  });

  it("does not duplicate the shell-level close action inside the panel", () => {
    renderWithProviders(
      <AgentWorkbenchPanel
        events={[
          {
            id: "call-read",
            name: "read_file",
            status: "running",
            startedAt: 1,
            iteration: 1,
            input: { path: "src/app.tsx" },
          },
        ]}
        onClose={() => {}}
      />,
      { locale: "zh-CN" },
    );

    expect(
      screen.queryByRole("button", { name: "收起工作台" }),
    ).not.toBeInTheDocument();
  });

  it("closes from Escape without stealing Escape from text inputs", () => {
    let closed = 0;
    renderWithProviders(
      <div>
        <input aria-label="draft" />
        <AgentWorkbenchPanel
          events={[
            {
              id: "call-read",
              name: "read_file",
              status: "running",
              startedAt: 1,
              iteration: 1,
              input: { path: "src/app.tsx" },
            },
          ]}
          onClose={() => {
            closed += 1;
          }}
        />
      </div>,
      { locale: "zh-CN" },
    );

    fireEvent.keyDown(screen.getByRole("textbox", { name: "draft" }), {
      key: "Escape",
    });
    expect(closed).toBe(0);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(closed).toBe(1);
  });
});
