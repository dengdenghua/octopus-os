import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import { getLinkOpenTarget } from "@/core/settings/automation-preferences";

import {
  BrowserAutomationSettingsPage,
  DesktopAutomationSettingsPage,
} from "./automation-capability-settings";

const api = vi.hoisted(() => ({
  getCapabilities: vi.fn(),
  saveCapabilities: vi.fn(),
  getBrowserRelayStatus: vi.fn(),
  subscribeBrowserRelayStatus: vi.fn(),
  getDesktopAutomationPermissions: vi.fn(),
  openDesktopAutomationPermission: vi.fn(),
  getBrowserConfig: vi.fn(),
  updateBrowserConfig: vi.fn(),
}));

vi.mock("@/core/settings/capabilities-api", () => ({
  getCapabilities: api.getCapabilities,
  saveCapabilities: api.saveCapabilities,
}));

vi.mock("@/core/settings/automation-status-api", () => ({
  getBrowserRelayStatus: api.getBrowserRelayStatus,
  subscribeBrowserRelayStatus: api.subscribeBrowserRelayStatus,
  getDesktopAutomationPermissions: api.getDesktopAutomationPermissions,
  openDesktopAutomationPermission: api.openDesktopAutomationPermission,
}));

vi.mock("@/core/browser/api", () => ({
  getBrowserConfig: api.getBrowserConfig,
  updateBrowserConfig: api.updateBrowserConfig,
}));

describe("automation capability settings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    api.getCapabilities.mockResolvedValue({
      browser_automation: true,
      desktop_automation: true,
    });
    api.saveCapabilities.mockImplementation(async (capabilities) => ({
      ok: true,
      capabilities,
      restart_required: true,
      message: "saved",
    }));
    api.getBrowserRelayStatus.mockResolvedValue({
      connected: true,
      connection_state: "online",
      extension_version: "0.2.0",
      push_connected: true,
      last_seen: 1,
      manifest_exists: true,
      extension_path: "/extension",
    });
    api.subscribeBrowserRelayStatus.mockReturnValue(() => undefined);
    api.getDesktopAutomationPermissions.mockResolvedValue({
      supported: false,
      platform: "web",
      screenRecording: "unknown",
      accessibility: "unknown",
    });
    api.getBrowserConfig.mockResolvedValue({
      max_open_tabs: 20,
      max_saved_tabs: 10,
      connection_mode: "extension",
      cdp_port: 9222,
      headless: false,
      viewport_width: 1440,
      viewport_height: 900,
      relay_allowed_hosts: ["example.com"],
      relay_blocked_hosts: ["accounts.example.com"],
      relay_require_allowlist: true,
    });
    api.updateBrowserConfig.mockImplementation(async (patch) => ({
      ...(await api.getBrowserConfig()),
      ...patch,
    }));
  });

  it("persists website-scoped browser automation memory", async () => {
    const user = userEvent.setup();
    renderWithProviders(<BrowserAutomationSettingsPage />, {
      locale: "zh-CN",
    });

    const allowed = await screen.findByRole("textbox", { name: "允许的网站" });
    expect(allowed).toHaveValue("example.com");
    await user.clear(allowed);
    await user.type(allowed, "docs.example.com, *.safe.test");
    await user.click(screen.getByRole("button", { name: "保存网站权限" }));

    await waitFor(() =>
      expect(api.updateBrowserConfig.mock.calls[0]?.[0]).toEqual({
        relay_allowed_hosts: ["docs.example.com", "*.safe.test"],
        relay_blocked_hosts: ["accounts.example.com"],
        relay_require_allowlist: true,
      }),
    );
  });

  it("shows live relay state and persists the browser capability", async () => {
    const user = userEvent.setup();
    renderWithProviders(<BrowserAutomationSettingsPage />, {
      locale: "zh-CN",
    });

    expect(await screen.findByText("在线")).toBeInTheDocument();
    expect(screen.getByText("扩展版本 0.2.0")).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "允许浏览器操作" }));
    await waitFor(() =>
      expect(api.saveCapabilities).toHaveBeenCalledWith({
        browser_automation: false,
        desktop_automation: true,
      }),
    );
  });

  it("updates the status lamp from the relay WebSocket stream", async () => {
    let publish: ((status: unknown) => void) | undefined;
    api.subscribeBrowserRelayStatus.mockImplementation((callback) => {
      publish = callback;
      return () => undefined;
    });
    renderWithProviders(<BrowserAutomationSettingsPage />, {
      locale: "zh-CN",
    });

    expect(await screen.findByText("在线")).toBeInTheDocument();
    publish?.({
      connected: false,
      connection_state: "offline",
      extension_version: "0.2.0",
      push_connected: false,
      last_seen: 1,
      manifest_exists: true,
      extension_path: "/extension",
    });

    expect(await screen.findByText("离线")).toBeInTheDocument();
    expect(screen.getByText(/扩展版本 0\.2\.0/)).toBeInTheDocument();
    expect(screen.getByText(/打开 Chrome 扩展页/)).toBeInTheDocument();
  });

  it("shows the reconnecting relay state from the status stream", async () => {
    let publish: ((status: unknown) => void) | undefined;
    api.subscribeBrowserRelayStatus.mockImplementation((callback) => {
      publish = callback;
      return () => undefined;
    });
    renderWithProviders(<BrowserAutomationSettingsPage />, {
      locale: "zh-CN",
    });

    expect(await screen.findByText("在线")).toBeInTheDocument();
    publish?.({
      connected: false,
      connection_state: "reconnecting",
      extension_version: "0.2.0",
      push_connected: false,
      last_seen: 1,
      manifest_exists: true,
      extension_path: "/extension",
    });

    expect(await screen.findByText("重连中")).toBeInTheDocument();
    expect(screen.getByText("扩展版本 0.2.0")).toBeInTheDocument();
  });

  it("persists the selected link-open preference", async () => {
    const user = userEvent.setup();
    renderWithProviders(<BrowserAutomationSettingsPage />, {
      locale: "zh-CN",
    });

    const target = await screen.findByRole("combobox", {
      name: "链接打开方式",
    });
    expect(target).toHaveTextContent("外部浏览器");

    await user.click(target);
    await user.click(
      await screen.findByRole("option", { name: "Echo 应用内" }),
    );

    expect(getLinkOpenTarget()).toBe("in_app");
    expect(target).toHaveTextContent("Echo 应用内");
  });

  it("does not pretend macOS permissions are known in web mode", async () => {
    renderWithProviders(<DesktopAutomationSettingsPage />, {
      locale: "zh-CN",
    });

    expect(await screen.findByText("屏幕录制")).toBeInTheDocument();
    expect(screen.getAllByText("仅桌面端可检测")).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "打开设置" }),
    ).not.toBeInTheDocument();
  });

  it("persists the desktop capability and opens both macOS permission panes", async () => {
    const user = userEvent.setup();
    api.getDesktopAutomationPermissions.mockResolvedValue({
      supported: true,
      platform: "darwin",
      screenRecording: "denied",
      accessibility: "denied",
    });
    api.openDesktopAutomationPermission.mockResolvedValue(true);
    renderWithProviders(<DesktopAutomationSettingsPage />, {
      locale: "zh-CN",
    });

    await user.click(
      await screen.findByRole("switch", { name: "允许桌面操作" }),
    );
    await waitFor(() =>
      expect(api.saveCapabilities).toHaveBeenCalledWith({
        browser_automation: true,
        desktop_automation: false,
      }),
    );

    const permissionButtons = screen.getAllByRole("button", {
      name: "打开设置",
    });
    expect(permissionButtons).toHaveLength(2);
    await user.click(permissionButtons[0]);
    await user.click(permissionButtons[1]);
    expect(api.openDesktopAutomationPermission).toHaveBeenNthCalledWith(
      1,
      "screen-recording",
    );
    expect(api.openDesktopAutomationPermission).toHaveBeenNthCalledWith(
      2,
      "accessibility",
    );
  });
});
