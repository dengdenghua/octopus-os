import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SystemControlState } from "@/types/electron";
import {
  downloadDiagnosticBundle,
  fetchServiceHealth,
} from "@/appliance/diagnostics";

import { SystemDeviceSettings } from "./system-device-settings";

vi.mock("@/appliance/diagnostics", () => ({
  downloadDiagnosticBundle: vi.fn(),
  fetchServiceHealth: vi.fn(),
}));

const healthyServices = {
  schema: "echo.appliance-diagnostics-services.v1" as const,
  state: "healthy" as const,
  available: true,
  checkedAt: "2026-09-09T01:02:03Z",
  counts: { monitored: 7, expected: 5, active: 5, failed: 0, restarts: 2 },
  alerts: { total: 0, bySeverity: {}, codes: [] },
};

const nativeControls: SystemControlState = {
  nativeShell: true,
  wifi: { available: true, enabled: true, connection: "Echo Lab" },
  bluetooth: {
    available: true,
    present: true,
    enabled: false,
    controller: "hci0",
  },
  audio: { available: true, volume: 42, muted: false },
  display: { available: true, brightness: 78 },
  battery: {
    available: true,
    present: true,
    percentage: 86,
    state: "Discharging",
  },
};

describe("SystemDeviceSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchServiceHealth).mockResolvedValue(healthyServices);
  });

  it("operates real native connectivity controls", async () => {
    const user = userEvent.setup();
    const onSetWifiEnabled = vi.fn();
    const onSetBluetoothEnabled = vi.fn();
    render(
      <SystemDeviceSettings
        section="connectivity"
        controls={nativeControls}
        onSetWifiEnabled={onSetWifiEnabled}
        onSetBluetoothEnabled={onSetBluetoothEnabled}
      />,
    );

    expect(screen.getByText("Echo Lab")).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Wi-Fi" }));
    await user.click(screen.getByRole("switch", { name: "蓝牙" }));
    expect(onSetWifiEnabled).toHaveBeenCalledWith(false);
    expect(onSetBluetoothEnabled).toHaveBeenCalledWith(true);
  });

  it("commits native display and audio sliders", () => {
    const onSetDisplayBrightness = vi.fn();
    const onSetAudioVolume = vi.fn();
    render(
      <SystemDeviceSettings
        section="displaySound"
        controls={nativeControls}
        onSetDisplayBrightness={onSetDisplayBrightness}
        onSetAudioVolume={onSetAudioVolume}
      />,
    );

    const brightness = screen.getByRole("slider", { name: "显示器亮度" });
    fireEvent.change(brightness, { target: { value: "65" } });
    fireEvent.keyUp(brightness, { key: "ArrowLeft" });
    expect(onSetDisplayBrightness).toHaveBeenCalledWith(65);

    const volume = screen.getByRole("slider", { name: "系统音量" });
    fireEvent.change(volume, { target: { value: "35" } });
    fireEvent.keyUp(volume, { key: "ArrowLeft" });
    expect(onSetAudioVolume).toHaveBeenCalledWith(35);
  });

  it("uses the shared OS wallpaper rather than a workbench theme", async () => {
    const user = userEvent.setup();
    const onWallpaperChange = vi.fn();
    render(
      <SystemDeviceSettings
        section="wallpaper"
        wallpaper="orbit"
        onWallpaperChange={onWallpaperChange}
      />,
    );

    expect(
      screen.getByText(/液态玻璃效果统一采样当前系统壁纸/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /极光/ }));
    expect(onWallpaperChange).toHaveBeenCalledWith("aurora");
  });

  it("shows a truthful unavailable state outside the native shell", () => {
    render(<SystemDeviceSettings section="connectivity" controls={null} />);

    expect(
      screen.getByText(/仅在 Echo OS 原生 Linux 会话中可用/),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Wi-Fi" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "蓝牙" })).toBeDisabled();
  });

  it("exports the privacy-preserving support bundle from general settings", async () => {
    const user = userEvent.setup();
    vi.mocked(downloadDiagnosticBundle).mockResolvedValue(
      "echo-diagnostics-20260908T123456Z.zip",
    );
    render(<SystemDeviceSettings section="general" />);

    expect(
      screen.getByText(/不包含原始日志、账号、主机\/IP/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "导出诊断包" }));

    expect(downloadDiagnosticBundle).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "echo-diagnostics-20260908T123456Z.zip",
    );
  });

  it("shows bounded system service health and allows a fresh probe", async () => {
    const user = userEvent.setup();
    render(<SystemDeviceSettings section="general" />);

    expect(
      await screen.findByText("5 个应运行服务状态正常，累计重启 2 次"),
    ).toBeInTheDocument();
    expect(fetchServiceHealth).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "刷新系统服务健康" }));
    await waitFor(() => expect(fetchServiceHealth).toHaveBeenCalledTimes(2));
  });

  it("offers a durable entry point for reopening the first-use guide", async () => {
    const user = userEvent.setup();
    const onOpenGettingStarted = vi.fn();
    render(
      <SystemDeviceSettings
        section="general"
        onOpenGettingStarted={onOpenGettingStarted}
      />,
    );

    expect(
      screen.getByText(/数据目录、模型连接、执行权限和首个任务/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开使用引导" }));
    expect(onOpenGettingStarted).toHaveBeenCalledOnce();
  });

  it("keeps diagnostic export failures visible without disabling retry", async () => {
    const user = userEvent.setup();
    vi.mocked(downloadDiagnosticBundle).mockRejectedValueOnce(
      new Error("诊断服务暂不可用"),
    );
    render(<SystemDeviceSettings section="general" />);

    const button = screen.getByRole("button", { name: "导出诊断包" });
    await user.click(button);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "诊断服务暂不可用",
    );
    await waitFor(() => expect(button).toBeEnabled());
  });
});
