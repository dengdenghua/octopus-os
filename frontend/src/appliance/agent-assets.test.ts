import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyAgentCapabilityLifecycle,
  agentAssetManagementRoute,
  agentAssetWindowId,
  authorizeAgentCapability,
  connectAgentCapability,
  createAgentCapabilityPlan,
  disconnectAgentCapability,
  disableAgentCapability,
  fetchAgentCapabilityConnectionProfile,
  fetchAgentHubCatalog,
  type AgentHubAsset,
} from "./agent-assets";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Agent assets projected into Echo Hub", () => {
  it("reuses Agent catalog and installed state without reading its database", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            schema: "echo.agent-assets.v6",
            available: true,
            plugins: [
              {
                id: "documents",
                plugin: "documents",
                name: "Documents",
                name_zh: "文档助手",
                description: "创建与整理文档",
                source: "codex",
                version: "1.2.0",
                kind: "workbench",
                release_summary: "1.3.0：新增受信版本说明。",
                host_api: ">=0.2,<0.3",
                permissions: ["content.read", "content.write"],
                authModes: ["oauth"],
                dependencies: ["base-tools"],
                runtimeDependencies: ["renderer.whl"],
                connectors: ["documents-app"],
              },
            ],
            skills: [
              {
                name: "photo-organizer",
                description: "整理照片",
                source: "echo",
              },
            ],
            installed: { plugins: ["documents"], skills: [] },
            pluginStates: [
              {
                id: "documents",
                catalogId: "documents",
                kind: "workbench",
                source: "cloud",
                state: "update_available",
                installed: true,
                enabled: true,
                rollbackAvailable: true,
                recoveryCount: 2,
                trustLevel: "publisher",
                integrityVerified: true,
                publisherVerified: true,
                publisher: "Echo Publisher",
                compatibility: "compatible",
                hostApi: ">=0.2,<0.3",
                releaseSummary: "1.3.0：新增受信版本说明。",
                version: "1.2.0",
                availableVersion: "1.3.0",
                permissions: ["content.read", "content.write"],
                permissionsGranted: ["content.read", "content.write"],
                permissionReviewRequired: false,
                permissionActive: true,
                authModes: ["oauth"],
                dependencies: ["base-tools"],
                runtimeDependencies: ["renderer.whl"],
                connectors: ["documents-app"],
              },
            ],
            unavailableSources: [],
          }),
          { status: 200 },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchAgentHubCatalog();

    expect(result.available).toBe(true);
    expect(result.plugins).toBe(0);
    expect(result.workbenches).toBe(1);
    expect(result.connectors).toBe(0);
    expect(result.skills).toBe(1);
    expect(result.installed).toBe(1);
    expect(result.updates).toBe(1);
    expect(result.assets).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "workbench:documents",
          name: "文档助手",
          installed: true,
          lifecycleState: "update_available",
          rollbackAvailable: true,
          recoveryCount: 2,
          trustLevel: "publisher",
          publisherVerified: true,
          verifiedPublisher: "Echo Publisher",
          compatibility: "compatible",
          hostApi: ">=0.2,<0.3",
          releaseSummary: "1.3.0：新增受信版本说明。",
          permissions: ["content.read", "content.write"],
          permissionsGranted: ["content.read", "content.write"],
          permissionReviewRequired: false,
          permissionActive: true,
          authModes: ["oauth"],
          dependencies: ["base-tools"],
          runtimeDependencies: ["renderer.whl"],
          connectors: ["documents-app"],
        }),
        expect.objectContaining({
          id: "skill:photo-organizer",
          installed: false,
        }),
      ]),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/agent-assets/catalog?limit=80",
      { headers: {} },
    );
  });

  it("keeps the device catalog usable when the Agent directory is offline", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
    );

    await expect(fetchAgentHubCatalog()).resolves.toEqual({
      available: false,
      assets: [],
      plugins: 0,
      workbenches: 0,
      connectors: 0,
      skills: 0,
      installed: 0,
      updates: 0,
      attention: 0,
      error: "Agent 能力目录尚未连接",
    });
  });

  it("fails closed when the authenticated projection has an incompatible shape", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            schema: "echo.agent-assets.v5",
            available: true,
            plugins: [{ id: "documents", name: { private: true } }],
            skills: [],
            installed: { plugins: [], skills: [] },
            pluginStates: [],
            unavailableSources: [],
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(fetchAgentHubCatalog()).resolves.toEqual({
      available: false,
      assets: [],
      plugins: 0,
      workbenches: 0,
      connectors: 0,
      skills: 0,
      installed: 0,
      updates: 0,
      attention: 0,
      error: "Agent 能力目录返回了不兼容的数据",
    });
  });

  it("routes plugin and skill cards to their real Agent-owned management surfaces", () => {
    const plugin: AgentHubAsset = {
      id: "plugin:documents",
      installId: "documents",
      name: "文档助手",
      description: "创建与整理文档",
      kind: "plugin",
      source: "Agent",
      author: null,
      version: null,
      availableVersion: null,
      installed: true,
      enabled: true,
      lifecycleState: "enabled",
      rollbackAvailable: false,
      recoveryCount: 0,
      trustLevel: "unverified",
      integrityVerified: false,
      publisherVerified: false,
      verifiedPublisher: null,
      compatibility: "not_checked",
      hostApi: null,
      releaseSummary: null,
      permissions: [],
      permissionsGranted: [],
      permissionReviewRequired: false,
      permissionActive: true,
      authModes: [],
      dependencies: [],
      runtimeDependencies: [],
      connectors: [],
    };
    const skill: AgentHubAsset = {
      ...plugin,
      id: "skill:photo-organizer",
      installId: "photo-organizer",
      name: "照片整理",
      kind: "skill",
      installed: false,
    };

    expect(agentAssetManagementRoute(plugin)).toBe(
      "/workspace/agents?surface=chat&tab=plugins&view=installed",
    );
    expect(agentAssetManagementRoute({ ...plugin, installed: false })).toBe(
      "/workspace/agents?surface=chat&tab=plugins&view=all",
    );
    expect(agentAssetManagementRoute(skill)).toBe(
      "/workspace/agents?surface=chat&tab=skills",
    );
    expect(agentAssetWindowId(plugin)).toBe("agent-assets:plugin");
    expect(agentAssetWindowId(skill)).toBe("agent-assets:skill");

    const workbench: AgentHubAsset = {
      ...plugin,
      id: "workbench:design",
      installId: "design",
      name: "设计画布",
      kind: "workbench",
    };
    expect(agentAssetManagementRoute(workbench)).toContain("tab=plugins");
    expect(agentAssetWindowId(workbench)).toBe("agent-assets:plugin");
  });

  it("uses bound plans for device lifecycle and keeps approval out of the body", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/plans/install")) {
        return new Response(
          JSON.stringify({
            schema: "echo.capability_install_plan.v1",
            service_schema: "echo.capability-service.v1",
            capability_id: "wecom",
            plan_id: "a".repeat(64),
            can_install: true,
            permissions: ["account.credentials"],
            blockers: [],
            changes: ["verify_publisher_signature"],
          }),
          { status: 200 },
        );
      }
      return new Response(
        JSON.stringify({
          schema: "echo.capability-service.v1",
          operation: "install",
          capability: { id: "wecom", installed: true, enabled: false },
          result: { installed: true },
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const plan = await createAgentCapabilityPlan("install", "wecom");
    const result = await applyAgentCapabilityLifecycle(
      "install",
      "wecom",
      plan.planId,
      "single-use-approval",
    );

    expect(plan).toEqual(
      expect.objectContaining({
        capabilityId: "wecom",
        operation: "install",
        ready: true,
        permissions: ["account.credentials"],
      }),
    );
    expect(result).toEqual(
      expect.objectContaining({ capabilityId: "wecom", installed: true }),
    );
    const apply = fetchMock.mock.calls[1];
    expect(apply[1]?.headers).toEqual(
      expect.objectContaining({ "X-Echo-Approval": "single-use-approval" }),
    );
    expect(JSON.parse(String(apply[1]?.body))).toEqual({
      capabilityId: "wecom",
      planId: "a".repeat(64),
    });
  });

  it("renders Agent-owned credential fields and never fetches its private storage", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/demo-token/connection-profile")) {
        return new Response(
          JSON.stringify({
            schema: "echo.capability-service.v1",
            capability_id: "demo-token",
            auth_mode: "token",
            mode: "principal_credentials",
            can_connect: true,
            connected: false,
            minimum_credentials: 1,
            fields: [
              {
                key: "access_token",
                label: "Access Token",
                label_zh: "访问令牌",
                secret: true,
                required: false,
              },
              {
                key: "api_key",
                label: "API Key",
                label_zh: "API 密钥",
                secret: true,
                required: false,
              },
            ],
            blockers: [],
          }),
          { status: 200 },
        );
      }
      return new Response(
        JSON.stringify({
          schema: "echo.capability-service.v1",
          operation: url.endsWith("/disconnect") ? "disconnect" : "connect",
          capability: { id: "demo-token", installed: true, enabled: true },
          result: { connected: !url.endsWith("/disconnect") },
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const profile = await fetchAgentCapabilityConnectionProfile("demo-token");
    await connectAgentCapability("demo-token", { access_token: "secret" });
    await disconnectAgentCapability("demo-token");

    expect(profile).toEqual(
      expect.objectContaining({
        capabilityId: "demo-token",
        mode: "principal_credentials",
        minimumCredentials: 1,
        fields: [
          expect.objectContaining({ key: "access_token", labelZh: "访问令牌" }),
          expect.objectContaining({ key: "api_key", labelZh: "API 密钥" }),
        ],
      }),
    );
    const connectRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/agent-capabilities/connect"),
    );
    expect(JSON.parse(String(connectRequest?.[1]?.body))).toEqual({
      capabilityId: "demo-token",
      tokens: { access_token: "secret" },
    });
    expect(
      fetchMock.mock.calls.some(([url]) =>
        /database|sqlite|private-storage/.test(String(url)),
      ),
    ).toBe(false);
  });

  it("authorizes and disables one principal through the Echo lifecycle API", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const operation = url.endsWith("/disable") ? "disable" : "authorize";
      return new Response(
        JSON.stringify({
          schema: "echo.capability-service.v1",
          operation,
          capability: { id: "wecom", installed: true },
          result: { enabled: operation === "authorize" },
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      authorizeAgentCapability(
        "wecom",
        "b".repeat(64),
        ["account.credentials"],
        "authorize-once",
      ),
    ).resolves.toEqual(expect.objectContaining({ enabled: true }));
    await expect(disableAgentCapability("wecom")).resolves.toEqual(
      expect.objectContaining({ enabled: false }),
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      capabilityId: "wecom",
      planId: "b".repeat(64),
      permissions: ["account.credentials"],
      activate: true,
    });
    expect(fetchMock.mock.calls[0][1]?.headers).toEqual(
      expect.objectContaining({ "X-Echo-Approval": "authorize-once" }),
    );
  });

  it("rejects a malformed Agent operation plan before it reaches the UI", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            schema: "echo.capability_install_plan.v1",
            service_schema: "echo.capability-service.v1",
            capability_id: "wecom",
            plan_id: "/private/agent/state",
            can_install: true,
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(createAgentCapabilityPlan("install", "wecom")).rejects.toThrow(
      "不兼容的操作计划",
    );
  });
});
