import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import { applyExt4Check, fetchExt4Checks, planExt4Check } from "./ext4-check";
import { Ext4CheckPanel } from "./ext4-check-panel";
import { fetchNativeStatus } from "./omv";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({ fetchNativeStatus: vi.fn() }));
vi.mock("./ext4-check", () => ({
  applyExt4Check: vi.fn(),
  fetchExt4Checks: vi.fn(),
  planExt4Check: vi.fn(),
}));

const filesystemUuid = "11111111-2222-3333-4444-555555555555";
const filesystem = {
  filesystemUuid,
  name: "family",
  mountpoint: "/data/family",
  devicefile: "/dev/md/echo-family",
  array: {
    name: "family",
    devicefile: "/dev/md/echo-family",
    uuid: "11111111:22222222:33333333:44444444",
    level: "raid1" as const,
    devices: ["/dev/sdb", "/dev/sdc"],
    filesystem: null,
  },
  fstabSha256: "b".repeat(64),
  mounted: false,
  stateHash: "c".repeat(64),
  canCheck: true,
  reason: null,
};
const desired = {
  schema: "echo.omv.ext4-check-desired.v1" as const,
  filesystemUuid,
  operation: "check" as const,
};
const plan = {
  schema: "echo.omv.ext4-check-plan.v1" as const,
  planId: "a".repeat(64),
  operation: "offlineReadOnlyCheck" as const,
  desired,
  filesystem,
  mountUnit: "data-family.mount",
  mountUnitState: "generated" as const,
  requiresApproval: true as const,
  safety: {
    filesystem: "echoManagedExt4OnHealthyMdRaid1Only" as const,
    mountedFilesystem: "rejected" as const,
    automaticUnmount: false as const,
    automaticMount: false as const,
    repair: false as const,
    command: "e2fsckForcedReadOnly" as const,
    mountUnit: "runtimeMaskedDuringCheck" as const,
    ioLoad: "high" as const,
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: ["storage.volume.ext4.offline-check.v1"],
    source: "native",
  });
  vi.mocked(fetchExt4Checks).mockResolvedValue([filesystem]);
  vi.mocked(planExt4Check).mockResolvedValue(plan);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "one-shot",
    expiresIn: 90,
    action: "omv.ext4.offline-check",
    target: plan.planId,
  });
  vi.mocked(applyExt4Check).mockResolvedValue({
    ...plan,
    applied: true,
    verified: true,
    clean: true,
    errorsDetected: false,
    result: "clean",
    exitCode: 0,
  });
});

describe("EXT4 offline check panel", () => {
  it("previews and approves an already-unmounted read-only check", async () => {
    const user = userEvent.setup();
    render(<Ext4CheckPanel />);

    await user.click(
      await screen.findByRole("button", { name: "预览离线检查" }),
    );
    expect(planExt4Check).toHaveBeenCalledWith(desired);
    await user.type(screen.getByLabelText("设备管理员密码"), "admin-password");
    await user.click(screen.getByRole("button", { name: "开始只读检查" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.ext4.offline-check",
        plan.planId,
        "admin-password",
      ),
    );
    expect(applyExt4Check).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "one-shot",
    );
    expect(await screen.findByText(/未发现错误/)).toBeInTheDocument();
  });

  it("does not offer e2fsck while the filesystem is mounted", async () => {
    vi.mocked(fetchExt4Checks).mockResolvedValue([
      { ...filesystem, mounted: true, canCheck: false, reason: "mounted" },
    ]);
    render(<Ext4CheckPanel />);

    const button = await screen.findByRole("button", { name: "需先安全卸载" });
    expect(button).toBeDisabled();
    expect(screen.getByText(/已挂载，不能检查/)).toBeInTheDocument();
    expect(planExt4Check).not.toHaveBeenCalled();
  });
});
