import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ToolEffectsPanel } from "./page";

const snapshot = {
  backend: "redis",
  shared_across_hosts: true,
  can_authorize_retry: true,
  count: 1,
  state_counts: { indeterminate: 1 },
  receipts: [
    {
      effect_key: "effect:payment",
      task_id: "task-1234567890",
      step_id: 2,
      sucker_id: "payment_tool",
      side_effecting: true,
      state: "indeterminate",
      holder_id: "host-a",
      fencing_token: 7,
      lease_expires_at: 0,
      call_id: "call-a",
      reason: "provider outcome unknown",
      updated_at: 1,
      has_result: false,
    },
  ],
};

describe("ToolEffectsPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows indeterminate receipts and submits a fenced retry authorization", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (_url, init) => {
        if (init?.method === "POST") {
          return new Response(JSON.stringify({ ok: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify(snapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      });

    renderWithProviders(<ToolEffectsPanel />, { locale: "zh-CN" });

    expect(await screen.findByText("payment_tool")).toBeInTheDocument();
    expect(screen.getByText("1 项待核对")).toBeInTheDocument();
    const read = fetchMock.mock.calls.find(([, init]) => !init?.method);
    expect(String(read?.[0])).toContain(
      "/api/tool-effects?limit=100&cross_tenant=true",
    );
    fireEvent.click(screen.getByRole("button", { name: "核对后重试" }));
    fireEvent.change(
      screen.getByPlaceholderText(
        "填写核对依据，例如：支付平台确认没有生成订单。",
      ),
      { target: { value: "支付平台确认没有生成任何订单" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "确认未发生并允许重试" }),
    );

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([, init]) => init?.method === "POST",
      );
      expect(post).toBeDefined();
      expect(String(post?.[0])).toContain(
        "/api/tool-effects/effect%3Apayment/authorize-retry?cross_tenant=true",
      );
      expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
        confirm: "AUTHORIZE RETRY",
        fencing_token: 7,
      });
    });
  });

  it.each([
    ["zh-CN", "需要跨租户管理员权限。"],
    ["en-US", "Cross-tenant administrator permission is required."],
  ] as const)(
    "shows the localized admin gate for %s and stops heartbeat retries after a 403",
    async (locale, expectedMessage) => {
      let heartbeat: TimerHandler | undefined;
      vi.spyOn(window, "setInterval").mockImplementation((handler) => {
        heartbeat = handler;
        return 1;
      });
      const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ detail: "forbidden" }), {
          status: 403,
          headers: { "Content-Type": "application/json" },
        }),
      );

      renderWithProviders(<ToolEffectsPanel />, { locale });

      expect(await screen.findByText(expectedMessage)).toBeInTheDocument();
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
        "/api/tool-effects?limit=100&cross_tenant=true",
      );

      await act(async () => {
        if (typeof heartbeat === "function") heartbeat();
        await Promise.resolve();
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("collapses old committed receipts without hiding later risk receipts", async () => {
    const receipts = Array.from({ length: 9 }, (_, index) => ({
      ...snapshot.receipts[0],
      effect_key: `effect:committed:${index}`,
      sucker_id: `committed_tool_${index}`,
      state: "committed",
    }));
    receipts.push({
      ...snapshot.receipts[0],
      effect_key: "effect:late-risk",
      sucker_id: "late_risk_tool",
      state: "indeterminate",
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          ...snapshot,
          count: receipts.length,
          receipts,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    renderWithProviders(<ToolEffectsPanel />, { locale: "zh-CN" });

    expect(await screen.findByText("late_risk_tool")).toBeInTheDocument();
    expect(screen.getByText("committed_tool_5")).toBeInTheDocument();
    expect(screen.queryByText("committed_tool_6")).not.toBeInTheDocument();
    expect(
      screen.getByText("已收起历史提交记录，仅保留需关注项与最近 6 条"),
    ).toBeInTheDocument();
  });
});
