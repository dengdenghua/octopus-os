import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { UnifiedAssetsPanel } from "./unified-assets-panel";

const mocks = vi.hoisted(() => ({
  fetchUnifiedAssets: vi.fn(),
  syncUnifiedAssets: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/core/agents/agent-world-api", () => ({
  fetchUnifiedAssets: mocks.fetchUnifiedAssets,
  syncUnifiedAssets: mocks.syncUnifiedAssets,
}));

vi.mock("sonner", () => ({
  toast: mocks.toast,
}));

const summary = {
  root: "/tmp/assets",
  title: "统一资产仓库",
  sources: ["codex", "workbuddy", "local", "builtin"],
  updated_at: "2026-08-21T00:00:00",
  counts: { plugin: 3, skill: 2, agent: 2, team: 1 },
};

function makeItems() {
  return [
    {
      id: "browser",
      kind: "plugin",
      source: "codex",
      type: "codex-plugin",
      name: "Browser",
      name_zh: "浏览器",
      description: "控制浏览器",
      version: "1.0.0",
      skills_count: 1,
    },
    {
      id: "github",
      kind: "plugin",
      source: "workbuddy",
      type: "connector",
      name: "GitHub",
      name_zh: "GitHub 连接器",
      description: "GitHub 授权",
      auth_mode: "oauth",
      mcp_servers: ["github"],
    },
    {
      id: "writing",
      kind: "skill",
      source: "local",
      name: "Writing",
      name_zh: "写作",
      description: "写作技能",
    },
    {
      id: "wb_agent",
      kind: "agent",
      source: "workbuddy",
      name: "专家一",
      name_zh: "专家一",
      description: "领域专家",
    },
  ] as const;
}

describe("UnifiedAssetsPanel", () => {
  it("按 kind 拉取并展示插件卡 + 来源徽章", async () => {
    mocks.fetchUnifiedAssets.mockResolvedValue({
      summary,
      total: 3,
      items: makeItems().filter((i) => i.kind === "plugin"),
      kind_filter: "plugin",
    });
    renderWithProviders(<UnifiedAssetsPanel />);
    await waitFor(() => {
      expect(screen.getByText("浏览器")).toBeInTheDocument();
      expect(screen.getByText("GitHub 连接器")).toBeInTheDocument();
    });
    // 来源徽章
    expect(screen.getAllByText("WorkBuddy").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Codex").length).toBeGreaterThan(0);
    // 调用参数
    expect(mocks.fetchUnifiedAssets).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "plugin", limit: 60, offset: 0 }),
    );
  });

  it("按页加载统一资产而不是首屏拉取全部数据", async () => {
    const firstPage = Array.from({ length: 60 }, (_, index) => ({
      ...makeItems()[0],
      id: `plugin-${index}`,
      name_zh: `插件 ${index}`,
    }));
    mocks.fetchUnifiedAssets
      .mockResolvedValueOnce({
        summary,
        total: 61,
        items: firstPage,
        kind_filter: "plugin",
      })
      .mockResolvedValueOnce({
        summary,
        total: 61,
        items: [{ ...makeItems()[0], id: "plugin-60", name_zh: "插件 60" }],
        kind_filter: "plugin",
      });

    renderWithProviders(<UnifiedAssetsPanel />);
    await userEvent.click(
      await screen.findByRole("button", { name: "加载更多(1)" }),
    );

    await waitFor(() =>
      expect(mocks.fetchUnifiedAssets).toHaveBeenLastCalledWith(
        expect.objectContaining({ limit: 60, offset: 60 }),
      ),
    );
    expect(await screen.findByText("插件 60")).toBeInTheDocument();
  });

  it("切换技能 tab 会按 skill 重新拉取", async () => {
    mocks.fetchUnifiedAssets.mockImplementation(
      async (params: { kind?: string }) => {
        const all = makeItems();
        return {
          summary,
          total: all.filter((i) => i.kind === params.kind).length,
          items: all.filter((i) => i.kind === params.kind),
          kind_filter: params.kind ?? null,
        };
      },
    );
    renderWithProviders(<UnifiedAssetsPanel />);
    await userEvent.click(screen.getByRole("tab", { name: /技能/ }));
    await waitFor(() => {
      expect(screen.getByText("写作")).toBeInTheDocument();
    });
    expect(mocks.fetchUnifiedAssets).toHaveBeenLastCalledWith(
      expect.objectContaining({ kind: "skill" }),
    );
  });

  it("应用库模式只展示插件和技能并隐藏维护入口", async () => {
    mocks.fetchUnifiedAssets.mockImplementation(
      async (params: { kind?: string }) => {
        const all = makeItems();
        return {
          summary,
          total: all.filter((i) => i.kind === params.kind).length,
          items: all.filter((i) => i.kind === params.kind),
          kind_filter: params.kind ?? null,
        };
      },
    );

    renderWithProviders(
      <UnifiedAssetsPanel
        allowedKinds={["plugin", "skill"]}
        showSyncAction={false}
      />,
    );

    expect(screen.getByRole("tab", { name: /插件/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /技能/ })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /角色/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /专家团/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /重建索引/ }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /技能/ }));
    await waitFor(() => {
      expect(mocks.fetchUnifiedAssets).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: "skill" }),
      );
    });
  });

  it("单一 Skills 视图不重复展示类型导航", async () => {
    mocks.fetchUnifiedAssets.mockResolvedValue({
      summary,
      total: 1,
      items: makeItems().filter((item) => item.kind === "skill"),
      kind_filter: "skill",
    });

    renderWithProviders(
      <UnifiedAssetsPanel
        allowedKinds={["skill"]}
        initialKind="skill"
        showSyncAction={false}
      />,
    );

    expect(screen.queryByRole("tab", { name: /技能/ })).toBeNull();
    expect(screen.getByRole("button", { name: "全部" })).toBeVisible();
    await waitFor(() => {
      expect(mocks.fetchUnifiedAssets).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "skill" }),
      );
    });
  });

  it("点击卡片打开详情", async () => {
    mocks.fetchUnifiedAssets.mockResolvedValue({
      summary,
      total: 1,
      items: [makeItems()[1]],
      kind_filter: "plugin",
    });
    renderWithProviders(<UnifiedAssetsPanel />);
    await waitFor(() =>
      expect(screen.getByText("GitHub 连接器")).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByText("GitHub 连接器"));
    await waitFor(() => {
      // 详情弹窗里才有的标签(MCP 服务行)
      expect(screen.getByText("MCP 服务")).toBeInTheDocument();
    });
  });

  it("重建索引调用 sync 并刷新", async () => {
    mocks.fetchUnifiedAssets.mockResolvedValue({
      summary,
      total: 3,
      items: makeItems().filter((i) => i.kind === "plugin"),
      kind_filter: "plugin",
    });
    mocks.syncUnifiedAssets.mockResolvedValue({
      root: "/tmp/assets",
      counts: { plugin: 3, skill: 2, agent: 2, team: 1 },
      files_copied: 10,
      updated_at: "2026-08-21T00:00:00",
    });
    renderWithProviders(<UnifiedAssetsPanel />);
    await userEvent.click(screen.getByRole("button", { name: /重建索引/ }));
    await waitFor(() => {
      expect(mocks.syncUnifiedAssets).toHaveBeenCalledTimes(1);
    });
    expect(mocks.toast.success).toHaveBeenCalled();
  });
});
