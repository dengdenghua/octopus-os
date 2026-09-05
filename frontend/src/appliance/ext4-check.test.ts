import { beforeEach, describe, expect, it, vi } from "vitest";

import { applyExt4Check, fetchExt4Checks, planExt4Check } from "./ext4-check";

vi.mock("./auth", () => ({
  authHeader: () => ({ Authorization: "Bearer session" }),
}));

describe("EXT4 offline check API", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("keeps inventory, preview, and approved apply separate", async () => {
    const desired = {
      schema: "echo.omv.ext4-check-desired.v1" as const,
      filesystemUuid: "11111111-2222-3333-4444-555555555555",
      operation: "check" as const,
    };
    const plan = {
      schema: "echo.omv.ext4-check-plan.v1",
      planId: "a".repeat(64),
      desired,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ filesystems: [] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...plan, verified: true }), {
          status: 200,
        }),
      );

    await fetchExt4Checks();
    expect((await planExt4Check(desired)).planId).toBe(plan.planId);
    expect(
      (await applyExt4Check(desired, plan.planId, "approval-token")).verified,
    ).toBe(true);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/volumes/ext4/checks",
      "/api/appliance/omv/volumes/ext4/check/plan",
      "/api/appliance/omv/volumes/ext4/check/apply",
    ]);
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).headers).toMatchObject(
      {
        Authorization: "Bearer session",
        "X-Echo-Approval": "approval-token",
      },
    );
  });
});
