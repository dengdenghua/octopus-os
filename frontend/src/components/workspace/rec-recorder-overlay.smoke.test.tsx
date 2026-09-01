import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";

import { renderWithProviders } from "@/test/harness";

vi.mock("@/core/teach-repeat/api", () => ({
  appendRecordingEvents: vi.fn(() =>
    Promise.resolve({
      recording: true,
      thread_id: "t1",
      accepted: 1,
      event_count: 1,
      step_count: 1,
    }),
  ),
  startRecording: vi.fn(() =>
    Promise.resolve({ recording: true, thread_id: "t1", name: "x" }),
  ),
  stopRecording: vi.fn(() =>
    Promise.resolve({ name: "x", status: "promoted", forged: ["skill-a"] }),
  ),
  getRecordingStatus: vi.fn(() =>
    Promise.resolve({ recording: true, step_count: 3, name: "x" }),
  ),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() },
}));

import { RecRecorderOverlay } from "./rec-recorder-overlay";

describe("RecRecorderOverlay", () => {
  const renderRecorder = (ui: ReactElement) =>
    renderWithProviders(ui, { locale: "zh-CN" });

  it("renders nothing when closed", () => {
    const { container } = renderRecorder(
      <RecRecorderOverlay
        open={false}
        threadId="t1"
        defaultName="任务"
        onClose={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the pre-record form when opened idle", () => {
    const onOpenLibrary = vi.fn();
    renderRecorder(
      <RecRecorderOverlay
        open
        threadId="t1"
        defaultName="导出对账单"
        onClose={() => {}}
        onOpenLibrary={onOpenLibrary}
      />,
    );
    expect(screen.getByText("录什么任务?")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /开始录制/ }),
    ).toBeInTheDocument();
    screen.getByRole("button", { name: "查看已保存的自动化" }).click();
    expect(onOpenLibrary).toHaveBeenCalledOnce();
  });

  it("enters the countdown after pressing start", async () => {
    const user = userEvent.setup();
    renderRecorder(
      <RecRecorderOverlay
        open
        threadId="t1"
        defaultName="任务"
        onClose={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: /开始录制/ }));
    expect(screen.getByText("准备录制…")).toBeInTheDocument();
  });

  it("shows the stop control when opened mid-recording", async () => {
    renderRecorder(
      <RecRecorderOverlay
        open
        threadId="t1"
        defaultName="任务"
        initiallyRecording
        onClose={() => {}}
      />,
    );
    expect(
      await screen.findByRole("button", { name: /停止并提炼技能/ }),
    ).toBeInTheDocument();
  });
});
