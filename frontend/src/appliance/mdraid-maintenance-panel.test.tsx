import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyMdRaidCheck,
  applyMdRaidCheckSchedule,
  fetchMdRaidCheckSchedule,
  fetchMdRaidMaintenance,
  planMdRaidCheck,
  planMdRaidCheckSchedule,
} from "./mdraid-maintenance";
import { MdRaidMaintenancePanel } from "./mdraid-maintenance-panel";
import { fetchNativeStatus } from "./omv";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({ fetchNativeStatus: vi.fn() }));
vi.mock("./mdraid-maintenance", () => ({
  applyMdRaidCheck: vi.fn(),
  applyMdRaidCheckSchedule: vi.fn(),
  fetchMdRaidCheckSchedule: vi.fn(),
  fetchMdRaidMaintenance: vi.fn(),
  planMdRaidCheck: vi.fn(),
  planMdRaidCheckSchedule: vi.fn(),
}));

const arrayUuid = "11111111:22222222:33333333:44444444";
const maintenance = {
  array: { name: "family", devicefile: "/dev/md/echo-family", uuid: arrayUuid },
  kernelDevice: "md127",
  members: [
    { devicefile: "/dev/sdb", slot: 0, states: ["in_sync"] },
    { devicefile: "/dev/sdc", slot: 1, states: ["in_sync"] },
  ],
  healthy: true,
  degradedDevices: 0,
  action: "idle" as const,
  progressPercent: null,
  mismatchCount: 0,
  stateHash: "b".repeat(64),
  canStartCheck: true,
};
const checkDesired = {
  schema: "echo.omv.mdraid-check-desired.v1" as const,
  name: "family",
  arrayUuid,
  operation: "start" as const,
};
const checkPlan = {
  schema: "echo.omv.mdraid-check-plan.v1" as const,
  planId: "a".repeat(64),
  baseRevision: maintenance.stateHash,
  operation: "start" as const,
  requiresApproval: true as const,
  desired: checkDesired,
  array: maintenance.array,
  before: {
    healthy: true,
    degradedDevices: 0,
    action: "idle" as const,
    progressPercent: null,
    mismatchCount: 0,
    stateHash: maintenance.stateHash,
  },
};
const schedule = {
  schemaVersion: 1 as const,
  enabled: false,
  configured: false,
  schedulerInstalled: true,
  source: "localPolicy" as const,
  operation: "check" as const,
  scope: "echoManagedHealthyRaid1Only" as const,
  schedule:
    "first Sunday of each month after 00:45 local time, randomized within 24 hours",
};
const schedulePlan = {
  schema: "echo.mdraid-check-schedule-desired.v1" as const,
  planId: "c".repeat(64),
  current: { schemaVersion: 1 as const, enabled: false },
  desired: { schemaVersion: 1 as const, enabled: true },
  configured: false,
  schedulerInstalled: true,
  operation: "enable" as const,
  checkAction: "check" as const,
  scope: "echoManagedHealthyRaid1Only" as const,
  schedule: schedule.schedule,
  requiresApproval: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: [
      "storage.array.mdraid.check.start.v1",
      "storage.array.mdraid.check.schedule.v1",
    ],
    source: "native",
  });
  vi.mocked(fetchMdRaidMaintenance).mockResolvedValue([maintenance]);
  vi.mocked(fetchMdRaidCheckSchedule).mockResolvedValue(schedule);
  vi.mocked(planMdRaidCheck).mockResolvedValue(checkPlan);
  vi.mocked(planMdRaidCheckSchedule).mockResolvedValue(schedulePlan);
  vi.mocked(requestHighRiskApproval).mockImplementation(
    async (action, target) => ({
      approvalToken: `approval-${action}`,
      expiresIn: 90,
      action,
      target,
    }),
  );
  vi.mocked(applyMdRaidCheck).mockResolvedValue({
    ...checkPlan,
    applied: true,
    verified: true,
    maintenanceState: "checking",
  });
  vi.mocked(applyMdRaidCheckSchedule).mockResolvedValue({
    ...schedulePlan,
    applied: true,
    verified: true,
  });
});

describe("md RAID1 maintenance panel", () => {
  it("previews and separately approves a manual consistency check", async () => {
    const user = userEvent.setup();
    render(<MdRaidMaintenancePanel />);

    await user.click(
      await screen.findByRole("button", { name: "预览一致性校验" }),
    );
    expect(planMdRaidCheck).toHaveBeenCalledWith(checkDesired);
    await user.type(screen.getByLabelText("设备管理员密码"), "admin-password");
    await user.click(screen.getByRole("button", { name: "启动一致性校验" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.mdraid.check.start",
        checkPlan.planId,
        "admin-password",
      ),
    );
    expect(applyMdRaidCheck).toHaveBeenCalledWith(
      checkDesired,
      checkPlan.planId,
      "approval-omv.mdraid.check.start",
    );
    expect(await screen.findByText(/一致性校验已启动/)).toBeInTheDocument();
  });

  it("previews and separately approves the fixed monthly schedule", async () => {
    const user = userEvent.setup();
    render(<MdRaidMaintenancePanel />);

    await user.click(
      await screen.findByRole("button", { name: "预览启用月度校验" }),
    );
    expect(planMdRaidCheckSchedule).toHaveBeenCalledWith({
      schema: "echo.mdraid-check-schedule-desired.v1",
      enabled: true,
    });
    await user.type(screen.getByLabelText("设备管理员密码"), "admin-password");
    await user.click(screen.getByRole("button", { name: "确认更新月度校验" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "storage.mdraid.check.schedule",
        schedulePlan.planId,
        "admin-password",
      ),
    );
    expect(applyMdRaidCheckSchedule).toHaveBeenCalledWith(
      { schema: "echo.mdraid-check-schedule-desired.v1", enabled: true },
      schedulePlan.planId,
      "approval-storage.mdraid.check.schedule",
    );
    expect(await screen.findByText("RAID1 月度校验已启用")).toBeInTheDocument();
  });
});
