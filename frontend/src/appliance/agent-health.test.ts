import { beforeEach, describe, expect, it, vi } from "vitest";

import { probeAgentDesktopHealth } from "./agent-health";

beforeEach(() => {
  vi.useRealTimers();
});

function healthResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Agent desktop health probe", () => {
  it("reports a healthy local Runtime as ready", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      healthResponse({
        status: "ok",
        runtime: {
          version: "0.2.0",
          sourceId: "a".repeat(40),
          verifiedBundle: true,
        },
        lifecycle: {
          restartRequired: false,
          traceStore: { ready: true },
        },
      }),
    );

    await expect(probeAgentDesktopHealth(fetcher, "/api")).resolves.toEqual({
      state: "ready",
      version: "0.2.0",
      sourceId: "a".repeat(40),
      verifiedBundle: true,
    });
    expect(fetcher).toHaveBeenCalledWith("/api/health", {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: expect.any(AbortSignal),
    });
  });

  it("surfaces a Runtime update that requires a restart", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      healthResponse({
        status: "ok",
        lifecycle: {
          restartRequired: true,
          traceStore: { ready: true },
        },
      }),
    );

    await expect(probeAgentDesktopHealth(fetcher, "/api/")).resolves.toEqual({
      state: "restart-required",
      version: null,
      sourceId: null,
      verifiedBundle: false,
    });
  });

  it("keeps an older healthy Runtime online but marks its identity unknown", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      healthResponse({
        status: "ok",
        lifecycle: { restartRequired: false, traceStore: { ready: true } },
      }),
    );

    await expect(probeAgentDesktopHealth(fetcher, "/api")).resolves.toEqual({
      state: "ready",
      version: null,
      sourceId: null,
      verifiedBundle: false,
    });
  });

  it("rejects a forged verified flag without a canonical source revision", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      healthResponse({
        status: "ok",
        runtime: {
          version: "0.2.0",
          sourceId: "development-tree",
          verifiedBundle: true,
        },
      }),
    );

    await expect(probeAgentDesktopHealth(fetcher, "/api")).resolves.toEqual({
      state: "unavailable",
      version: null,
      sourceId: null,
      verifiedBundle: false,
    });
  });

  it.each([
    ["non-200 response", vi.fn().mockResolvedValue(healthResponse({}, 503))],
    [
      "unready trace store",
      vi.fn().mockResolvedValue(
        healthResponse({
          status: "ok",
          lifecycle: { traceStore: { ready: false } },
        }),
      ),
    ],
    ["network failure", vi.fn().mockRejectedValue(new Error("offline"))],
  ])("fails closed for %s", async (_name, fetcher) => {
    await expect(probeAgentDesktopHealth(fetcher, "/api")).resolves.toEqual({
      state: "unavailable",
      version: null,
      sourceId: null,
      verifiedBundle: false,
    });
  });
});
