import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyMdRaidCheck,
  applyMdRaidCheckSchedule,
  fetchMdRaidCheckSchedule,
  fetchMdRaidMaintenance,
  planMdRaidCheck,
  planMdRaidCheckSchedule,
} from "./mdraid-maintenance";

vi.mock("./auth", () => ({
  authHeader: () => ({ Authorization: "Bearer session" }),
}));

describe("md RAID1 maintenance API", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("keeps status, preview, and approved apply endpoints separate", async () => {
    const desired = {
      schema: "echo.omv.mdraid-check-desired.v1" as const,
      name: "family",
      arrayUuid: "11111111:22222222:33333333:44444444",
      operation: "start" as const,
    };
    const scheduleDesired = {
      schema: "echo.mdraid-check-schedule-desired.v1" as const,
      enabled: true,
    };
    const responses = [
      { arrays: [] },
      { enabled: false },
      { planId: "a".repeat(64) },
      { planId: "a".repeat(64), verified: true },
      { planId: "b".repeat(64) },
      { planId: "b".repeat(64), verified: true },
    ];
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(
        async () =>
          new Response(JSON.stringify(responses.shift()), { status: 200 }),
      );

    await fetchMdRaidMaintenance();
    await fetchMdRaidCheckSchedule();
    const checkPlan = await planMdRaidCheck(desired);
    await applyMdRaidCheck(desired, checkPlan.planId, "check-approval");
    const schedulePlan = await planMdRaidCheckSchedule(scheduleDesired);
    await applyMdRaidCheckSchedule(
      scheduleDesired,
      schedulePlan.planId,
      "schedule-approval",
    );

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/arrays/mdraid1/maintenance",
      "/api/appliance/omv/arrays/mdraid1/check/schedule",
      "/api/appliance/omv/arrays/mdraid1/check/plan",
      "/api/appliance/omv/arrays/mdraid1/check/apply",
      "/api/appliance/omv/arrays/mdraid1/check/schedule/plan",
      "/api/appliance/omv/arrays/mdraid1/check/schedule/apply",
    ]);
    expect((fetchMock.mock.calls[3]?.[1] as RequestInit).headers).toMatchObject(
      {
        Authorization: "Bearer session",
        "X-Echo-Approval": "check-approval",
      },
    );
    expect((fetchMock.mock.calls[5]?.[1] as RequestInit).headers).toMatchObject(
      {
        "X-Echo-Approval": "schedule-approval",
      },
    );
  });
});
