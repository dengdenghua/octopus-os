/**
 * Component tests for EvolutionControlPanel.
 *
 * Focus: rendering, tab switching, empty state, error banner — with
 * `fetch` stubbed to resolve empty arrays / 404s.  Deeper data-flow
 * assertions live in the companion `.integration.test.tsx`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
  getEchoBaseURL: () => "/api",
}));
vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test-token" }),
}));

import { EvolutionControlPanel } from "./index";
import { I18nContext } from "@/core/i18n/context";
import { zhCN } from "@/core/i18n/locales";

function mockFetchOk<T>(data: T) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
  });
}

/**
 * Smarter stub: Budget snapshot requires `{components: []}` shape; every
 * other endpoint in the panel expects an array. Routing by URL keeps
 * BudgetSection from crashing while we target a different tab.
 */
function mockFetchByURL() {
  return vi.fn((url: RequestInfo | URL) => {
    const u = typeof url === "string" ? url : url.toString();
    const body = u.includes("/budget/snapshot") ? { components: [] } : [];
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    });
  });
}

function mockFetch404() {
  return vi.fn().mockResolvedValue({
    ok: false,
    status: 404,
    json: () => Promise.resolve({}),
  });
}

function renderPanel() {
  return render(
    <I18nContext.Provider
      value={{
        locale: "zh-CN",
        t: zhCN,
        setLocale: () => Promise.resolve(),
      }}
    >
      <EvolutionControlPanel />
    </I18nContext.Provider>,
  );
}

describe("EvolutionControlPanel — component", () => {
  beforeEach(() => {
    // Default to URL-aware empty responses so every section renders cleanly.
    global.fetch = mockFetchByURL() as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the control-plane header with all Chinese tab labels", async () => {
    renderPanel();
    expect(screen.getByText("控制面板")).toBeInTheDocument();
    for (const label of [
      "预算",
      "技能提案",
      "模型",
      "MCP",
      "课程",
      "框架",
      "漂移",
      "A/B 分发",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    // Flush the BudgetSection fetch so its setState lands inside act().
    await screen.findByText("暂无预算数据。");
  });

  it("defaults to the Budget section and calls /api/evolution/budget/snapshot", async () => {
    global.fetch = mockFetchOk({ components: [] }) as unknown as typeof fetch;
    renderPanel();
    expect(screen.getByText("预算与熔断器")).toBeInTheDocument();
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/evolution/budget/snapshot",
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: "Bearer test-token",
          }),
        }),
      );
    });
    expect(await screen.findByText("暂无预算数据。")).toBeInTheDocument();
  });

  it("switches to the MCP tab and shows the localized empty state", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "MCP" }));

    expect(await screen.findByText("MCP 提案")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "审核全部待定" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("暂无 MCP 提案。")).toBeInTheDocument();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/intel-evolution/mcp/proposals",
        expect.anything(),
      );
    });
  });

  it("renders a red error banner when the endpoint returns 404", async () => {
    global.fetch = mockFetch404() as unknown as typeof fetch;
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "A/B 分发" }));

    expect(await screen.findByText("真实流量 A/B 分发")).toBeInTheDocument();
    expect(
      await screen.findByText("GET /api/evolution/dispatch/snapshot → 404"),
    ).toBeInTheDocument();
  });

  it.each([
    ["技能提案", "来自情报的技能提案", "暂无待处理的技能提案。"],
    ["模型", "模型提案", "尚未发现模型提案。"],
    ["课程", "课程目标", "暂无待处理的课程目标。是否已积累足够失败可聚类？"],
    [
      "框架",
      "框架基准测试",
      "尚未运行任何基准测试。请注册策略并 POST 到 /frameworks/benchmarks/run。",
    ],
    ["漂移", "协议漂移与修复", "未检测到漂移事件。"],
    ["A/B 分发", "真实流量 A/B 分发", "暂无进行中的 A/B 分配。"],
  ])(
    "tab %s renders its Chinese title and empty state",
    async (tab, title, empty) => {
      renderPanel();
      fireEvent.click(screen.getByRole("button", { name: tab }));
      expect(await screen.findByText(title)).toBeInTheDocument();
      expect(await screen.findByText(empty)).toBeInTheDocument();
    },
  );

  it("marks the active tab with the primary styling", async () => {
    renderPanel();
    const mcp = screen.getByRole("button", { name: "MCP" });
    const budget = screen.getByRole("button", { name: "预算" });
    expect(budget.className).toMatch(/bg-primary/);
    expect(mcp.className).not.toMatch(/bg-primary/);

    fireEvent.click(mcp);
    expect(mcp.className).toMatch(/bg-primary/);
    expect(budget.className).not.toMatch(/bg-primary/);
    // Flush the McpProposalsSection fetch.
    await screen.findByText("暂无 MCP 提案。");
  });
});
