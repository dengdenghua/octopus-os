import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import AutomationSettingsPage from "./automation-settings-page";

const api = vi.hoisted(() => ({
  getCapabilities: vi.fn(),
  saveCapabilities: vi.fn(),
  restartBackend: vi.fn(),
  addPermissionRule: vi.fn(),
  deletePermissionRule: vi.fn(),
  listPermissionRules: vi.fn(),
  movePermissionRule: vi.fn(),
}));

vi.mock("@/core/settings/capabilities-api", () => ({
  getCapabilities: api.getCapabilities,
  saveCapabilities: api.saveCapabilities,
  restartBackend: api.restartBackend,
}));

vi.mock("@/core/settings/permissions-api", () => ({
  addPermissionRule: api.addPermissionRule,
  deletePermissionRule: api.deletePermissionRule,
  listPermissionRules: api.listPermissionRules,
  movePermissionRule: api.movePermissionRule,
}));

describe("AutomationSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getCapabilities.mockResolvedValue({
      browser_automation: true,
      desktop_automation: true,
    });
    api.listPermissionRules.mockResolvedValue([]);
    api.addPermissionRule.mockResolvedValue([]);
    api.deletePermissionRule.mockResolvedValue([]);
    api.movePermissionRule.mockResolvedValue([]);
    api.restartBackend.mockResolvedValue({ ok: true });
  });

  it("uses the saved capability snapshot and skips restart when the backend says it is unnecessary", async () => {
    const user = userEvent.setup();
    api.saveCapabilities.mockResolvedValue({
      ok: true,
      capabilities: {
        browser_automation: true,
        desktop_automation: false,
      },
      restart_required: false,
      message: "raw backend message",
    });
    renderWithProviders(<AutomationSettingsPage />, { locale: "zh-CN" });

    const desktop = await screen.findByRole("switch", {
      name: "允许桌面操作",
    });
    await user.click(desktop);
    await user.click(screen.getAllByRole("button", { name: "保存" })[0]);

    await waitFor(() =>
      expect(api.saveCapabilities).toHaveBeenCalledWith({
        browser_automation: true,
        desktop_automation: false,
      }),
    );
    expect(
      screen.queryByRole("dialog", { name: "重启后端以生效" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("下一步：验证自动化是否可用")).toBeInTheDocument();
  });

  it("does not offer an impossible in-app restart in web mode", async () => {
    const user = userEvent.setup();
    api.saveCapabilities.mockResolvedValue({
      ok: true,
      capabilities: {
        browser_automation: false,
        desktop_automation: true,
      },
      restart_required: true,
      message: "saved",
    });
    renderWithProviders(<AutomationSettingsPage />, { locale: "zh-CN" });

    await user.click(
      await screen.findByRole("switch", { name: "允许浏览器操作" }),
    );
    await user.click(screen.getAllByRole("button", { name: "保存" })[0]);

    const dialog = await screen.findByRole("dialog", {
      name: "重启后端以生效",
    });
    expect(dialog).toHaveTextContent("网页模式无法代为重启");
    expect(
      within(dialog).queryByRole("button", { name: "立即重启" }),
    ).not.toBeInTheDocument();
  });

  it("does not suggest verification when every automation capability is off", async () => {
    api.getCapabilities.mockResolvedValue({
      browser_automation: false,
      desktop_automation: false,
    });
    renderWithProviders(<AutomationSettingsPage />, { locale: "zh-CN" });

    expect(
      await screen.findByText("浏览器和桌面操作均已关闭"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "打开电脑自动化" }),
    ).not.toBeInTheDocument();
  });

  it("associates rule labels and submits the add form", async () => {
    const user = userEvent.setup();
    api.addPermissionRule.mockResolvedValue([
      {
        effect: "allow",
        tool: "read_*",
        args_contains: "",
        reason: "",
      },
    ]);
    renderWithProviders(<AutomationSettingsPage />, { locale: "zh-CN" });

    const tool = await screen.findByLabelText("工具名（支持通配符）");
    await screen.findByText("尚未配置审批规则，可直接使用下方表单添加第一条。");
    await waitFor(() => expect(tool).toBeEnabled());
    expect(screen.getByLabelText("调用处理方式")).toBeVisible();
    expect(screen.getByLabelText("参数包含（可选）")).toBeVisible();
    expect(screen.getByLabelText("理由（可选）")).toBeVisible();
    await user.type(tool, "read_*");
    await user.click(screen.getByRole("button", { name: "保存审批规则" }));

    await waitFor(() =>
      expect(api.addPermissionRule).toHaveBeenCalledWith({
        effect: "allow",
        tool: "read_*",
        args_contains: undefined,
        reason: undefined,
      }),
    );
  });

  it("numbers rules from one, localizes argument matching, and confirms deletion", async () => {
    const user = userEvent.setup();
    const rule = {
      effect: "deny",
      tool: "exec_shell",
      args_contains: "rm -rf",
      reason: "保护文件",
    };
    api.listPermissionRules.mockResolvedValue([rule]);
    renderWithProviders(<AutomationSettingsPage />, { locale: "zh-CN" });

    await screen.findByText("exec_shell");
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("rm -rf").parentElement).toHaveTextContent(
      "参数包含（可选）",
    );
    await user.click(screen.getByRole("button", { name: "删除: exec_shell" }));
    expect(api.deletePermissionRule).not.toHaveBeenCalled();

    const dialog = screen.getByRole("dialog", { name: "删除审批规则" });
    expect(dialog).toHaveTextContent("exec_shell");
    await user.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() =>
      expect(api.deletePermissionRule).toHaveBeenCalledWith(0),
    );
  });

  it("keeps rule creation disabled while the current policy is unavailable", async () => {
    api.listPermissionRules.mockRejectedValue(new Error("raw policy path"));
    renderWithProviders(<AutomationSettingsPage />, { locale: "zh-CN" });

    const failure = await screen.findByText("加载规则失败");
    expect(failure).toBeInTheDocument();
    expect(screen.getByLabelText("工具名（支持通配符）")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "保存审批规则" }),
    ).toBeDisabled();
    expect(screen.queryByText("raw policy path")).not.toBeInTheDocument();
  });
});
