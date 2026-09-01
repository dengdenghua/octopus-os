import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToolEffectsProvider } from "@/core/observability/tool-effects-context";
import { renderWithProviders } from "@/test/harness";

import { ToolEffectDetailPanel } from "./tool-effect-detail-panel";

const snapshot = {
  backend: "redis",
  shared_across_hosts: true,
  can_authorize_retry: true,
  count: 1,
  state_counts: { indeterminate: 1 },
  receipts: [
    {
      effect_key: "effect:payment",
      task_id: "task-123",
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

describe("ToolEffectDetailPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows operator-safe metadata and submits a fenced one-time retry", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (_url, init) => {
        if (init?.method === "POST") {
          return new Response(
            JSON.stringify({
              ok: true,
              effect_key: "effect:payment",
              state: "retry_authorized",
              fencing_token: 7,
              actor: "admin",
              audit_warning: "",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify(snapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      });

    renderWithProviders(
      <ToolEffectsProvider>
        <ToolEffectDetailPanel effectKey="effect:payment" onBack={vi.fn()} />
      </ToolEffectsProvider>,
      { locale: "zh-CN" },
    );

    expect(await screen.findByText("payment_tool")).toBeInTheDocument();
    expect(screen.getByText("跨节点共享")).toBeInTheDocument();
    expect(screen.getByText("provider outcome unknown")).toBeInTheDocument();
    expect(
      screen.queryByText(/arguments|result body/i),
    ).not.toBeInTheDocument();

    fireEvent.change(
      screen.getByPlaceholderText(
        "填写核对依据，例如：支付平台确认没有生成订单。",
      ),
      { target: { value: "支付平台确认没有生成任何订单" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "确认未发生并允许一次重试" }),
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
        reason: "支付平台确认没有生成任何订单",
      });
    });
  });

  it("keeps the receipt visible but hides mutation controls from read-only users", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ ...snapshot, can_authorize_retry: false }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    renderWithProviders(
      <ToolEffectsProvider>
        <ToolEffectDetailPanel effectKey="effect:payment" onBack={vi.fn()} />
      </ToolEffectsProvider>,
      { locale: "zh-CN" },
    );

    expect(await screen.findByText("payment_tool")).toBeInTheDocument();
    expect(
      screen.getByText(
        "当前账号可以查看回执，但只有管理员能放行外部动作重试。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认未发生并允许一次重试" }),
    ).not.toBeInTheDocument();
  });

  it("does not expose a raw internal tool name in the operator panel", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          ...snapshot,
          receipts: [{ ...snapshot.receipts[0], sucker_id: "exec_shell" }],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    renderWithProviders(
      <ToolEffectsProvider>
        <ToolEffectDetailPanel effectKey="effect:payment" onBack={vi.fn()} />
      </ToolEffectsProvider>,
      { locale: "zh-CN" },
    );

    expect(await screen.findByText("外部动作")).toBeInTheDocument();
    expect(screen.queryByText("exec_shell")).not.toBeInTheDocument();
  });
});
