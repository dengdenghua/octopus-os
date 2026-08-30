import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyOmvGroup,
  applyOmvNfsShare,
  applyOmvSharedFolder,
  applyOmvSharePrivilege,
  applyOmvSmbShare,
  applyOmvUser,
  applyOmvUserPassword,
  fetchOmvFilesystems,
  fetchOmvHealth,
  fetchOmvSharePrivileges,
  fetchOmvSharingOverview,
  fetchOmvSmart,
  fetchOmvSmartDevices,
  fetchOmvStorageTopology,
  fetchOmvStatus,
  planOmvNfsShare,
  planOmvGroup,
  planOmvSharedFolder,
  planOmvSharePrivilege,
  planOmvSmbShare,
  planOmvUser,
  planOmvUserPassword,
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
});
