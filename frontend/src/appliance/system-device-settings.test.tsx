import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { SystemControlState } from "@/types/electron";

import { SystemDeviceSettings } from "./system-device-settings";

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
  battery: { available: true, present: true, percentage: 86, state: "Discharging" },
};

describe("SystemDeviceSettings", () => {
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

    expect(screen.getByText(/液态玻璃效果统一采样当前系统壁纸/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /极光/ }));
    expect(onWallpaperChange).toHaveBeenCalledWith("aurora");
  });

  it("shows a truthful unavailable state outside the native shell", () => {
    render(<SystemDeviceSettings section="connectivity" controls={null} />);

    expect(screen.getByText(/仅在 Echo OS 原生 Linux 会话中可用/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Wi-Fi" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "蓝牙" })).toBeDisabled();
  });
});
