import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { IntelligencePanel } from "./intelligence-panel";

const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: toastMocks }));
vi.mock("./messages/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <div>{content}</div>,
}));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function emptyApi(input: RequestInfo | URL) {
  const url = String(input);
  if (url.endsWith("/api/intelligence/subscriptions")) {
    return jsonResponse({ subscriptions: [] });
  }
  if (url.endsWith("/api/intelligence/reports")) {
    return jsonResponse({ reports: [] });
  }
  return jsonResponse({});
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock.mockImplementation(emptyApi);
});

describe("IntelligencePanel", () => {
  it("shows an accessible recoverable loading error", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(() => jsonResponse({}, 500));

    renderWithProviders(<IntelligencePanel />, { locale: "zh-CN" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("自动订阅加载失败，请稍后重试。");
    await user.click(within(alert).getByRole("button", { name: "重试" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(2));
  });

  it("labels every AI draft field and clears stale drafts when using an example", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (
          url.endsWith("/api/intelligence/subscriptions/draft") &&
          init?.method === "POST"
        ) {
          return jsonResponse({
            draft: {
              topic: "Agent 生态",
              display_name: "Agent 生态",
              keywords: ["agent", "release"],
              cadence: "每天",
              schedule_time: "09:00",
              schedule_day: "1",
              timezone: "Asia/Shanghai",
              instructions: "只保留重要变化",
              sources: ["github", "web"],
            },
          });
        }
        return emptyApi(input);
      },
    );

    renderWithProviders(<IntelligencePanel />, { locale: "zh-CN" });

    const goal = await screen.findByRole("textbox", {
      name: "描述你想持续追踪的内容",
    });
    expect(screen.getByRole("button", { name: "AI 定制订阅" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    await user.type(goal, "每天 09:00 跟踪 Agent 生态");
    await user.click(screen.getByRole("button", { name: "生成订阅草案" }));

    expect(
      await screen.findByRole("textbox", { name: "订阅名称" }),
    ).toHaveValue("Agent 生态");
    expect(
      screen.getByRole("textbox", { name: "关键词（用逗号分隔）" }),
    ).toHaveValue("agent, release");
    expect(
      screen.getByRole("combobox", { name: "执行频率" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "数据来源（用逗号分隔）" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "筛选与报告要求" }),
    ).toBeInTheDocument();

    const example =
      "每天跟踪 EchoAI Agent 相关 GitHub release、issue 和竞品动态，只保留和产品决策有关的变化";
    await user.click(screen.getAllByRole("button", { name: example })[0]!);
    expect(goal).toHaveValue(example);
    expect(
      screen.queryByRole("textbox", { name: "订阅名称" }),
    ).not.toBeInTheDocument();
  });

  it("keeps subscription selection separate from named action controls", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/intelligence/subscriptions")) {
        return jsonResponse({
          subscriptions: [
            {
              id: "alpha/id",
              topic: "alpha",
              display_name: "Alpha 观察",
              keywords: ["alpha"],
              enabled: true,
            },
          ],
        });
      }
      if (url.endsWith("/api/intelligence/reports")) {
        return jsonResponse({ reports: [] });
      }
      return jsonResponse({});
    });

    renderWithProviders(<IntelligencePanel />, { locale: "zh-CN" });

    const select = await screen.findByRole("button", {
      name: "查看订阅报告：Alpha 观察",
    });
    expect(select.querySelector("button")).toBeNull();
    expect(
      screen.getByRole("button", { name: "立即运行订阅：Alpha 观察" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "停用订阅：Alpha 观察" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "删除订阅：Alpha 观察" }),
    ).toBeInTheDocument();
  });

  it("requires confirmation and encodes the subscription id before deleting", async () => {
    const user = userEvent.setup();
    let subscriptions: unknown[] = [
      {
        id: "alpha/id",
        topic: "alpha",
        display_name: "Alpha 观察",
        keywords: ["alpha"],
        enabled: true,
      },
    ];
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "DELETE") {
          subscriptions = [];
          return jsonResponse({ ok: true, id: "alpha/id" });
        }
        if (url.endsWith("/api/intelligence/subscriptions")) {
          return jsonResponse({ subscriptions });
        }
        if (url.endsWith("/api/intelligence/reports")) {
          return jsonResponse({ reports: [] });
        }
        return jsonResponse({});
      },
    );

    renderWithProviders(<IntelligencePanel />, { locale: "zh-CN" });
    await user.click(
      await screen.findByRole("button", {
        name: "删除订阅：Alpha 观察",
      }),
    );

    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
    ).toBe(false);
    const dialog = await screen.findByRole("dialog", {
      name: "删除自动订阅",
    });
    expect(dialog).toHaveTextContent(
      "确定删除自动订阅「Alpha 观察」吗？已有报告不会被删除，此操作不可撤销。",
    );
    await user.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(
        ([, init]) => init?.method === "DELETE",
      );
      expect(String(deleteCall?.[0])).toContain(
        "/api/intelligence/subscriptions/alpha%2Fid",
      );
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});
