import { beforeEach, describe, expect, it, vi } from "vitest";

import { applyDiskIdle, fetchDiskIdle, planDiskIdle } from "./disk-idle";

vi.mock("./auth", () => ({
  authHeader: () => ({ Authorization: "Bearer session" }),
}));

describe("disk idle API", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("keeps read, preview, and approved apply separate", async () => {
    const desired = {
      schema: "echo.disk-idle-policy-desired.v1" as const,
      idleMinutes: 60 as const,
    };
    const status = {
      schemaVersion: 1,
      enabled: false,
      idleMinutes: 0,
      configured: false,
      serviceInstalled: true,
      eligibleDevices: [],
      eligibleDeviceCount: 0,
      allowedIdleMinutes: [0, 30, 60, 120, 240],
      scope: "stableInternalRotationalAtaSataNonSystemWholeDisksOnly",
      hardwareVerification: "commandAcceptanceOnly",
      source: "localPolicy",
    };
    const plan = {
      schema: desired.schema,
      planId: "a".repeat(64),
      operation: "set",
      devices: [],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(status), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...plan, verified: true }), {
          status: 200,
        }),
      );

    expect((await fetchDiskIdle()).idleMinutes).toBe(0);
    expect((await planDiskIdle(desired)).operation).toBe("set");
    expect(
      (await applyDiskIdle(desired, plan.planId, "approval-token")).verified,
    ).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/disks/idle",
      "/api/appliance/omv/disks/idle/plan",
      "/api/appliance/omv/disks/idle/apply",
    ]);
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).headers).toMatchObject(
      {
        Authorization: "Bearer session",
        "X-Echo-Approval": "approval-token",
      },
    );
  });
});
