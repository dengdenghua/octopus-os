import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { AutomationControlDock } from "./automation-control-dock";

const getRelayStatusMock = vi.fn();
const getControlSessionReplayMock = vi.fn();
const setControlSessionStateMock = vi.fn();

vi.mock("@/core/browser/api", () => ({
  getRelayStatus: (...args: unknown[]) => getRelayStatusMock(...args),
}));

vi.mock("@/core/control-session", () => ({
  getControlSessionReplay: (...args: unknown[]) =>
    getControlSessionReplayMock(...args),
  setControlSessionState: (...args: unknown[]) =>
    setControlSessionStateMock(...args),
}));

describe("<AutomationControlDock />", () => {
  beforeEach(() => {
    getRelayStatusMock.mockReset().mockResolvedValue({
      connected: true,
      connection_state: "online",
      extension_version: "1.0.0",
      pending_commands: 0,
    });
    getControlSessionReplayMock.mockReset().mockResolvedValue({
      schema: "echo.control_session_replay.v1",
      session: {
        session_id: "thread:thread-1",
        owner_id: "agent",
        owner_label: "Agent",
        surface: "browser",
        target_id: "42",
        status: "running",
        paused: false,
        takeover_count: 0,
        metadata: {},
        created_at: 1,
        updated_at: 2,
      },
      actions: [],
      evidence: [],
      timeline: {
        schema: "echo.control_session_replay_timeline.v1",
        items: [
          {
            id: "ev-1",
            kind: "evidence",
            phase: "result",
            at: 2,
            status: "done",
            summary: "Clicked Continue",
          },
        ],
        count: 1,
      },
    });
    setControlSessionStateMock
      .mockReset()
      .mockResolvedValue({ status: "paused" });
  });

  it("shows connection, ownership controls, and action receipts", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <AutomationControlDock
        threadId="thread-1"
        target={{
          kind: "browser_tab",
          source: "browser_relay",
          id: "42",
          title: "Release dashboard",
        }}
      />,
    );

    expect(await screen.findByText("Browser online")).toBeInTheDocument();
    expect(screen.getByText("Agent controlling")).toBeInTheDocument();
    expect(screen.getByText("Clicked Continue")).toBeInTheDocument();
    const dock = screen.getByTestId("automation-control-dock");
    expect(dock).toHaveClass(
      "pointer-events-none",
      "rounded-[12px]",
      "bg-secondary/90",
      "backdrop-blur-sm",
      "ring-[0.5px]",
      "shadow-[0px_8px_16px_-4px_rgba(0,0,0,.12)]",
    );
    expect(dock.firstElementChild).toHaveClass("pointer-events-auto");

    await user.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() =>
      expect(setControlSessionStateMock).toHaveBeenCalledWith(
        "thread:thread-1",
        "pause",
        "user pause",
      ),
    );

    await user.click(screen.getByRole("button", { name: "Action receipts" }));
    expect(screen.getAllByText("Clicked Continue")).toHaveLength(2);
  });
});
