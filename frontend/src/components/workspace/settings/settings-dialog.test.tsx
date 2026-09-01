import { fireEvent, waitFor, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { normalizeSettingsSection, SettingsDialog } from "./settings-dialog";

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    user: null,
    logout: vi.fn(),
    authStatus: null,
    isLoading: false,
  }),
}));

describe("SettingsDialog", () => {
  beforeEach(() => {
    window.localStorage.removeItem("echo_settings_dialog_size");
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  it("maps legacy section ids to the new categories", () => {
    expect(normalizeSettingsSection("mcp")).toBe("tools");
    expect(normalizeSettingsSection("personalSpace")).toBe("privacy");
    expect(normalizeSettingsSection("session")).toBe("privacy");
    expect(normalizeSettingsSection("conversation")).toBe("conversation");
    expect(normalizeSettingsSection("automation")).toBe("browserAutomation");
    expect(normalizeSettingsSection("sandbox")).toBe("automationSecurity");
    expect(normalizeSettingsSection("unknown")).toBe("appearance");
  });

  it("exposes browser and desktop automation as independent destinations", () => {
    renderWithProviders(
      <SettingsDialog
        open
        defaultSection="browserAutomation"
        onOpenChange={vi.fn()}
      />,
      { locale: "zh-CN" },
    );

    expect(
      screen.getByRole("button", { name: "浏览器自动化" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: "桌面自动化" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "执行与安全" }),
    ).toBeInTheDocument();

    const destinations = screen
      .getAllByRole("button")
      .map((button) => button.textContent?.trim())
      .filter(Boolean);
    expect(destinations.indexOf("通用")).toBeLessThan(
      destinations.indexOf("对话"),
    );
    expect(destinations.indexOf("对话")).toBeLessThan(
      destinations.indexOf("浏览器自动化"),
    );
    expect(destinations.indexOf("浏览器自动化")).toBeLessThan(
      destinations.indexOf("桌面自动化"),
    );
  });

  it("names merged categories after the personal-space features they contain", () => {
    renderWithProviders(
      <SettingsDialog open defaultSection="privacy" onOpenChange={vi.fn()} />,
      { locale: "zh-CN" },
    );

    expect(
      screen.getByRole("button", { name: "个人空间与安全" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: "记忆与个人规则" }),
    ).toBeInTheDocument();
  });

  it("returns the content viewport to the top when switching sections", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <SettingsDialog
        open
        defaultSection="appearance"
        onOpenChange={vi.fn()}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByRole("button", { name: "通用" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: "对话" })).toBeInTheDocument();

    const viewport = document.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]',
    );
    expect(viewport).not.toBeNull();
    if (!viewport) return;
    viewport.scrollTop = 160;

    await user.click(screen.getByRole("button", { name: "工具与集成" }));

    await waitFor(() => expect(viewport.scrollTop).toBe(0));
    expect(screen.getByRole("button", { name: "工具与集成" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: "通用" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("uses a soft overflow edge without narrow-screen paging buttons", () => {
    renderWithProviders(
      <SettingsDialog
        open
        defaultSection="appearance"
        onOpenChange={vi.fn()}
      />,
      { locale: "zh-CN" },
    );

    const scroller = screen.getByTestId("settings-section-scroll");
    Object.defineProperties(scroller, {
      clientWidth: { configurable: true, value: 320 },
      scrollWidth: { configurable: true, value: 900 },
      scrollLeft: { configurable: true, writable: true, value: 0 },
    });
    fireEvent.scroll(scroller);
    expect(
      screen.queryByRole("button", { name: "查看前面的设置" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "查看更多设置" }),
    ).not.toBeInTheDocument();

    Object.defineProperty(scroller, "scrollLeft", {
      configurable: true,
      writable: true,
      value: 580,
    });
    fireEvent.scroll(scroller);
    expect(
      screen.queryByRole("button", { name: "查看前面的设置" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "查看更多设置" }),
    ).not.toBeInTheDocument();
  });

  it("resizes one axis at a time from the keyboard handle", () => {
    renderWithProviders(
      <SettingsDialog
        open
        defaultSection="appearance"
        onOpenChange={vi.fn()}
      />,
      { locale: "zh-CN" },
    );

    const dialog = screen.getByRole("dialog", { name: "设置" });
    const handle = screen.getByRole("separator", { name: "拖动调整大小" });

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(dialog).toHaveStyle({ width: "776px", height: "560px" });

    fireEvent.keyDown(handle, { key: "ArrowDown" });
    expect(dialog).toHaveStyle({ width: "776px", height: "576px" });
    expect(window.localStorage.getItem("echo_settings_dialog_size")).toBe(
      JSON.stringify({ w: 776, h: 576 }),
    );
  });

  it("explains observability before opening its dedicated workspace", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    renderWithProviders(
      <SettingsDialog
        open
        defaultSection="observability"
        onOpenChange={onOpenChange}
      />,
      { locale: "zh-CN" },
    );

    expect(
      screen.getByRole("heading", { name: "运行可观测性" }),
    ).toBeInTheDocument();
    expect(screen.getByText("实时活动")).toBeInTheDocument();
    expect(screen.getByText("操作与文件轨迹")).toBeInTheDocument();
    expect(screen.getByText("运行健康")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "打开可观测性工作台" }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
