import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createPairingInvitation,
  disableDeviceLink,
  enableDeviceLink,
  fetchDeviceLinkStatus,
  revokeLinkedDevice,
} from "@/appliance/device-link";

describe("device link API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("reads status and sends approval-bound mutations", async () => {
    const status = {
      schema: "echo.device-link.v1",
      enabled: false,
      devices: [],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(status), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...status, enabled: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.device-link.invitation.v1",
            connectString: "echo://join?ws=x&token=y",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...status, enabled: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(status), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    await fetchDeviceLinkStatus();
    await enableDeviceLink("enable-approval");
    await createPairingInvitation("pair-approval");
    await revokeLinkedDevice("phone/one", "revoke-approval");
    await disableDeviceLink("disable-approval");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/appliance/device-link",
      expect.any(Object),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/appliance/device-link/enable",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Echo-Approval": "enable-approval",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/api/appliance/device-link/devices/phone%2Fone",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.objectContaining({
          "X-Echo-Approval": "revoke-approval",
        }),
      }),
    );
  });
});
