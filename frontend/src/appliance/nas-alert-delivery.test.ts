import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyNasAlertDelivery,
  planNasAlertDelivery,
  testNasAlertDelivery,
} from "./nas-alert-delivery";

vi.mock("@/appliance/auth", () => ({
  authHeader: () => ({ Authorization: "Bearer current-session" }),
}));

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("headless NAS alert API", () => {
  it("keeps configuration and one-shot approval in separate requests", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ planId: "a".repeat(64) }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ enabled: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ sent: true, sentAt: "2026-09-08T08:00:00Z" }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await planNasAlertDelivery({
      enabled: true,
      url: "https://hooks.example.com/echo?key=secret",
      bearerToken: "bearer-secret",
    });
    await applyNasAlertDelivery("a".repeat(64), "configure-once");
    await testNasAlertDelivery("test-once");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/appliance/notifications/webhook/plan",
      expect.objectContaining({
        body: JSON.stringify({
          enabled: true,
          url: "https://hooks.example.com/echo?key=secret",
          bearerToken: "bearer-secret",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/appliance/notifications/webhook/apply",
      expect.objectContaining({
        body: JSON.stringify({ planId: "a".repeat(64) }),
        headers: expect.objectContaining({
          "X-Echo-Approval": "configure-once",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/appliance/notifications/webhook/test",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Echo-Approval": "test-once" }),
      }),
    );
  });
});
