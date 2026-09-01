import { beforeEach, describe, expect, it, vi } from "vitest";

import { revokeAllSessions, rotateAdminPassword } from "./account-security";

vi.mock("@/appliance/auth", () => ({
  authHeader: () => ({ Authorization: "Bearer current-session" }),
}));

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("account security API", () => {
  it("consumes a one-shot approval when revoking every session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          sessionsRevoked: true,
          sessionNotBefore: 42,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await revokeAllSessions("signed-once");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/sessions/revoke",
      expect.objectContaining({
        method: "POST",
        headers: {
          Authorization: "Bearer current-session",
          "X-Echo-Approval": "signed-once",
        },
      }),
    );
  });

  it("sends only the new password and approval during rotation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          sessionsRevoked: true,
          sessionNotBefore: 43,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await rotateAdminPassword("replacement-device-pass", "rotation-ticket");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/credentials/rotate",
      expect.objectContaining({
        method: "POST",
        headers: {
          Authorization: "Bearer current-session",
          "X-Echo-Approval": "rotation-ticket",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ newPassword: "replacement-device-pass" }),
      }),
    );
  });

  it("turns a stale session into a clear relogin message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "authentication required" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(revokeAllSessions("stale-ticket")).rejects.toThrow(
      "登录已失效，请重新登录",
    );
  });
});
