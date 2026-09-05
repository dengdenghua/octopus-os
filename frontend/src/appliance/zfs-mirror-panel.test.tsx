import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvZfsMirror,
  fetchNativeStatus,
  fetchOmvZfsMirrorCandidates,
  planOmvZfsMirror,
} from "./omv";
import { ZfsMirrorPanel } from "./zfs-mirror-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({
  applyOmvZfsMirror: vi.fn(),
  applyOmvZfsMirrorReplace: vi.fn(),
  applyOmvZfsPoolExport: vi.fn(),
  applyOmvZfsPoolImport: vi.fn(),
  fetchNativeStatus: vi.fn(),
  fetchOmvZfsImportCandidates: vi.fn(),
  fetchOmvZfsMirrorReplacementCandidates: vi.fn(),
  fetchOmvZfsMirrorCandidates: vi.fn(),
  fetchOmvZfsPools: vi.fn(),
  planOmvZfsMirror: vi.fn(),
  planOmvZfsMirrorReplace: vi.fn(),
  planOmvZfsPoolExport: vi.fn(),
  planOmvZfsPoolImport: vi.fn(),
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
  schema: "echo.omv.zfs-mirror-desired.v1" as const,
  name: "family",
  devices: ["/dev/sdb", "/dev/sdc"] as [string, string],
  dataLossConfirmed: true as const,
};

const plan = {
  schema: "echo.omv.zfs-mirror-plan.v1" as const,
  planId: "a".repeat(64),
  baseRevision: "b".repeat(64),
  operation: "create" as const,
  requiresApproval: true as const,
  desired,
  devices: [...candidates] as [
    (typeof candidates)[number],
    (typeof candidates)[number],
  ],
  mountpoint: "/data/family",
  safety: {
    destructive: true as const,
    dataLossConfirmed: true as const,
    layout: "twoDiskMirrorOnly" as const,
    devices: "wholeBlankNonRemovableWithPersistentIdentity" as const,
    force: false as const,
    rollback: "bestEffortPoolDestroyBeforeHandoff" as const,
    unsupported: ["expand"],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: ["storage.pool.zfs-mirror.create.v1"],
    source: "native",
  });
  vi.mocked(fetchOmvZfsMirrorCandidates).mockResolvedValue([...candidates]);
  vi.mocked(planOmvZfsMirror).mockResolvedValue(plan);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "one-shot",
    expiresIn: 90,
    action: "omv.zfs-mirror.create",
    target: plan.planId,
  });
  vi.mocked(applyOmvZfsMirror).mockResolvedValue({
    ...plan,
    applied: true,
    verified: true,
    pool: {
      name: "family",
      health: "ONLINE",
      layout: "mirror",
      mountpoint: "/data/family",
      compression: "lz4",
      atime: "off",
      xattr: "sa",
      acltype: "posix",
    },
  });
});

describe("ZFS mirror panel", () => {
  it("runs candidate selection, destructive preview, step-up and apply", async () => {
    const user = userEvent.setup();
    render(<ZfsMirrorPanel />);

    await user.click(await screen.findByRole("button", { name: /\/dev\/sdb/ }));
    await user.click(screen.getByRole("button", { name: /\/dev\/sdc/ }));
    await user.type(
      screen.getByRole("textbox", { name: /存储池名称/ }),
      "family",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "生成建池预览" }));

    await waitFor(() => expect(planOmvZfsMirror).toHaveBeenCalledWith(desired));
    expect(screen.getByText(/挂载到 \/data\/family/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("设备管理员密码"),
      "correct-password",
    );
    await user.click(
      screen.getByRole("button", { name: "确认清空并创建镜像" }),
    );

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.zfs-mirror.create",
        plan.planId,
        "correct-password",
      ),
    );
    expect(applyOmvZfsMirror).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "one-shot",
    );
    expect(
      await screen.findByText(/存储池 family 已创建并验证为 ONLINE/),
    ).toBeInTheDocument();
  });

  it("does not probe disks or show destructive controls when tools are unavailable", async () => {
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: null,
      capabilities: [],
      source: "native",
    });
    render(<ZfsMirrorPanel />);

    expect(
      await screen.findByText(/当前主机未提供受控 ZFS 存储池能力/),
    ).toBeInTheDocument();
    expect(fetchOmvZfsMirrorCandidates).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("keeps preview disabled for reserved OpenZFS names", async () => {
    const user = userEvent.setup();
    render(<ZfsMirrorPanel />);

    await user.click(await screen.findByRole("button", { name: /\/dev\/sdb/ }));
    await user.click(screen.getByRole("button", { name: /\/dev\/sdc/ }));
    await user.type(
      screen.getByRole("textbox", { name: /存储池名称/ }),
      "mirrorhome",
    );
    await user.click(screen.getByRole("checkbox"));

    expect(screen.getByRole("button", { name: "生成建池预览" })).toBeDisabled();
  });
});
