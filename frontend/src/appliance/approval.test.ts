import { beforeEach, describe, expect, it, vi } from "vitest";

import { approvalHeader, requestHighRiskApproval } from "./approval";

vi.mock("@/appliance/auth", () => ({
  authHeader: () => ({ Authorization: "Bearer browser-session" }),
}));

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("high-risk approval API", () => {
  it("sends the intent and password once without persisting it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          approvalToken: "one-shot.signature",
          expiresIn: 90,
          action: "app.start",
          target: "a".repeat(12),
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await requestHighRiskApproval(
      "app.start",
      "a".repeat(12),
      "admin-password",
    );

    expect(result.approvalToken).toBe("one-shot.signature");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/approvals",
      expect.objectContaining({
        method: "POST",
        headers: {
          Authorization: "Bearer browser-session",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          action: "app.start",
          target: "a".repeat(12),
          password: "admin-password",
        }),
      }),
    );
  });

  it("turns server lockout into an actionable retry message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "locked" }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "60" },
        }),
      ),
    );

    await expect(
      requestHighRiskApproval("files.trash.empty", "recycle-bin", "wrong"),
    ).rejects.toThrow("60 秒后重试");
  });

  it("does not expose a raw server detail for an incorrect password", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: "administrator password is incorrect",
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(
      requestHighRiskApproval("files.trash.empty", "recycle-bin", "wrong"),
    ).rejects.toThrow("设备管理员密码不正确，操作未执行");
  });

  it("uses the dedicated non-bearer header for the consumed token", () => {
    expect(approvalHeader("signed-once")).toEqual({
      "X-Echo-Approval": "signed-once",
    });
  });
});
