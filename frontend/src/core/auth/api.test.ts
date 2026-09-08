import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "https://backend.example.test",
}));

import {
  BackendStartingError,
  getAuthStatus,
  isBackendStartingError,
} from "./api";

describe("getAuthStatus", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("recognizes the appliance cold-start response and retry delay", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "appliance_starting",
              message: "Echo OS 正在启动，请稍后重试",
            },
          }),
          {
            status: 503,
            headers: {
              "Content-Type": "application/json",
              "Retry-After": "3",
            },
          },
        ),
      ),
    );

    const error = await getAuthStatus().catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(BackendStartingError);
    expect(isBackendStartingError(error)).toBe(true);
    expect(error).toMatchObject({
      code: "appliance_starting",
      retryAfterMs: 3_000,
      message: "Echo OS 正在启动，请稍后重试",
    });
  });

  it("clamps an excessive startup retry delay", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: { code: "appliance_starting" } }),
            { status: 503, headers: { "Retry-After": "900" } },
          ),
        ),
    );

    await expect(getAuthStatus()).rejects.toMatchObject({
      code: "appliance_starting",
      retryAfterMs: 10_000,
    });
  });

  it("does not disguise an unrelated backend 503 as appliance startup", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "database unavailable" }), {
          status: 503,
          statusText: "Service Unavailable",
        }),
      ),
    );

    const error = await getAuthStatus().catch((reason: unknown) => reason);

    expect(isBackendStartingError(error)).toBe(false);
    expect(error).toEqual(
      new Error("Failed to get auth status: Service Unavailable"),
    );
  });
});
