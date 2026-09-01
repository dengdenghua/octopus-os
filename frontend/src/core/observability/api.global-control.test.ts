import { afterEach, describe, expect, it, vi } from "vitest";

import {
  GLOBAL_CONTROL_PLANE_ACCESS_CODE,
  GlobalControlPlaneAccessError,
  authorizeToolEffectRetry,
  getEvolutionStatus,
  getToolEffectsSnapshot,
  globalControlPlaneUrl,
  type ToolEffectReceipt,
} from "./api";

const receipt: ToolEffectReceipt = {
  effect_key: "effect:payment",
  task_id: "task-1",
  step_id: 1,
  sucker_id: "payment_tool",
  side_effecting: true,
  state: "indeterminate",
  holder_id: "worker-1",
  fencing_token: 7,
  lease_expires_at: 0,
  call_id: "call-1",
  reason: "provider outcome unknown",
  updated_at: 1,
  has_result: false,
};

describe("global observability control-plane URLs", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("adds the explicit cross-tenant opt-in without losing existing query parameters", () => {
    expect(globalControlPlaneUrl("/api/knowledge/stats")).toMatch(
      /\/api\/knowledge\/stats\?cross_tenant=true$/,
    );
    expect(globalControlPlaneUrl("/api/knowledge/graph?limit=100")).toMatch(
      /\/api\/knowledge\/graph\?limit=100&cross_tenant=true$/,
    );
  });

  it("uses the opt-in for global reads and normalizes 403 as a stable admin gate", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "forbidden" }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(getToolEffectsSnapshot()).rejects.toMatchObject({
      name: "GlobalControlPlaneAccessError",
      message: GLOBAL_CONTROL_PLANE_ACCESS_CODE,
      code: GLOBAL_CONTROL_PLANE_ACCESS_CODE,
      status: 403,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/api/tool-effects?limit=100&cross_tenant=true",
    );
  });

  it("uses the opt-in for evolution status and mutations", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ enabled: false }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            effect_key: receipt.effect_key,
            state: "retry_authorized",
            fencing_token: receipt.fencing_token,
            actor: "admin",
            audit_warning: "",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    await expect(getEvolutionStatus()).resolves.toMatchObject({
      enabled: false,
    });
    await expect(
      authorizeToolEffectRetry(receipt, "confirmed no external effect"),
    ).resolves.toMatchObject({ state: "retry_authorized" });

    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/api\/evolution\/status\?cross_tenant=true$/,
    );
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(
      "/api/tool-effects/effect%3Apayment/authorize-retry?cross_tenant=true",
    );
  });

  it("exports a typed access error for query retry policies", () => {
    const error = new GlobalControlPlaneAccessError();
    expect(error).toBeInstanceOf(Error);
    expect(error.status).toBe(403);
  });
});
