import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvMdRaid1Replace,
  fetchNativeStatus,
  fetchOmvMdRaid1ReplacementCandidates,
  planOmvMdRaid1Replace,
} from "./omv";
import { MdRaid1RepairPanel } from "./mdraid1-repair-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./mdraid-maintenance-panel", () => ({
  MdRaidMaintenancePanel: () => null,
}));
vi.mock("./omv", () => ({
  applyOmvMdRaid1Replace: vi.fn(),
  fetchNativeStatus: vi.fn(),
  fetchOmvMdRaid1ReplacementCandidates: vi.fn(),
  planOmvMdRaid1Replace: vi.fn(),
}));

const desired = {
  schema: "echo.omv.mdraid1-replace-desired.v1" as const,
  name: "family",
  arrayUuid: "11111111:22222222:33333333:44444444",
  replacementDevice: "/dev/sdc",
  dataPreserved: true as const,
};

const replacement = {
  devicefile: "/dev/sdc",
  sizeBytes: 12 * 1024 ** 3,
  serial: "replacement",
  wwn: null,
  model: "QEMU HARDDISK",
};

const candidate = {
  array: {
    name: "family",
    devicefile: "/dev/md/echo-family",
    uuid: desired.arrayUuid,
  },
  survivingMember: {
    devicefile: "/dev/sdb",
    slot: 0,
    states: ["in_sync"],
    sizeBytes: 10 * 1024 ** 3,
    serial: "survivor",
    wwn: null,
  },
  failedMember: {
    devicefile: "/dev/sdd",
    slot: null,
    states: ["faulty"],
  },
  missingSlot: 1 as const,
  minimumReplacementBytes: 10 * 1024 ** 3,
  replacementDevices: [replacement],
};

const plan = {
  schema: "echo.omv.mdraid1-replace-plan.v1" as const,
  planId: "a".repeat(64),
  baseRevision: "b".repeat(64),
  operation: "replaceFailedMember" as const,
  requiresApproval: true as const,
  desired,
  array: candidate.array,
  survivingMember: candidate.survivingMember,
  failedMember: candidate.failedMember,
  missingSlot: candidate.missingSlot,
  replacement,
  minimumReplacementBytes: candidate.minimumReplacementBytes,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: ["storage.array.mdraid1.replace-failed.blank.v1"],
    source: "native",
  });
  vi.mocked(fetchOmvMdRaid1ReplacementCandidates).mockResolvedValue([
    candidate,
  ]);
  vi.mocked(planOmvMdRaid1Replace).mockResolvedValue(plan);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "one-shot",
    expiresIn: 90,
    action: "omv.mdraid1.replace",
    target: plan.planId,
  });
  vi.mocked(applyOmvMdRaid1Replace).mockResolvedValue({
    ...plan,
    applied: true,
    verified: true,
    dataPreserved: true,
    maintenanceState: "recovering",
  });
});

describe("Linux RAID1 repair panel", () => {
  it("previews and approves a kernel-confirmed failed-member replacement", async () => {
    const user = userEvent.setup();
    render(<MdRaid1RepairPanel />);

    await user.click(await screen.findByRole("button", { name: /\/dev\/sdc/ }));
    await waitFor(() =>
      expect(planOmvMdRaid1Replace).toHaveBeenCalledWith(desired),
    );
    expect(screen.getByText(/不会主动标坏正常盘/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("设备管理员密码"),
      "correct-password",
    );
    await user.click(
      screen.getByRole("button", { name: "确认换盘并开始重建" }),
    );

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.mdraid1.replace",
        plan.planId,
        "correct-password",
      ),
    );
    expect(applyOmvMdRaid1Replace).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "one-shot",
    );
    expect(await screen.findByText(/正在后台重建/)).toBeInTheDocument();
  });

  it("does not probe candidates without the replacement capability", async () => {
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: null,
      capabilities: [],
      source: "native",
    });
    render(<MdRaid1RepairPanel />);

    expect(
      await screen.findByText(/未提供受控 RAID1 故障盘替换能力/),
    ).toBeInTheDocument();
    expect(fetchOmvMdRaid1ReplacementCandidates).not.toHaveBeenCalled();
  });
});
