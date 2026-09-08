import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyNutDeviceConfig,
  fetchNutDeviceConfig,
  planNutDeviceConfig,
} from "./nut-device-config";

beforeEach(() => vi.restoreAllMocks());

describe("local USB UPS API", () => {
  it("separates status, preview and approved apply routes", async () => {
    const desired = {
      schema: "echo.nut-local-ups-desired.v1" as const,
      enabled: true,
      driver: "usbhid-ups" as const,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({ planId: "c".repeat(64) }), {
          status: 200,
        }),
    );

    await fetchNutDeviceConfig();
    await planNutDeviceConfig(desired);
    await applyNutDeviceConfig(desired, "c".repeat(64), "approval-once");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/power/ups/config",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/power/ups/config/plan",
    );
    expect(fetchMock.mock.calls[2]).toEqual([
      "/api/appliance/omv/power/ups/config/apply",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Echo-Approval": "approval-once",
        }),
      }),
    ]);
  });
});
