import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import { fetchNativeStatus } from "./omv";
import {
  applySmartSchedule,
  fetchSmartSchedule,
  planSmartSchedule,
} from "./smart-schedule";
import { SmartSchedulePanel } from "./smart-schedule-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({ fetchNativeStatus: vi.fn() }));
vi.mock("./smart-schedule", () => ({
  applySmartSchedule: vi.fn(),
  fetchSmartSchedule: vi.fn(),
  planSmartSchedule: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: ["storage.smart.self-test.schedule.v1"],
  });
  vi.mocked(fetchSmartSchedule).mockResolvedValue({
    schemaVersion: 1,
    configured: false,
    enabled: false,
    source: "localPolicy",
    test: "short",
    schedule: "Sunday 03:30 local time, with up to 30 minutes randomized delay",
  });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "schedule-once",
    expiresIn: 300,
    action: "storage.smart.self-test.schedule",
    target: "d".repeat(64),
  });
});

describe("SMART schedule panel", () => {
  it("previews and password-approves enabling the weekly short test", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.smart-self-test-schedule-desired.v1" as const,
      enabled: true,
    };
    vi.mocked(planSmartSchedule).mockResolvedValue({
      schema: desired.schema,
      planId: "d".repeat(64),
      operation: "enable",
      requiresApproval: true,
      configured: false,
      current: { schemaVersion: 1, enabled: false },
      desired: { schemaVersion: 1, enabled: true },
      test: "short",
      schedule: "weekly",
    });
    vi.mocked(applySmartSchedule).mockResolvedValue({} as never);
    render(<SmartSchedulePanel />);

    await user.click(
      await screen.findByRole("button", { name: "启用定时短检" }),
    );
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "不会自动启动长检",
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认启用" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "storage.smart.self-test.schedule",
        "d".repeat(64),
        "device-password",
      ),
    );
    expect(applySmartSchedule).toHaveBeenCalledWith(
      desired,
      "d".repeat(64),
      "schedule-once",
    );
  });

  it("does not enable controls when smartctl capability is absent", async () => {
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: null,
      capabilities: [],
    });
    render(<SmartSchedulePanel />);

    expect(
      await screen.findByText("本机缺少 smartctl，定时自检不可用。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "启用定时短检" })).toBeDisabled();
  });
});
