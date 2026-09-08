import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyNasEmailAlertDelivery,
  planNasEmailAlertDelivery,
  testNasEmailAlertDelivery,
} from "./nas-email-alert-delivery";

vi.mock("@/appliance/auth", () => ({
  authHeader: () => ({ Authorization: "Bearer current-session" }),
}));

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("headless NAS email alert API", () => {
  it("keeps SMTP secrets and one-shot approvals in separate requests", async () => {
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
          JSON.stringify({ sent: true, sentAt: "2026-09-09T08:00:00Z" }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await planNasEmailAlertDelivery({
      enabled: true,
      smtpHost: "smtp.example.com",
      smtpPort: 465,
      username: "echo@example.com",
      password: "private-app-password",
      fromAddress: "echo@example.com",
      recipient: "owner@example.net",
    });
    await applyNasEmailAlertDelivery("a".repeat(64), "configure-once");
    await testNasEmailAlertDelivery("test-once");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/appliance/notifications/email/plan",
      expect.objectContaining({
        body: JSON.stringify({
          enabled: true,
          smtpHost: "smtp.example.com",
          smtpPort: 465,
          username: "echo@example.com",
          password: "private-app-password",
          fromAddress: "echo@example.com",
          recipient: "owner@example.net",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/appliance/notifications/email/apply",
      expect.objectContaining({
        body: JSON.stringify({ planId: "a".repeat(64) }),
        headers: expect.objectContaining({
          "X-Echo-Approval": "configure-once",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/appliance/notifications/email/test",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Echo-Approval": "test-once" }),
      }),
    );
  });
});
