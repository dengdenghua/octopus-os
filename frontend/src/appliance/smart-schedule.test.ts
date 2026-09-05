import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applySmartSchedule,
  fetchSmartSchedule,
  planSmartSchedule,
} from "./smart-schedule";

beforeEach(() => vi.restoreAllMocks());

describe("SMART schedule API", () => {
  it("keeps read, preview and approved apply on separate fixed routes", async () => {
    const desired = {
      schema: "echo.smart-self-test-schedule-desired.v1" as const,
      enabled: true,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            schema: desired.schema,
            planId: "e".repeat(64),
            operation: "enable",
            requiresApproval: true,
          }),
          { status: 200 },
        ),
    );

    await fetchSmartSchedule();
    await planSmartSchedule(desired);
    await applySmartSchedule(desired, "e".repeat(64), "approval-once");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/smart/self-test/schedule",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/smart/self-test/schedule/plan",
    );
    expect(fetchMock.mock.calls[2]).toEqual([
      "/api/appliance/omv/smart/self-test/schedule/apply",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-Echo-Approval": "approval-once",
        }),
      }),
    ]);
    expect(
      JSON.parse(String((fetchMock.mock.calls[2]?.[1] as RequestInit).body)),
    ).toEqual({ desired, planId: "e".repeat(64) });
  });
});
