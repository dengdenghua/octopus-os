import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchDeviceSyncStatus, setDeviceSyncScope } from "./device-sync";

describe("device sync API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("reads status and binds one approval to one device scope", async () => {
    const response = {
      schema: "echo.device-sync.v1",
      available: true,
      mode: "echo-managed",
      conflictPolicy: "keep-both",
      roots: {
        photos: "Mobile Uploads/<device>/Photos",
        files: "Mobile Uploads/<device>/Files",
      },
      devices: [],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(
        async () => new Response(JSON.stringify(response), { status: 200 }),
      );

    await fetchDeviceSyncStatus();
    await setDeviceSyncScope("phone/one", "photos", true, "approval-once");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/appliance/sync",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/appliance/sync/devices/phone%2Fone/photos/enable",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Echo-Approval": "approval-once",
        }),
      }),
    );
  });
});
