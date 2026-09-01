import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "@/components/ui/sidebar";
import { renderWithProviders } from "@/test/harness";
import type * as AgentsApiModule from "@/core/agents/api";

const listLocalAgentsMock = vi.hoisted(() => vi.fn());
const listRegistryRolesMock = vi.hoisted(() => vi.fn());
const installRegistryRoleMock = vi.hoisted(() => vi.fn());
const waitForBackendAvailabilityMock = vi.hoisted(() => vi.fn());
const agentWorldApiMocks = vi.hoisted(() => ({
  installAgent: vi.fn(),
  fetchCloudInstalled: vi.fn(),
  fetchRuntimePluginStatus: vi.fn(),
  fetchRuntimePluginStatuses: vi.fn(),
  installCloudPlugin: vi.fn(),
  rollbackCloudPlugin: vi.fn(),
  setCloudPluginEnabled: vi.fn(),
  uninstallCloudPlugin: vi.fn(),
}));

vi.mock("@/core/agents/agent-world-api", () => agentWorldApiMocks);

vi.mock("@/core/backend/readiness", () => ({
  waitForBackendAvailability: (...args: unknown[]) =>
    waitForBackendAvailabilityMock(...args),
}));

vi.mock("@/core/agents/api", async (importOriginal) => {
  const actual = await importOriginal<typeof AgentsApiModule>();
  return {
    ...actual,
    listAgents: listLocalAgentsMock,
  };
});

vi.mock("@/core/registry/api", () => ({
  listRegistryRoles: listRegistryRolesMock,
  installRegistryRole: installRegistryRoleMock,
}));

vi.mock("@/components/store/workbuddy-cloud-store-panel", () => ({
  WorkBuddyCloudStorePanel: ({
    embedded,
    kind,
    searchQuery,
    showTypeFilter,
    showTeamFilter,
  }: {
    embedded?: boolean;
    kind?: string;
    searchQuery?: string;
    showTypeFilter?: boolean;
    showTeamFilter?: boolean;
  }) => (
    <div
      data-testid="workbuddy-talent-market"
      data-embedded={String(embedded)}
      data-kind={kind}
      data-search={searchQuery}
      data-show-type-filter={String(showTypeFilter)}
      data-show-team-filter={String(showTeamFilter)}
    />
  ),
}));

vi.mock("./agent-role-profile-dialog", () => ({
  AgentRoleProfileDialog: ({
    agent,
  }: {
    agent: { display_name: string } | null;
  }) => (
    <div data-testid="agent-role-profile-dialog">
      {agent?.display_name ?? ""}
    </div>
  ),
}));

import {
  AgentWorldUnified,
  AgentsTab,
  resolveHubMarketRoute,
  resolveHubTalentView,
} from "./agent-world-unified";

beforeEach(() => {
  listLocalAgentsMock.mockReset();
  listLocalAgentsMock.mockResolvedValue([]);
  listRegistryRolesMock.mockReset();
  listRegistryRolesMock.mockResolvedValue({ roles: [] });
  installRegistryRoleMock.mockReset();
  waitForBackendAvailabilityMock.mockReset().mockResolvedValue(undefined);
  agentWorldApiMocks.fetchCloudInstalled.mockReset().mockResolvedValue({
    skills: [],
    plugins: [],
    plugin_states: {},
  });
  agentWorldApiMocks.fetchRuntimePluginStatus
    .mockReset()
    .mockRejectedValue(new Error("not installed"));
  agentWorldApiMocks.fetchRuntimePluginStatuses
    .mockReset()
    .mockResolvedValue(new Map());
  agentWorldApiMocks.installCloudPlugin.mockReset().mockResolvedValue({
    installed: true,
    plugin_id: "test",
    path: "/tmp/test",
  });
  agentWorldApiMocks.rollbackCloudPlugin.mockReset();
  agentWorldApiMocks.setCloudPluginEnabled.mockReset();
  agentWorldApiMocks.uninstallCloudPlugin.mockReset();
});

function renderAgentsTab({
  loading = false,
  loadError = false,
  sceneOnly = false,
  onRetry = vi.fn(),
}: {
  loading?: boolean;
  loadError?: boolean;
  sceneOnly?: boolean;
  onRetry?: () => void;
} = {}) {
  renderWithProviders(
    <AgentsTab
      agents={[]}
      filteredAgents={[]}
      loading={loading}
      loadError={loadError}
      activeCategory="all"
      onCategoryChange={vi.fn()}
      onSelectAgent={vi.fn()}
      onInstallChange={vi.fn()}
      onRetry={onRetry}
      sceneOnly={sceneOnly}
    />,
    { locale: "zh-CN" },
  );
}

describe("Agent Hub role list states", () => {
  it("announces loading without exposing empty controls", () => {
    renderAgentsTab({ loading: true });
    expect(screen.getByRole("status")).toHaveTextContent("正在加载角色");
    expect(screen.queryByRole("button", { name: "全部" })).toBeNull();
  });

  it("keeps a failed load recoverable", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    renderAgentsTab({ loadError: true, onRetry });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "角色列表加载失败，请稍后重试。",
    );
    await user.click(screen.getByRole("button", { name: "重新加载" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("keeps a scene-only failure compact", () => {
    renderAgentsTab({ loadError: true, sceneOnly: true });
    expect(screen.getByRole("alert")).toHaveTextContent("精选场景暂时不可用");
    expect(screen.queryByText("角色列表加载失败，请稍后重试。")).toBeNull();
  });

  it("exposes category selection state without counts polluting names", () => {
    renderAgentsTab();
    expect(screen.getByRole("group", { name: "按业务领域筛选" })).toBeVisible();
    expect(screen.getByRole("button", { name: "全部" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "编程" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });
});

describe("HUB market shell", () => {
  it("keeps the heavy role profile out of the scene-first directory", async () => {
    listLocalAgentsMock.mockResolvedValue([
      {
        id: "general",
        name: "general",
        display_name: "Lazy Agent",
        description: "Loaded on demand",
        author: "echo",
        category: "assistant",
        tags: [],
        icon: "🤖",
        version: "1.0.0",
        downloads: 1,
        rating: 0,
        rating_count: 0,
        is_featured: false,
        is_official: true,
        is_installed: true,
        created_at: "0",
      },
    ]);

    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents?tab=agents",
        locale: "zh-CN",
      },
    );

    expect(
      await screen.findByRole("button", { name: "启动场景：白幽灵行动组" }),
    ).toBeVisible();
    expect(screen.queryByTestId("agent-role-profile-dialog")).toBeNull();
  });

  it("opens the HUB directly on the role directory", () => {
    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents",
        locale: "zh-CN",
      },
    );

    expect(screen.queryByRole("tab", { name: "开始" })).toBeNull();
    expect(screen.getByRole("tab", { name: "角色" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "应用" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Skills" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "添加", exact: true }),
    ).toBeVisible();
    expect(screen.getAllByRole("heading", { name: "HUB" })).not.toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "开始一项工作" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "常用应用" })).toBeNull();
    expect(
      screen.getByRole("textbox", {
        name: "搜索角色、应用或 Skills…",
      }),
    ).toBeVisible();
    expect(screen.queryByText(/统一资产/)).toBeNull();
    expect(
      within(screen.getByTestId("hub-market-navigation"))
        .getAllByRole("tab")
        .map((tab) => tab.textContent?.trim()),
    ).toEqual(["角色", "应用", "Skills"]);
  });

  it("keeps primary personas in scenes and aggregates every remote source", async () => {
    listLocalAgentsMock.mockResolvedValue([
      {
        id: "general",
        name: "general",
        display_name: "通用助手",
        description: "White Ghost local identity",
        author: "echo",
        category: "assistant",
        tags: [],
        icon: "🤖",
        version: "1.0.0",
        downloads: 0,
        rating: 0,
        rating_count: 0,
        is_featured: true,
        is_official: true,
        is_installed: true,
        created_at: "0",
      },
      {
        id: "twin_legal",
        name: "twin_legal",
        display_name: "法务合规分身",
        description: "Cloud-origin persona",
        author: "registry",
        category: "specialist",
        tags: ["legal"],
        icon: "⚖️",
        version: "1.0.0",
        downloads: 0,
        rating: 0,
        rating_count: 0,
        is_featured: false,
        is_official: false,
        is_installed: true,
        source_kind: "digital-twin-human-role",
        created_at: "0",
      },
    ]);
    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      { initialRoute: "/workspace/agents?tab=agents", locale: "zh-CN" },
    );

    expect((await screen.findAllByText("通用助手")).length).toBeGreaterThan(0);
    expect(screen.queryByText("法务合规分身")).toBeNull();
    expect(screen.getByRole("heading", { name: "精选场景" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "远端角色" })).toBeVisible();
    expect(screen.getByTestId("workbuddy-talent-market")).not.toHaveAttribute(
      "data-kind",
    );
    expect(screen.getByTestId("workbuddy-talent-market")).toHaveAttribute(
      "data-show-type-filter",
      "false",
    );
    expect(screen.getByTestId("workbuddy-talent-market")).toHaveAttribute(
      "data-show-team-filter",
      "true",
    );
  });

  it("keeps legacy HUB tabs mapped into the new market hierarchy", () => {
    expect(resolveHubMarketRoute("")).toEqual({
      section: "agents",
      applicationView: "all",
    });
    expect(resolveHubMarketRoute("?tab=agents")).toEqual({
      section: "agents",
      applicationView: "all",
    });
    expect(resolveHubMarketRoute("?tab=plugins")).toEqual({
      section: "applications",
      applicationView: "featured",
    });
    expect(resolveHubMarketRoute("?tab=plugins&view=featured")).toEqual({
      section: "applications",
      applicationView: "featured",
    });
    expect(resolveHubMarketRoute("?tab=plugins&view=all")).toEqual({
      section: "applications",
      applicationView: "all",
    });
    expect(resolveHubMarketRoute("?tab=plugins&view=installed")).toEqual({
      section: "applications",
      applicationView: "installed",
    });
    expect(resolveHubMarketRoute("?tab=skills")).toEqual({
      section: "skills",
      applicationView: "all",
    });
    expect(resolveHubMarketRoute("?tab=assets")).toEqual({
      section: "applications",
      applicationView: "all",
    });
    expect(resolveHubTalentView("?tab=agents")).toBe("roles");
    expect(resolveHubTalentView("?tab=agents&talent=experts")).toBe("experts");
    expect(resolveHubTalentView("?tab=agents&talent=teams")).toBe("teams");
    expect(resolveHubTalentView("?tab=agents&talent=cloud")).toBe("cloud");
    expect(resolveHubTalentView("?tab=agents&talent=remote")).toBe("remote");
  });

  it("keeps plugin discovery separate from role management", async () => {
    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents?surface=chat&tab=plugins",
        locale: "zh-CN",
      },
    );

    expect(
      screen.getByRole("heading", { name: "应用中心" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "工作台内嵌应用" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "推荐插件" })).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "从这些能力开始" }),
    ).toBeVisible();
    expect(screen.getByRole("tab", { name: "推荐" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "全部" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "已安装" })).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: "模拟炒股 · 策略验证与模拟交易",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: "设计画布 · 视觉创作、素材编排与设计工作流",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "将模拟炒股添加到侧栏" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "从侧栏移除设计画布" }),
    ).toBeVisible();
    expect(screen.queryByRole("combobox", { name: "应用范围" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "应用来源" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Skills" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "管理已添加应用" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "添加", exact: true }),
    ).toBeNull();
    expect(screen.queryByText("创建 AI 成员")).toBeNull();
    await waitFor(() => {
      expect(
        agentWorldApiMocks.fetchRuntimePluginStatuses,
      ).toHaveBeenCalledTimes(1);
    });
    expect(agentWorldApiMocks.fetchRuntimePluginStatus).not.toHaveBeenCalled();
  });

  it("shows role actions only on the AI member tab", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents?tab=agents",
        locale: "zh-CN",
      },
    );

    await user.click(screen.getByRole("button", { name: "添加", exact: true }));
    expect(
      screen.getByRole("menuitem", { name: "创建 AI 成员" }),
    ).toBeVisible();
  });

  it("keeps installed state on individual remote items", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents?surface=chat&tab=assets",
        locale: "zh-CN",
      },
    );

    expect(screen.queryByRole("tab", { name: "已添加" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "应用范围" })).toBeNull();
    expect(screen.getByRole("heading", { name: "插件" })).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "角色" }));
    expect(screen.queryByRole("group", { name: "成员范围" })).toBeNull();
    expect(screen.getByRole("heading", { name: "远端角色" })).toBeVisible();
  });

  it("shows repair and rollback controls for a broken workbench package", async () => {
    const user = userEvent.setup();
    agentWorldApiMocks.fetchCloudInstalled.mockResolvedValue({
      skills: [],
      plugins: ["design"],
      plugin_states: {
        design: {
          installed: true,
          enabled: false,
          lifecycle_state: "broken",
          rollback_available: true,
          rollback_operation: "update",
          transaction_id: "tx-update",
          error: "digest mismatch",
        },
      },
    });

    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents?surface=chat&tab=plugins",
        locale: "zh-CN",
      },
    );

    expect(await screen.findByText("安装损坏 · 点击修复")).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: /设计画布添加到侧栏/ }),
    );
    expect(
      screen.getByRole("menuitem", { name: "重新安装修复" }),
    ).toBeVisible();
    expect(
      screen.getByRole("menuitem", { name: "回退上个版本" }),
    ).toBeVisible();
  });

  it("offers to restore retained work when reinstalling", async () => {
    const user = userEvent.setup();
    agentWorldApiMocks.fetchCloudInstalled.mockResolvedValue({
      skills: [],
      plugins: [],
      plugin_states: {
        narrative_studio: {
          installed: false,
          enabled: false,
          lifecycle_state: "available",
          recoveries: [{ recovery_id: "recover-1" }],
        },
      },
    });

    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents?surface=chat&tab=plugins",
        locale: "zh-CN",
      },
    );

    expect(await screen.findByText("有可恢复的作品")).toBeVisible();
    await user.click(
      screen.getByRole("button", {
        name: "叙事工坊 · 角色、世界观、剧情分支与正典协作",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "恢复叙事工坊作品" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "安装并恢复作品" }),
    ).toBeVisible();
  });

  it("uses one WorkBuddy directory for remote roles and teams", () => {
    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents?tab=agents",
        locale: "zh-CN",
      },
    );

    expect(screen.getByRole("tab", { name: "角色" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByRole("group", { name: "成员库" })).toBeNull();
    expect(screen.getByTestId("workbuddy-talent-market")).toHaveAttribute(
      "data-embedded",
      "true",
    );
    expect(screen.getByTestId("workbuddy-talent-market")).not.toHaveAttribute(
      "data-kind",
    );
  });

  it("maps legacy talent deep links into the unified remote directory", () => {
    renderWithProviders(
      <SidebarProvider>
        <AgentWorldUnified />
      </SidebarProvider>,
      {
        initialRoute: "/workspace/agents?tab=agents&talent=teams",
        locale: "zh-CN",
      },
    );

    expect(screen.getByRole("tab", { name: "角色" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByRole("button", { name: "专家团" })).toBeNull();
    expect(screen.getByTestId("workbuddy-talent-market")).not.toHaveAttribute(
      "data-kind",
    );
  });

  it("returns to the role directory when the HUB link clears a legacy tab", async () => {
    function NavigableHub() {
      const navigate = useNavigate();
      return (
        <>
          <button
            type="button"
            onClick={() => navigate("/workspace/agents?surface=chat")}
          >
            返回 HUB
          </button>
          <SidebarProvider>
            <AgentWorldUnified />
          </SidebarProvider>
        </>
      );
    }

    renderWithProviders(<NavigableHub />, {
      initialRoute: "/workspace/agents?tab=plugins",
      locale: "zh-CN",
    });

    expect(screen.getByRole("tab", { name: "应用" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByRole("tab", { name: "全部应用" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "返回 HUB" }));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "角色" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
  });

  it("leaves the HUD deep-link surface free of marketplace chrome", () => {
    renderWithProviders(<AgentWorldUnified />, {
      initialRoute: "/workspace/agents?hud=1&agent=eve",
      locale: "zh-CN",
    });

    expect(screen.queryByTestId("hub-market-navigation")).toBeNull();
  });
});
