import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyEchoAccountLink,
  fetchEchoAccounts,
  planEchoAccountLink,
} from "./accounts";

vi.mock("@/appliance/auth", () => ({
  authHeader: () => ({ Authorization: "Bearer session" }),
}));
vi.mock("@/appliance/approval", () => ({
  approvalHeader: (token: string) => ({ "X-Echo-Approval": token }),
}));

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("Echo family account API", () => {
  it("keeps member credentials only in plan/apply request bodies", async () => {
    const plan = {
      schema: "echo.account-link-plan.v1",
      planId: "a".repeat(64),
      operation: "linkExistingOmvMember",
      requiresApproval: true,
      account: {
        username: "alice",
        displayName: "Alice",
        role: "member",
        omvUsername: "alice",
      },
      changes: [],
      safety: {
        omvPasswordReused: false,
        privateDatabaseRead: false,
        passwordReturned: false,
      },
    } as const;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.account-directory.v1",
            accounts: [],
            canManage: true,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ linked: true, account: plan.account }), {
          status: 200,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const desired = {
      omvUsername: "alice",
      displayName: "Alice",
      password: "member-secret-123",
    };

    await fetchEchoAccounts();
    await planEchoAccountLink(desired);
    await applyEchoAccountLink(desired, plan.planId, "approval");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/appliance/accounts");
    expect(fetchMock.mock.calls[1][1].body).toBe(JSON.stringify(desired));
    expect(fetchMock.mock.calls[2][1]).toEqual(
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Echo-Approval": "approval" }),
        body: JSON.stringify({ planId: plan.planId, desired }),
      }),
    );
    expect(JSON.stringify(plan)).not.toContain(desired.password);
  });
});
