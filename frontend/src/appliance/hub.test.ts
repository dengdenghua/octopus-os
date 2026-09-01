import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyHubInstall,
  applyHubUninstall,
  applyHubUpdate,
  claimHubOperationCredentials,
  createHubControlPlan,
  createHubInstallPlan,
  createHubUninstallPlan,
  createHubUpdatePlan,
  fetchHubAppDetail,
  fetchHubCatalog,
  fetchHubOperations,
  queueHubInstall,
  queueHubControl,
} from "./hub";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Echo Hub API", () => {
  it("uses the authenticated bounded catalog and plan endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ schema: "echo.hub.catalog-response.v1", apps: [] }),
          {
            status: 200,
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.hub.install-plan.v1",
            planId: "a".repeat(64),
            ready: true,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.hub.install-result.v1",
            appId: "demo-app",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.hub.update-plan.v1",
            planId: "c".repeat(64),
            ready: true,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.hub.update-result.v1",
            appId: "demo-app",
            dataVolumesRetained: true,
            nasDataRetained: true,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.hub.uninstall-plan.v1",
            planId: "b".repeat(64),
            ready: true,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.hub.uninstall-result.v1",
            appId: "demo-app",
            dataVolumesRetained: true,
            nasDataRetained: true,
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await fetchHubCatalog();
    await createHubInstallPlan("demo-app");
    await applyHubInstall("demo-app", "a".repeat(64), "approval-once");
    await createHubUpdatePlan("demo-app");
    await applyHubUpdate("demo-app", "c".repeat(64), "approval-update-once");
    await createHubUninstallPlan("demo-app");
    await applyHubUninstall("demo-app", "b".repeat(64), "approval-remove-once");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/appliance/hub/catalog",
      expect.any(Object),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/appliance/hub/plans/install",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ appId: "demo-app" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/appliance/hub/plans/install/apply",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Echo-Approval": "approval-once",
        }),
        body: JSON.stringify({ appId: "demo-app", planId: "a".repeat(64) }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/api/appliance/hub/plans/update",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ appId: "demo-app" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      "/api/appliance/hub/plans/update/apply",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Echo-Approval": "approval-update-once",
        }),
        body: JSON.stringify({ appId: "demo-app", planId: "c".repeat(64) }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      "/api/appliance/hub/plans/uninstall",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ appId: "demo-app" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      7,
      "/api/appliance/hub/plans/uninstall/apply",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Echo-Approval": "approval-remove-once",
        }),
        body: JSON.stringify({ appId: "demo-app", planId: "b".repeat(64) }),
      }),
    );
  });

  it("turns structured service failures into a readable message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              message: "Hub install is blocked",
              blockers: [{ code: "PACKAGE_NOT_PUBLISHED" }],
            },
          }),
          { status: 409 },
        ),
      ),
    );

    await expect(createHubInstallPlan("demo-app")).rejects.toThrow(
      "Hub install is blocked",
    );
  });

  it("queues durable work, validates activity, and claims credentials once", async () => {
    const operation = {
      schema: "echo.hub.operation.v1",
      operationId: "1".repeat(32),
      operation: "install",
      appId: "demo-app",
      planId: "a".repeat(64),
      catalogDigest: "b".repeat(64),
      status: "queued",
      createdAt: "2026-08-29T00:00:00.000Z",
      updatedAt: "2026-08-29T00:00:00.000Z",
      startedAt: null,
      finishedAt: null,
      error: null,
      warning: null,
      progress: {
        schema: "echo.hub.progress.v1",
        stage: "queued",
        step: "waiting",
        completed: null,
        total: null,
        unit: null,
        item: null,
        items: null,
        sequence: 0,
      },
      credentialsAvailable: false,
      result: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(operation), { status: 202 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.hub.operations.v1",
            operations: [operation],
            total: 1,
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: "echo.hub.operation-credentials.v1",
            operationId: operation.operationId,
            credentials: { "admin-password": "shown-once" },
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await queueHubInstall("demo-app", "a".repeat(64), "approval-once");
    expect((await fetchHubOperations()).operations).toHaveLength(1);
    expect(await claimHubOperationCredentials(operation.operationId)).toEqual({
      "admin-password": "shown-once",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/appliance/hub/plans/install/queue",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Echo-Approval": "approval-once",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      `/api/appliance/hub/operations/${operation.operationId}/credentials/claim`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("creates and queues a plan-bound whole-app restart", async () => {
    const planId = "e".repeat(64);
    const plan = {
      schema: "echo.hub.restart-plan.v1",
      planId,
      operation: "restart",
      ready: true,
      requiresApproval: true,
      approvalAction: "hub.app.restart",
      approvalTarget: planId,
      current: {},
      desired: {},
      changes: [],
      blockers: [],
    };
    const operation = {
      schema: "echo.hub.operation.v1",
      operationId: "2".repeat(32),
      operation: "restart",
      appId: "demo-app",
      planId,
      catalogDigest: "b".repeat(64),
      status: "queued",
      createdAt: "2026-08-29T00:00:00.000Z",
      updatedAt: "2026-08-29T00:00:00.000Z",
      startedAt: null,
      finishedAt: null,
      error: null,
      warning: null,
      progress: {
        schema: "echo.hub.progress.v1",
        stage: "queued",
        step: "waiting",
        completed: null,
        total: null,
        unit: null,
        item: null,
        items: null,
        sequence: 0,
      },
      credentialsAvailable: false,
      result: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(plan), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(operation), { status: 202 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(createHubControlPlan("restart", "demo-app")).resolves.toEqual(
      plan,
    );
    await expect(
      queueHubControl("restart", "demo-app", planId, "approval-restart"),
    ).resolves.toEqual(operation);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/appliance/hub/plans/restart/queue",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-Echo-Approval": "approval-restart",
        }),
      }),
    );
  });

  it("loads only a matching authenticated application-detail envelope", async () => {
    const resourcePreflight = {
      schema: "echo.hub.resource-preflight.v1",
      readyForInstall: true,
      blockingIssues: [],
      checks: [
        { id: "architecture", status: "pass", blocking: true },
        { id: "docker-runtime", status: "pass", blocking: true },
        { id: "docker-storage", status: "pass", blocking: true },
        { id: "ports", status: "pass", blocking: true },
        { id: "providers", status: "pass", blocking: true },
        { id: "nas-capacity", status: "not-requested", blocking: false },
      ],
      runtime: {
        serviceCount: 1,
        memoryLimitMiB: 1024,
        pidsLimit: 256,
        shmLimitMiB: 64,
        healthcheckedServices: 0,
      },
      network: {
        mode: "bridge",
        ports: [],
        requiredProviders: [],
        providersReady: true,
      },
      storage: {
        appDataVolumes: 1,
        nasVolumes: 0,
        nasAccess: "none",
        snapshotVolumes: 1,
        nasCapacity: {
          status: "not-requested",
          totalBytes: null,
          freeBytes: null,
          usedPercent: null,
        },
        imageStorage: {
          status: "sufficient",
          downloadBytes: 256 * 1024 ** 2,
          blobCount: 8,
          requiredFreeBytes: 768 * 1024 ** 2,
          reservePolicy: "compressed-times-three-or-plus-512MiB",
          capacity: {
            schema: "echo.hub.docker-storage.v1",
            status: "observed",
            totalBytes: 128 * 1024 ** 3,
            freeBytes: 64 * 1024 ** 3,
            usedPercent: 50,
          },
        },
      },
      notices: [],
    };
    const detail = {
      schema: "echo.hub.app-detail.v1",
      catalogDigest: "d".repeat(64),
      architecture: "amd64",
      runtime: { available: true, error: null },
      appRuntime: {
        schema: "echo.hub.runtime.v1",
        status: "not-installed",
        summary: {
          serviceCount: 0,
          runningServices: 0,
          healthyServices: 0,
          restartCount: 0,
          cpuPercent: null,
          memoryUsageBytes: null,
          memoryLimitBytes: null,
          pids: null,
        },
        services: [],
      },
      diagnostics: {
        schema: "echo.hub.diagnostics.v1",
        status: "not-installed",
        incidents: [],
      },
      app: { id: "demo-app" },
      resourcePreflight,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(detail), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...detail,
            app: { id: "another-app" },
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...detail,
            resourcePreflight: {
              ...resourcePreflight,
              network: {
                ...resourcePreflight.network,
                ports: [
                  {
                    container: 8080,
                    host: 18080,
                    protocol: "tcp",
                    status: "secret-internal-state",
                  },
                ],
              },
            },
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...detail,
            appRuntime: {
              ...detail.appRuntime,
              logs: "PASSWORD=must-not-cross-boundary",
            },
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchHubAppDetail("demo-app")).resolves.toEqual(detail);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/appliance/hub/apps/demo-app",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    await expect(fetchHubAppDetail("demo-app")).rejects.toThrow(
      "应用详情数据无效",
    );
    await expect(fetchHubAppDetail("demo-app")).rejects.toThrow(
      "应用详情数据无效",
    );
    await expect(fetchHubAppDetail("demo-app")).rejects.toThrow(
      "应用详情数据无效",
    );
    await expect(fetchHubAppDetail("../escape")).rejects.toThrow(
      "应用标识无效",
    );
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});
