import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/harness";

const mocks = vi.hoisted(() => ({
  getCapabilities: vi.fn(),
  refetchMemory: vi.fn(),
  loadMCPConfig: vi.fn(),
  listMCPTrust: vi.fn(),
}));

vi.mock("@/core/settings/capabilities-api", () => ({
  getCapabilities: mocks.getCapabilities,
  saveCapabilities: vi.fn(),
  restartBackend: vi.fn(),
}));

vi.mock("@/core/settings/permissions-api", () => ({
  addPermissionRule: vi.fn(),
  deletePermissionRule: vi.fn(),
  listPermissionRules: vi.fn().mockResolvedValue([]),
  movePermissionRule: vi.fn(),
}));

vi.mock("@/core/memory/hooks", () => {
  const idleMutation = () => ({
    isPending: false,
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
  });
  return {
    useMemory: () => ({
      memory: null,
      isLoading: false,
      error: new Error("sensitive raw upstream detail"),
      refetch: mocks.refetchMemory,
      isRefreshing: false,
    }),
    useMemoryConfig: () => ({ config: null }),
    useUpdateMemoryConfig: idleMutation,
    useClearMemory: idleMutation,
    useCreateMemoryFact: idleMutation,
    useDeleteMemoryFact: idleMutation,
    useImportMemory: idleMutation,
    useUpdateMemoryFact: idleMutation,
  };
});

vi.mock("@/core/streamdown", () => ({
  useStreamdownPlugins: () => ({}),
}));

vi.mock("@/core/mcp/api", () => ({
  approveMCPTrust: vi.fn(),
  listMCPTrust: mocks.listMCPTrust,
  loadMCPConfig: mocks.loadMCPConfig,
  revokeMCPTrust: vi.fn(),
  updateMCPConfig: vi.fn(),
}));

import AutomationSettingsPage from "./automation-settings-page";
import { getAboutMarkdown } from "./about-content";
import MemorySettingsPage from "./memory-settings-page";
import { McpSettingsPage } from "./mcp-settings-page";
import {
  formatAiModeDevice,
  getMemoryLoadErrorCopy,
  isSupportedMcpUrl,
  resolvePricingAccountLabel,
} from "./settings-resilience";

beforeEach(() => {
  mocks.getCapabilities.mockReset();
  mocks.refetchMemory.mockReset();
  mocks.loadMCPConfig.mockReset().mockResolvedValue({ mcp_servers: {} });
  mocks.listMCPTrust.mockReset().mockResolvedValue({ entries: [] });
});

describe("settings error recovery", () => {
  it("localizes the memory load failure, hides raw backend text, and retries", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MemorySettingsPage />, { locale: "zh-CN" });

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("暂时无法读取记忆");
    expect(alert).not.toHaveTextContent("sensitive raw upstream detail");

    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(mocks.refetchMemory).toHaveBeenCalledTimes(1);
  });

  it("keeps capability errors user-facing and offers an in-place retry", async () => {
    const user = userEvent.setup();
    mocks.getCapabilities
      .mockRejectedValueOnce(new Error("raw capability stack"))
      .mockResolvedValueOnce({
        browser_automation: true,
        desktop_automation: true,
      });

    renderWithProviders(<AutomationSettingsPage />, { locale: "zh-CN" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("加载失败");
    expect(alert).not.toHaveTextContent("raw capability stack");

    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(mocks.getCapabilities).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("执行与权限")).toBeInTheDocument();
  });

  it("names automation capability switches for assistive technology", async () => {
    mocks.getCapabilities.mockResolvedValue({
      browser_automation: true,
      desktop_automation: false,
    });

    renderWithProviders(<AutomationSettingsPage />, { locale: "zh-CN" });

    expect(
      await screen.findByRole("switch", { name: "允许浏览器操作" }),
    ).toBeChecked();
    expect(
      screen.getByRole("switch", { name: "允许桌面操作" }),
    ).not.toBeChecked();
  });
});

describe("settings identity and input guards", () => {
  it("uses the signed-in auth identity when the optional profile is unavailable", () => {
    expect(
      resolvePricingAccountLabel({
        profileName: null,
        userName: "codex-ui-check",
        userEmail: "user@example.test",
        fallback: "当前账号",
      }),
    ).toBe("codex-ui-check");
  });

  it("accepts only HTTP(S) MCP endpoints", () => {
    expect(isSupportedMcpUrl("https://server.example/mcp")).toBe(true);
    expect(isSupportedMcpUrl("http://127.0.0.1:8000/mcp")).toBe(true);
    expect(isSupportedMcpUrl("glm-5.1")).toBe(false);
    expect(isSupportedMcpUrl("file:///tmp/mcp.sock")).toBe(false);
  });

  it("formats structured AI-mode device data instead of rendering an object", () => {
    expect(
      formatAiModeDevice(
        {
          has_local_model: true,
          has_gpu: true,
          ram_gb: 24,
          cpu_count: 12,
          cloud_reachable: false,
        },
        "zh-CN",
      ),
    ).toBe("24 GB RAM · 12 CPU · GPU · 本地模型可用 · 云端不可用");
    expect(
      formatAiModeDevice(
        { ram_gb: 0, cpu_count: 8, cloud_reachable: true },
        "zh-CN",
      ),
    ).toBe("8 CPU · 云端可用");
  });

  it("prevents credential autofill and keeps the MCP token masked", async () => {
    renderWithProviders(<McpSettingsPage />, { locale: "zh-CN" });

    const urlInput = screen.getByLabelText("服务地址");
    const tokenInput = screen.getByLabelText("访问令牌（可选）");
    expect(urlInput).toHaveAttribute("type", "url");
    expect(urlInput).toHaveAttribute("autocomplete", "url");
    expect(tokenInput).toHaveAttribute("type", "password");
    expect(tokenInput).toHaveAttribute("autocomplete", "new-password");
    expect(
      screen.getByText("令牌只用于连接该服务，不会在列表中显示。"),
    ).toBeInTheDocument();
  });

  it("distinguishes an MCP load failure from an empty service list", async () => {
    const user = userEvent.setup();
    mocks.loadMCPConfig
      .mockRejectedValueOnce(new Error("raw config path"))
      .mockResolvedValueOnce({ mcp_servers: {} });

    renderWithProviders(<McpSettingsPage />, { locale: "zh-CN" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("暂时无法读取 MCP 服务");
    expect(alert).not.toHaveTextContent("raw config path");
    expect(
      screen.queryByText("还没有连接 MCP 服务。可在下方添加远程服务。"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新加载" }));

    expect(
      await screen.findByText("还没有连接 MCP 服务。可在下方添加远程服务。"),
    ).toBeInTheDocument();
    expect(mocks.loadMCPConfig).toHaveBeenCalledTimes(2);
  });

  it("prevents a case-insensitive duplicate MCP name from overwriting config", async () => {
    const user = userEvent.setup();
    mocks.loadMCPConfig.mockResolvedValue({
      mcp_servers: {
        github: {
          transport: "http",
          url: "https://existing.example/mcp",
          enabled: true,
        },
      },
    });

    renderWithProviders(<McpSettingsPage />, { locale: "zh-CN" });

    await screen.findByText("github");
    await user.type(screen.getByLabelText("服务名称"), "GitHub");
    await user.type(
      screen.getByLabelText("服务地址"),
      "https://new.example/mcp",
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "已存在名为“GitHub”的服务",
    );
    expect(screen.getByRole("button", { name: "添加服务" })).toBeDisabled();
  });
});

describe("localized About content", () => {
  it("uses the active locale instead of an English-only document", () => {
    expect(getAboutMarkdown("zh-CN")).toContain("核心能力");
    expect(getAboutMarkdown("ja-JP")).toContain("主な機能");
    expect(getAboutMarkdown("ko-KR")).toContain("핵심 기능");
    expect(getMemoryLoadErrorCopy("en-US")).toContain("try again");
  });
});
