import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvBtrfsRaid1,
  applyOmvBtrfsScrub,
  fetchNativeStatus,
  fetchOmvBtrfsMaintenance,
  fetchOmvBtrfsRaid1Candidates,
  planOmvBtrfsRaid1,
  planOmvBtrfsScrub,
  type OmvBtrfsMaintenance,
  type OmvBtrfsRaid1Plan,
  type OmvBtrfsScrubPlan,
} from "./omv";
import { BtrfsRaid1Panel } from "./btrfs-raid1-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({
  applyOmvBtrfsRaid1: vi.fn(),
  applyOmvBtrfsScrub: vi.fn(),
  fetchNativeStatus: vi.fn(),
  fetchOmvBtrfsMaintenance: vi.fn(),
  fetchOmvBtrfsRaid1Candidates: vi.fn(),
  planOmvBtrfsRaid1: vi.fn(),
  planOmvBtrfsScrub: vi.fn(),
}));

const filesystemUuid = "11111111-2222-3333-4444-555555555555";
const candidates = [
  {
    devicefile: "/dev/sdb",
    sizeBytes: 8 * 1024 ** 3,
    serial: "disk-b",
    wwn: null,
    model: "QEMU HARDDISK",
  },
  {
    devicefile: "/dev/sdc",
    sizeBytes: 8 * 1024 ** 3,
    serial: "disk-c",
    wwn: null,
    model: "QEMU HARDDISK",
  },
] as const;

const desired = {
  schema: "echo.omv.btrfs-raid1-desired.v1" as const,
  name: "family",
  devices: ["/dev/sdb", "/dev/sdc"] as [string, string],
  dataLossConfirmed: true as const,
};

const createPlan: OmvBtrfsRaid1Plan = {
  schema: "echo.omv.btrfs-raid1-plan.v1",
  planId: "a".repeat(64),
  baseRevision: "b".repeat(64),
  operation: "createAndMount",
  requiresApproval: true,
  desired,
  devices: [...candidates],
  mountpoint: "/data/family",
  safety: {
    destructive: true,
    dataLossConfirmed: true,
    source: "twoBlankWholeDisksWithStableIdentityOnly",
    dataProfile: "raid1",
    metadataProfile: "raid1",
    mountRoot: "/data",
    persistentIdentity: "filesystemUuid",
    force: false,
  },
  rollback: "unmountRestoreFstabAndClearNewFilesystemSignatures",
};

const maintenance: OmvBtrfsMaintenance = {
  filesystem: {
    devicefile: filesystemUuid,
    uuid: filesystemUuid,
    mountpoint: "/data/family",
    level: "btrfs-raid1",
    status: "healthy",
    totalDevices: 2,
    activeDevices: 2,
    missingDevices: 0,
    dataProfile: "raid1",
    metadataProfile: "raid1",
    operation: null,
    operationPercent: null,
    deviceErrors: {},
    deviceErrorCount: 0,
    readOnly: false,
    kind: "btrfs",
  },
  scan: {
    kind: "scrub",
    state: "idle",
    progressPercent: null,
    errors: null,
  },
  canStartScrub: true,
};

const scrubDesired = {
  schema: "echo.omv.btrfs-scrub-desired.v1" as const,
  filesystemUuid,
  operation: "start" as const,
};

const scrubPlan: OmvBtrfsScrubPlan = {
  schema: "echo.omv.btrfs-scrub-plan.v1",
  planId: "c".repeat(64),
  baseRevision: "d".repeat(64),
  operation: "start",
  requiresApproval: true,
  desired: scrubDesired,
  filesystem: maintenance.filesystem,
  before: maintenance.scan,
  safety: {
    scope: "echoManagedMountedBtrfsRaid1Only",
    data: "checksummedReplicasMayBeReadAndRepaired",
    activeMaintenance: "mustBeAbsent",
    ioLoad: "high",
    wait: false,
    readOnly: false,
    force: false,
    cancel: false,
    rollback: "noneAfterScrubAccepted",
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: [
      "storage.volume.btrfs-raid1.create-mount.v1",
      "storage.volume.btrfs.scrub.start.v1",
    ],
    source: "native",
  });
  vi.mocked(fetchOmvBtrfsRaid1Candidates).mockResolvedValue([...candidates]);
  vi.mocked(fetchOmvBtrfsMaintenance).mockResolvedValue([maintenance]);
  vi.mocked(planOmvBtrfsRaid1).mockResolvedValue(createPlan);
  vi.mocked(planOmvBtrfsScrub).mockResolvedValue(scrubPlan);
  vi.mocked(requestHighRiskApproval).mockImplementation(
    async (action, target) => ({
      approvalToken: `approval-${action}`,
      expiresIn: 90,
      action,
      target,
    }),
  );
  vi.mocked(applyOmvBtrfsRaid1).mockResolvedValue({
    ...createPlan,
    applied: true,
    verified: true,
    filesystem: {
      uuid: filesystemUuid,
      label: "family",
      type: "btrfs",
      devices: ["/dev/sdb", "/dev/sdc"],
      mountpoint: "/data/family",
      dataProfile: "raid1",
      metadataProfile: "raid1",
      readOnly: false,
    },
  });
  vi.mocked(applyOmvBtrfsScrub).mockResolvedValue({
    ...scrubPlan,
    applied: true,
    verified: true,
    maintenanceState: "scrubbing",
    scan: { ...maintenance.scan, state: "inProgress", progressPercent: 0 },
  });
});

describe("Btrfs RAID1 panel", () => {
  it("runs candidate selection, destructive preview, step-up and create", async () => {
    const user = userEvent.setup();
    render(<BtrfsRaid1Panel />);

    await user.click(await screen.findByRole("button", { name: /\/dev\/sdb/ }));
    await user.click(screen.getByRole("button", { name: /\/dev\/sdc/ }));
    await user.type(
      screen.getByRole("textbox", { name: /Btrfs 卷名称/ }),
      "family",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(
      screen.getByRole("button", { name: "生成 Btrfs 创建预览" }),
    );

    await waitFor(() =>
      expect(planOmvBtrfsRaid1).toHaveBeenCalledWith(desired),
    );
    expect(screen.getByText(/data\/metadata 双 RAID1/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("Btrfs 创建管理员密码"),
      "correct-password",
    );
    await user.click(
      screen.getByRole("button", { name: "确认清空并创建 Btrfs RAID1" }),
    );

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.btrfs-raid1.create",
        createPlan.planId,
        "correct-password",
      ),
    );
    expect(applyOmvBtrfsRaid1).toHaveBeenCalledWith(
      desired,
      createPlan.planId,
      "approval-omv.btrfs-raid1.create",
    );
    expect(await screen.findByText(/已创建、挂载并验证/)).toBeInTheDocument();
  });

  it("previews repair-capable scrub and requires its separate approval action", async () => {
    const user = userEvent.setup();
    render(<BtrfsRaid1Panel />);

    await user.click(await screen.findByRole("button", { name: "预览 scrub" }));
    await waitFor(() =>
      expect(planOmvBtrfsScrub).toHaveBeenCalledWith(scrubDesired),
    );
    expect(screen.getAllByText(/可能.*冗余副本修复/).length).toBeGreaterThan(0);

    await user.type(
      screen.getByLabelText("scrub 管理员密码"),
      "scrub-password",
    );
    await user.click(screen.getByRole("button", { name: "启动 Btrfs scrub" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.btrfs.scrub.start",
        scrubPlan.planId,
        "scrub-password",
      ),
    );
    expect(applyOmvBtrfsScrub).toHaveBeenCalledWith(
      scrubDesired,
      scrubPlan.planId,
      "approval-omv.btrfs.scrub.start",
    );
    expect(await screen.findByText(/scrub 已启动/)).toBeInTheDocument();
  });

  it("does not probe or display mutation controls without capabilities", async () => {
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      available: true,
      readOnly: true,
      adminUrl: null,
      capabilities: [],
      source: "native",
    });
    render(<BtrfsRaid1Panel />);

    expect(
      await screen.findByText(/未提供受控 Btrfs RAID1 创建或 scrub 能力/),
    ).toBeInTheDocument();
    expect(fetchOmvBtrfsRaid1Candidates).not.toHaveBeenCalled();
    expect(fetchOmvBtrfsMaintenance).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("keeps scrub unavailable for incomplete, read-only or active filesystems", async () => {
    vi.mocked(fetchOmvBtrfsMaintenance).mockResolvedValue([
      {
        ...maintenance,
        filesystem: {
          ...maintenance.filesystem,
          status: "degraded",
          activeDevices: 1,
          missingDevices: 1,
          readOnly: true,
        },
        scan: { ...maintenance.scan, state: "inProgress", progressPercent: 42 },
        canStartScrub: false,
      },
    ]);
    render(<BtrfsRaid1Panel />);

    const button = await screen.findByRole("button", { name: "预览 scrub" });
    expect(button).toBeDisabled();
    expect(screen.getByText(/scrub 进行中 · 42%/)).toBeInTheDocument();
    expect(screen.getByText(/不满足安全启动条件/)).toBeInTheDocument();
  });
});
