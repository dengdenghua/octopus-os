import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { McpSettingsPage } from "./mcp-settings-page";

const api = vi.hoisted(() => ({
  approveMCPTrust: vi.fn(),
  forgetMCPOAuth: vi.fn(),
  listMCPTrust: vi.fn(),
  loadMCPConfig: vi.fn(),
  revokeMCPTrust: vi.fn(),
  updateMCPConfig: vi.fn(),
}));

vi.mock("@/core/mcp/api", () => api);

const remoteServer = {
  enabled: false,
  description: "Remote search tools",
  transport: "http" as const,
  url: "https://mcp.example.test/tools",
};

describe("McpSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.loadMCPConfig.mockResolvedValue({
      mcp_servers: { search: remoteServer },
    });
    api.listMCPTrust.mockResolvedValue({ entries: [] });
    api.approveMCPTrust.mockResolvedValue({ ok: true });
    api.revokeMCPTrust.mockResolvedValue({ ok: true, server_name: "search" });
    api.forgetMCPOAuth.mockResolvedValue(undefined);
    api.updateMCPConfig.mockImplementation(async (config) => config);
  });

  it("shows the persisted transport instead of mislabeling remote services as stdio", async () => {
    renderWithProviders(<McpSettingsPage />, { locale: "zh-CN" });

    await screen.findByText("Remote search tools");
    expect(
      screen.getByText("http · https://mcp.example.test/tools"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/stdio ·/)).not.toBeInTheDocument();
  });

  it("adds a remote service in a safe disabled state", async () => {
    const user = userEvent.setup();
    renderWithProviders(<McpSettingsPage />, { locale: "zh-CN" });
    await screen.findByText("Remote search tools");

    await user.type(screen.getByLabelText("服务名称"), "calendar");
    await user.type(
      screen.getByLabelText("服务地址"),
      "https://calendar.example.test/mcp",
    );
    await user.click(screen.getByRole("button", { name: "添加服务" }));

    await waitFor(() => {
      expect(api.updateMCPConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          mcp_servers: expect.objectContaining({
            calendar: expect.objectContaining({
              enabled: false,
              transport: "http",
              url: "https://calendar.example.test/mcp",
            }),
          }),
        }),
      );
    });
  });

  it("reflects a runtime activation failure instead of leaving the switch on", async () => {
    const user = userEvent.setup();
    api.updateMCPConfig.mockResolvedValue({
      mcp_servers: {
        search: {
          ...remoteServer,
          enabled: false,
          error: "connection refused",
        },
      },
      _status: { search: { ok: false, error: "connection refused" } },
    });
    renderWithProviders(<McpSettingsPage />, { locale: "zh-CN" });

    const toggle = await screen.findByRole("switch", {
      name: "启用或停用 search",
    });
    await user.click(toggle);

    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "运行错误：connection refused",
    );
  });

  it("removes configuration only after confirmation and clears trust and OAuth", async () => {
    const user = userEvent.setup();
    api.loadMCPConfig.mockResolvedValue({
      mcp_servers: { search: { ...remoteServer, enabled: true } },
    });
    api.listMCPTrust.mockResolvedValue({
      entries: [
        {
          server_name: "search",
          approved: true,
          added_ts: 1,
          tool_digest: "digest",
          note: "",
        },
      ],
    });
    api.updateMCPConfig.mockImplementation(async (config) => config);
    renderWithProviders(<McpSettingsPage />, { locale: "zh-CN" });

    await screen.findByText("Remote search tools");
    await user.click(screen.getByRole("button", { name: "移除 search" }));
    const dialog = screen.getByRole("dialog", { name: "移除 MCP 服务" });
    expect(dialog).toHaveTextContent("撤销信任与已保存的 OAuth 授权");
    expect(api.updateMCPConfig).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "移除服务" }));

    await waitFor(() => {
      expect(api.revokeMCPTrust).toHaveBeenCalledWith("search");
      expect(api.updateMCPConfig).toHaveBeenNthCalledWith(
        1,
        expect.objectContaining({
          mcp_servers: expect.objectContaining({
            search: expect.objectContaining({ enabled: false }),
          }),
        }),
      );
      expect(api.updateMCPConfig).toHaveBeenNthCalledWith(2, {
        mcp_servers: {},
      });
      expect(api.forgetMCPOAuth).toHaveBeenCalledWith("search");
      expect(screen.queryByText("Remote search tools")).not.toBeInTheDocument();
    });
  });
});
