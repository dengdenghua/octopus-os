import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyBtrfsSnapshot,
  applyBtrfsSnapshotRestoreCopy,
  applyBtrfsSnapshotSchedule,
  fetchBtrfsSnapshots,
  planBtrfsSnapshot,
  planBtrfsSnapshotRestoreCopy,
  planBtrfsSnapshotSchedule,
} from "./btrfs-snapshots";

const desired = {
  schema: "echo.omv.btrfs-snapshot-desired.v1" as const,
  sharedFolderRef: "11111111-2222-4333-8444-555555555555",
  name: "before_upgrade",
};

describe("Btrfs snapshot API", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("uses the opaque share id and never sends a host path", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          sharedFolderRef: desired.sharedFolderRef,
          snapshots: [],
          limit: 256,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await fetchBtrfsSnapshots(desired.sharedFolderRef);

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/appliance/omv/sharing/${desired.sharedFolderRef}/snapshots`,
      expect.any(Object),
    );
  });

  it("keeps preview and apply separate and binds approval to apply", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ planId: "a".repeat(64) }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await planBtrfsSnapshot(desired);
    await applyBtrfsSnapshot(desired, "a".repeat(64), "approval-token");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/snapshots/plan",
    );
    const applyOptions = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/snapshots/apply",
    );
    expect(applyOptions.headers).toMatchObject({
      "X-Echo-Approval": "approval-token",
    });
    expect(JSON.parse(String(applyOptions.body))).toEqual({
      desired,
      planId: "a".repeat(64),
    });
  });

  it("uses a separate approval-bound endpoint for automatic retention", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ planId: "b".repeat(64) }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const scheduleDesired = {
      schema: "echo.btrfs-snapshot-schedule-desired.v1" as const,
      sharedFolderRef: desired.sharedFolderRef,
      enabled: true,
      keepLatest: 8,
    };
    await planBtrfsSnapshotSchedule(scheduleDesired);
    await applyBtrfsSnapshotSchedule(
      scheduleDesired,
      "b".repeat(64),
      "schedule-approval",
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/snapshots/schedule/plan",
    );
    const applyOptions = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/snapshots/schedule/apply",
    );
    expect(applyOptions.headers).toMatchObject({
      "X-Echo-Approval": "schedule-approval",
    });
  });

  it("binds non-destructive recovery to its own plan and approval", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ planId: "c".repeat(64) }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const restoreDesired = {
      schema: "echo.omv.btrfs-snapshot-restore-copy-desired.v1" as const,
      sharedFolderRef: desired.sharedFolderRef,
      snapshotId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      name: "Photos_recovered",
    };
    await planBtrfsSnapshotRestoreCopy(restoreDesired);
    await applyBtrfsSnapshotRestoreCopy(
      restoreDesired,
      "c".repeat(64),
      "restore-approval",
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/snapshots/restore-copy/plan",
    );
    const applyOptions = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/snapshots/restore-copy/apply",
    );
    expect(applyOptions.headers).toMatchObject({
      "X-Echo-Approval": "restore-approval",
    });
  });
});
