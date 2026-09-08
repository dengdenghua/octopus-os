import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyOmvBtrfsRaid1,
  applyOmvBtrfsReplace,
  applyOmvBtrfsScrub,
  applyOmvBtrfsScrubSchedule,
  applyOmvExt4Volume,
  applyOmvGroup,
  applyOmvMdRaid1,
  applyOmvNfsShare,
  applyOmvNfsShareRemove,
  applyOmvSharedFolder,
  applyOmvSharedFolderDelete,
  applyOmvSharedFolderDetach,
  applyOmvSharedFolderRename,
  applyOmvSharePrivilege,
  applyOmvSmbShare,
  applyOmvTimeMachine,
  applyOmvUser,
  applyOmvUserPassword,
  applyOmvZfsMirror,
  applyOmvZfsMirrorReplace,
  applyOmvZfsPoolExport,
  applyOmvZfsPoolImport,
  applyOmvZfsScrub,
  fetchOmvFilesystems,
  fetchOmvBtrfsMaintenance,
  fetchOmvBtrfsScrubSchedule,
  fetchOmvBtrfsRaid1Candidates,
  fetchOmvBtrfsReplacementCandidates,
  fetchOmvExt4VolumeCandidates,
  fetchOmvHealth,
  fetchOmvMdRaid1Candidates,
  fetchOmvSharePrivileges,
  fetchOmvSharingOverview,
  fetchOmvSmart,
  fetchOmvSmartDevices,
  fetchOmvStorageTopology,
  fetchOmvStatus,
  fetchOmvTimeMachineStatus,
  fetchOmvUpsStatus,
  fetchOmvZfsMirrorCandidates,
  fetchOmvZfsMirrorReplacementCandidates,
  fetchOmvZfsImportCandidates,
  fetchOmvZfsPools,
  fetchOmvZfsMaintenance,
  planOmvNfsShare,
  planOmvBtrfsRaid1,
  planOmvBtrfsReplace,
  planOmvBtrfsScrub,
  planOmvBtrfsScrubSchedule,
  planOmvExt4Volume,
  planOmvMdRaid1,
  planOmvNfsShareRemove,
  planOmvGroup,
  planOmvSharedFolder,
  planOmvSharedFolderDelete,
  planOmvSharedFolderDetach,
  planOmvSharedFolderRename,
  planOmvSharePrivilege,
  planOmvSmbShare,
  planOmvTimeMachine,
  planOmvUser,
  planOmvUserPassword,
  planOmvZfsMirror,
  planOmvZfsMirrorReplace,
  planOmvZfsPoolExport,
  planOmvZfsPoolImport,
  planOmvZfsScrub,
} from "./omv";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("OMV read-only API client", () => {
  it("does not expose a raw route error when OMV integration is absent", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }),
    );

    await expect(fetchOmvStatus()).rejects.toThrow("无法读取 OMV 接入状态");
  });

  it("reads status, filesystems and an encoded SMART device path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ configured: true, available: true, readOnly: true }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            state: "healthy",
            activeAlerts: [],
            readOnly: true,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ filesystems: [{ devicefile: "/dev/sda1" }] }),
          {
            status: 200,
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            devices: [{ devicefile: "/dev/sda", health: "GOOD" }],
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            devices: [{ devicefile: "/dev/md0", type: "raid1" }],
            arrays: [{ devicefile: "/dev/md0", status: "healthy" }],
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sharedFolders: [{ uuid: "share-1", name: "Family" }],
            sharedFolderTargets: [],
            users: [{ name: "alice" }],
            groups: [],
            smb: { enabled: true, shares: [] },
            nfs: { enabled: false, shares: [] },
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            privileges: [{ name: "alice", permission: "readWrite" }],
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ smart: { devicefile: "/dev/disk/by-id/a" } }),
          {
            status: 200,
          },
        ),
      );

    expect(await fetchOmvStatus()).toMatchObject({ available: true });
    expect(await fetchOmvHealth()).toMatchObject({ state: "healthy" });
    expect(await fetchOmvFilesystems()).toHaveLength(1);
    expect(await fetchOmvSmartDevices()).toHaveLength(1);
    expect((await fetchOmvStorageTopology()).arrays).toHaveLength(1);
    expect((await fetchOmvSharingOverview()).users).toHaveLength(1);
    expect(
      (await fetchOmvSharePrivileges("11111111-2222-4333-8444-555555555555"))[0]
        ?.permission,
    ).toBe("readWrite");
    expect((await fetchOmvSmart("/dev/disk/by-id/a")).devicefile).toBe(
      "/dev/disk/by-id/a",
    );
    expect(fetchMock.mock.calls[7]?.[0]).toBe(
      "/api/appliance/omv/smart?devicefile=%2Fdev%2Fdisk%2Fby-id%2Fa",
    );
    expect(
      fetchMock.mock.calls.every(
        (call) => (call[1] as RequestInit).method == null,
      ),
    ).toBe(true);
  });

  it("reads the bounded local NUT UPS snapshot", async () => {
    const snapshot = {
      schemaVersion: 1,
      source: "nut",
      readOnly: true,
      configured: true,
      available: true,
      state: "ready",
      code: null,
      devices: [
        {
          name: "family-ups",
          available: true,
          state: "online",
          statusFlags: ["OL"],
          chargePercent: 100,
          runtimeSeconds: 3600,
          loadPercent: 25,
          inputVoltage: 230,
          outputVoltage: 230,
          batteryVoltage: 24,
          temperatureC: 30,
          manufacturer: "APC",
          model: "Back-UPS",
        },
      ],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(snapshot), { status: 200 }),
      );

    expect(await fetchOmvUpsStatus()).toEqual(snapshot);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/omv/power/ups",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });

  it("keeps group and family-user preview separate from approved apply", async () => {
    const groupDesired = {
      schema: "echo.omv.group-desired.v1" as const,
      name: "family",
      comment: "Family members",
    };
    const groupPlan = {
      schema: "echo.omv.group-plan.v1",
      planId: "5".repeat(64),
      baseRevision: "3".repeat(64),
      operation: "create",
      requiresApproval: true,
      desired: groupDesired,
      changes: [],
      safety: {},
    };
    const userDesired = {
      schema: "echo.omv.user-desired.v1" as const,
      name: "mother",
      displayName: "Mother",
      password: "Echo-Family-2026!",
      groups: ["family"],
    };
    const userPlan = {
      schema: "echo.omv.user-plan.v1",
      planId: "4".repeat(64),
      baseRevision: "2".repeat(64),
      operation: "create",
      requiresApproval: true,
      desired: {
        schema: userDesired.schema,
        name: userDesired.name,
        displayName: userDesired.displayName,
        groups: userDesired.groups,
        passwordBound: true,
      },
      changes: [],
      safety: {},
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(groupPlan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...groupPlan, applied: true, verified: true }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(userPlan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...userPlan, applied: true, verified: true }),
          { status: 200 },
        ),
      );

    expect((await planOmvGroup(groupDesired)).planId).toBe(groupPlan.planId);
    await applyOmvGroup(groupDesired, groupPlan.planId, "group-token");
    const safePlan = await planOmvUser(userDesired);
    await applyOmvUser(userDesired, userPlan.planId, "user-token");

    expect(JSON.stringify(safePlan)).not.toContain(userDesired.password);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/accounts/groups/plan",
      "/api/appliance/omv/accounts/groups/apply",
      "/api/appliance/omv/accounts/users/plan",
      "/api/appliance/omv/accounts/users/apply",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual(
      userDesired,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[3]?.[1]?.body))).toEqual({
      desired: userDesired,
      planId: userPlan.planId,
    });
    expect(
      (fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>)[
        "X-Echo-Approval"
      ],
    ).toBe("group-token");
    expect(
      (fetchMock.mock.calls[3]?.[1]?.headers as Record<string, string>)[
        "X-Echo-Approval"
      ],
    ).toBe("user-token");
  });

  it("keeps a replacement member password inside preview and approved apply requests", async () => {
    const desired = {
      schema: "echo.omv.user-password-desired.v1" as const,
      name: "mother",
      password: "Replacement-Family-2026!",
    };
    const plan = {
      schema: "echo.omv.user-password-plan.v1" as const,
      planId: "6".repeat(64),
      baseRevision: "7".repeat(64),
      operation: "resetPassword" as const,
      requiresApproval: true as const,
      desired: {
        schema: desired.schema,
        name: desired.name,
        passwordBound: true as const,
      },
      changes: [
        {
          field: "password" as const,
          before: "currentCredential" as const,
          after: "replacementCredential" as const,
        },
      ],
      safety: {
        scope: "existingConstrainedNormalOmvUser" as const,
        password: "hmacBoundNeverReturnedOrAudited" as const,
        accountFields: "preservedAndVerified" as const,
        loginShell: "nologin" as const,
        sshKeys: "none" as const,
        rollback: "notAvailableAfterAcceptedSecretRpc" as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          { status: 200 },
        ),
      );

    const preview = await planOmvUserPassword(desired);
    await applyOmvUserPassword(desired, plan.planId, "password-reset-token");

    expect(JSON.stringify(preview)).not.toContain(desired.password);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/accounts/users/password/plan",
      "/api/appliance/omv/accounts/users/password/apply",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      desired,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect(
      (fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>)[
        "X-Echo-Approval"
      ],
    ).toBe("password-reset-token");
  });

  it("keeps shared-folder preview and approved create as separate requests", async () => {
    const desired = {
      schema: "echo.omv.shared-folder-desired.v1" as const,
      mountPointRef: "11111111-2222-4333-8444-555555555555",
      name: "Family_Photos",
      comment: "Family photos",
    };
    const plan = {
      schema: "echo.omv.shared-folder-plan.v1" as const,
      planId: "1".repeat(64),
      baseRevision: "2".repeat(64),
      operation: "create" as const,
      requiresApproval: true,
      shareUuid: "22222222-3333-4444-8555-666666666666",
      target: {
        mountPointRef: desired.mountPointRef,
        filesystemUuid: "33333333-4444-4555-8666-777777777777",
        label: "Data",
        type: "ext4",
        sizeBytes: 2 * 1024 ** 4,
        availableBytes: 1024 ** 4,
        readOnly: false as const,
      },
      desired,
      changes: [
        { field: "name" as const, before: null, after: desired.name },
        { field: "comment" as const, before: null, after: desired.comment },
      ],
      safety: {
        filesystem: "existingMountedWritableOnly" as const,
        relativePath: "derivedFromPortableName" as const,
        directoryMode: "2770UsersGroup" as const,
        acl: "notManaged" as const,
        update: "notManaged" as const,
        delete: "notManaged" as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          { status: 200 },
        ),
      );

    expect((await planOmvSharedFolder(desired)).planId).toBe(plan.planId);
    expect(
      (
        await applyOmvSharedFolder(
          desired,
          plan.planId,
          "one-shot-folder-token",
        )
      ).verified,
    ).toBe(true);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/folders/plan",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/folders/apply",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      desired,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect(
      (fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>)[
        "X-Echo-Approval"
      ],
    ).toBe("one-shot-folder-token");
  });

  it("keeps shared-folder rename data-preserving and approval-bound", async () => {
    const desired = {
      schema: "echo.omv.shared-folder-rename-desired.v1" as const,
      sharedFolderRef: "11111111-2222-4333-8444-555555555555",
      name: "Family_Archive",
    };
    const plan = {
      schema: "echo.omv.shared-folder-rename-plan.v1" as const,
      planId: "8".repeat(64),
      baseRevision: "9".repeat(64),
      operation: "rename" as const,
      requiresApproval: true,
      shareUuid: desired.sharedFolderRef,
      sharedFolder: {
        uuid: desired.sharedFolderRef,
        name: "Family_Photos",
        comment: "Family photos",
        relativePath: "Family_Photos",
        device: "/dev/sdb1",
        status: "MOUNTED",
        inUse: true,
        supportsAcl: true,
      },
      desired,
      changes: [
        {
          field: "name" as const,
          before: "Family_Photos",
          after: desired.name,
        },
      ],
      safety: {
        filesystem: "sameMountedWritableVolume" as const,
        data: "preserved" as const,
        identity: "uuidPreserved" as const,
        acl: "preservedWithDirectory" as const,
        dependentShares: "mustBeAbsent" as const,
        rollback: "directoryAndRegistry" as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...plan,
            applied: true,
            verified: true,
            dataPreserved: true,
          }),
          { status: 200 },
        ),
      );

    expect((await planOmvSharedFolderRename(desired)).planId).toBe(plan.planId);
    expect(
      (
        await applyOmvSharedFolderRename(
          desired,
          plan.planId,
          "one-shot-rename-token",
        )
      ).dataPreserved,
    ).toBe(true);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/folders/rename/plan",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/folders/rename/apply",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      desired,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect(
      (fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>)[
        "X-Echo-Approval"
      ],
    ).toBe("one-shot-rename-token");
  });

  it("keeps data-preserving shared-folder detach preview and apply separate", async () => {
    const desired = {
      schema: "echo.omv.shared-folder-detach-desired.v1" as const,
      sharedFolderRef: "11111111-2222-4333-8444-555555555555",
      preserveData: true as const,
    };
    const plan = {
      schema: "echo.omv.shared-folder-detach-plan.v1" as const,
      planId: "4".repeat(64),
      baseRevision: "5".repeat(64),
      operation: "remove" as const,
      requiresApproval: true as const,
      shareUuid: desired.sharedFolderRef,
      sharedFolder: {
        uuid: desired.sharedFolderRef,
        name: "Family_Photos",
        comment: "Family photos",
        relativePath: "Family_Photos",
        device: "/dev/sdb1",
        status: "MOUNTED",
        inUse: true,
        supportsAcl: true,
      },
      desired,
      changes: [
        {
          field: "registration" as const,
          before: "managed" as const,
          after: "detached" as const,
        },
      ],
      safety: {
        data: "preserved" as const,
        directory: "neverDeleted" as const,
        dependentShares: "mustBeAbsent" as const,
        acl: "untouched" as const,
        rollback: "registryOnly" as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...plan,
            applied: true,
            verified: true,
            dataPreserved: true,
          }),
          { status: 200 },
        ),
      );

    expect((await planOmvSharedFolderDetach(desired)).planId).toBe(plan.planId);
    expect(
      (
        await applyOmvSharedFolderDetach(
          desired,
          plan.planId,
          "one-shot-detach-token",
        )
      ).dataPreserved,
    ).toBe(true);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/folders/detach/plan",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/folders/detach/apply",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      desired,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect(
      (fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>)[
        "X-Echo-Approval"
      ],
    ).toBe("one-shot-detach-token");
  });

  it("keeps empty-folder deletion behind preview and one-shot approval", async () => {
    const desired = {
      schema: "echo.omv.shared-folder-delete-desired.v1" as const,
      sharedFolderRef: "11111111-2222-4333-8444-555555555555",
      emptyOnly: true as const,
    };
    const plan = {
      schema: "echo.omv.shared-folder-delete-plan.v1" as const,
      planId: "6".repeat(64),
      baseRevision: "7".repeat(64),
      operation: "remove" as const,
      requiresApproval: true as const,
      shareUuid: desired.sharedFolderRef,
      sharedFolder: {
        uuid: desired.sharedFolderRef,
        name: "Empty_Drop",
        comment: "Temporary share",
        relativePath: "Empty_Drop",
        device: "/dev/sdb1",
        status: "MOUNTED",
        inUse: true,
        supportsAcl: true,
      },
      desired,
      changes: [
        {
          field: "directory" as const,
          before: "empty" as const,
          after: "deleted" as const,
        },
        {
          field: "registration" as const,
          before: "managed" as const,
          after: "removed" as const,
        },
      ],
      safety: {
        data: "emptyDirectoryOnly" as const,
        directory: "deleted" as const,
        dependentShares: "mustBeAbsent" as const,
        recursive: "never" as const,
        mount: "mountedWritableOnly" as const,
        rollback: "registryAndEmptyDirectory" as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...plan,
            applied: true,
            verified: true,
            directoryDeleted: true,
            dataDeleted: false,
          }),
          { status: 200 },
        ),
      );

    expect((await planOmvSharedFolderDelete(desired)).planId).toBe(plan.planId);
    expect(
      (
        await applyOmvSharedFolderDelete(
          desired,
          plan.planId,
          "one-shot-delete-token",
        )
      ).directoryDeleted,
    ).toBe(true);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/sharing/folders/delete/plan",
      "/api/appliance/omv/sharing/folders/delete/apply",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      desired,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect(
      (fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>)[
        "X-Echo-Approval"
      ],
    ).toBe("one-shot-delete-token");
  });

  it("keeps share privilege preview and approved apply as separate requests", async () => {
    const desired = {
      schema: "echo.omv.share-privilege-desired.v1" as const,
      sharedFolderRef: "11111111-2222-4333-8444-555555555555",
      principalType: "user" as const,
      principalName: "alice",
      permission: "readWrite" as const,
    };
    const plan = {
      schema: "echo.omv.share-privilege-plan.v1" as const,
      planId: "7".repeat(64),
      baseRevision: "6".repeat(64),
      operation: "update" as const,
      requiresApproval: true,
      sharedFolder: {
        uuid: desired.sharedFolderRef,
        name: "Family",
        status: "OK",
      },
      principal: {
        type: desired.principalType,
        id: 1000,
        name: desired.principalName,
        before: "inherit" as const,
        after: desired.permission,
      },
      desired,
      changes: [
        {
          field: "permission" as const,
          before: "inherit" as const,
          after: desired.permission,
        },
      ],
      safety: {
        scope: "sharedFolderConfigPrivilege" as const,
        principal: "existingOmvUserOrGroup" as const,
        filesystemAcl: "notModified" as const,
        recursive: "never" as const,
        serviceDeploy: "sambaAndRsyncdWhenDirty" as const,
        delete: "notManaged" as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...plan,
            applied: true,
            verified: true,
            deployedServices: ["samba"],
          }),
          { status: 200 },
        ),
      );

    expect((await planOmvSharePrivilege(desired)).principal.before).toBe(
      "inherit",
    );
    expect(
      (
        await applyOmvSharePrivilege(
          desired,
          plan.planId,
          "one-shot-privilege-token",
        )
      ).verified,
    ).toBe(true);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/privileges/plan",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/privileges/apply",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      desired,
    );
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect(
      (fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>)[
        "X-Echo-Approval"
      ],
    ).toBe("one-shot-privilege-token");
  });

  it("keeps SMB preview and approved apply as separate requests", async () => {
    const desired = {
      schema: "echo.omv.smb-share-desired.v1" as const,
      sharedFolderRef: "11111111-2222-4333-8444-555555555555",
      enabled: true,
      readOnly: true,
      browseable: true,
      recycleBin: true,
      comment: "Family share",
    };
    const plan = {
      schema: "echo.omv.smb-share-plan.v1",
      planId: "a".repeat(64),
      baseRevision: "b".repeat(64),
      operation: "create",
      requiresApproval: true,
      shareUuid: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      sharedFolder: {
        uuid: desired.sharedFolderRef,
        name: "Family",
        status: "OK",
      },
      desired,
      changes: [],
      safety: { guestAccess: "disabled" },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          { status: 200 },
        ),
      );

    expect((await planOmvSmbShare(desired)).planId).toBe(plan.planId);
    expect(
      (await applyOmvSmbShare(desired, plan.planId, "one-shot-token")).verified,
    ).toBe(true);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/smb/plan",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/smb/apply",
    );
    const preview = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const apply = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(preview.method).toBe("POST");
    expect(JSON.parse(String(preview.body))).toEqual(desired);
    expect(JSON.parse(String(apply.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect((apply.headers as Record<string, string>)["X-Echo-Approval"]).toBe(
      "one-shot-token",
    );
  });

  it("keeps Time Machine status, preview, and approved apply on bounded routes", async () => {
    const desired = {
      schema: "echo.storage.time-machine-desired.v1" as const,
      sharedFolderRef: "11111111-2222-4333-8444-555555555555",
      enabled: true,
      owner: "alice",
      maximumBytes: 256 * 1024 ** 3,
    };
    const status = {
      schema: "echo.storage.time-machine-status.v1" as const,
      enabled: false,
      available: true,
      shares: [],
      source: "native" as const,
      readOnly: true as const,
    };
    const plan = {
      schema: "echo.storage.time-machine-plan.v1" as const,
      planId: "c".repeat(64),
      baseRevision: "d".repeat(64),
      operation: "create" as const,
      requiresApproval: true,
      sharedFolder: {
        uuid: desired.sharedFolderRef,
        name: "TimeMachine",
        status: "MOUNTED",
      },
      desired,
      changes: [],
      safety: { protocol: "smb3-vfs-fruit" },
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
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          { status: 200 },
        ),
      );

    expect((await fetchOmvTimeMachineStatus()).available).toBe(true);
    expect((await planOmvTimeMachine(desired)).planId).toBe(plan.planId);
    expect(
      (
        await applyOmvTimeMachine(
          desired,
          plan.planId,
          "one-shot-time-machine-token",
        )
      ).verified,
    ).toBe(true);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/sharing/time-machine",
      "/api/appliance/omv/sharing/time-machine/plan",
      "/api/appliance/omv/sharing/time-machine/apply",
    ]);
    const apply = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(JSON.parse(String(apply.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect((apply.headers as Record<string, string>)["X-Echo-Approval"]).toBe(
      "one-shot-time-machine-token",
    );
  });

  it("keeps private NFS preview and approved apply as separate requests", async () => {
    const desired = {
      schema: "echo.omv.nfs-share-desired.v1" as const,
      sharedFolderRef: "11111111-2222-4333-8444-555555555555",
      clientCidr: "192.168.1.0/24",
      readOnly: true,
      comment: "Family NFS",
    };
    const plan = {
      schema: "echo.omv.nfs-share-plan.v1" as const,
      planId: "e".repeat(64),
      baseRevision: "f".repeat(64),
      operation: "create" as const,
      requiresApproval: true,
      shareUuid: "99999999-8888-4777-8666-555555555555",
      sharedFolder: {
        uuid: desired.sharedFolderRef,
        name: "Family",
        status: "OK",
      },
      desired,
      changes: [],
      safety: {
        clientScope: "privateCidrOnly" as const,
        rootSquash: "required" as const,
        syncWrites: "required" as const,
        advancedOptions: "notManaged" as const,
        delete: "notManaged" as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          {
            status: 200,
          },
        ),
      );

    expect((await planOmvNfsShare(desired)).planId).toBe(plan.planId);
    expect(
      (await applyOmvNfsShare(desired, plan.planId, "nfs-approval-token"))
        .verified,
    ).toBe(true);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/nfs/plan",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/nfs/apply",
    );
    const apply = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.parse(String(apply.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect((apply.headers as Record<string, string>)["X-Echo-Approval"]).toBe(
      "nfs-approval-token",
    );
  });

  it("keeps NFS rule removal data-preserving and approval-bound", async () => {
    const desired = {
      schema: "echo.omv.nfs-share-remove-desired.v1" as const,
      sharedFolderRef: "11111111-2222-4333-8444-555555555555",
      clientCidr: "192.168.1.0/24",
    };
    const plan = {
      schema: "echo.omv.nfs-share-remove-plan.v1" as const,
      planId: "a".repeat(64),
      baseRevision: "b".repeat(64),
      operation: "remove" as const,
      requiresApproval: true as const,
      shareUuid: "99999999-8888-4777-8666-555555555555",
      sharedFolder: {
        uuid: desired.sharedFolderRef,
        name: "Family",
        status: "MOUNTED",
      },
      desired,
      changes: [
        {
          field: "registration" as const,
          before: "managed" as const,
          after: "removed" as const,
        },
      ],
      safety: {
        export: "managedRuleOnly" as const,
        data: "preserved" as const,
        directory: "neverModified" as const,
        clientScope: "privateCidrOnly" as const,
        rollback: "exportsAndLiveTable" as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...plan,
            applied: true,
            verified: true,
            dataPreserved: true,
          }),
          { status: 200 },
        ),
      );

    expect((await planOmvNfsShareRemove(desired)).planId).toBe(plan.planId);
    expect(
      (await applyOmvNfsShareRemove(desired, plan.planId, "nfs-remove-token"))
        .dataPreserved,
    ).toBe(true);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/omv/sharing/nfs/remove/plan",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/omv/sharing/nfs/remove/apply",
    );
    const apply = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.parse(String(apply.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect((apply.headers as Record<string, string>)["X-Echo-Approval"]).toBe(
      "nfs-remove-token",
    );
  });

  it("keeps ZFS candidate read, destructive preview and approved apply separate", async () => {
    const desired = {
      schema: "echo.omv.zfs-mirror-desired.v1" as const,
      name: "family",
      devices: ["/dev/sdb", "/dev/sdc"] as [string, string],
      dataLossConfirmed: true as const,
    };
    const plan = {
      schema: "echo.omv.zfs-mirror-plan.v1" as const,
      planId: "f".repeat(64),
      baseRevision: "e".repeat(64),
      operation: "create" as const,
      requiresApproval: true as const,
      desired,
      devices: [
        {
          devicefile: "/dev/sdb",
          sizeBytes: 8 * 1024 ** 3,
          serial: "disk-b",
          wwn: null,
          model: "QEMU HARDDISK",
        },
        {
          devicefile: "/dev/sdc",
          sizeBytes: 8 * 1024 ** 3,
          serial: "disk-c",
          wwn: null,
          model: "QEMU HARDDISK",
        },
      ] as const,
      mountpoint: "/data/family",
      safety: {
        destructive: true as const,
        dataLossConfirmed: true as const,
        layout: "twoDiskMirrorOnly" as const,
        devices: "wholeBlankNonRemovableWithPersistentIdentity" as const,
        force: false as const,
        rollback: "bestEffortPoolDestroyBeforeHandoff" as const,
        unsupported: ["expand"],
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ devices: plan.devices }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          { status: 200 },
        ),
      );

    expect(await fetchOmvZfsMirrorCandidates()).toHaveLength(2);
    expect((await planOmvZfsMirror(desired)).planId).toBe(plan.planId);
    expect(
      (await applyOmvZfsMirror(desired, plan.planId, "zfs-approval-token"))
        .verified,
    ).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/pools/zfs-mirror/candidates",
      "/api/appliance/omv/pools/zfs-mirror/plan",
      "/api/appliance/omv/pools/zfs-mirror/apply",
    ]);
    const apply = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(JSON.parse(String(apply.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect((apply.headers as Record<string, string>)["X-Echo-Approval"]).toBe(
      "zfs-approval-token",
    );
  });

  it("keeps md RAID1 candidate read, destructive preview and approved apply separate", async () => {
    const desired = {
      schema: "echo.omv.mdraid1-desired.v1" as const,
      name: "family",
      devices: ["/dev/sdb", "/dev/sdc"] as [string, string],
      dataLossConfirmed: true as const,
    };
    const devices = [
      {
        devicefile: "/dev/sdb",
        sizeBytes: 8 * 1024 ** 3,
        serial: "disk-b",
        wwn: null,
        model: "QEMU HARDDISK",
      },
      {
        devicefile: "/dev/sdc",
        sizeBytes: 8 * 1024 ** 3,
        serial: "disk-c",
        wwn: null,
        model: "QEMU HARDDISK",
      },
    ] as const;
    const plan = {
      schema: "echo.omv.mdraid1-plan.v1" as const,
      planId: "d".repeat(64),
      baseRevision: "c".repeat(64),
      operation: "create" as const,
      target: "/dev/md/echo-family",
      requiresApproval: true as const,
      desired,
      devices,
      usableBytes: 8 * 1024 ** 3,
      filesystemCreated: false as const,
      safety: {
        destructive: true as const,
        dataLossConfirmed: true as const,
        layout: "twoDiskRaid1Only" as const,
        devices: "wholeBlankNonRemovableWithPersistentIdentity" as const,
        force: false as const,
        degradedStart: false as const,
        filesystemCreated: false as const,
        unsupported: ["filesystemCreate", "mount"],
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ devices }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          {
            status: 200,
          },
        ),
      );

    expect(await fetchOmvMdRaid1Candidates()).toHaveLength(2);
    expect((await planOmvMdRaid1(desired)).planId).toBe(plan.planId);
    expect(
      (await applyOmvMdRaid1(desired, plan.planId, "mdraid-approval-token"))
        .verified,
    ).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/arrays/mdraid1/candidates",
      "/api/appliance/omv/arrays/mdraid1/plan",
      "/api/appliance/omv/arrays/mdraid1/apply",
    ]);
    const apply = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(JSON.parse(String(apply.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect((apply.headers as Record<string, string>)["X-Echo-Approval"]).toBe(
      "mdraid-approval-token",
    );
  });

  it("binds Btrfs creation, scrub and replacement to distinct endpoints", async () => {
    const createDesired = {
      schema: "echo.omv.btrfs-raid1-desired.v1" as const,
      name: "family",
      devices: ["/dev/sdb", "/dev/sdc"] as [string, string],
      dataLossConfirmed: true as const,
    };
    const createPlan = {
      schema: "echo.omv.btrfs-raid1-plan.v1",
      planId: "b".repeat(64),
      desired: createDesired,
    };
    const scrubDesired = {
      schema: "echo.omv.btrfs-scrub-desired.v1" as const,
      filesystemUuid: "11111111-2222-3333-4444-555555555555",
      operation: "start" as const,
    };
    const scrubPlan = {
      schema: "echo.omv.btrfs-scrub-plan.v1",
      planId: "c".repeat(64),
      desired: scrubDesired,
    };
    const replaceDesired = {
      schema: "echo.omv.btrfs-replace-desired.v1" as const,
      filesystemUuid: scrubDesired.filesystemUuid,
      missingDevid: 2,
      replacementDevice: "/dev/sdd",
      dataPreserved: true as const,
    };
    const replacePlan = {
      schema: "echo.omv.btrfs-replace-plan.v1",
      planId: "d".repeat(64),
      desired: replaceDesired,
    };
    const devices = [{ devicefile: "/dev/sdb" }, { devicefile: "/dev/sdc" }];
    const filesystems = [{ filesystem: { uuid: scrubDesired.filesystemUuid } }];
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ devices }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(createPlan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...createPlan, applied: true, verified: true }),
          {
            status: 200,
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ filesystems }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(scrubPlan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...scrubPlan,
            applied: true,
            verified: true,
            maintenanceState: "scrubbing",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ replacements: [{ filesystem: {} }] }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(replacePlan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...replacePlan,
            applied: true,
            verified: true,
            maintenanceState: "replacing",
          }),
          { status: 200 },
        ),
      );

    expect(await fetchOmvBtrfsRaid1Candidates()).toHaveLength(2);
    expect((await planOmvBtrfsRaid1(createDesired)).planId).toBe(
      createPlan.planId,
    );
    expect(
      (
        await applyOmvBtrfsRaid1(
          createDesired,
          createPlan.planId,
          "create-token",
        )
      ).verified,
    ).toBe(true);
    expect(await fetchOmvBtrfsMaintenance()).toEqual(filesystems);
    expect((await planOmvBtrfsScrub(scrubDesired)).planId).toBe(
      scrubPlan.planId,
    );
    expect(
      (await applyOmvBtrfsScrub(scrubDesired, scrubPlan.planId, "scrub-token"))
        .maintenanceState,
    ).toBe("scrubbing");
    expect(await fetchOmvBtrfsReplacementCandidates()).toHaveLength(1);
    expect((await planOmvBtrfsReplace(replaceDesired)).planId).toBe(
      replacePlan.planId,
    );
    expect(
      (
        await applyOmvBtrfsReplace(
          replaceDesired,
          replacePlan.planId,
          "replace-token",
        )
      ).maintenanceState,
    ).toBe("replacing");
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/volumes/btrfs-raid1/candidates",
      "/api/appliance/omv/volumes/btrfs-raid1/plan",
      "/api/appliance/omv/volumes/btrfs-raid1/apply",
      "/api/appliance/omv/volumes/btrfs-raid1/maintenance",
      "/api/appliance/omv/volumes/btrfs-raid1/scrub/plan",
      "/api/appliance/omv/volumes/btrfs-raid1/scrub/apply",
      "/api/appliance/omv/volumes/btrfs-raid1/replacement-candidates",
      "/api/appliance/omv/volumes/btrfs-raid1/replace/plan",
      "/api/appliance/omv/volumes/btrfs-raid1/replace/apply",
    ]);
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).headers).toMatchObject(
      {
        "X-Echo-Approval": "create-token",
      },
    );
    expect((fetchMock.mock.calls[5]?.[1] as RequestInit).headers).toMatchObject(
      {
        "X-Echo-Approval": "scrub-token",
      },
    );
    expect((fetchMock.mock.calls[8]?.[1] as RequestInit).headers).toMatchObject(
      {
        "X-Echo-Approval": "replace-token",
      },
    );
  });

  it("keeps Btrfs scrub schedule read, preview, and approved apply separate", async () => {
    const desired = {
      schema: "echo.btrfs-scrub-schedule-desired.v1" as const,
      enabled: true,
    };
    const status = {
      schemaVersion: 1 as const,
      enabled: false,
      configured: false,
      schedulerInstalled: true,
      source: "localPolicy" as const,
      operation: "scrub" as const,
      scope: "echoManagedHealthyBtrfsRaid1Only" as const,
      schedule: "monthly",
    };
    const plan = {
      schema: desired.schema,
      planId: "8".repeat(64),
      operation: "enable" as const,
      requiresApproval: true,
      current: { schemaVersion: 1 as const, enabled: false },
      desired: { schemaVersion: 1 as const, enabled: true },
      configured: false,
      schedulerInstalled: true,
      scrubAction: "scrub" as const,
      scope: status.scope,
      schedule: status.schedule,
      safety: {
        replicaRepair: true as const,
        ioLoad: "high" as const,
        degradedFilesystems: "skipped" as const,
        readOnlyFilesystems: "skipped" as const,
        knownDeviceErrors: "skipped" as const,
        activeMaintenance: "skipped" as const,
        force: false as const,
        cancel: false as const,
      },
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
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          {
            status: 200,
          },
        ),
      );

    expect((await fetchOmvBtrfsScrubSchedule()).enabled).toBe(false);
    expect((await planOmvBtrfsScrubSchedule(desired)).operation).toBe("enable");
    expect(
      (await applyOmvBtrfsScrubSchedule(desired, plan.planId, "schedule-token"))
        .verified,
    ).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/volumes/btrfs-raid1/scrub/schedule",
      "/api/appliance/omv/volumes/btrfs-raid1/scrub/schedule/plan",
      "/api/appliance/omv/volumes/btrfs-raid1/scrub/schedule/apply",
    ]);
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).headers).toMatchObject(
      {
        "X-Echo-Approval": "schedule-token",
      },
    );
  });

  it("keeps EXT4 candidate read, destructive preview and approved apply separate", async () => {
    const array = {
      name: "array1",
      devicefile: "/dev/md/echo-array1",
      uuid: "11111111:22222222:33333333:44444444",
      level: "raid1" as const,
      devices: ["/dev/sdb", "/dev/sdc"],
      filesystem: null,
    };
    const desired = {
      schema: "echo.omv.ext4-volume-desired.v1" as const,
      arrayUuid: array.uuid,
      name: "family",
      dataLossConfirmed: true as const,
    };
    const plan = {
      schema: "echo.omv.ext4-volume-plan.v1" as const,
      planId: "a".repeat(64),
      baseRevision: "b".repeat(64),
      operation: "createAndMount" as const,
      requiresApproval: true as const,
      desired,
      array,
      mountpoint: "/data/family",
      safety: {
        destructive: true as const,
        dataLossConfirmed: true as const,
        source: "healthyBlankEchoManagedMdRaid1Only" as const,
        filesystem: "ext4Only" as const,
        mountRoot: "/data",
        persistentIdentity: "filesystemUuid" as const,
        force: false as const,
      },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ arrays: [array] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          {
            status: 200,
          },
        ),
      );

    expect(await fetchOmvExt4VolumeCandidates()).toEqual([array]);
    expect((await planOmvExt4Volume(desired)).planId).toBe(plan.planId);
    expect(
      (await applyOmvExt4Volume(desired, plan.planId, "ext4-token")).verified,
    ).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/volumes/ext4/candidates",
      "/api/appliance/omv/volumes/ext4/plan",
      "/api/appliance/omv/volumes/ext4/apply",
    ]);
    const apply = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(JSON.parse(String(apply.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect((apply.headers as Record<string, string>)["X-Echo-Approval"]).toBe(
      "ext4-token",
    );
  });

  it("keeps ZFS export/import discovery, preview and approvals separate", async () => {
    const pool = {
      name: "family",
      poolGuid: "15451357997522795478",
      health: "ONLINE",
      sizeBytes: 16 * 1024 ** 3,
      rootMountpoint: "/data/family",
      datasetCount: 1,
      mountedCount: 1,
      safeToExport: true as const,
    };
    const candidate = {
      name: "archive",
      poolGuid: "9876543210123456789",
      state: "ONLINE",
      layout: "mirror" as const,
      configHash: "c".repeat(64),
      safeToImport: true as const,
    };
    const exportDesired = {
      schema: "echo.omv.zfs-pool-export-desired.v1" as const,
      name: pool.name,
      poolGuid: pool.poolGuid,
      dataPreserved: true as const,
    };
    const importDesired = {
      schema: "echo.omv.zfs-pool-import-desired.v1" as const,
      name: candidate.name,
      poolGuid: candidate.poolGuid,
      mountPolicy: "echoDataRootOnly" as const,
    };
    const exportPlan = {
      schema: "echo.omv.zfs-pool-export-plan.v1",
      planId: "1".repeat(64),
      operation: "export",
      desired: exportDesired,
    };
    const importPlan = {
      schema: "echo.omv.zfs-pool-import-plan.v1",
      planId: "2".repeat(64),
      operation: "import",
      desired: importDesired,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ pools: [pool] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ pools: [candidate] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(exportPlan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...exportPlan, verified: true }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(importPlan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...importPlan, verified: true }), {
          status: 200,
        }),
      );

    expect(await fetchOmvZfsPools()).toEqual([pool]);
    expect(await fetchOmvZfsImportCandidates()).toEqual([candidate]);
    expect((await planOmvZfsPoolExport(exportDesired)).planId).toBe(
      exportPlan.planId,
    );
    expect(
      (
        await applyOmvZfsPoolExport(
          exportDesired,
          exportPlan.planId,
          "export-token",
        )
      ).verified,
    ).toBe(true);
    expect((await planOmvZfsPoolImport(importDesired)).planId).toBe(
      importPlan.planId,
    );
    expect(
      (
        await applyOmvZfsPoolImport(
          importDesired,
          importPlan.planId,
          "import-token",
        )
      ).verified,
    ).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/pools/zfs",
      "/api/appliance/omv/pools/zfs/import-candidates",
      "/api/appliance/omv/pools/zfs/export/plan",
      "/api/appliance/omv/pools/zfs/export/apply",
      "/api/appliance/omv/pools/zfs/import/plan",
      "/api/appliance/omv/pools/zfs/import/apply",
    ]);
    expect((fetchMock.mock.calls[3]?.[1] as RequestInit).headers).toMatchObject(
      { "X-Echo-Approval": "export-token" },
    );
    expect((fetchMock.mock.calls[5]?.[1] as RequestInit).headers).toMatchObject(
      { "X-Echo-Approval": "import-token" },
    );
  });

  it("reads ZFS maintenance and keeps scrub preview separate from approved start", async () => {
    const desired = {
      schema: "echo.omv.zfs-scrub-desired.v1" as const,
      name: "family",
      poolGuid: "15451357997522795478",
      operation: "start" as const,
    };
    const scan = {
      kind: "none" as const,
      state: "idle" as const,
      progressPercent: null,
      errors: null,
      summaryHash: "a".repeat(64),
    };
    const maintenance = {
      pool: {
        name: "family",
        poolGuid: desired.poolGuid,
        health: "ONLINE",
        sizeBytes: 16 * 1024 ** 3,
      },
      rootMountpoint: "/data/family",
      scan,
      canStartScrub: true,
    };
    const plan = {
      schema: "echo.omv.zfs-scrub-plan.v1",
      planId: "8".repeat(64),
      operation: "start",
      desired,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ pools: [maintenance] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...plan,
            applied: true,
            verified: true,
            maintenanceState: "scrubbing",
          }),
          { status: 200 },
        ),
      );

    expect(await fetchOmvZfsMaintenance()).toEqual([maintenance]);
    expect((await planOmvZfsScrub(desired)).planId).toBe(plan.planId);
    expect(
      (await applyOmvZfsScrub(desired, plan.planId, "scrub-token"))
        .maintenanceState,
    ).toBe("scrubbing");
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/pools/zfs/maintenance",
      "/api/appliance/omv/pools/zfs/scrub/plan",
      "/api/appliance/omv/pools/zfs/scrub/apply",
    ]);
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).headers).toMatchObject(
      { "X-Echo-Approval": "scrub-token" },
    );
  });

  it("binds ZFS mirror replacement discovery and apply to the failed vdev GUID", async () => {
    const replacement = {
      pool: {
        name: "family",
        poolGuid: "15451357997522795478",
        health: "DEGRADED",
        sizeBytes: 16 * 1024 ** 3,
      },
      layout: "twoDiskMirror" as const,
      replaceableMember: {
        slot: 2,
        vdevGuid: "2222222222222222222",
        state: "UNAVAIL" as const,
      },
      minimumReplacementBytes: 16 * 1024 ** 3,
      replacementDevices: [
        {
          devicefile: "/dev/sdd",
          sizeBytes: 20 * 1024 ** 3,
          serial: "disk-d",
          wwn: null,
          model: "QEMU HARDDISK",
        },
      ],
    };
    const desired = {
      schema: "echo.omv.zfs-mirror-replace-desired.v1" as const,
      name: "family",
      poolGuid: "15451357997522795478",
      oldVdevGuid: "2222222222222222222",
      replacementDevice: "/dev/sdd",
      dataPreserved: true as const,
    };
    const plan = {
      schema: "echo.omv.zfs-mirror-replace-plan.v1",
      planId: "7".repeat(64),
      operation: "replace",
      desired,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ replacements: [replacement] }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...plan, applied: true, verified: true }),
          {
            status: 200,
          },
        ),
      );

    expect(await fetchOmvZfsMirrorReplacementCandidates()).toEqual([
      replacement,
    ]);
    expect((await planOmvZfsMirrorReplace(desired)).planId).toBe(plan.planId);
    expect(
      (await applyOmvZfsMirrorReplace(desired, plan.planId, "replace-token"))
        .verified,
    ).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/appliance/omv/pools/zfs-mirror/replacement-candidates",
      "/api/appliance/omv/pools/zfs-mirror/replace/plan",
      "/api/appliance/omv/pools/zfs-mirror/replace/apply",
    ]);
    const apply = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(JSON.parse(String(apply.body))).toEqual({
      desired,
      planId: plan.planId,
    });
    expect((apply.headers as Record<string, string>)["X-Echo-Approval"]).toBe(
      "replace-token",
    );
  });
});
