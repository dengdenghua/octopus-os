import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchOmvFilesystems,
  fetchOmvHealth,
  fetchOmvSmart,
  fetchOmvSmartDevices,
  fetchOmvStorageTopology,
  fetchOmvStatus,
} from "./omv";
import { OmvStorageHealth } from "./omv-storage-health";

vi.mock("./omv", () => ({
  fetchOmvFilesystems: vi.fn(),
  fetchOmvHealth: vi.fn(),
  fetchOmvSmart: vi.fn(),
  fetchOmvSmartDevices: vi.fn(),
  fetchOmvStorageTopology: vi.fn(),
  fetchOmvStatus: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchOmvStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: true,
    adminUrl: "https://nas.example.test",
  });
  vi.mocked(fetchOmvHealth).mockResolvedValue({
    schemaVersion: 1,
    state: "critical",
    stale: false,
    checkedAt: "2026-08-26T01:05:00Z",
    lastSuccessfulAt: "2026-08-26T01:05:00Z",
    intervalSeconds: 300,
    persistenceHealthy: true,
    monitoring: true,
    activeAlerts: [
      {
        id: "111111111111111111111111",
        code: "raid.degraded",
        severity: "critical",
        resource: "/dev/md0",
        message: "软件阵列已降级",
        firstSeenAt: "2026-08-26T01:00:00Z",
        lastSeenAt: "2026-08-26T01:05:00Z",
        occurrences: 2,
      },
    ],
    events: [
      {
        id: "222222222222222222222222",
        alertId: "111111111111111111111111",
        event: "opened",
        at: "2026-08-26T01:00:00Z",
        code: "raid.degraded",
        severity: "critical",
        resource: "/dev/md0",
        message: "软件阵列已降级",
      },
    ],
    summary: { critical: 1, warning: 0, total: 1 },
    readOnly: true,
  });
  vi.mocked(fetchOmvFilesystems).mockResolvedValue([
    {
      devicefile: "/dev/sda1",
      parentdevicefile: "/dev/sda",
      uuid: "volume-uuid",
      label: "Family",
      type: "ext4",
      mountpoint: "/srv/family",
      sizeBytes: 1_000_000,
      availableBytes: 750_000,
      usedPercent: 25,
      readOnly: false,
      supportsAcl: true,
      supportsQuota: true,
    },
  ]);
  vi.mocked(fetchOmvSmartDevices).mockResolvedValue([
    {
      devicefile: "/dev/sda",
      model: "Example Disk",
      sizeBytes: 2_000_000,
      health: "GOOD",
      temperatureC: 31,
    },
  ]);
  vi.mocked(fetchOmvStorageTopology).mockResolvedValue({
    devices: [
      {
        devicefile: "/dev/sda",
        type: "disk",
        sizeBytes: 2_000_000,
        filesystemType: null,
        rotational: true,
        parentDevicefiles: [],
      },
      {
        devicefile: "/dev/sda1",
        type: "part",
        sizeBytes: 1_900_000,
        filesystemType: "linux_raid_member",
        rotational: true,
        parentDevicefiles: ["/dev/sda"],
      },
      {
        devicefile: "/dev/sdb1",
        type: "part",
        sizeBytes: 1_900_000,
        filesystemType: "linux_raid_member",
        rotational: true,
        parentDevicefiles: ["/dev/sdb"],
      },
      {
        devicefile: "/dev/md0",
        type: "raid1",
        sizeBytes: 1_900_000,
        filesystemType: "LVM2_member",
        rotational: true,
        parentDevicefiles: ["/dev/sda1", "/dev/sdb1"],
      },
      {
        devicefile: "/dev/mapper/vg-data",
        type: "lvm",
        sizeBytes: 1_800_000,
        filesystemType: "ext4",
        rotational: true,
        parentDevicefiles: ["/dev/md0"],
      },
    ],
    arrays: [
      {
        devicefile: "/dev/md0",
        level: "raid1",
        status: "degraded",
        totalDevices: 2,
        activeDevices: 1,
        operation: null,
        operationPercent: null,
      },
    ],
  });
  vi.mocked(fetchOmvSmart).mockResolvedValue({
    devicefile: "/dev/sda",
    model: "Example Disk",
    health: "PASSED",
    temperatureC: 31,
    powerOnHours: 1_234,
    powerCycles: 42,
  });
});

describe("OMV storage health settings", () => {
  it("shows mounted capacity and loads sanitized SMART data on demand", async () => {
    const user = userEvent.setup();
    render(<OmvStorageHealth />);

    expect(await screen.findByText("Family")).toBeInTheDocument();
    expect(screen.getByText("物理磁盘")).toBeInTheDocument();
    expect(screen.getByText("存储拓扑")).toBeInTheDocument();
    expect(screen.getByText("Example Disk")).toBeInTheDocument();
    expect(screen.getByText("已使用 25%")).toBeInTheDocument();
    expect(screen.getByText("/dev/sda1 + /dev/sdb1")).toBeInTheDocument();
    expect(screen.getByText("RAID1 · 已降级 1/2")).toBeInTheDocument();
    expect(screen.getByText("持续监测发现严重故障")).toBeInTheDocument();
    expect(screen.getByText("软件阵列已降级")).toBeInTheDocument();
    expect(screen.getByText(/连续 2 次/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看 SMART" }));

    await waitFor(() => expect(fetchOmvSmart).toHaveBeenCalledWith("/dev/sda"));
    expect(await screen.findByText("PASSED")).toBeInTheDocument();
    expect(screen.getAllByText("31°C")).toHaveLength(2);
  });

  it("explains that an unconfigured bridge does not block other features", async () => {
    vi.mocked(fetchOmvStatus).mockResolvedValue({
      configured: false,
      available: false,
      readOnly: true,
      adminUrl: null,
    });
    render(<OmvStorageHealth />);

    expect(
      await screen.findByText("尚未接入 OpenMediaVault"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Echo 其他桌面、Agent 和文件功能不受影响。"),
    ).toBeInTheDocument();
    expect(fetchOmvFilesystems).not.toHaveBeenCalled();
    expect(fetchOmvHealth).not.toHaveBeenCalled();
    expect(fetchOmvSmartDevices).not.toHaveBeenCalled();
    expect(fetchOmvStorageTopology).not.toHaveBeenCalled();
  });

  it("keeps the persistent alert visible while the OMV bridge is offline", async () => {
    vi.mocked(fetchOmvStatus).mockResolvedValue({
      configured: true,
      available: false,
      readOnly: true,
      adminUrl: "https://nas.example.test",
    });
    vi.mocked(fetchOmvHealth).mockResolvedValue({
      schemaVersion: 1,
      state: "unavailable",
      stale: true,
      checkedAt: "2026-08-26T01:10:00Z",
      lastSuccessfulAt: "2026-08-26T01:05:00Z",
      intervalSeconds: 300,
      persistenceHealthy: true,
      monitoring: true,
      activeAlerts: [
        {
          id: "333333333333333333333333",
          code: "bridge.unavailable",
          severity: "critical",
          resource: "openmediavault",
          message: "OMV 只读桥暂时不可用，之前的存储状态已标记为过期",
          firstSeenAt: "2026-08-26T01:10:00Z",
          lastSeenAt: "2026-08-26T01:10:00Z",
          occurrences: 1,
        },
      ],
      events: [],
      summary: { critical: 1, warning: 0, total: 1 },
      readOnly: true,
    });

    render(<OmvStorageHealth />);

    expect(
      await screen.findByText("持续监测：OMV 连接中断"),
    ).toBeInTheDocument();
    expect(screen.getByText(/当前数据已过期/)).toBeInTheDocument();
    expect(fetchOmvHealth).toHaveBeenCalledOnce();
    expect(fetchOmvFilesystems).not.toHaveBeenCalled();
  });
});
