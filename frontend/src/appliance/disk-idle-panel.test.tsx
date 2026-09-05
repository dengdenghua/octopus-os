import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import { DiskIdlePanel } from "./disk-idle-panel";
import { applyDiskIdle, fetchDiskIdle, planDiskIdle } from "./disk-idle";
import { fetchNativeStatus } from "./omv";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./disk-idle", () => ({
  applyDiskIdle: vi.fn(),
  fetchDiskIdle: vi.fn(),
  planDiskIdle: vi.fn(),
}));
vi.mock("./omv", () => ({ fetchNativeStatus: vi.fn() }));

const device = {
  devicefile: "/dev/sda",
  model: "NAS HDD",
  sizeBytes: 4_000_000_000_000,
  transport: "sata" as const,
  identityHash: "b".repeat(64),
};
const status = {
  schemaVersion: 1 as const,
  enabled: false,
  idleMinutes: 0 as const,
  configured: false,
  serviceInstalled: true,
  eligibleDevices: [device],
  eligibleDeviceCount: 1,
  allowedIdleMinutes: [0, 30, 60, 120, 240] as const,
  scope: "stableInternalRotationalAtaSataNonSystemWholeDisksOnly" as const,
  hardwareVerification: "commandAcceptanceOnly" as const,
  source: "localPolicy" as const,
};
const plan = {
  schema: "echo.disk-idle-policy-desired.v1" as const,
  planId: "c".repeat(64),
  operation: "set" as const,
  requiresApproval: true,
  current: { schemaVersion: 1 as const, idleMinutes: 0 as const },
  desired: { schemaVersion: 1 as const, idleMinutes: 60 as const },
  configured: false,
  serviceInstalled: true,
  devices: [device],
  scope: status.scope,
  hardwareVerification: status.hardwareVerification,
  safety: {
    minimumIdleMinutes: 30 as const,
    nvme: "skipped" as const,
    usbAndRemovable: "skipped" as const,
    unknownIdentity: "skipped" as const,
    systemBackingDisk: "skipped" as const,
    firmwareMayIgnoreTimer: true as const,
    activeIoPreventsStandby: true as const,
  },
};

describe("DiskIdlePanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchDiskIdle).mockResolvedValue(status);
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      readOnly: false,
      adminUrl: null,
      capabilities: ["storage.disk.idle.configure.v1"],
      source: "native",
    });
    vi.mocked(planDiskIdle).mockResolvedValue(plan);
    vi.mocked(requestHighRiskApproval).mockResolvedValue({
      approvalToken: "disk-idle-token",
      expiresIn: 60,
    });
    vi.mocked(applyDiskIdle).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
      hardwareUpdated: 1,
    });
  });

  it("previews exact devices and requires a distinct approval", async () => {
    const user = userEvent.setup();
    render(<DiskIdlePanel />);

    const select = await screen.findByLabelText("磁盘空闲时长");
    await user.selectOptions(select, "60");
    await user.click(screen.getByRole("button", { name: "预览更新" }));

    expect(planDiskIdle).toHaveBeenCalledWith({
      schema: "echo.disk-idle-policy-desired.v1",
      idleMinutes: 60,
    });
    expect(
      await screen.findByText(/预览绑定的 1 块内置机械盘/),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("设备管理员密码"), "disk-password");
    await user.click(screen.getByRole("button", { name: "确认更新" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "storage.disk.idle.configure",
        plan.planId,
        "disk-password",
      ),
    );
    expect(applyDiskIdle).toHaveBeenCalledWith(
      { schema: "echo.disk-idle-policy-desired.v1", idleMinutes: 60 },
      plan.planId,
      "disk-idle-token",
    );
    expect(
      await screen.findByText(/已向 1 块磁盘下发 1 小时/),
    ).toBeInTheDocument();
  });

  it("disables controls when the capability is absent", async () => {
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      readOnly: false,
      adminUrl: null,
      capabilities: [],
      source: "native",
    });
    render(<DiskIdlePanel />);
    expect(await screen.findByText(/磁盘休眠不可用/)).toBeInTheDocument();
    expect(screen.getByLabelText("磁盘空闲时长")).toBeDisabled();
  });
});
