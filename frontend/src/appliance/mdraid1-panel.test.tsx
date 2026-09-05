import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvMdRaid1,
  fetchNativeStatus,
  fetchOmvMdRaid1Candidates,
  planOmvMdRaid1,
} from "./omv";
import { MdRaid1Panel } from "./mdraid1-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({
  applyOmvMdRaid1: vi.fn(),
  fetchNativeStatus: vi.fn(),
  fetchOmvMdRaid1Candidates: vi.fn(),
  planOmvMdRaid1: vi.fn(),
}));

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
  schema: "echo.omv.mdraid1-desired.v1" as const,
  name: "family",
  devices: ["/dev/sdb", "/dev/sdc"] as [string, string],
  dataLossConfirmed: true as const,
};

const plan = {
  schema: "echo.omv.mdraid1-plan.v1" as const,
  planId: "a".repeat(64),
  baseRevision: "b".repeat(64),
  operation: "create" as const,
  target: "/dev/md/echo-family",
  requiresApproval: true as const,
  desired,
  devices: [...candidates] as [
    (typeof candidates)[number],
    (typeof candidates)[number],
  ],
  usableBytes: 8 * 1024 ** 3,
  filesystemCreated: false as const,
  safety: {
    destructive: true as const,
    dataLossConfirmed: true as const,
    layout: "twoDiskRaid1Only" as const,
    devices: "wholeBlankNonRemovableWithPersistentIdentity" as const,
    force: false as const,
    degradedStart: false as const,
    filesystemCreated: false as const,
    unsupported: ["filesystemCreate", "mount"],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: ["storage.array.mdraid1.create.v1"],
    source: "native",
  });
  vi.mocked(fetchOmvMdRaid1Candidates).mockResolvedValue([...candidates]);
  vi.mocked(planOmvMdRaid1).mockResolvedValue(plan);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "one-shot",
    expiresIn: 90,
    action: "omv.mdraid1.create",
    target: plan.planId,
  });
  vi.mocked(applyOmvMdRaid1).mockResolvedValue({
    ...plan,
    applied: true,
    verified: true,
    array: {
      name: "family",
      devicefile: "/dev/md/echo-family",
      uuid: "11111111:22222222:33333333:44444444",
      level: "raid1",
      devices: ["/dev/sdb", "/dev/sdc"],
      filesystem: null,
    },
  });
});

describe("Linux RAID1 panel", () => {
  it("runs candidate selection, destructive preview, step-up and apply", async () => {
    const user = userEvent.setup();
    render(<MdRaid1Panel />);

    await user.click(await screen.findByRole("button", { name: /\/dev\/sdb/ }));
    await user.click(screen.getByRole("button", { name: /\/dev\/sdc/ }));
    await user.type(
      screen.getByRole("textbox", { name: /阵列名称/ }),
      "family",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "生成阵列预览" }));

    await waitFor(() => expect(planOmvMdRaid1).toHaveBeenCalledWith(desired));
    expect(screen.getByText(/创建后仍不能存文件/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("设备管理员密码"),
      "correct-password",
    );
    await user.click(
      screen.getByRole("button", { name: "确认清空并创建 RAID1" }),
    );

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.mdraid1.create",
        plan.planId,
        "correct-password",
      ),
    );
    expect(applyOmvMdRaid1).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "one-shot",
    );
    expect(
      await screen.findByText(/已创建并验证；尚未格式化或挂载/),
    ).toBeInTheDocument();
  });

  it("does not probe disks or show destructive controls without the capability", async () => {
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: null,
      capabilities: [],
      source: "native",
    });
    render(<MdRaid1Panel />);

    expect(
      await screen.findByText(/当前主机未提供受控 Linux RAID1 创建能力/),
    ).toBeInTheDocument();
    expect(fetchOmvMdRaid1Candidates).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
