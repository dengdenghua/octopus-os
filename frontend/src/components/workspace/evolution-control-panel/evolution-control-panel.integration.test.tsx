/**
 * Integration tests for EvolutionControlPanel.
 *
 * These drive the real component against a routed fetch stub, so we
 * exercise the full load → render → act → reload cycle for each tab.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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

type Handler = (url: string, init: RequestInit | undefined) => unknown;

/**
 * Build a fetch stub that dispatches to per-URL + per-method handlers.
 * Any unmatched URL resolves to an empty-array 200 (keeps the other
 * tab sections quiet while we focus on one).
 */
function routedFetch(routes: Record<string, Handler>) {
  // Budget snapshot is fetched on initial mount regardless of which tab
  // a given test targets, and its payload shape is `{components: [...]}`
  // — not a plain array — so default it here to keep tab-focused tests
  // terse.
  const merged: Record<string, Handler> = {
    "GET /api/evolution/budget/snapshot": () => ({ components: [] }),
    ...routes,
  };
  return vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof url === "string" ? url : url.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${u}`;
    const handler = merged[key] ?? merged[u] ?? (() => [] as unknown[]); // default: empty list
    const body = handler(u, init);
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    });
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

describe("EvolutionControlPanel — integration", () => {
  beforeEach(() => {
    global.fetch = routedFetch({}) as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("Budget tab renders component rows, usage bar, and resets an open breaker", async () => {
    const user = userEvent.setup();
    const resetCalls: RequestInit[] = [];

    global.fetch = routedFetch({
      "GET /api/evolution/budget/snapshot": () => ({
        components: [
          {
            name: "skill-improver",
            budget: {
              max_calls_per_hour: 10,
              max_calls_per_day: 100,
              breaker_failure_threshold: 3,
              breaker_cooldown_seconds: 60,
            },
            usage: {
              hourly_used: 7,
              hourly_limit: 10,
              hourly_remaining: 3,
              daily_used: 20,
              daily_limit: 100,
              daily_remaining: 80,
            },
            breaker: {
              component: "skill-improver",
              state: "open",
              consecutive_failures: 4,
              opened_at: "2026-04-15T00:00:00Z",
            },
            last_24h: {
              success: 12,
              failure: 3,
              rejected_budget: 1,
              rejected_breaker: 2,
            },
            cost: {
              hourly_tokens: 700,
              daily_tokens: 1250,
              hourly_usd: 0.02,
              daily_usd: 0.0456,
            },
            last_reset_at: "2026-04-15T01:00:00Z",
          },
        ],
        source: "journal",
        events: 8,
      }),
      "POST /api/evolution/budget/breaker/reset": (_u, init) => {
        resetCalls.push(init!);
        return { ok: true };
      },
    }) as unknown as typeof fetch;

    renderPanel();

    expect(await screen.findByText("skill-improver")).toBeInTheDocument();
    expect(screen.getByText(/连续失败 4 次/)).toBeInTheDocument();
    expect(screen.getByText(/上限 10\/小时/)).toBeInTheDocument();
    expect(screen.getByText(/每小时/)).toBeInTheDocument();
    expect(screen.getByText("7/10")).toBeInTheDocument();
    expect(screen.getByText(/24 小时：12✓ · 3✗/)).toBeInTheDocument();
    expect(screen.getByText(/拒绝：1 预算 \/ 2 熔断/)).toBeInTheDocument();

    const resetBtn = screen.getByRole("button", { name: "重置" });
    await user.click(resetBtn);

    await waitFor(() => expect(resetCalls.length).toBe(1));
    expect(JSON.parse(resetCalls[0].body as string)).toEqual({
      component: "skill-improver",
    });
  });

  it("Skill Proposals tab approves a proposal, POSTs the right payload, and reloads", async () => {
    const user = userEvent.setup();
    const approveCalls: string[] = [];
    let listCallCount = 0;

    global.fetch = routedFetch({
      "GET /api/intel-evolution/skills/proposals?status=pending": () => {
        listCallCount += 1;
        return listCallCount === 1
          ? [
              {
                id: 1,
                name: "summarize-pdfs",
                created_at: "2026-04-14T10:00:00Z",
                topic: "summarization",
                source_url: "https://example.com/post",
                status: "pending",
              },
            ]
          : [];
      },
      "POST /api/intel-evolution/skills/proposals/approve": (_u, init) => {
        approveCalls.push(init!.body as string);
        return { ok: true };
      },
    }) as unknown as typeof fetch;

    renderPanel();
    await user.click(screen.getByRole("button", { name: "技能提案" }));

    expect(await screen.findByText("summarize-pdfs")).toBeInTheDocument();
    expect(screen.getByText("summarization")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "批准" }));

    await waitFor(() => expect(approveCalls.length).toBe(1));
    expect(JSON.parse(approveCalls[0])).toEqual({
      name: "summarize-pdfs",
      reason: "approved",
    });

    // After approval the list re-fetches and the row disappears.
    await waitFor(() =>
      expect(screen.queryByText("summarize-pdfs")).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByText("暂无待处理的技能提案。"),
    ).toBeInTheDocument();
  });

  it("MCP tab triggers 'vet all' and the install button shows only for vetted rows", async () => {
    const user = userEvent.setup();
    const vetCalls: string[] = [];

    global.fetch = routedFetch({
      "GET /api/intel-evolution/mcp/proposals": () => [
        {
          id: 1,
          server_name: "mcp-fs",
          created_at: "2026-04-14T10:00:00Z",
          status: "vetted",
          description: "filesystem access",
          suggested_cmd: "npx @mcp/fs",
        },
        {
          id: 2,
          server_name: "mcp-pending",
          created_at: "2026-04-14T10:00:00Z",
          status: "pending_vet",
        },
      ],
      "POST /api/intel-evolution/mcp/proposals/vet": (_u, init) => {
        vetCalls.push(init!.body as string);
        return [];
      },
    }) as unknown as typeof fetch;

    renderPanel();
    await user.click(screen.getByRole("button", { name: "MCP" }));

    expect(await screen.findByText("mcp-fs")).toBeInTheDocument();
    expect(screen.getByText("mcp-pending")).toBeInTheDocument();
    expect(screen.getByText("filesystem access")).toBeInTheDocument();
    expect(screen.getByText("npx @mcp/fs")).toBeInTheDocument();

    // Only the 'vetted' row exposes the install button.
    expect(
      screen.getAllByRole("button", { name: "安装（已禁用）" }),
    ).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "审核全部待定" }));
    await waitFor(() => expect(vetCalls.length).toBe(1));
  });

  it("A/B Dispatch tab renders bucket stats for each live test", async () => {
    global.fetch = routedFetch({
      "GET /api/evolution/dispatch/snapshot": () => ({
        "test-abcdef1234567890": {
          skill_name: "summarizer",
          a_assigned: 42,
          a_reported: 40,
          b_assigned: 38,
          b_reported: 37,
          outcomes: { success: 60, failure: 17 },
        },
      }),
    }) as unknown as typeof fetch;

    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "A/B 分发" }));

    expect(await screen.findByText("真实流量 A/B 分发")).toBeInTheDocument();
    expect(await screen.findByText("summarizer")).toBeInTheDocument();
    expect(screen.getByText(/测试 test-abc…/)).toBeInTheDocument();
    expect(screen.getByText("A: 42 (40)")).toBeInTheDocument();
    expect(screen.getByText("B: 38 (37)")).toBeInTheDocument();
    expect(screen.getByText(/success: 60/)).toBeInTheDocument();
    expect(screen.getByText(/failure: 17/)).toBeInTheDocument();
  });

  it("Drift tab fires scan + sweep and ACKs a specific event", async () => {
    const user = userEvent.setup();
    const scanCalls: number[] = [];
    const sweepCalls: number[] = [];
    const ackCalls: string[] = [];

    global.fetch = routedFetch({
      "GET /api/intel-evolution/protocols/drift?acknowledged=false": () => [
        {
          id: 7,
          protocol_id: "proto.x",
          detected_at: "2026-04-15T00:00:00Z",
          old_hash: "aaa",
          new_hash: "bbb",
          summary: "schema drift detected",
          acknowledged: false,
        },
      ],
      "GET /api/intel-evolution/protocols/repair/proposals?status=pending":
        () => [
          {
            id: 9,
            drift_event_id: 11,
            protocol_id: "learned_rule:read_before_write_guard",
            created_at: "2026-04-15T00:00:00Z",
            suggested_diff: "Insert read_file before edit_file.",
            rationale: "planner learned the rule but needs a hard guard",
            status: "pending",
            source: "learned_rules",
            repair_tasks: [
              {
                id: 12,
                proposal_id: 9,
                protocol_id: "learned_rule:read_before_write_guard",
                priority: "high",
                title: "Harden read-before-write planning guard for edit_file",
                target_layer: "planner/tool preflight",
                target_modules: [
                  "runtime/execution/tool_engine/executor.py",
                  "runtime/core/cerebrum/react_execution.py",
                ],
                verification_commands: [
                  ".venv/bin/python -m pytest tests/test_write_skills.py -q",
                ],
              },
            ],
          },
        ],
      "POST /api/intel-evolution/protocols/drift/scan": (_u) => {
        scanCalls.push(1);
        return { ok: true };
      },
      "POST /api/intel-evolution/protocols/repair/sweep": (_u) => {
        sweepCalls.push(1);
        return { ok: true };
      },
      "POST /api/intel-evolution/protocols/drift/7/acknowledge": (u) => {
        ackCalls.push(u);
        return { ok: true };
      },
    }) as unknown as typeof fetch;

    renderPanel();
    await user.click(screen.getByRole("button", { name: "漂移" }));

    expect(await screen.findByText("proto.x")).toBeInTheDocument();
    expect(screen.getByText("schema drift detected")).toBeInTheDocument();
    expect(
      screen.getByText("learned_rule:read_before_write_guard"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Harden read-before-write planning guard for edit_file"),
    ).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(
      screen.getByText(/runtime\/execution\/tool_engine\/executor.py/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "立即扫描" }));
    await user.click(screen.getByRole("button", { name: "生成修复" }));

    // Find the ACK-equivalent button within the drift event row.
    const eventRow = screen.getByText("proto.x").closest("div")!.parentElement!
      .parentElement!;
    const ackBtn = within(eventRow).getByRole("button", { name: "确认" });
    await user.click(ackBtn);

    await waitFor(() => {
      expect(scanCalls.length).toBe(1);
      expect(sweepCalls.length).toBe(1);
      expect(ackCalls[0]).toBe(
        "/api/intel-evolution/protocols/drift/7/acknowledge",
      );
    });
  });

  it("propagates auth headers on every GET", async () => {
    const headersSeen: HeadersInit[] = [];
    global.fetch = vi.fn((_url, init?: RequestInit) => {
      if (init?.headers) headersSeen.push(init.headers);
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
      });
    }) as unknown as typeof fetch;

    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "框架" }));

    await waitFor(() => expect(headersSeen.length).toBeGreaterThan(0));
    for (const h of headersSeen) {
      expect((h as Record<string, string>).Authorization).toBe(
        "Bearer test-token",
      );
    }
  });
});
