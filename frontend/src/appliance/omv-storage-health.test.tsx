import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchNativeFilesystems,
  fetchNativeHealth,
  fetchNativeSmart,
  fetchNativeSmartDevices,
  fetchNativeStatus,
  fetchNativeStorageTopology,
  type OmvHealthSnapshot,
  type OmvSmart,
} from "./omv";
import { OmvStorageHealth } from "./omv-storage-health";

vi.mock("./omv", () => ({
  fetchNativeFilesystems: vi.fn(),
  fetchNativeHealth: vi.fn(),
  fetchNativeSmart: vi.fn(),
  fetchNativeSmartDevices: vi.fn(),
  fetchNativeStatus: vi.fn(),
  fetchNativeStorageTopology: vi.fn(),
}));

function completeHealth(
  overrides: Partial<OmvHealthSnapshot> = {},
): OmvHealthSnapshot {
  return {
    schemaVersion: 1,
    state: "healthy",
    stale: false,
    checkedAt: "2026-09-05T01:00:00Z",
    lastSuccessfulAt: "2026-09-05T01:00:00Z",
    intervalSeconds: 0,
    persistenceHealthy: null,
    persistence: "not-applicable",
    monitoring: false,
    activeAlerts: [],
    events: [],
    summary: { critical: 0, warning: 0, total: 0 },
    readOnly: true,
    available: true,
    coverage: "complete",
    probeEvidence: [
      {
        source: "block-devices",
        state: "ok",
        count: 1,
        required: true,
        checkedAt: "2026-09-05T01:00:00Z",
      },
      {
        source: "smart",
        target: "/dev/sda",
        state: "ok",
        required: true,
        checkedAt: "2026-09-05T01:00:00Z",
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: true,
    adminUrl: null,
    capabilities: [],
    source: "native",
  });
  vi.mocked(fetchNativeHealth).mockResolvedValue({
    schemaVersion: 1,
    state: "critical",
    stale: false,
    checkedAt: "2026-08-26T01:05:00Z",
    lastSuccessfulAt: "2026-08-26T01:05:00Z",
    intervalSeconds: 0,
    persistenceHealthy: true,
    monitoring: false,
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
    source: "native",
  });
  vi.mocked(fetchNativeFilesystems).mockResolvedValue([
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
  vi.mocked(fetchNativeSmartDevices).mockResolvedValue([
    {
      devicefile: "/dev/sda",
      model: "Example Disk",
      sizeBytes: 2_000_000,
      health: "GOOD",
      temperatureC: 31,
    },
  ]);
  vi.mocked(fetchNativeStorageTopology).mockResolvedValue({
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
    readOnly: true,
  });
  vi.mocked(fetchNativeSmart).mockResolvedValue({
    devicefile: "/dev/sda",
    model: "Example Disk",
    health: "PASSED",
    temperatureC: 31,
    powerOnHours: 1_234,
    powerCycles: 42,
  });
});

describe("native storage health settings", () => {
  it("shows mounted capacity and loads SMART data on demand", async () => {
    const user = userEvent.setup();
    render(<OmvStorageHealth />);

    expect(await screen.findByText("Family")).toBeInTheDocument();
    expect(screen.getByText("物理磁盘")).toBeInTheDocument();
    expect(screen.getByText("存储拓扑")).toBeInTheDocument();
    expect(screen.getByText("Example Disk")).toBeInTheDocument();
    expect(screen.getByText("已使用 25%")).toBeInTheDocument();
    expect(screen.getByText("/dev/sda1 + /dev/sdb1")).toBeInTheDocument();
    expect(screen.getByText("RAID1 · 已降级 1/2")).toBeInTheDocument();
    expect(screen.getByText("本次检查发现严重故障")).toBeInTheDocument();
    expect(screen.getByText("软件阵列已降级")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看 SMART" }));

    await waitFor(() =>
      expect(fetchNativeSmart).toHaveBeenCalledWith("/dev/sda"),
    );
    expect(await screen.findAllByText("PASSED")).toHaveLength(2);
    expect(screen.getAllByText("31°C")).toHaveLength(2);
  });

  it("reports the native plane as connected without any OMV wording", async () => {
    render(<OmvStorageHealth />);

    expect(await screen.findByText("原生存储面已连接")).toBeInTheDocument();
    expect(screen.getByText(/只读显示本机存储卷/)).toBeInTheDocument();
    expect(screen.queryByText(/OpenMediaVault/i)).not.toBeInTheDocument();
    expect(fetchNativeFilesystems).toHaveBeenCalledOnce();
  });

  it("surfaces degraded arrays from the native health snapshot", async () => {
    render(<OmvStorageHealth />);

    expect(await screen.findByText("软件阵列已降级")).toBeInTheDocument();
    expect(screen.getByText(/连续 2 次/)).toBeInTheDocument();
  });

  it("only shows green overall health with complete fresh evidence", async () => {
    vi.mocked(fetchNativeHealth).mockResolvedValue(completeHealth());
    render(<OmvStorageHealth />);

    const summary = await screen.findByRole("region", { name: "存储健康检查" });
    expect(summary).toHaveClass("bg-emerald-50");
    expect(
      within(summary).getByText("本次检查已完成，已检查项目未发现异常"),
    ).toBeInTheDocument();
    expect(screen.getByText("按需检查")).toBeInTheDocument();
    expect(screen.queryByText(/每 1 分钟/)).not.toBeInTheDocument();
    expect(screen.queryByText(/告警状态无法安全写入/)).not.toBeInTheDocument();
  });

  it.each(["pending", "unknown", "notConfigured"] as const)(
    "does not turn %s without alerts into a healthy result",
    async (state) => {
      vi.mocked(fetchNativeHealth).mockResolvedValue(
        completeHealth({ state, coverage: "none", probeEvidence: [] }),
      );
      render(<OmvStorageHealth />);

      const summary = await screen.findByRole("region", {
        name: "存储健康检查",
      });
      expect(summary).not.toHaveClass("bg-emerald-50");
      expect(summary).not.toHaveTextContent("未发现异常");
      expect(summary).toHaveTextContent("尚无足够检查结果判断整体健康");
      expect(screen.getByText("Family")).toBeInTheDocument();
    },
  );

  it.each([
    { stale: true },
    { probeEvidence: undefined, coverage: undefined },
    { checkedAt: null },
    { checkedAt: "not-a-date" },
  ])(
    "does not promote stale or unsubstantiated healthy snapshots: %j",
    async (overrides) => {
      vi.mocked(fetchNativeHealth).mockResolvedValue(completeHealth(overrides));
      render(<OmvStorageHealth />);

      const summary = await screen.findByRole("region", {
        name: "存储健康检查",
      });
      expect(summary).not.toHaveClass("bg-emerald-50");
      expect(summary).not.toHaveTextContent("未发现异常");
    },
  );

  it("shows zero devices as unconfirmed with a connection action", async () => {
    const probe = {
      source: "block-devices",
      state: "empty",
      code: "empty_inventory",
      count: 0,
      required: true,
      checkedAt: "2026-09-05T01:00:00Z",
    } as const;
    vi.mocked(fetchNativeHealth).mockResolvedValue(
      completeHealth({
        state: "unknown",
        available: false,
        coverage: "none",
        probeEvidence: [probe],
      }),
    );
    vi.mocked(fetchNativeStatus).mockResolvedValue({
      configured: true,
      available: false,
      readOnly: true,
      adminUrl: null,
      capabilities: [],
      probeEvidence: [probe],
    });
    vi.mocked(fetchNativeSmartDevices).mockResolvedValue([]);
    vi.mocked(fetchNativeStorageTopology).mockResolvedValue({
      devices: [],
      arrays: [],
    });
    vi.mocked(fetchNativeFilesystems).mockResolvedValue([]);
    render(<OmvStorageHealth />);

    expect(await screen.findByText("未检测到存储设备")).toBeInTheDocument();
    expect(
      screen.getByText(/请确认磁盘连接或虚拟机磁盘映射后刷新/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "存储健康检查" }),
    ).not.toHaveClass("bg-emerald-50");
    expect(screen.queryByText(/当前是直连磁盘/)).not.toBeInTheDocument();
  });

  it("preserves known healthy disks and capacity when another SMART probe is missing", async () => {
    vi.mocked(fetchNativeHealth).mockResolvedValue(
      completeHealth({
        state: "degraded",
        stale: true,
        coverage: "partial",
        probeEvidence: [
          {
            source: "smart",
            target: "/dev/sdb",
            state: "unavailable",
            code: "tool_missing",
            required: true,
            checkedAt: "2026-09-05T01:00:00Z",
          },
        ],
      }),
    );
    vi.mocked(fetchNativeSmartDevices).mockResolvedValue([
      {
        devicefile: "/dev/sda",
        model: "Known Disk",
        sizeBytes: 100,
        health: "PASSED",
        temperatureC: 30,
      },
      {
        devicefile: "/dev/sdb",
        model: "Unknown Disk",
        sizeBytes: 100,
        health: "UNKNOWN",
        temperatureC: null,
      },
    ]);
    render(<OmvStorageHealth />);

    expect(await screen.findByText("Family")).toBeInTheDocument();
    expect(screen.getByText("PASSED")).toHaveClass("text-emerald-700");
    expect(screen.getByText("健康状态未知")).toHaveClass("text-slate-600");
    expect(screen.getByText(/SMART 组件尚未安装/)).toBeInTheDocument();
    expect(screen.queryByText(/发现严重故障/)).not.toBeInTheDocument();
  });

  it.each([fetchNativeHealth, fetchNativeSmartDevices, fetchNativeStatus])(
    "keeps independent volume results after a probe request fails",
    async (request) => {
      vi.mocked(request).mockRejectedValue(new Error("权限不足"));
      render(<OmvStorageHealth />);

      expect(await screen.findByText("Family")).toBeInTheDocument();
      expect(screen.getByText("已使用 25%")).toBeInTheDocument();
      expect(screen.getByRole("alert")).toHaveTextContent("权限不足");
      expect(screen.getByRole("alert")).toHaveTextContent(
        "已成功读取的卷和磁盘仍显示",
      );
      expect(
        screen.getByRole("region", { name: "存储健康检查" }),
      ).not.toHaveClass("bg-emerald-50");
    },
  );

  it("keeps real critical alerts visible when coverage is incomplete", async () => {
    const previous = await vi.mocked(fetchNativeHealth)();
    vi.mocked(fetchNativeHealth).mockResolvedValue({
      ...previous,
      stale: true,
      coverage: "partial",
      persistenceHealthy: null,
    });
    render(<OmvStorageHealth />);

    expect(await screen.findByText("软件阵列已降级")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "存储健康检查" })).toHaveClass(
      "bg-red-50",
    );
    expect(
      screen.getByText(/当前数据不完整或已过期，已知告警仍需处理/),
    ).toBeInTheDocument();
  });

  it.each([null, 0])(
    "does not turn missing volume capacity into zero percent usage: %s",
    async (usedPercent) => {
      const [volume] = await vi.mocked(fetchNativeFilesystems)();
      vi.mocked(fetchNativeFilesystems).mockResolvedValue([
        { ...volume!, sizeBytes: 0, availableBytes: 0, usedPercent },
      ]);
      render(<OmvStorageHealth />);

      expect(await screen.findByText("容量使用率未知")).toBeInTheDocument();
      expect(screen.queryByText("已使用 0%")).not.toBeInTheDocument();
    },
  );

  it("honors per-device missing evidence while preserving a reported disk failure", async () => {
    vi.mocked(fetchNativeHealth).mockResolvedValue(
      completeHealth({ state: "degraded", coverage: "partial", stale: true }),
    );
    const partialProbe = {
      source: "smart",
      state: "partial",
      code: "command_incomplete",
      required: true,
      checkedAt: "2026-09-05T01:00:00Z",
    } as const;
    vi.mocked(fetchNativeSmartDevices).mockResolvedValue([
      {
        devicefile: "/dev/sda",
        model: "Incomplete Disk",
        sizeBytes: 100,
        health: "PASSED",
        temperatureC: 30,
        coverage: "partial",
        probeEvidence: [partialProbe],
      },
      {
        devicefile: "/dev/sdb",
        model: "Failing Disk",
        sizeBytes: 100,
        health: "FAILED",
        temperatureC: 40,
        coverage: "partial",
        probeEvidence: [partialProbe],
      },
    ]);
    render(<OmvStorageHealth />);

    expect(await screen.findByText("健康状态未知")).toHaveClass(
      "text-slate-600",
    );
    expect(screen.getByText("FAILED")).toHaveClass("text-amber-800");
    expect(screen.queryByText("PASSED")).not.toBeInTheDocument();
    expect(screen.getByText("Family")).toBeInTheDocument();
  });

  it("discards SMART replies from before a refresh", async () => {
    const user = userEvent.setup();
    let resolve!: (value: OmvSmart) => void;
    vi.mocked(fetchNativeSmart).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    render(<OmvStorageHealth />);
    await screen.findByText("Family");
    await user.click(screen.getByRole("button", { name: "查看 SMART" }));
    await user.click(screen.getByRole("button", { name: "刷新" }));
    await screen.findByText("Family");

    await act(async () =>
      resolve({
        devicefile: "/dev/sda",
        model: "Old Probe",
        health: "FAILED",
        temperatureC: 65,
        powerOnHours: 0,
        powerCycles: 0,
      }),
    );

    expect(screen.queryByText("FAILED")).not.toBeInTheDocument();
    expect(screen.queryByText("Old Probe")).not.toBeInTheDocument();
  });
});
