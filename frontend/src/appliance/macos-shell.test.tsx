import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BotIcon, FolderIcon } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

import {
  MacControlCenter,
  MacAboutDialog,
  MacAppIcon,
  MacDesktopWallpaperArtwork,
  MacDesktopWidgets,
  MacLaunchpad,
  MacLiquidGlassPanel,
  MacMenuBar,
  MacNotificationCenter,
  MacSpotlight,
  MacSystemActionDialog,
  type MacShellApp,
} from "./macos-shell";
import {
  CLEAR_LIQUID_GLASS_TUNING,
  DEFAULT_LIQUID_GLASS_TUNING,
} from "./liquid-glass-settings";

const apps: MacShellApp[] = [
  {
    id: "workspace",
    name: "工作台",
    subtitle: "对话、编程、项目",
    icon: BotIcon,
    gradient: "linear-gradient(#7c5cff, #2b54d6)",
    onOpen: vi.fn(),
  },
  {
    id: "files",
    name: "文件",
    subtitle: "文件",
    icon: FolderIcon,
    gradient: "linear-gradient(#6bc9ff, #1d78d4)",
    onOpen: vi.fn(),
  },
];

const unverifiedAgentHealth = {
  state: "ready" as const,
  version: "0.2.0",
  sourceId: null,
  verifiedBundle: false,
};

describe("Echo desktop shell", () => {
  it("uses layered original artwork for built-in shell apps", () => {
    const { container } = render(
      <MacAppIcon
        icon={BotIcon}
        gradient="linear-gradient(#55c7ff, #087bd8)"
        appId="echo:/browser"
        liquidBackdrop
      />,
    );

    const icon = container.querySelector('[data-icon-source="art"]');
    expect(icon).toBeInTheDocument();
    expect(icon?.querySelector("svg")).toBeInTheDocument();
    expect(icon?.querySelector("circle")).toBeInTheDocument();
    expect(icon?.querySelector(".mac-app-icon-specular")).toBeInTheDocument();
    expect(
      icon?.querySelector(".mac-app-icon-liquid-backdrop > img"),
    ).toHaveAttribute(
      "src",
      "/third-party/appletechie-macos/wallpaper-day2.jpg",
    );
    expect(
      icon?.querySelector(".mac-app-icon-liquid-backdrop > svg"),
    ).toBeNull();
  });

  it("keeps ordinary first-party icons optically clean", () => {
    const { container } = render(
      <MacAppIcon
        icon={FolderIcon}
        gradient="linear-gradient(#6bc9ff, #1d78d4)"
        appId="system:finder"
      />,
    );

    const icon = container.querySelector('[data-app-id="system:finder"]');
    expect(icon).toHaveAttribute("data-echo-family", "true");
    expect(icon).toHaveAttribute("data-icon-source", "art");
    expect(icon?.querySelector(".mac-app-icon-tech-field")).toBeNull();
    expect(
      icon?.querySelector(".mac-app-icon-echo-signal"),
    ).toBeInTheDocument();
    expect(
      icon?.querySelector(".mac-app-icon-optical-rim"),
    ).toBeInTheDocument();
    expect(
      (icon as HTMLElement).style.getPropertyValue("--echo-app-surface"),
    ).toBe("#1594d3");
  });

  it("reserves the technology field for telemetry applications", () => {
    const { container } = render(
      <MacAppIcon
        icon={FolderIcon}
        gradient="linear-gradient(#46c7df, #1768cf)"
        appId="echo:/storage-center"
      />,
    );

    expect(
      container.querySelector(".mac-app-icon-tech-field"),
    ).toBeInTheDocument();
  });

  it("uses the selected desktop wallpaper asset", () => {
    const { container } = render(<MacDesktopWallpaperArtwork />);
    const wallpaper = container.querySelector("img.desktop-wallpaper-art");

    expect(wallpaper).toBeInTheDocument();
    expect(wallpaper).toHaveAttribute(
      "src",
      "/third-party/appletechie-macos/wallpaper-day2.jpg",
    );
    expect(wallpaper).toHaveAttribute("alt", "");
  });

  it("samples the Orbit wallpaper with viewport images", () => {
    const { container } = render(
      <MacDesktopWidgets
        agentHealth={unverifiedAgentHealth}
        onOpenWorkspace={vi.fn()}
      />,
    );

    const samples = container.querySelectorAll(
      ".mac-surface-lens-raster > img",
    );
    expect(samples.length).toBeGreaterThanOrEqual(2);
    samples.forEach((sample) => {
      expect(sample).toHaveAttribute(
        "src",
        "/third-party/appletechie-macos/wallpaper-day2.jpg",
      );
    });
    expect(
      container.querySelector(".mac-surface-lens-raster > svg"),
    ).toBeNull();
  });

  it.each([
    ["checking", "正在连接 Echo Agent，打开工作台", "正在连接 Echo"],
    [
      "ready",
      "Echo Agent 在线，Runtime 版本未知 · 来源未验证，打开工作台",
      "Echo Agent 在线",
    ],
    [
      "restart-required",
      "Echo Agent 等待重启，打开工作台",
      "Echo Agent 等待重启",
    ],
    [
      "unavailable",
      "Echo Agent 未连接，打开工作台检查连接",
      "Echo Agent 未连接",
    ],
  ] as const)(
    "renders the %s Agent health state without claiming static readiness",
    (agentStatus, accessibleName, title) => {
      render(
        <MacDesktopWidgets
          agentHealth={{
            state: agentStatus,
            version: null,
            sourceId: null,
            verifiedBundle: false,
          }}
          onOpenWorkspace={vi.fn()}
        />,
      );

      expect(
        screen.getByRole("button", { name: accessibleName }),
      ).toHaveAttribute("data-agent-status", agentStatus);
      expect(screen.getByText(title)).toBeInTheDocument();
      expect(screen.queryByText("Echo 已就绪")).toBeNull();
    },
  );

  it("shows the verified Agent bundle version and source revision", () => {
    render(
      <MacDesktopWidgets
        agentHealth={{
          state: "ready",
          version: "0.2.0",
          sourceId: "a".repeat(40),
          verifiedBundle: true,
        }}
        onOpenWorkspace={vi.fn()}
      />,
    );

    expect(screen.getByText("Echo Agent 已验证")).toBeInTheDocument();
    expect(screen.getByText("v0.2.0 · aaaaaaaa")).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Echo Agent 已验证，v0.2.0 · aaaaaaaa，打开工作台",
      }),
    ).toHaveAttribute("data-agent-status", "ready");
  });

  it("opens notifications from the calendar widget when wired by the desktop", async () => {
    const user = userEvent.setup();
    const onOpenNotifications = vi.fn();

    render(
      <MacDesktopWidgets
        agentHealth={unverifiedAgentHealth}
        onOpenWorkspace={vi.fn()}
        onOpenNotifications={onOpenNotifications}
      />,
    );

    await user.click(screen.getByRole("button", { name: "日历" }));
    expect(onOpenNotifications).toHaveBeenCalledOnce();
  });

  it("shows an authenticated OS update and invokes only the fixed apply callback", async () => {
    const user = userEvent.setup();
    const onApplyUpdate = vi.fn();

    render(
      <MacAboutDialog
        open
        onClose={vi.fn()}
        onOpenSettings={vi.fn()}
        agentHealth={unverifiedAgentHealth}
        updateCapabilities={{
          nativeShell: true,
          status: true,
          apply: true,
        }}
        updateStatus={{
          schema: 1,
          state: "ready",
          phase: "fetch",
          version: "0.2.1",
          manifestSha256: "d".repeat(64),
          updatedAt: 1_800_000_000,
        }}
        onRefreshUpdate={vi.fn()}
        onApplyUpdate={onApplyUpdate}
      />,
    );

    expect(screen.getByText("Echo OS 0.2.1 已认证")).toBeInTheDocument();
    expect(screen.getByText(/未启用的系统槽/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "安装更新…" }));
    expect(onApplyUpdate).toHaveBeenCalledOnce();
  });

  it("offers restart only after the inactive update slot is ready", async () => {
    const user = userEvent.setup();
    const onRestart = vi.fn();

    render(
      <MacAboutDialog
        open
        onClose={vi.fn()}
        onOpenSettings={vi.fn()}
        agentHealth={unverifiedAgentHealth}
        updateCapabilities={{
          nativeShell: true,
          status: true,
          apply: true,
        }}
        updateStatus={{
          schema: 1,
          state: "reboot-required",
          phase: "apply",
          version: "0.2.1",
          manifestSha256: "d".repeat(64),
          updatedAt: 1_800_000_000,
        }}
        onRestart={onRestart}
      />,
    );

    expect(screen.queryByRole("button", { name: "安装更新…" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "重新启动…" }));
    expect(onRestart).toHaveBeenCalledOnce();
  });

  it("shows the full verified Agent runtime identity in About Echo OS", () => {
    const sourceId = "a".repeat(40);
    render(
      <MacAboutDialog
        open
        onClose={vi.fn()}
        onOpenSettings={vi.fn()}
        agentHealth={{
          state: "ready",
          version: "0.2.0",
          sourceId,
          verifiedBundle: true,
        }}
      />,
    );

    expect(screen.getByText("已验证并在线")).toBeInTheDocument();
    expect(screen.getByText("0.2.0")).toBeInTheDocument();
    expect(screen.getByText(sourceId)).toBeInTheDocument();
    expect(screen.getByText("已验证镜像 bundle")).toBeInTheDocument();
  });

  it("opens the best Spotlight match and closes the overlay", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const onClose = vi.fn();

    render(
      <MacSpotlight
        open
        query="工作"
        apps={[{ ...apps[0]!, onOpen }, apps[1]!]}
        onQueryChange={vi.fn()}
        onClose={onClose}
        onSubmit={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /工作台/ }));

    expect(onOpen).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("filters Launchpad applications", async () => {
    const user = userEvent.setup();

    render(<MacLaunchpad open apps={apps} onClose={vi.fn()} />);

    await user.type(screen.getByPlaceholderText("搜索"), "文件");

    expect(screen.getByRole("button", { name: "文件" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "工作台" })).toBeNull();
  });

  it("toggles Wi-Fi in Control Center", async () => {
    const user = userEvent.setup();

    render(
      <MacControlCenter open onClose={vi.fn()} onOpenSettings={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: /Wi-Fi/ }));

    expect(
      screen.getByRole("button", { name: /Wi-Fi.*关闭/ }),
    ).toBeInTheDocument();
  });

  it("keeps the Focus control state available to assistive technology", async () => {
    const user = userEvent.setup();

    render(
      <MacControlCenter open onClose={vi.fn()} onOpenSettings={vi.fn()} />,
    );

    const focusControl = screen.getByRole("button", { name: "专注模式" });

    expect(focusControl).toHaveAttribute("aria-pressed", "false");

    await user.click(focusControl);

    expect(focusControl).toHaveAttribute("aria-pressed", "true");
  });

  it("projects native Linux radios and invokes the real control bridge", async () => {
    const user = userEvent.setup();
    const onSetWifiEnabled = vi.fn().mockResolvedValue(undefined);
    const onSetBluetoothEnabled = vi.fn().mockResolvedValue(undefined);

    render(
      <MacControlCenter
        open
        onClose={vi.fn()}
        onOpenSettings={vi.fn()}
        systemControls={{
          nativeShell: true,
          wifi: {
            available: true,
            enabled: true,
            connection: "Echo Lab",
          },
          bluetooth: {
            available: true,
            present: true,
            enabled: false,
            controller: "Echo Radio",
          },
          audio: { available: true, volume: 64, muted: false },
          display: { available: true, brightness: 71 },
          battery: {
            available: true,
            present: true,
            percentage: 67,
            state: "Discharging",
          },
        }}
        onSetWifiEnabled={onSetWifiEnabled}
        onSetBluetoothEnabled={onSetBluetoothEnabled}
      />,
    );

    expect(
      screen.getByRole("button", { name: /Wi-Fi.*Echo Lab/ }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("显示器亮度")).toHaveValue("71");
    expect(screen.getByLabelText("系统音量")).toHaveValue("64");

    await user.click(screen.getByRole("button", { name: /Wi-Fi/ }));
    await user.click(screen.getByRole("button", { name: /蓝牙/ }));
    expect(onSetWifiEnabled).toHaveBeenCalledWith(false);
    expect(onSetBluetoothEnabled).toHaveBeenCalledWith(true);
  });

  it("renders and dismisses real native session notifications", async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    const onClear = vi.fn();

    render(
      <MacNotificationCenter
        open
        onClose={vi.fn()}
        nativeServiceAvailable
        notifications={[
          {
            id: 7,
            appName: "文件",
            summary: "复制完成",
            body: "已复制 3 个项目。",
            createdAt: 1000,
            updatedAt: Date.now(),
          },
        ]}
        onDismiss={onDismiss}
        onClear={onClear}
      />,
    );

    expect(screen.getByText("复制完成")).toBeInTheDocument();
    expect(screen.getByText("已复制 3 个项目。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "清除 文件 通知" }));
    expect(onDismiss).toHaveBeenCalledWith(7);
    await user.click(screen.getByRole("button", { name: "全部清除" }));
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("exposes native menu-bar actions", async () => {
    const user = userEvent.setup();
    const onOpenAbout = vi.fn();

    render(
      <MacMenuBar
        controlCenterOpen={false}
        notificationsOpen={false}
        liquidGlassOpen={false}
        onOpenSpotlight={vi.fn()}
        onToggleControlCenter={vi.fn()}
        onToggleNotifications={vi.fn()}
        onToggleLiquidGlass={vi.fn()}
        onOpenAbout={onOpenAbout}
        onOpenFiles={vi.fn()}
        onOpenSettings={vi.fn()}
        appStoreAvailable={false}
        onOpenAppStore={vi.fn()}
        onOpenLaunchpad={vi.fn()}
        systemCapabilities={{
          lock: false,
          logout: false,
          suspend: false,
          restart: false,
          shutdown: false,
        }}
        onLockScreen={vi.fn()}
        onSystemAction={vi.fn()}
      />,
    );

    await user.click(screen.getByLabelText("Echo 菜单"));
    await user.click(screen.getByRole("button", { name: "关于本机" }));

    expect(onOpenAbout).toHaveBeenCalledOnce();
  });

  it("keeps the active application name distinct from the File menu", () => {
    render(
      <MacMenuBar
        controlCenterOpen={false}
        notificationsOpen={false}
        liquidGlassOpen={false}
        onOpenSpotlight={vi.fn()}
        onToggleControlCenter={vi.fn()}
        onToggleNotifications={vi.fn()}
        onToggleLiquidGlass={vi.fn()}
        onOpenAbout={vi.fn()}
        onOpenFiles={vi.fn()}
        onOpenSettings={vi.fn()}
        appStoreAvailable={false}
        onOpenAppStore={vi.fn()}
        onOpenLaunchpad={vi.fn()}
        systemCapabilities={{
          lock: false,
          logout: false,
          suspend: false,
          restart: false,
          shutdown: false,
        }}
        onLockScreen={vi.fn()}
        onSystemAction={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "文件管理器", exact: true }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "文件", exact: true }),
    ).toBeInTheDocument();
  });

  it("only enables power actions exposed by the native shell", async () => {
    const user = userEvent.setup();
    const onSystemAction = vi.fn();
    const onLockScreen = vi.fn();

    render(
      <MacMenuBar
        controlCenterOpen={false}
        notificationsOpen={false}
        liquidGlassOpen={false}
        onOpenSpotlight={vi.fn()}
        onToggleControlCenter={vi.fn()}
        onToggleNotifications={vi.fn()}
        onToggleLiquidGlass={vi.fn()}
        onOpenAbout={vi.fn()}
        onOpenFiles={vi.fn()}
        onOpenSettings={vi.fn()}
        appStoreAvailable={false}
        onOpenAppStore={vi.fn()}
        onOpenLaunchpad={vi.fn()}
        systemCapabilities={{
          lock: true,
          logout: true,
          suspend: false,
          restart: true,
          shutdown: true,
        }}
        onLockScreen={onLockScreen}
        onSystemAction={onSystemAction}
      />,
    );

    await user.click(screen.getByLabelText("Echo 菜单"));
    expect(screen.getByRole("button", { name: "睡眠…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重新启动…" }));
    expect(onSystemAction).toHaveBeenCalledWith("restart");

    await user.click(screen.getByLabelText("Echo 菜单"));
    await user.click(screen.getByRole("button", { name: /锁定屏幕/ }));
    expect(onLockScreen).toHaveBeenCalledOnce();

    await user.click(screen.getByLabelText("Echo 菜单"));
    await user.click(screen.getByRole("button", { name: "退出登录…" }));
    expect(onSystemAction).toHaveBeenCalledWith("logout");
  });

  it("requires explicit confirmation before a destructive system action", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    render(
      <MacSystemActionDialog
        action="shutdown"
        busy={false}
        error={null}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    expect(
      screen.getByRole("alertdialog", { name: "关闭 Echo OS？" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "关机" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("opens the native app store when the OS exposes it", async () => {
    const user = userEvent.setup();
    const onOpenAppStore = vi.fn();

    render(
      <MacMenuBar
        controlCenterOpen={false}
        notificationsOpen={false}
        liquidGlassOpen={false}
        onOpenSpotlight={vi.fn()}
        onToggleControlCenter={vi.fn()}
        onToggleNotifications={vi.fn()}
        onToggleLiquidGlass={vi.fn()}
        onOpenAbout={vi.fn()}
        onOpenFiles={vi.fn()}
        onOpenSettings={vi.fn()}
        appStoreAvailable
        onOpenAppStore={onOpenAppStore}
        onOpenLaunchpad={vi.fn()}
        systemCapabilities={{
          lock: false,
          logout: false,
          suspend: false,
          restart: false,
          shutdown: false,
        }}
        onLockScreen={vi.fn()}
        onSystemAction={vi.fn()}
      />,
    );

    await user.click(screen.getByLabelText("Echo 菜单"));
    await user.click(screen.getByRole("button", { name: "应用中心…" }));
    expect(onOpenAppStore).toHaveBeenCalledOnce();
  });

  it("switches liquid glass style and optical intensity explicitly", async () => {
    const user = userEvent.setup();
    const onStyleChange = vi.fn();
    const onIntensityChange = vi.fn();
    const onTuningChange = vi.fn();
    const onResetTuning = vi.fn();

    render(
      <MacLiquidGlassPanel
        open
        style="crystal"
        intensity="balanced"
        tuning={DEFAULT_LIQUID_GLASS_TUNING}
        onStyleChange={onStyleChange}
        onIntensityChange={onIntensityChange}
        onTuningChange={onTuningChange}
        onResetTuning={onResetTuning}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /晶透/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: /柔光/ }));
    await user.click(screen.getByRole("button", { name: "沉浸", exact: true }));
    await user.click(screen.getByRole("button", { name: "使用暖金滤镜" }));
    await user.click(screen.getByRole("button", { name: "应用净透液态预设" }));
    await user.click(
      screen.getByRole("button", { name: "恢复全部默认玻璃设置" }),
    );
    fireEvent.change(screen.getByRole("slider", { name: "透明度" }), {
      target: { value: "84" },
    });

    expect(onStyleChange).toHaveBeenCalledWith("softlight");
    expect(onIntensityChange).toHaveBeenCalledWith("balanced");
    expect(onTuningChange).toHaveBeenCalledWith({ tint: "#ffe4b8" });
    expect(onTuningChange).toHaveBeenCalledWith(CLEAR_LIQUID_GLASS_TUNING);
    expect(onStyleChange).toHaveBeenCalledWith("crystal");
    expect(onTuningChange).toHaveBeenCalledWith({ transparency: 84 });
    expect(onResetTuning).toHaveBeenCalledOnce();
    expect(screen.getByRole("slider", { name: "透明度" })).toHaveValue("72");
    expect(screen.getByRole("slider", { name: "磨砂度" })).toHaveValue("32");
    expect(screen.getByRole("slider", { name: "折射率" })).toHaveValue("60");
    expect(screen.getByRole("slider", { name: "光学厚度" })).toHaveValue("8");
    expect(screen.getByRole("slider", { name: "色散 Δn" })).toHaveValue("8");
  });

  it("exposes the liquid glass studio from the menu bar", async () => {
    const user = userEvent.setup();
    const onToggleLiquidGlass = vi.fn();

    render(
      <MacMenuBar
        controlCenterOpen={false}
        notificationsOpen={false}
        liquidGlassOpen={false}
        onOpenSpotlight={vi.fn()}
        onToggleControlCenter={vi.fn()}
        onToggleNotifications={vi.fn()}
        onToggleLiquidGlass={onToggleLiquidGlass}
        onOpenAbout={vi.fn()}
        onOpenFiles={vi.fn()}
        onOpenSettings={vi.fn()}
        appStoreAvailable={false}
        onOpenAppStore={vi.fn()}
        onOpenLaunchpad={vi.fn()}
        systemCapabilities={{
          lock: false,
          logout: false,
          suspend: false,
          restart: false,
          shutdown: false,
        }}
        onLockScreen={vi.fn()}
        onSystemAction={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "流光玻璃设置" }));
    expect(onToggleLiquidGlass).toHaveBeenCalledOnce();
  });
});
