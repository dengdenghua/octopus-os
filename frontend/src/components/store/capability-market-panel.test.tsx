import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import { CAPABILITY_SURFACE_QUERY_KEY } from "@/core/plugins/use-capability-surface";

import { CapabilityMarketPanel } from "./capability-market-panel";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

const mocks = vi.hoisted(() => ({
  listCapabilities: vi.fn(),
  loadCapabilityIcon: vi.fn(),
  getCapabilityInstallPlan: vi.fn(),
  installCapability: vi.fn(),
  uninstallCapability: vi.fn(),
  setCapabilityEnabled: vi.fn(),
  getCapabilityStatus: vi.fn(),
  getCapabilityDeviceFlow: vi.fn(),
  cancelCapabilityDeviceFlow: vi.fn(),
  connectCapability: vi.fn(),
  disconnectCapability: vi.fn(),
  oauthAuthorize: vi.fn(),
  oauthStatus: vi.fn(),
}));

vi.mock("@/core/agents/agent-world-api", () => ({
  listCapabilities: mocks.listCapabilities,
  loadCapabilityIcon: mocks.loadCapabilityIcon,
  getCapabilityInstallPlan: mocks.getCapabilityInstallPlan,
  installCapability: mocks.installCapability,
  uninstallCapability: mocks.uninstallCapability,
  setCapabilityEnabled: mocks.setCapabilityEnabled,
  getCapabilityStatus: mocks.getCapabilityStatus,
  getCapabilityDeviceFlow: mocks.getCapabilityDeviceFlow,
  cancelCapabilityDeviceFlow: mocks.cancelCapabilityDeviceFlow,
  connectCapability: mocks.connectCapability,
  disconnectCapability: mocks.disconnectCapability,
}));

vi.mock("@/core/mcp/api", () => ({
  deleteOAuthApp: vi.fn(),
  getOAuthApp: vi.fn(),
  oauthAuthorize: mocks.oauthAuthorize,
  oauthStatus: mocks.oauthStatus,
  saveOAuthApp: vi.fn(),
}));

const westock = {
  id: "westock-mcp",
  name: "westock-mcp",
  name_zh: "腾讯股票",
  description: "腾讯股票行情",
  description_zh: "腾讯股票行情",
  type: "mcp" as const,
  auth_mode: "token",
  source: "connector" as const,
  provider_id: "",
  mcp_servers: ["westock-mcp"],
  skill_count: 3,
  examples_zh: [],
  installed: true,
  enabled: false,
  version: "1.0.0",
};

const cliCapability = {
  ...westock,
  id: "cli-one",
  name: "CLI One",
  name_zh: "CLI One",
  description: "CLI device flow",
  description_zh: "CLI 设备流",
  type: "cli" as const,
  has_cli_auth: true,
  mcp_servers: [],
};

const freebuffCapability = {
  ...cliCapability,
  id: "freebuff-cli",
  name: "Freebuff CLI",
  name_zh: "Freebuff 本地 Agent",
  description: "Official Freebuff CLI",
  description_zh: "官方 Freebuff CLI",
};

const activeDeviceFlow = {
  flow_id: "flow-a",
  connector_id: "cli-one",
  verification_uri: "https://example.test/device",
  user_code: "ABCD-EFGH",
  expires_in: 240,
  code_embedded_in_uri: false,
  message: "请在浏览器完成授权",
};

const browserPlugin = {
  id: "browser",
  name: "Browser",
  name_zh: "Browser",
  description: "Control the in-app browser",
  description_zh: "控制 in-app 浏览器",
  type: "plugin" as const,
  auth_mode: "none",
  source: "codex_plugin" as const,
  author: "OpenAI",
  mcp_servers: [],
  skill_count: 1,
  installed: false,
  enabled: false,
  version: "26.810.52044",
};

const documentsPlugin = {
  ...browserPlugin,
  id: "documents",
  name: "Documents",
  name_zh: "文档",
  description: "Create and edit documents",
  description_zh: "创建和编辑文档",
};

const sheetsPlugin = {
  ...browserPlugin,
  id: "spreadsheets",
  name: "Spreadsheets",
  name_zh: "表格",
  description: "Create spreadsheets",
  description_zh: "创建电子表格",
};

const openCodeZen = {
  ...westock,
  id: "opencode-zen",
  name: "OpenCode Zen Models",
  name_zh: "OpenCode Zen 模型适配器",
  description: "Direct Zen model API",
  description_zh: "直连 Zen 模型 API",
  type: "plugin" as const,
  mcp_servers: [],
  model_provider: {
    entry_id: "opencode-zen",
    protocol: "openai-compatible",
    base_url: "https://opencode.ai/zen/v1",
    dashboard_url: "https://opencode.ai/zen",
    api_key_label_zh: "OpenCode Zen API Key",
    login_cta_zh: "登录 OpenCode Zen 并获取 API Key",
    connection_note_zh: "直连模型 API，不安装或检测 OpenCode CLI",
    model_list_label_zh: "当前免费模型",
    free_models: ["big-pickle", "mimo-v2.5-free"],
    privacy_notices_zh: ["免费模型可能记录请求，不要发送机密数据。"],
  },
};

const freebuff2apiCommunity = {
  ...openCodeZen,
  id: "freebuff2api-community",
  name: "Freebuff2API Community Adapter",
  name_zh: "Freebuff2API 社区适配器",
  description_zh: "社区适配器，非 Freebuff 官方服务或官方插件。",
  model_provider: {
    entry_id: "freebuff2api-community",
    display_name_zh: "Freebuff2API 社区适配器",
    protocol: "openai-compatible",
    base_url: "https://open.freebuff.app/v1",
    dashboard_url: "https://open.freebuff.app",
    configurable_base_url: true,
    api_key_label_zh: "Freebuff2API API Key",
    login_cta_zh: "打开社区服务并获取 API Key",
    connection_note_zh: "第三方社区模型网关，不安装 Freebuff CLI",
    model_list_label_zh: "模型将在连接时动态读取",
    free_models: [],
    privacy_notices_zh: [
      "这是第三方社区适配器，不是 Freebuff 官方服务或官方插件。",
    ],
  },
};

describe("CapabilityMarketPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.removeItem("echoai.plugin-category-collapse.v1");
    mocks.getCapabilityStatus.mockResolvedValue({
      connected: false,
      auth_mode: "token",
    });
    mocks.getCapabilityDeviceFlow.mockResolvedValue({
      connector_id: "cli-one",
      active: false,
      device_flow: null,
    });
    mocks.cancelCapabilityDeviceFlow.mockResolvedValue({
      cancelled: true,
      connector_id: "cli-one",
    });
    mocks.uninstallCapability.mockResolvedValue(undefined);
    mocks.setCapabilityEnabled.mockResolvedValue(undefined);
    mocks.getCapabilityInstallPlan.mockImplementation(
      async (capabilityId: string) => ({
        schema: "echo.capability_install_plan.v1",
        capability_id: capabilityId,
        kind: "codex",
        version: "1.0.0",
        host_api: ">=0",
        permissions: ["content.read"],
        auth_modes: [],
        dependencies: [],
        runtime_dependencies: [],
        changes: ["verify_publisher_signature"],
        permission_review_required: true,
        can_install: true,
        blockers: [],
        plan_id: `plan:${capabilityId}`,
      }),
    );
    mocks.loadCapabilityIcon.mockResolvedValue(
      "data:image/png;base64,b3JpZ2luYWwtaWNvbg==",
    );
    mocks.oauthStatus.mockResolvedValue({
      server: "tdx-finance",
      authorized: false,
    });
  });

  it("explains desktop-only OAuth callbacks instead of timing out on web", async () => {
    const tdx = {
      ...westock,
      id: "tdx-connector",
      name: "Tongdaxin",
      name_zh: "通达信",
      auth_mode: "oauth",
      oauth_supported: true,
      mcp_servers: [
        {
          name: "tdx-finance",
          url: "https://txmcp.tdx.com.cn:3001/txmcp",
        },
      ],
    };
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [tdx],
      total: 1,
    });
    mocks.oauthAuthorize.mockResolvedValue({
      ok: true,
      authorize_url: "https://auth.tdx.com.cn/tdx-oauth/authorize",
      callback_transport: "desktop-deep-link",
    });
    const open = vi.spyOn(window, "open");

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
    await waitFor(() => expect(screen.getByText("通达信")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "连接" }));

    await waitFor(() =>
      expect(
        screen.getByText(/请在 EchoAI 桌面版中完成授权/),
      ).toBeInTheDocument(),
    );
    expect(open).not.toHaveBeenCalled();
  });

  it("renders connectors + plugins unified from the backend", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [westock, browserPlugin],
      total: 2,
    });
    mocks.getCapabilityStatus.mockResolvedValue({
      connected: false,
      auth_mode: "token",
    });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    await waitFor(() =>
      expect(screen.getByText("腾讯股票")).toBeInTheDocument(),
    );
    // 连接器 + 插件统一展示,不再出现「连接器」字样
    expect(screen.getByText("Browser")).toBeInTheDocument();
    expect(screen.queryByText(/连接器/)).not.toBeInTheDocument();
    expect(screen.getByText("技能 ×3")).toBeInTheDocument();
    // 已安装插件(连接器) → 连接 / 已禁用 按钮
    expect(screen.getByRole("button", { name: "连接" })).toBeInTheDocument();
    // 未安装插件 → 安装 按钮
    expect(screen.getByRole("button", { name: "安装" })).toBeInTheDocument();
    expect(mocks.listCapabilities).toHaveBeenCalledTimes(1);
    expect(mocks.listCapabilities).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 60, offset: 0 }),
    );
  });

  it("explains Freebuff CLI boundaries before starting official login", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [freebuffCapability],
      total: 1,
    });
    mocks.getCapabilityDeviceFlow.mockResolvedValue({
      connector_id: "freebuff-cli",
      active: false,
      device_flow: null,
    });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
    fireEvent.click(await screen.findByRole("button", { name: "连接" }));

    expect(
      await screen.findByText("官方 Freebuff CLI · 交互式本地 Agent"),
    ).toBeInTheDocument();
    expect(screen.getByText(/不会出现在模型选择器中/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登录 Freebuff" })).toBeEnabled();
  });

  it("refreshes contributed UI surfaces immediately after plugin installation", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [browserPlugin],
      total: 1,
    });
    mocks.installCapability.mockResolvedValue({
      installed: true,
      enabled: false,
      permissions: ["content.read"],
      permission_review_required: true,
    });
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");

    try {
      renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
      await screen.findByText("Browser");
      invalidate.mockClear();

      fireEvent.click(screen.getByRole("button", { name: "安装" }));

      expect(
        await screen.findByRole("heading", {
          name: "确认插件权限 · Browser",
        }),
      ).toBeInTheDocument();
      expect(screen.getByText("读取工作内容")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "确认并启用" }));

      await waitFor(() =>
        expect(mocks.installCapability).toHaveBeenCalledWith(
          "browser",
          "plan:browser",
        ),
      );
      expect(mocks.setCapabilityEnabled).toHaveBeenCalledWith("browser", true, [
        "content.read",
      ]);
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: CAPABILITY_SURFACE_QUERY_KEY,
      });
    } finally {
      invalidate.mockRestore();
    }
  });

  it("installs a model adapter before requesting its API key", async () => {
    const permissions = ["account.credentials", "network.remote"];
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [
        {
          ...openCodeZen,
          installed: false,
          enabled: false,
          permissions,
          permission_review_required: false,
        },
      ],
      total: 1,
    });
    mocks.getCapabilityInstallPlan.mockResolvedValue({
      schema: "echo.capability_install_plan.v1",
      capability_id: "opencode-zen",
      kind: "connector",
      version: "2.0.0",
      host_api: ">=0.2,<0.3",
      permissions,
      auth_modes: ["token"],
      dependencies: [],
      runtime_dependencies: [],
      changes: ["verify_publisher_signature"],
      permission_review_required: true,
      can_install: true,
      blockers: [],
      plan_id: "plan:opencode-zen",
    });
    mocks.installCapability.mockResolvedValue({
      installed: true,
      enabled: false,
      permissions,
      permission_review_required: true,
    });
    mocks.connectCapability.mockResolvedValue({
      connected: true,
      message: "已接入 2 个免费模型。",
    });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
    fireEvent.click(await screen.findByRole("button", { name: "安装" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "确认并配置" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "连接 · OpenCode Zen 模型适配器",
      }),
    ).toBeInTheDocument();
    expect(mocks.setCapabilityEnabled).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("OpenCode Zen API Key"), {
      target: { value: "zen-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证并接入免费模型" }));

    await waitFor(() =>
      expect(mocks.connectCapability).toHaveBeenCalledWith("opencode-zen", {
        tokens: { api_key: "zen-test-key" },
        run_cli: false,
        grant_permissions: permissions,
      }),
    );
  });

  it("configures an installed model adapter while granting pending permissions", async () => {
    const permissions = ["account.credentials", "network.remote"];
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [
        {
          ...openCodeZen,
          installed: true,
          enabled: false,
          permissions,
          permission_review_required: true,
        },
      ],
      total: 1,
    });
    mocks.connectCapability.mockResolvedValue({ connected: true });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
    fireEvent.click(
      await screen.findByRole("button", { name: "配置并启用" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "连接 · OpenCode Zen 模型适配器",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", {
        name: "确认插件权限 · OpenCode Zen 模型适配器",
      }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("OpenCode Zen API Key"), {
      target: { value: "zen-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证并接入免费模型" }));

    await waitFor(() =>
      expect(mocks.connectCapability).toHaveBeenCalledWith("opencode-zen", {
        tokens: { api_key: "zen-test-key" },
        run_cli: false,
        grant_permissions: permissions,
      }),
    );
    expect(mocks.setCapabilityEnabled).not.toHaveBeenCalled();
  });

  it("分页加载全部应用，首屏不再请求完整目录", async () => {
    const firstPage = Array.from({ length: 60 }, (_, index) => ({
      ...browserPlugin,
      id: `app-${index}`,
      name_zh: `应用 ${index}`,
    }));
    mocks.listCapabilities
      .mockResolvedValueOnce({ capabilities: firstPage, total: 61 })
      .mockResolvedValueOnce({
        capabilities: [{ ...browserPlugin, id: "app-60", name_zh: "应用 60" }],
        total: 61,
      });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
    fireEvent.click(await screen.findByRole("button", { name: "加载更多(1)" }));

    await waitFor(() =>
      expect(mocks.listCapabilities).toHaveBeenLastCalledWith(
        expect.objectContaining({ limit: 60, offset: 60 }),
      ),
    );
    expect(await screen.findByText("应用 60")).toBeInTheDocument();
  });

  it("shows install button for not-installed capabilities", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [{ ...westock, installed: false, enabled: false }],
      total: 1,
    });
    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "安装" })).toBeInTheDocument(),
    );
  });

  it("connects OpenCode Zen as an API model plugin without CLI fields", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [openCodeZen],
      total: 1,
    });
    mocks.connectCapability.mockResolvedValue({
      connected: true,
      message: "已接入 2 个免费模型。",
    });
    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    fireEvent.click(await screen.findByRole("button", { name: "连接" }));

    expect(screen.getByText(/不安装或检测 OpenCode CLI/)).toBeInTheDocument();
    expect(screen.getByText("big-pickle")).toBeInTheDocument();
    expect(screen.getByText(/不要发送机密数据/)).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("粘贴 access_token"),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("OpenCode Zen API Key"), {
      target: { value: "zen-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证并接入免费模型" }));

    await waitFor(() =>
      expect(mocks.connectCapability).toHaveBeenCalledWith("opencode-zen", {
        tokens: { api_key: "zen-test-key" },
        run_cli: false,
      }),
    );
  });

  it("labels Freebuff2API as community and submits an optional self-hosted URL", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [freebuff2apiCommunity],
      total: 1,
    });
    mocks.connectCapability.mockResolvedValue({ connected: true });
    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    fireEvent.click(await screen.findByRole("button", { name: "连接" }));

    expect(screen.getByText(/第三方社区模型网关/)).toBeInTheDocument();
    expect(screen.getByText(/不是 Freebuff 官方服务/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Freebuff2API API Key"), {
      target: { value: "sk-fb-test" },
    });
    fireEvent.change(screen.getByLabelText("服务地址"), {
      target: { value: "https://my-gateway.example/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证并接入免费模型" }));

    await waitFor(() =>
      expect(mocks.connectCapability).toHaveBeenCalledWith(
        "freebuff2api-community",
        {
          tokens: {
            api_key: "sk-fb-test",
            base_url: "https://my-gateway.example/v1",
          },
          run_cli: false,
        },
      ),
    );
  });

  it("disables uninstall when the backend marks a local package read-only", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [
        {
          ...browserPlugin,
          installed: true,
          enabled: true,
          lifecycle_manageable: false,
        },
      ],
      total: 1,
    });
    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    await screen.findByText("Browser");
    expect(screen.getByTitle("当前账号或工作区不允许卸载")).toBeDisabled();
  });

  it("按显式 ID 顺序展示精选并限制数量", async () => {
    const remoteDocuments = {
      ...documentsPlugin,
      id: "codex-marketplace:documents@openai-curated",
      provider_id: "documents",
    };
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [browserPlugin, remoteDocuments, sheetsPlugin],
      total: 3,
    });

    const { container } = renderWithProviders(
      <CapabilityMarketPanel
        view="featured"
        featuredIds={["documents", "browser", "spreadsheets"]}
        maxItems={2}
        showToolbar={false}
      />,
      { locale: "zh-CN" },
    );

    await waitFor(() => expect(screen.getByText("文档")).toBeInTheDocument());
    const ids = Array.from(
      container.querySelectorAll<HTMLElement>("[data-capability-id]"),
    ).map((card) => card.dataset.capabilityId);
    expect(ids).toEqual([
      "codex-marketplace:documents@openai-curated",
      "browser",
    ]);
    expect(screen.queryByText("表格")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("搜索插件")).not.toBeInTheDocument();
  });

  it("installed 视图只展示已安装应用", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [westock, browserPlugin],
      total: 2,
    });

    const { container } = renderWithProviders(
      <CapabilityMarketPanel view="installed" showToolbar={false} />,
      { locale: "zh-CN" },
    );

    await waitFor(() =>
      expect(screen.getByText("腾讯股票")).toBeInTheDocument(),
    );
    expect(
      container.querySelector('[data-capability-id="westock-mcp"]'),
    ).toBeInTheDocument();
    expect(
      container.querySelector('[data-capability-id="browser"]'),
    ).not.toBeInTheDocument();
  });

  it("外部搜索词在隐藏工具栏时仍然生效", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [westock, browserPlugin],
      total: 2,
    });

    renderWithProviders(
      <CapabilityMarketPanel searchQuery="in-app 浏览器" showToolbar={false} />,
      { locale: "zh-CN" },
    );

    await waitFor(() =>
      expect(screen.getByText("Browser")).toBeInTheDocument(),
    );
    expect(mocks.listCapabilities).toHaveBeenCalledWith(
      expect.objectContaining({ search: "in-app 浏览器" }),
    );
    expect(screen.queryByText("腾讯股票")).not.toBeInTheDocument();
  });

  it("紧凑目录按分类折叠并在搜索时展开匹配项", async () => {
    const businessPlugin = {
      ...browserPlugin,
      id: "shopify",
      name: "Shopify",
      name_zh: "Shopify",
      category: "business",
      description: "Build and manage your store",
      description_zh: "搭建并管理商店",
    };
    const creativePlugin = {
      ...browserPlugin,
      id: "canva",
      name: "Canva",
      name_zh: "Canva",
      category: "creative",
      description: "Create and edit designs",
      description_zh: "创建设计",
    };
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [westock, businessPlugin, creativePlugin],
      total: 3,
    });

    const view = renderWithProviders(
      <CapabilityMarketPanel compact showToolbar={false} />,
      { locale: "zh-CN" },
    );

    const installed = await screen.findByTestId("plugin-category-installed");
    const business = screen.getByTestId("plugin-category-business");
    expect(installed).toHaveAttribute("aria-expanded", "true");
    expect(business).toHaveTextContent("业务与运营");
    expect(screen.getByText("Shopify")).toBeInTheDocument();

    fireEvent.click(business);
    expect(business).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Shopify")).not.toBeInTheDocument();

    view.rerender(
      <CapabilityMarketPanel
        compact
        showToolbar={false}
        searchQuery="Shopify"
      />,
    );
    expect(await screen.findByText("Shopify")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("plugin-category-business")).toHaveAttribute(
        "aria-expanded",
        "true",
      ),
    );
  });

  it("优先显示插件清单声明的原始图标", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [
        {
          ...browserPlugin,
          id: "github",
          name: "GitHub",
          name_zh: "GitHub",
          icon: "./assets/logo.png",
        },
      ],
      total: 1,
    });

    renderWithProviders(<CapabilityMarketPanel compact showToolbar={false} />, {
      locale: "zh-CN",
    });

    const icon = await screen.findByTestId("capability-icon-github");
    expect(icon).toHaveAttribute(
      "src",
      "data:image/png;base64,b3JpZ2luYWwtaWNvbg==",
    );
    expect(mocks.loadCapabilityIcon).toHaveBeenCalledWith(
      expect.stringContaining("/api/plugins/github/assets/assets/logo.png"),
      expect.any(AbortSignal),
    );
  });

  it("重开连接弹窗会恢复设备流，关闭时取消并停止轮询", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    mocks.getCapabilityDeviceFlow.mockResolvedValue({
      connector_id: "cli-one",
      active: true,
      device_flow: activeDeviceFlow,
    });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    await waitFor(() =>
      expect(mocks.getCapabilityStatus.mock.calls.length).toBeGreaterThan(1),
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() =>
      expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
        "cli-one",
        "flow-a",
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("ABCD-EFGH")).not.toBeInTheDocument(),
    );

    const statusCallsAfterClose = mocks.getCapabilityStatus.mock.calls.length;
    await new Promise((resolve) => window.setTimeout(resolve, 2_100));
    expect(mocks.getCapabilityStatus).toHaveBeenCalledTimes(
      statusCallsAfterClose,
    );
  }, 7_000);

  it("父组件直接卸载时使用已展示设备流的 generation 清理", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    mocks.getCapabilityDeviceFlow.mockResolvedValue({
      connector_id: "cli-one",
      active: true,
      device_flow: activeDeviceFlow,
    });

    const rendered = renderWithProviders(<CapabilityMarketPanel />, {
      locale: "zh-CN",
    });
    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    await waitFor(() =>
      expect(mocks.getCapabilityStatus.mock.calls.length).toBeGreaterThan(1),
    );

    rendered.unmount();

    await waitFor(() =>
      expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
        "cli-one",
        "flow-a",
      ),
    );
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledTimes(1);
    const statusCallsAfterUnmount = mocks.getCapabilityStatus.mock.calls.length;
    await new Promise((resolve) => window.setTimeout(resolve, 2_100));
    expect(mocks.getCapabilityStatus).toHaveBeenCalledTimes(
      statusCallsAfterUnmount,
    );
  }, 7_000);

  it("恢复请求未返回时关闭会等待并清理迟到 generation", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    const recovery = deferred<{
      connector_id: string;
      active: boolean;
      device_flow: typeof activeDeviceFlow;
    }>();
    mocks.getCapabilityDeviceFlow.mockReturnValue(recovery.promise);

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    await waitFor(() =>
      expect(mocks.getCapabilityDeviceFlow).toHaveBeenCalledWith("cli-one"),
    );

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByRole("button", { name: "正在关闭…" })).toBeDisabled();
    expect(mocks.cancelCapabilityDeviceFlow).not.toHaveBeenCalled();

    await act(async () => {
      recovery.resolve({
        connector_id: "cli-one",
        active: true,
        device_flow: activeDeviceFlow,
      });
      await recovery.promise;
    });

    await waitFor(() =>
      expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
        "cli-one",
        "flow-a",
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("连接 · CLI One")).not.toBeInTheDocument(),
    );
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledTimes(1);
  });

  it("恢复请求未返回时卸载会由迟到响应自清理 generation", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    const recovery = deferred<{
      connector_id: string;
      active: boolean;
      device_flow: typeof activeDeviceFlow;
    }>();
    mocks.getCapabilityDeviceFlow.mockReturnValue(recovery.promise);
    const rendered = renderWithProviders(<CapabilityMarketPanel />, {
      locale: "zh-CN",
    });
    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    await waitFor(() =>
      expect(mocks.getCapabilityDeviceFlow).toHaveBeenCalledWith("cli-one"),
    );

    rendered.unmount();
    await act(async () => {
      recovery.resolve({
        connector_id: "cli-one",
        active: true,
        device_flow: activeDeviceFlow,
      });
      await recovery.promise;
    });

    await waitFor(() =>
      expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
        "cli-one",
        "flow-a",
      ),
    );
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledTimes(1);
  });

  it("连接响应落地但状态提交前卸载仍按 generation 清理", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    const connectPost = deferred<{
      connected: boolean;
      device_flow: typeof activeDeviceFlow;
    }>();
    mocks.connectCapability.mockReturnValue(connectPost.promise);
    const rendered = renderWithProviders(<CapabilityMarketPanel />, {
      locale: "zh-CN",
    });
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => {
      rendered.unmount();
      return null;
    });

    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    const submit = await screen.findByRole("button", {
      name: "执行 CLI 登录",
    });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);

    await act(async () => {
      connectPost.resolve({
        connected: false,
        device_flow: activeDeviceFlow,
      });
      await connectPost.promise;
    });

    await waitFor(() =>
      expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
        "cli-one",
        "flow-a",
      ),
    );
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledTimes(1);
    openSpy.mockRestore();
  });

  it("连接响应落地但状态提交前关闭优先使用 active generation", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    const connectPost = deferred<{
      connected: boolean;
      device_flow: typeof activeDeviceFlow;
    }>();
    mocks.connectCapability.mockReturnValue(connectPost.promise);
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => {
      fireEvent.click(screen.getByRole("button", { name: "取消" }));
      return null;
    });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    const submit = await screen.findByRole("button", {
      name: "执行 CLI 登录",
    });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);

    await act(async () => {
      connectPost.resolve({
        connected: false,
        device_flow: activeDeviceFlow,
      });
      await connectPost.promise;
    });

    await waitFor(() =>
      expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
        "cli-one",
        "flow-a",
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("连接 · CLI One")).not.toBeInTheDocument(),
    );
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledTimes(1);
    openSpy.mockRestore();
  });

  it("连接请求未完成时关闭会隔离迟到设备流并在重开前回收", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    const connectPost = deferred<{
      connected: boolean;
      device_flow: typeof activeDeviceFlow;
    }>();
    mocks.connectCapability.mockReturnValue(connectPost.promise);
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    const submit = await screen.findByRole("button", {
      name: "执行 CLI 登录",
    });
    await waitFor(() => expect(submit).toBeEnabled());
    act(() => {
      submit.click();
      submit.click();
    });
    await waitFor(() =>
      expect(mocks.connectCapability).toHaveBeenCalledWith("cli-one", {
        run_cli: true,
        tokens: undefined,
      }),
    );
    expect(mocks.connectCapability).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    const closingButton = await screen.findByRole("button", {
      name: "正在关闭…",
    });
    expect(closingButton).toBeDisabled();
    expect(mocks.cancelCapabilityDeviceFlow).not.toHaveBeenCalled();

    const statusCallsBeforeLateResponse =
      mocks.getCapabilityStatus.mock.calls.length;
    await act(async () => {
      connectPost.resolve({
        connected: false,
        device_flow: activeDeviceFlow,
      });
      await connectPost.promise;
    });

    await waitFor(() =>
      expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledTimes(1),
    );
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
      "cli-one",
      "flow-a",
    );
    await waitFor(() =>
      expect(screen.queryByText("连接 · CLI One")).not.toBeInTheDocument(),
    );
    expect(openSpy).not.toHaveBeenCalled();
    expect(screen.queryByText("ABCD-EFGH")).not.toBeInTheDocument();
    expect(mocks.getCapabilityStatus).toHaveBeenCalledTimes(
      statusCallsBeforeLateResponse,
    );

    fireEvent.click(screen.getByRole("button", { name: "连接" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "执行 CLI 登录" }),
      ).toBeEnabled(),
    );
    expect(screen.queryByText("ABCD-EFGH")).not.toBeInTheDocument();
    expect(openSpy).not.toHaveBeenCalled();

    openSpy.mockRestore();
  });

  it("关闭等待连接请求有界超时并保持旧 generation 隔离", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    const connectPost = deferred<{
      connected: boolean;
      device_flow: typeof activeDeviceFlow;
    }>();
    mocks.connectCapability.mockReturnValue(connectPost.promise);

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    const submit = await screen.findByRole("button", {
      name: "执行 CLI 登录",
    });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mocks.connectCapability).toHaveBeenCalledTimes(1),
    );

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole("button", { name: "取消" }));
      expect(screen.getByRole("button", { name: "正在关闭…" })).toBeDisabled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_001);
      });
      expect(
        screen.getByText(
          "连接操作仍在处理中，已隔离迟到结果；请稍后重试关闭。",
        ),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
      expect(submit).toBeDisabled();
    } finally {
      vi.useRealTimers();
    }

    await act(async () => {
      connectPost.resolve({
        connected: false,
        device_flow: activeDeviceFlow,
      });
      await connectPost.promise;
    });
    await waitFor(() =>
      expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledTimes(1),
    );
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
      "cli-one",
      "flow-a",
    );
    await waitFor(() => expect(submit).toBeEnabled());
    expect(screen.queryByText("ABCD-EFGH")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() =>
      expect(screen.queryByText("连接 · CLI One")).not.toBeInTheDocument(),
    );
  });

  it("迟到设备流回收失败时恢复授权信息供再次清理", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    const connectPost = deferred<{
      connected: boolean;
      device_flow: typeof activeDeviceFlow;
    }>();
    mocks.connectCapability.mockReturnValue(connectPost.promise);
    mocks.getCapabilityDeviceFlow
      .mockResolvedValueOnce({
        connector_id: "cli-one",
        active: false,
        device_flow: null,
      })
      .mockResolvedValue({
        connector_id: "cli-one",
        active: true,
        device_flow: activeDeviceFlow,
      });
    mocks.cancelCapabilityDeviceFlow.mockRejectedValueOnce(
      new Error("late cleanup failed"),
    );
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    const submit = await screen.findByRole("button", {
      name: "执行 CLI 登录",
    });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    await act(async () => {
      connectPost.resolve({
        connected: false,
        device_flow: activeDeviceFlow,
      });
      await connectPost.promise;
    });

    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    expect(screen.getByText("late cleanup failed")).toBeInTheDocument();
    expect(openSpy).not.toHaveBeenCalled();
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
      "cli-one",
      "flow-a",
    );

    mocks.cancelCapabilityDeviceFlow.mockResolvedValue({
      cancelled: true,
      connector_id: "cli-one",
    });
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() =>
      expect(screen.queryByText("连接 · CLI One")).not.toBeInTheDocument(),
    );
    openSpy.mockRestore();
  });

  it("取消设备流失败时保持弹窗打开", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    mocks.getCapabilityDeviceFlow.mockResolvedValue({
      connector_id: "cli-one",
      active: true,
      device_flow: activeDeviceFlow,
    });
    mocks.cancelCapabilityDeviceFlow.mockRejectedValue(
      new Error("cancel unavailable"),
    );

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));

    expect(await screen.findByText("cancel unavailable")).toBeInTheDocument();
    expect(screen.getByText("ABCD-EFGH")).toBeInTheDocument();
  });

  it("卸载 CLI 能力前先幂等取消设备流", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    mocks.getCapabilityDeviceFlow.mockResolvedValue({
      connector_id: "cli-one",
      active: true,
      device_flow: activeDeviceFlow,
    });
    const lifecycle: string[] = [];
    mocks.cancelCapabilityDeviceFlow.mockImplementation(async () => {
      lifecycle.push("cancel");
      return { cancelled: true, connector_id: "cli-one" };
    });
    mocks.uninstallCapability.mockImplementation(async () => {
      lifecycle.push("uninstall");
    });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    await screen.findByText("CLI One");
    fireEvent.click(screen.getByTitle("卸载能力包"));
    await waitFor(() => expect(lifecycle).toEqual(["cancel", "uninstall"]));
    expect(mocks.cancelCapabilityDeviceFlow).toHaveBeenCalledWith(
      "cli-one",
      "flow-a",
    );
  });

  it("旧 generation 取消失配时明示由权威卸载流程回收新会话", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    mocks.getCapabilityDeviceFlow.mockResolvedValue({
      connector_id: "cli-one",
      active: true,
      device_flow: activeDeviceFlow,
    });
    mocks.cancelCapabilityDeviceFlow.mockResolvedValue({
      cancelled: false,
      connector_id: "cli-one",
      reason: "generation_mismatch",
    });

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });
    await screen.findByText("CLI One");
    fireEvent.click(screen.getByTitle("卸载能力包"));

    expect(
      await screen.findByText(
        "授权会话已被新窗口替换，将由卸载操作统一回收最新会话。",
      ),
    ).toBeInTheDocument();
    expect(mocks.uninstallCapability).toHaveBeenCalledWith("cli-one");
  });

  it("设备流取消失败时阻止卸载", async () => {
    mocks.listCapabilities.mockResolvedValue({
      capabilities: [cliCapability],
      total: 1,
    });
    mocks.getCapabilityDeviceFlow.mockResolvedValue({
      connector_id: "cli-one",
      active: true,
      device_flow: activeDeviceFlow,
    });
    mocks.cancelCapabilityDeviceFlow.mockRejectedValue(
      new Error("cleanup failed"),
    );

    renderWithProviders(<CapabilityMarketPanel />, { locale: "zh-CN" });

    await screen.findByText("CLI One");
    fireEvent.click(screen.getByTitle("卸载能力包"));

    expect(await screen.findByText("cleanup failed")).toBeInTheDocument();
    expect(mocks.uninstallCapability).not.toHaveBeenCalled();
  });
});
