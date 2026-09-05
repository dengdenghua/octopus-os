import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvExt4Volume,
  fetchNativeStatus,
  fetchOmvExt4VolumeCandidates,
  planOmvExt4Volume,
} from "./omv";
import { Ext4VolumePanel } from "./ext4-volume-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./ext4-check-panel", () => ({ Ext4CheckPanel: () => null }));
vi.mock("./omv", () => ({
  applyOmvExt4Volume: vi.fn(),
  fetchNativeStatus: vi.fn(),
  fetchOmvExt4VolumeCandidates: vi.fn(),
  planOmvExt4Volume: vi.fn(),
}));

const array = {
  name: "array1",
  devicefile: "/dev/md/echo-array1",
  uuid: "11111111:22222222:33333333:44444444",
  level: "raid1" as const,
  devices: ["/dev/sdb", "/dev/sdc"],
  filesystem: null,
};

const desired = {
  schema: "echo.omv.ext4-volume-desired.v1" as const,
  arrayUuid: array.uuid,
  name: "family",
  dataLossConfirmed: true as const,
};

const plan = {
  schema: "echo.omv.ext4-volume-plan.v1" as const,
  planId: "e".repeat(64),
  baseRevision: "f".repeat(64),
  operation: "createAndMount" as const,
  requiresApproval: true as const,
  desired,
  array,
  mountpoint: "/data/family",
  safety: {
    destructive: true as const,
    dataLossConfirmed: true as const,
    source: "healthyBlankEchoManagedMdRaid1Only" as const,
    filesystem: "ext4Only" as const,
    mountRoot: "/data",
    persistentIdentity: "filesystemUuid" as const,
    force: false as const,
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: ["storage.volume.ext4.create-mount.v1"],
    source: "native",
  });
  vi.mocked(fetchOmvExt4VolumeCandidates).mockResolvedValue([array]);
  vi.mocked(planOmvExt4Volume).mockResolvedValue(plan);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "one-shot",
    expiresIn: 90,
    action: "omv.ext4-volume.create",
    target: plan.planId,
  });
  vi.mocked(applyOmvExt4Volume).mockResolvedValue({
    ...plan,
    applied: true,
    verified: true,
    filesystem: {
      uuid: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      label: "family",
      type: "ext4",
      devicefile: array.devicefile,
      mountpoint: "/data/family",
      readOnly: false,
    },
  });
});

describe("EXT4 volume panel", () => {
  it("runs managed-array selection, preview, step-up and mount", async () => {
    const user = userEvent.setup();
    render(<Ext4VolumePanel />);

    await user.click(
      await screen.findByRole("button", { name: /\/dev\/md\/echo-array1/ }),
    );
    await user.type(
      screen.getByRole("textbox", { name: /数据卷名称/ }),
      "family",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "生成格式化预览" }));

    await waitFor(() =>
      expect(planOmvExt4Volume).toHaveBeenCalledWith(desired),
    );
    expect(
      screen.getByText(/按新文件系统 UUID 挂载到 \/data\/family/),
    ).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("设备管理员密码"),
      "correct-password",
    );
    await user.click(screen.getByRole("button", { name: "确认格式化并挂载" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.ext4-volume.create",
        plan.planId,
        "correct-password",
      ),
    );
    expect(applyOmvExt4Volume).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "one-shot",
    );
    expect(
      await screen.findByText("EXT4 卷已创建并挂载到 /data/family"),
    ).toBeInTheDocument();
  });

  it("does not enumerate arrays when the host capability is unavailable", async () => {
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: null,
      capabilities: [],
      source: "native",
    });
    render(<Ext4VolumePanel />);

    expect(
      await screen.findByText("当前主机未提供受控 EXT4 创建与挂载能力。"),
    ).toBeInTheDocument();
    expect(fetchOmvExt4VolumeCandidates).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
