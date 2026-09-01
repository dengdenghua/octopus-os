import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HubPanel } from "./hub-panel";
import type {
  HubApp,
  HubAppDetailResponse,
  HubCatalogResponse,
  HubInstallPlan,
  HubUninstallPlan,
  HubUpdatePlan,
} from "./hub";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), message: vi.fn() },
}));

const pendingApp: HubApp = {
  id: "immich",
  name: "Immich",
  nameZh: "智能相册",
  version: "3.1.0",
  summary: "自动整理与浏览家庭照片。",
  category: "photos",
  icon: "photos",
  sourceUrl: "https://example.com/immich",
  featured: true,
  imageStorage: null,
  package: null,
  integrationStatus: "integration-pending",
  integrationNote: "等待安全合同完成。",
  installation: {
    installed: false,
    containerId: null,
    state: "not-installed",
    status: "",
    image: null,
    version: null,
  },
  installable: false,
  installBlockers: ["PACKAGE_NOT_PUBLISHED"],
  updateAvailable: false,
};

const bundledPendingApp: HubApp = {
  ...pendingApp,
  id: "nextcloud",
  name: "Nextcloud",
  nameZh: "家庭云盘",
  bundle: {
    schema: "echo.hub.bundle-package.v1",
    architectures: ["amd64", "arm64"],
    publicService: "app",
    networks: [],
    volumes: [],
    secrets: [],
    services: [],
    upgradePolicy: {
      applicationVersion: "34.0.3",
      maxMajorStep: 1,
      snapshotVolumes: [],
      serviceOrder: [],
    },
  },
};

const installableApp: HubApp = {
  ...pendingApp,
  id: "demo-app",
  name: "Demo App",
  nameZh: "演示应用",
  version: "1.0.0",
  summary: "已通过固定镜像与存储边界检查。",
  category: "media",
  icon: "media",
  imageStorage: {
    schema: "echo.hub.image-storage.v1",
    architectures: {
      amd64: { downloadBytes: 256 * 1024 ** 2, blobCount: 8 },
    },
  },
  package: {
    schema: "echo.hub.docker-package.v1",
    image: `registry.example.com/echo/demo@sha256:${"a".repeat(64)}`,
    architectures: ["amd64"],
    ports: [{ container: 8080, host: 18080, protocol: "tcp" }],
    volumes: [],
    environment: {},
    runtime: {
      memoryMiB: 2048,
      pids: 384,
      shmSizeMiB: 128,
      readOnlyRootfs: false,
    },
  },
  integrationStatus: "available",
  integrationNote: "已验证。",
  installable: true,
  installBlockers: [],
};

const detailApp: HubApp = {
  ...installableApp,
  package: {
    ...installableApp.package!,
    volumes: [
      {
        source: "app-data",
        name: "config",
        target: "/config",
        readOnly: false,
      },
      {
        source: "nas-root",
        name: "media",
        target: "/media",
        readOnly: true,
      },
    ],
  },
};

const hostLanApp: HubApp = {
  ...installableApp,
  id: "home-assistant",
  name: "Home Assistant",
  nameZh: "智能家庭",
  version: "2026.8.3",
  category: "automation",
  icon: "home",
  package: null,
  bundle: {
    schema: "echo.hub.bundle-package.v1",
    architectures: ["amd64", "arm64"],
    publicService: "app",
    networks: [],
    volumes: [
      {
        name: "config",
        source: "app-data",
        relativePath: null,
        retention: "retain",
        snapshotOnUpdate: true,
      },
    ],
    secrets: [],
    services: [
      {
        id: "app",
        role: "app",
        version: "2026.8.3",
        image: `ghcr.io/home-assistant/home-assistant@sha256:${"a".repeat(64)}`,
        dependsOn: [],
        networks: [],
        networkMode: "host",
        ports: [{ container: 8123, host: 8123, protocol: "tcp" }],
        mounts: [{ volume: "config", target: "/config", readOnly: false }],
        secrets: [],
        secretEnvironment: {},
        environment: { TZ: "system" },
        entrypoint: [],
        command: [],
        healthcheck: null,
        runtime: {
          profile: "unprivileged",
          memoryMiB: 2048,
          pids: 512,
          shmSizeMiB: 64,
          readOnlyRootfs: false,
        },
      },
    ],
    upgradePolicy: {
      applicationVersion: "2026.8.3",
      maxMajorStep: 1,
      snapshotVolumes: ["config"],
      serviceOrder: ["app"],
    },
  },
};

const installedApp: HubApp = {
  ...installableApp,
  installation: {
    installed: true,
    containerId: "d".repeat(12),
    state: "running",
    status: "Up",
    image: installableApp.package?.image ?? null,
    version: "1.0.0",
  },
  installable: false,
  installBlockers: ["ALREADY_INSTALLED"],
  updateAvailable: false,
};

const runtimeOfflineApp: HubApp = {
  ...installableApp,
  installable: false,
  installBlockers: ["DOCKER_RUNTIME_UNAVAILABLE"],
};

const portConflictApp: HubApp = {
  ...installableApp,
  installable: false,
  installBlockers: ["PORT_IN_USE"],
};

const updatableApp: HubApp = {
  ...installedApp,
  installation: {
    ...installedApp.installation,
    image: `registry.example.com/echo/demo@sha256:${"9".repeat(64)}`,
    version: "0.9.0",
  },
  updateAvailable: true,
};

function catalog(apps: HubApp[]): HubCatalogResponse {
  return {
    schema: "echo.hub.catalog-response.v1",
    version: "test.1",
    digest: "b".repeat(64),
    publisher: { id: "echo", name: "Echo" },
    architecture: "amd64",
    runtime: { available: true, error: null },
    total: apps.length,
    apps,
  };
}

function operationList(operations: unknown[] = []) {
  return {
    schema: "echo.hub.operations.v1",
    operations,
    total: operations.length,
  };
}

function hubOperation({
  operation,
  appId,
  planId,
  status,
  result = null,
  credentialsAvailable = false,
  progress,
}: {
  operation: "install" | "update" | "uninstall";
  appId: string;
  planId: string;
  status: "queued" | "running" | "succeeded";
  result?: unknown;
  credentialsAvailable?: boolean;
  progress?: Record<string, unknown>;
}) {
  return {
    schema: "echo.hub.operation.v1",
    operationId: "1".repeat(32),
    operation,
    appId,
    planId,
    catalogDigest: "b".repeat(64),
    status,
    createdAt: "2026-08-29T00:00:00.000Z",
    updatedAt: "2026-08-29T00:00:00.000Z",
    startedAt: status === "queued" ? null : "2026-08-29T00:00:00.010Z",
    finishedAt: status === "queued" ? null : "2026-08-29T00:00:00.020Z",
    error: null,
    warning: null,
    progress:
      progress ??
      ({
        schema: "echo.hub.progress.v1",
        stage:
          status === "queued"
            ? "queued"
            : status === "running"
              ? "pulling"
              : "completed",
        step:
          status === "queued"
            ? "waiting"
            : status === "running"
              ? "pulling-image"
              : "finished",
        completed: null,
        total: null,
        unit: null,
        item: null,
        items: null,
        sequence: status === "queued" ? 0 : 8,
      } as const),
    credentialsAvailable,
    result,
  };
}

function resourcePreflight(app: HubApp) {
  const bundleServices = app.bundle?.services ?? [];
  const packageRuntime = app.package?.runtime;
  const ports =
    app.package?.ports ??
    bundleServices.find((service) => service.id === app.bundle?.publicService)
      ?.ports ??
    [];
  const nasVolumes = app.package
    ? app.package.volumes.filter((volume) => volume.source === "nas-root")
        .length
    : (app.bundle?.volumes.filter((volume) => volume.source === "nas-data")
        .length ?? 0);
  return {
    schema: "echo.hub.resource-preflight.v1" as const,
    readyForInstall: app.installable,
    blockingIssues: app.installBlockers,
    checks: [
      { id: "architecture" as const, status: "pass" as const, blocking: true },
      {
        id: "docker-runtime" as const,
        status: "pass" as const,
        blocking: true,
      },
      {
        id: "docker-storage" as const,
        status: "pass" as const,
        blocking: true,
      },
      { id: "ports" as const, status: "pass" as const, blocking: true },
      { id: "providers" as const, status: "pass" as const, blocking: true },
      {
        id: "nas-capacity" as const,
        status: nasVolumes ? ("observed" as const) : ("not-requested" as const),
        blocking: false,
      },
    ],
    runtime: {
      serviceCount: app.package ? 1 : bundleServices.length,
      memoryLimitMiB:
        packageRuntime?.memoryMiB ??
        bundleServices.reduce(
          (total, service) => total + service.runtime.memoryMiB,
          0,
        ),
      pidsLimit:
        packageRuntime?.pids ??
        bundleServices.reduce(
          (total, service) => total + service.runtime.pids,
          0,
        ),
      shmLimitMiB:
        packageRuntime?.shmSizeMiB ??
        bundleServices.reduce(
          (total, service) => total + service.runtime.shmSizeMiB,
          0,
        ),
      healthcheckedServices: bundleServices.filter(
        (service) => service.healthcheck,
      ).length,
    },
    network: {
      mode: app.bundle?.services.some(
        (service) => service.networkMode === "host",
      )
        ? ("host" as const)
        : ("bridge" as const),
      ports: ports.map((port) => ({
        ...port,
        status: app.installBlockers.includes("PORT_IN_USE")
          ? ("conflict" as const)
          : app.installation.installed
            ? ("owned" as const)
            : ("available" as const),
      })),
      requiredProviders: app.bundle?.providers ?? [],
      providersReady: true,
    },
    storage: {
      appDataVolumes: app.package
        ? app.package.volumes.filter((volume) => volume.source === "app-data")
            .length
        : (app.bundle?.volumes.filter((volume) => volume.source === "app-data")
            .length ?? 0),
      nasVolumes,
      nasAccess: nasVolumes
        ? app.package?.volumes.some(
            (volume) => volume.source === "nas-root" && !volume.readOnly,
          )
          ? ("read-write" as const)
          : ("read-only" as const)
        : ("none" as const),
      snapshotVolumes:
        app.bundle?.upgradePolicy.snapshotVolumes.length ??
        app.package?.volumes.filter(
          (volume) => volume.source === "app-data" && !volume.readOnly,
        ).length ??
        0,
      nasCapacity: {
        status: nasVolumes ? ("observed" as const) : ("not-requested" as const),
        totalBytes: nasVolumes ? 2 * 1024 ** 4 : null,
        freeBytes: nasVolumes ? 1.25 * 1024 ** 4 : null,
        usedPercent: nasVolumes ? 37.5 : null,
      },
      imageStorage: {
        status:
          app.package || app.bundle
            ? ("sufficient" as const)
            : ("unavailable" as const),
        downloadBytes: app.package || app.bundle ? 256 * 1024 ** 2 : null,
        blobCount: app.package || app.bundle ? 8 : null,
        requiredFreeBytes: app.package || app.bundle ? 768 * 1024 ** 2 : null,
        reservePolicy: "compressed-times-three-or-plus-512MiB" as const,
        capacity: {
          schema: "echo.hub.docker-storage.v1" as const,
          status: "observed" as const,
          totalBytes: 128 * 1024 ** 3,
          freeBytes: 64 * 1024 ** 3,
          usedPercent: 50,
        },
      },
    },
    notices: [] as Array<
      | "HOST_LAN"
      | "NAS_READ_WRITE"
      | "NAS_READ_ONLY"
      | "MULTI_SERVICE"
      | "ONE_TIME_CREDENTIALS"
    >,
  };
}

function appDetail(app: HubApp, architecture = "amd64"): HubAppDetailResponse {
  const running = app.installation.state === "running";
  const installed = app.installation.installed;
  return {
    schema: "echo.hub.app-detail.v1",
    catalogDigest: "b".repeat(64),
    architecture,
    runtime: { available: true, error: null },
    appRuntime: {
      schema: "echo.hub.runtime.v1" as const,
      status: installed
        ? running
          ? ("healthy" as const)
          : ("stopped" as const)
        : ("not-installed" as const),
      summary: {
        serviceCount: installed ? 1 : 0,
        runningServices: running ? 1 : 0,
        healthyServices: running ? 1 : 0,
        restartCount: 0,
        cpuPercent: running ? 3.2 : null,
        memoryUsageBytes: running ? 256 * 1024 ** 2 : null,
        memoryLimitBytes: running ? 1024 * 1024 ** 2 : null,
        pids: running ? 18 : null,
      },
      services: installed
        ? [
            {
              id: "app",
              role: "app" as const,
              public: true,
              state: running ? ("running" as const) : ("exited" as const),
              health: "not-configured" as const,
              restartCount: 0,
              oomKilled: false,
              exitCode: 0,
              cpuPercent: running ? 3.2 : null,
              memoryUsageBytes: running ? 256 * 1024 ** 2 : null,
              memoryLimitBytes: running ? 1024 * 1024 ** 2 : null,
              pids: running ? 18 : null,
            },
          ]
        : [],
    },
    diagnostics: {
      schema: "echo.hub.diagnostics.v1" as const,
      status: installed
        ? running
          ? ("ok" as const)
          : ("stopped" as const)
        : ("not-installed" as const),
      incidents: [],
    },
    app,
    resourcePreflight: resourcePreflight(app),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Echo Hub panel", () => {
  it("keeps member browsing available but blocks device lifecycle planning", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(catalog([installableApp])), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<HubPanel open canManageDevice={false} onClose={vi.fn()} />);

    expect(await screen.findByText("演示应用")).toBeInTheDocument();
    expect(
      screen.getByText(
        "你可以浏览设备与 Agent 目录，并连接自己的账户；安装、更新和设备控制由管理员完成。",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "安装" }));

    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/api/appliance/hub/plans/install"),
      ),
    ).toBe(false);
  });

  it("shows truthful integration state and filters the featured catalog", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(catalog([pendingApp, installableApp])), {
          status: 200,
        }),
      ),
    );
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} />);

    expect(
      await screen.findByRole("dialog", { name: "Echo Hub" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("智能相册")).toBeInTheDocument();
    expect(screen.getAllByText("接入中")).toHaveLength(2);
    expect(screen.getByText("可安装")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "接入中" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "照片" }));
    expect(screen.getByText("智能相册")).toBeInTheDocument();
    expect(screen.queryByText("演示应用")).not.toBeInTheDocument();

    await user.clear(screen.getByRole("textbox", { name: "搜索 Hub 应用" }));
    await user.type(
      screen.getByRole("textbox", { name: "搜索 Hub 应用" }),
      "不存在",
    );
    expect(screen.getByText("没有找到匹配的应用")).toBeInTheDocument();
  });

  it("shows a locked multi-container contract without claiming installability", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(catalog([bundledPendingApp])), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<HubPanel open onClose={vi.fn()} />);

    expect(await screen.findByText("家庭云盘")).toBeInTheDocument();
    expect(screen.getByText("多容器合同已锁定")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "接入中" })).toBeDisabled();
  });

  it("distinguishes an offline installer from an unfinished integration", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ...catalog([runtimeOfflineApp]),
            runtime: { available: false, error: "Docker is offline" },
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    render(<HubPanel open onClose={vi.fn()} />);

    expect(await screen.findByText("演示应用")).toBeInTheDocument();
    expect(screen.getByText("暂不可用")).toBeInTheDocument();
    expect(screen.getByText("应用服务暂时离线")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "服务离线" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "接入中" }),
    ).not.toBeInTheDocument();
  });

  it("explains when the preview is not connected to an Echo OS app service", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ...catalog([runtimeOfflineApp]),
            runtime: {
              available: false,
              error:
                "direct Docker socket access is disabled in appliance mode; configure ECHO_DOCKER_HOST",
            },
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    render(<HubPanel open onClose={vi.fn()} />);

    expect(
      await screen.findByText(
        "当前预览未连接 Echo OS 设备应用服务；部署到设备或配置受限容器代理后即可安装。",
      ),
    ).toBeInTheDocument();
  });

  it("explains fixed ports, storage access and retention in one detail sheet", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/catalog")) {
        return new Response(JSON.stringify(catalog([detailApp])), {
          status: 200,
        });
      }
      if (url.endsWith(`/apps/${detailApp.id}`)) {
        return new Response(JSON.stringify(appDetail(detailApp)), {
          status: 200,
        });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} />);

    await user.click(
      await screen.findByRole("button", { name: "查看 演示应用 详情" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "演示应用 应用详情",
    });
    expect(dialog).toHaveTextContent("18080/TCP");
    expect(dialog).toHaveTextContent("应用内部 8080");
    expect(dialog).toHaveTextContent("NAS 全盘");
    expect(dialog).toHaveTextContent("应用私有数据");
    expect(within(dialog).getByText("只读")).toBeInTheDocument();
    expect(within(dialog).getByText("读写")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("运行上限：2 GB 内存 · 384 个进程");
    expect(dialog).toHaveTextContent("18080/TCP → 应用内部 8080可用");
    expect(dialog).toHaveTextContent("NAS 当前可用 1.3 TB，总容量 2 TB");
    expect(dialog).toHaveTextContent(
      "Docker 数据盘可用 64 GB，本次保守预留 768 MB",
    );
    expect(dialog).toHaveTextContent(
      "受信 OCI 清单下载量 256 MB · 8 个去重分层",
    );
    expect(dialog).toHaveTextContent("固定摘要镜像 1 个");
    expect(dialog).toHaveTextContent(
      "卸载只移除受管容器，应用配置和 NAS 文件继续保留",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/hub/apps/demo-app",
      expect.any(Object),
    );

    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("dialog", { name: "演示应用 应用详情" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("dialog", { name: "Echo Hub" }),
    ).toBeInTheDocument();
  });

  it("makes host-LAN scope and hardware exclusions visible before install", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([hostLanApp])), {
            status: 200,
          });
        }
        if (url.endsWith(`/apps/${hostLanApp.id}`)) {
          return new Response(JSON.stringify(appDetail(hostLanApp)), {
            status: 200,
          });
        }
        return new Response(null, { status: 404 });
      }),
    );
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} />);

    await user.click(
      await screen.findByRole("button", { name: "查看 智能家庭 详情" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "智能家庭 应用详情",
    });
    expect(dialog).toHaveTextContent("直接接入家庭局域网");
    expect(dialog).toHaveTextContent("mDNS/SSDP");
    expect(dialog).toHaveTextContent("8123/TCP");
    expect(dialog).toHaveTextContent("不开放 USB、Bluetooth 或 Zigbee");
    expect(dialog).toHaveTextContent("更新前创建回退快照");
  });

  it("keeps the catalog usable when application detail refresh fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/catalog")) {
        return new Response(JSON.stringify(catalog([installableApp])), {
          status: 200,
        });
      }
      if (url.endsWith(`/apps/${installableApp.id}`)) {
        return new Response(JSON.stringify({ detail: "详情服务暂时不可用" }), {
          status: 503,
        });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} />);

    await user.click(
      await screen.findByRole("button", { name: "查看 演示应用 详情" }),
    );
    expect(await screen.findByText("详情服务暂时不可用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).endsWith(`/apps/${installableApp.id}`),
        ),
      ).toHaveLength(2),
    );
    expect(
      within(screen.getByRole("dialog", { name: "Echo Hub" })).getByText(
        "演示应用",
      ),
    ).toBeInTheDocument();
  });

  it("names the exact occupied port instead of showing a generic install failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([portConflictApp])), {
            status: 200,
          });
        }
        if (url.endsWith(`/apps/${portConflictApp.id}`)) {
          return new Response(JSON.stringify(appDetail(portConflictApp)), {
            status: 200,
          });
        }
        return new Response(null, { status: 404 });
      }),
    );
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} />);

    expect(await screen.findByText("所需端口已被占用")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "查看 演示应用 详情" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "演示应用 应用详情",
    });
    expect(dialog).toHaveTextContent("18080/TCP → 应用内部 8080已被占用");
  });

  it("keeps open primary and routes stop through an all-service safety plan", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/plans/stop")) {
          return new Response(
            JSON.stringify({
              schema: "echo.hub.stop-plan.v1",
              planId: "e".repeat(64),
              operation: "stop",
              ready: true,
              requiresApproval: true,
              approvalAction: "hub.app.stop",
              approvalTarget: "e".repeat(64),
              current: { installation: installedApp.installation, runtime: {} },
              desired: {
                appId: installedApp.id,
                catalogDigest: "b".repeat(64),
                state: "stopped",
                serviceOrder: ["app"],
                dataVolumesRetained: true,
                nasDataRetained: true,
              },
              changes: [{ field: "services", before: [], after: [] }],
              blockers: [],
            }),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify(catalog([installedApp])), {
          status: 200,
        });
      }),
    );
    const onOpenDeviceApp = vi.fn();
    const user = userEvent.setup();

    render(
      <HubPanel open onClose={vi.fn()} onOpenDeviceApp={onOpenDeviceApp} />,
    );

    await user.click(await screen.findByRole("button", { name: "打开" }));
    expect(onOpenDeviceApp).toHaveBeenCalledWith(installedApp);
    expect(screen.getByText(/v1\.0\.0 · 影音/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "停止 演示应用" }));
    expect(
      await screen.findByRole("alertdialog", { name: "停止“演示应用”？" }),
    ).toHaveTextContent("停止全部受管服务");
    expect(
      screen.getByRole("button", { name: "卸载 演示应用" }),
    ).toBeInTheDocument();
  });

  it("shows sanitized post-install health and aggregate resources", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([installedApp])), {
            status: 200,
          });
        }
        if (url.endsWith(`/apps/${installedApp.id}`)) {
          return new Response(JSON.stringify(appDetail(installedApp)), {
            status: 200,
          });
        }
        return new Response(null, { status: 404 });
      }),
    );
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} />);

    await user.click(
      await screen.findByRole("button", { name: "查看 演示应用 详情" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "演示应用 应用详情",
    });
    expect(dialog).toHaveTextContent("运行健康");
    expect(dialog).toHaveTextContent("全部受管服务运行正常");
    expect(dialog).toHaveTextContent("3.2%");
    expect(dialog).toHaveTextContent("256 MB");
    expect(dialog).toHaveTextContent("不读取应用内容和原始日志");
    expect(dialog).not.toHaveTextContent("PASSWORD");
  });

  it("turns bounded incidents into recovery guidance and a safe restart plan", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([installedApp])), {
            status: 200,
          });
        }
        if (url.endsWith(`/apps/${installedApp.id}`)) {
          const detail = appDetail(installedApp);
          detail.appRuntime.status = "degraded";
          detail.appRuntime.services[0].state = "exited";
          detail.appRuntime.services[0].oomKilled = true;
          detail.appRuntime.services[0].exitCode = 137;
          detail.diagnostics.status = "attention";
          detail.diagnostics.incidents = [
            {
              code: "OOM_KILLED",
              severity: "critical",
              serviceId: "app",
              recovery: "restart",
            },
          ];
          return new Response(JSON.stringify(detail), { status: 200 });
        }
        if (url.endsWith("/plans/restart")) {
          return new Response(
            JSON.stringify({
              schema: "echo.hub.restart-plan.v1",
              planId: "f".repeat(64),
              operation: "restart",
              ready: true,
              requiresApproval: true,
              approvalAction: "hub.app.restart",
              approvalTarget: "f".repeat(64),
              current: { installation: installedApp.installation, runtime: {} },
              desired: {
                appId: installedApp.id,
                catalogDigest: "b".repeat(64),
                state: "running",
                serviceOrder: ["app"],
                dataVolumesRetained: true,
                nasDataRetained: true,
              },
              changes: [{ field: "services", before: [], after: [] }],
              blockers: [],
            }),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify(operationList()), { status: 200 });
      }),
    );
    const user = userEvent.setup();
    render(<HubPanel open onClose={vi.fn()} />);

    await user.click(
      await screen.findByRole("button", { name: "查看 演示应用 详情" }),
    );
    const details = await screen.findByRole("dialog", {
      name: "演示应用 应用详情",
    });
    expect(details).toHaveTextContent("内存达到上限，服务被系统终止");
    expect(details).toHaveTextContent("可执行安全重启");
    await user.click(within(details).getByRole("button", { name: "安全重启" }));

    expect(
      await screen.findByRole("alertdialog", { name: "安全重启“演示应用”？" }),
    ).toHaveTextContent("整组停止后按依赖顺序重新启动");
  });

  it("groups installed applications and available updates without another data store", async () => {
    const updateCandidate: HubApp = {
      ...updatableApp,
      id: "update-candidate",
      name: "Update Candidate",
      nameZh: "待更新应用",
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify(
              catalog([pendingApp, installedApp, updateCandidate]),
            ),
            { status: 200 },
          ),
        ),
    );
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "已安装 2" }));
    expect(screen.getByText("演示应用")).toBeInTheDocument();
    expect(screen.getByText("待更新应用")).toBeInTheDocument();
    expect(screen.queryByText("智能相册")).not.toBeInTheDocument();
    expect(screen.getByText(/2\/3 个应用/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "可更新 1" }));
    expect(screen.getByText("待更新应用")).toBeInTheDocument();
    expect(screen.queryByText("演示应用")).not.toBeInTheDocument();
    expect(screen.getByText(/1\/3 个应用/)).toBeInTheDocument();
  });

  it("reviews a deterministic plan, requires password approval, then applies it", async () => {
    const plan: HubInstallPlan = {
      schema: "echo.hub.install-plan.v1",
      planId: "c".repeat(64),
      operation: "install",
      ready: true,
      requiresApproval: true,
      approvalAction: "hub.app.install",
      approvalTarget: "c".repeat(64),
      current: installableApp.installation,
      desired: {
        appId: installableApp.id,
        architecture: "amd64",
        catalogDigest: "b".repeat(64),
        package: installableApp.package,
      },
      changes: [
        { field: "image", before: null, after: installableApp.package?.image },
      ],
      blockers: [],
      resourcePreflight: resourcePreflight(installableApp),
    };
    const installResult = {
      schema: "echo.hub.install-result.v1",
      appId: installableApp.id,
      planId: plan.planId,
      catalogDigest: "b".repeat(64),
      containerId: "d".repeat(12),
      state: "running",
      image: installableApp.package?.image,
      revealedSecrets: { "admin-password": "one-time-admin-secret" },
    };
    let queued = false;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([installableApp])), {
            status: 200,
          });
        }
        if (url.endsWith("/plans/install") && init?.method === "POST") {
          return new Response(JSON.stringify(plan), { status: 200 });
        }
        if (url.endsWith("/approvals")) {
          return new Response(
            JSON.stringify({
              approvalToken: "approval-once",
              expiresIn: 60,
              action: "hub.app.install",
              target: plan.planId,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/operations?")) {
          return new Response(
            JSON.stringify(
              operationList(
                queued
                  ? [
                      hubOperation({
                        operation: "install",
                        appId: installableApp.id,
                        planId: plan.planId,
                        status: "succeeded",
                        result: {
                          ...installResult,
                          revealedSecrets: undefined,
                        },
                        credentialsAvailable: true,
                      }),
                    ]
                  : [],
              ),
            ),
            { status: 200 },
          );
        }
        if (url.endsWith("/plans/install/queue")) {
          queued = true;
          return new Response(
            JSON.stringify(
              hubOperation({
                operation: "install",
                appId: installableApp.id,
                planId: plan.planId,
                status: "queued",
              }),
            ),
            { status: 202 },
          );
        }
        if (url.endsWith("/credentials/claim")) {
          return new Response(
            JSON.stringify({
              schema: "echo.hub.operation-credentials.v1",
              operationId: "1".repeat(32),
              credentials: { "admin-password": "one-time-admin-secret" },
            }),
            { status: 200 },
          );
        }
        return new Response(null, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const onAppsChanged = vi.fn();
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} onAppsChanged={onAppsChanged} />);

    await user.click(await screen.findByRole("button", { name: "安装" }));
    expect(
      await screen.findByRole("alertdialog", { name: "安装“演示应用”？" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      "内存上限 2 GB · 1 个固定端口已核对",
    );
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      "Docker 可用 64 GB，预留 768 MB",
    );
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      "下载量来自受信 OCI 清单",
    );
    await user.type(
      screen.getByLabelText("设备管理员密码"),
      "correct-horse-battery-staple",
    );
    await user.click(screen.getByRole("button", { name: "确认安装" }));

    await waitFor(() => expect(onAppsChanged).toHaveBeenCalledOnce());
    expect(
      screen.getByRole("dialog", { name: "演示应用 初始凭据" }),
    ).toBeInTheDocument();
    expect(screen.getByText("one-time-admin-secret")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "我已保存，关闭" }));
    expect(screen.queryByText("one-time-admin-secret")).not.toBeInTheDocument();
    const approvalRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/approvals"),
    );
    const applyRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/plans/install/queue"),
    );
    expect(JSON.parse(String(approvalRequest?.[1]?.body))).toEqual({
      action: "hub.app.install",
      target: plan.planId,
      password: "correct-horse-battery-staple",
    });
    expect(applyRequest?.[1]?.headers).toEqual(
      expect.objectContaining({ "X-Echo-Approval": "approval-once" }),
    );
  });

  it("shows real image-layer and multi-image progress without a fake percentage", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([installableApp])), {
            status: 200,
          });
        }
        if (url.includes("/operations?")) {
          return new Response(
            JSON.stringify(
              operationList([
                hubOperation({
                  operation: "install",
                  appId: installableApp.id,
                  planId: "c".repeat(64),
                  status: "running",
                  progress: {
                    schema: "echo.hub.progress.v1",
                    stage: "pulling",
                    step: "pulling-image",
                    completed: 4,
                    total: 11,
                    unit: "layers",
                    item: 2,
                    items: 3,
                    sequence: 7,
                  },
                }),
              ]),
            ),
            { status: 200 },
          );
        }
        return new Response(null, { status: 404 });
      }),
    );

    render(<HubPanel open onClose={vi.fn()} />);

    expect(
      await screen.findByText("镜像层 4/11 · 镜像 2/3"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("shows host-LAN scope and hardware exclusions before approval", async () => {
    const plan: HubInstallPlan = {
      schema: "echo.hub.install-plan.v1",
      planId: "7".repeat(64),
      operation: "install",
      ready: true,
      requiresApproval: true,
      approvalAction: "hub.app.install",
      approvalTarget: "7".repeat(64),
      current: hostLanApp.installation,
      desired: {
        appId: hostLanApp.id,
        architecture: "amd64",
        catalogDigest: "b".repeat(64),
        package: null,
        bundle: hostLanApp.bundle,
      },
      changes: [
        {
          field: "networkModes",
          before: [],
          after: [{ id: "app", mode: "host" }],
        },
      ],
      blockers: [],
      resourcePreflight: resourcePreflight(hostLanApp),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([hostLanApp])), {
            status: 200,
          });
        }
        if (url.endsWith("/plans/install") && init?.method === "POST") {
          return new Response(JSON.stringify(plan), { status: 200 });
        }
        return new Response(null, { status: 404 });
      }),
    );
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} />);

    expect(
      await screen.findByText(/v2026\.8\.3 · 局域网发现 · 无硬件直通/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "安装" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "安装“智能家庭”？",
    });
    expect(dialog).toHaveTextContent("mDNS/SSDP");
    expect(dialog).toHaveTextContent("当前不支持 USB、Bluetooth 或 Zigbee");
    expect(dialog).toHaveTextContent("局域网发现模式");
  });

  it("uninstalls only the container after review and explains that data is retained", async () => {
    const plan: HubUninstallPlan = {
      schema: "echo.hub.uninstall-plan.v1",
      planId: "e".repeat(64),
      operation: "uninstall",
      ready: true,
      requiresApproval: true,
      approvalAction: "hub.app.uninstall",
      approvalTarget: "e".repeat(64),
      current: installedApp.installation,
      desired: {
        appId: installedApp.id,
        catalogDigest: "b".repeat(64),
        containerRemoved: true,
        dataVolumesRetained: true,
        nasDataRetained: true,
      },
      changes: [
        {
          field: "container",
          before: installedApp.installation.containerId,
          after: null,
        },
      ],
      blockers: [],
    };
    const uninstallResult = {
      schema: "echo.hub.uninstall-result.v1",
      appId: installedApp.id,
      planId: plan.planId,
      catalogDigest: "b".repeat(64),
      containerId: installedApp.installation.containerId,
      state: "not-installed",
      dataVolumesRetained: true,
      nasDataRetained: true,
    };
    let queued = false;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([installedApp])), {
            status: 200,
          });
        }
        if (url.endsWith("/plans/uninstall") && init?.method === "POST") {
          return new Response(JSON.stringify(plan), { status: 200 });
        }
        if (url.endsWith("/approvals")) {
          return new Response(
            JSON.stringify({
              approvalToken: "approval-remove-once",
              expiresIn: 60,
              action: "hub.app.uninstall",
              target: plan.planId,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/operations?")) {
          return new Response(
            JSON.stringify(
              operationList(
                queued
                  ? [
                      hubOperation({
                        operation: "uninstall",
                        appId: installedApp.id,
                        planId: plan.planId,
                        status: "succeeded",
                        result: uninstallResult,
                      }),
                    ]
                  : [],
              ),
            ),
            { status: 200 },
          );
        }
        if (url.endsWith("/plans/uninstall/queue")) {
          queued = true;
          return new Response(
            JSON.stringify(
              hubOperation({
                operation: "uninstall",
                appId: installedApp.id,
                planId: plan.planId,
                status: "queued",
              }),
            ),
            { status: 202 },
          );
        }
        return new Response(null, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const onAppsChanged = vi.fn();
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} onAppsChanged={onAppsChanged} />);

    await user.click(
      await screen.findByRole("button", { name: "卸载 演示应用" }),
    );
    expect(
      await screen.findByRole("alertdialog", { name: "卸载“演示应用”？" }),
    ).toHaveTextContent("应用配置卷和 NAS 文件都会保留");
    await user.type(screen.getByLabelText("设备管理员密码"), "remove-safely");
    await user.click(screen.getByRole("button", { name: "确认卸载" }));

    await waitFor(() => expect(onAppsChanged).toHaveBeenCalledOnce());
    const approvalRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/approvals"),
    );
    const applyRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/plans/uninstall/queue"),
    );
    expect(JSON.parse(String(approvalRequest?.[1]?.body))).toEqual({
      action: "hub.app.uninstall",
      target: plan.planId,
      password: "remove-safely",
    });
    expect(applyRequest?.[1]?.headers).toEqual(
      expect.objectContaining({ "X-Echo-Approval": "approval-remove-once" }),
    );
  });

  it("updates through a reviewed rollback plan and retains application data", async () => {
    const plan: HubUpdatePlan = {
      schema: "echo.hub.update-plan.v1",
      planId: "f".repeat(64),
      operation: "update",
      ready: true,
      requiresApproval: true,
      approvalAction: "hub.app.update",
      approvalTarget: "f".repeat(64),
      current: updatableApp.installation,
      desired: {
        appId: updatableApp.id,
        architecture: "amd64",
        catalogDigest: "b".repeat(64),
        packageDigest: "1".repeat(64),
        package: updatableApp.package,
        appDataVolumesRetained: true,
        nasDataRetained: true,
        runningStatePreserved: true,
      },
      changes: [
        {
          field: "image",
          before: updatableApp.installation.image,
          after: updatableApp.package?.image,
        },
      ],
      blockers: [],
    };
    const updateResult = {
      schema: "echo.hub.update-result.v1",
      appId: updatableApp.id,
      planId: plan.planId,
      catalogDigest: "b".repeat(64),
      previousContainerId: updatableApp.installation.containerId,
      containerId: "a".repeat(12),
      previousImage: updatableApp.installation.image,
      image: updatableApp.package?.image,
      state: "running",
      dataVolumesRetained: true,
      nasDataRetained: true,
    };
    let queued = false;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/catalog")) {
          return new Response(JSON.stringify(catalog([updatableApp])), {
            status: 200,
          });
        }
        if (url.endsWith("/plans/update") && init?.method === "POST") {
          return new Response(JSON.stringify(plan), { status: 200 });
        }
        if (url.endsWith("/approvals")) {
          return new Response(
            JSON.stringify({
              approvalToken: "approval-update-once",
              expiresIn: 60,
              action: "hub.app.update",
              target: plan.planId,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/operations?")) {
          return new Response(
            JSON.stringify(
              operationList(
                queued
                  ? [
                      hubOperation({
                        operation: "update",
                        appId: updatableApp.id,
                        planId: plan.planId,
                        status: "succeeded",
                        result: updateResult,
                      }),
                    ]
                  : [],
              ),
            ),
            { status: 200 },
          );
        }
        if (url.endsWith("/plans/update/queue")) {
          queued = true;
          return new Response(
            JSON.stringify(
              hubOperation({
                operation: "update",
                appId: updatableApp.id,
                planId: plan.planId,
                status: "queued",
              }),
            ),
            { status: 202 },
          );
        }
        return new Response(null, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const onAppsChanged = vi.fn();
    const user = userEvent.setup();

    render(<HubPanel open onClose={vi.fn()} onAppsChanged={onAppsChanged} />);

    await user.click(await screen.findByRole("button", { name: "更新" }));
    expect(screen.getByText(/v0\.9\.0 → v1\.0\.0/)).toBeInTheDocument();
    expect(
      await screen.findByRole("alertdialog", { name: "更新“演示应用”？" }),
    ).toHaveTextContent("失败时恢复旧容器");
    expect(
      screen.getByRole("button", { name: "卸载 演示应用" }),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("设备管理员密码"), "update-safely");
    await user.click(screen.getByRole("button", { name: "确认更新" }));

    await waitFor(() => expect(onAppsChanged).toHaveBeenCalledOnce());
    const approvalRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/approvals"),
    );
    const applyRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/plans/update/queue"),
    );
    expect(JSON.parse(String(approvalRequest?.[1]?.body))).toEqual({
      action: "hub.app.update",
      target: plan.planId,
      password: "update-safely",
    });
    expect(applyRequest?.[1]?.headers).toEqual(
      expect.objectContaining({ "X-Echo-Approval": "approval-update-once" }),
    );
  });

  it("unifies standalone and workbench apps while keeping Agent capabilities separate", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/appliance/hub/catalog")) {
        return new Response(JSON.stringify(catalog([pendingApp])), {
          status: 200,
        });
      }
      if (url.includes("/api/appliance/agent-assets/catalog")) {
        return new Response(
          JSON.stringify({
            schema: "echo.agent-assets.v6",
            available: true,
            plugins: [
              {
                id: "documents",
                plugin: "documents",
                name_zh: "文档助手",
                description: "创建与整理文档",
                source: "codex",
                kind: "workbench",
                version: "1.0.0",
                release_summary: "1.1.0：新增受信版本说明。",
                host_api: ">=0.2,<0.3",
                permissions: ["content.read", "content.write"],
                authModes: ["oauth"],
                dependencies: [],
                runtimeDependencies: [],
                connectors: ["documents-app"],
              },
              {
                id: "wecom",
                plugin: "wecom",
                name_zh: "企业微信",
                description: "连接企业微信消息",
                source: "workbuddy",
                kind: "connector",
                version: "1.0.0",
                release_summary: "1.0.0：首次纳入受信连接器目录。",
                host_api: ">=0.2,<0.3",
                permissions: ["account.credentials", "network.remote"],
                authModes: ["token"],
                dependencies: [],
                runtimeDependencies: ["wecom.tgz"],
                connectors: [],
              },
            ],
            skills: [],
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
                releaseSummary: "1.1.0：新增受信版本说明。",
                version: "1.0.0",
                availableVersion: "1.1.0",
                permissions: ["content.read", "content.write"],
                permissionsGranted: ["content.read", "content.write"],
                permissionReviewRequired: false,
                permissionActive: true,
                authModes: ["oauth"],
                dependencies: [],
                runtimeDependencies: [],
                connectors: ["documents-app"],
              },
              {
                id: "wecom",
                catalogId: "wecom",
                kind: "connector",
                source: "cloud",
                state: "available",
                installed: false,
                enabled: false,
                rollbackAvailable: false,
                recoveryCount: 0,
                trustLevel: "catalog",
                integrityVerified: false,
                publisherVerified: false,
                compatibility: "not_checked",
                releaseSummary: "1.0.0：首次纳入受信连接器目录。",
                availableVersion: "1.0.0",
                permissions: ["account.credentials", "network.remote"],
                permissionsGranted: [],
                permissionReviewRequired: false,
                permissionActive: false,
                authModes: ["token"],
                dependencies: [],
                runtimeDependencies: ["wecom.tgz"],
                connectors: [],
              },
            ],
            unavailableSources: [],
          }),
          { status: 200 },
        );
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<HubPanel open onClose={vi.fn()} />);

    expect(await screen.findByText("文档助手")).toBeInTheDocument();
    expect(screen.getByText("智能相册")).toBeInTheDocument();
    expect(screen.getAllByText("独立窗口").length).toBeGreaterThan(0);
    expect(screen.getAllByText("工作台内嵌").length).toBeGreaterThan(0);
    expect(screen.queryByText("企业微信")).not.toBeInTheDocument();
    expect(screen.getByText("可更新")).toBeInTheDocument();
    expect(screen.getByText("可回滚")).toBeInTheDocument();
    expect(screen.getByText("2 个恢复点")).toBeInTheDocument();
    expect(screen.getByText("发布者已验证")).toBeInTheDocument();
    expect(screen.getByText("当前兼容")).toBeInTheDocument();
    expect(screen.getByText("2 项权限")).toBeInTheDocument();
    expect(screen.getByText("需认证")).toBeInTheDocument();
    expect(screen.getByText("1 个依赖")).toBeInTheDocument();
    expect(screen.getByText("版本说明（已验证）")).toBeInTheDocument();
    expect(screen.getByTitle("1.1.0：新增受信版本说明。")).toBeInTheDocument();
    expect(screen.getByTitle("需要 Agent >=0.2,<0.3")).toBeInTheDocument();
    expect(screen.getByText(/同一生命周期/)).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "查看“文档助手”详情" }),
    );
    const detail = screen.getByRole("dialog", { name: "文档助手 详情" });
    expect(within(detail).getByText("需要的权限")).toBeInTheDocument();
    expect(within(detail).getByText("读取内容")).toBeInTheDocument();
    expect(within(detail).getByText("修改内容")).toBeInTheDocument();
    expect(within(detail).getByText("外部连接器")).toBeInTheDocument();
    expect(within(detail).getByText("documents-app")).toBeInTheDocument();
    expect(
      within(detail).getByText("发布者签名已验证 · Echo Publisher"),
    ).toBeInTheDocument();
    await user.click(
      within(detail).getByRole("button", { name: "关闭 Agent 能力详情" }),
    );

    await user.click(screen.getByRole("button", { name: "Agent 能力" }));
    expect(await screen.findByText("企业微信")).toBeInTheDocument();
    expect(screen.queryByText("文档助手")).not.toBeInTheDocument();
    expect(screen.getByText("目录版本说明")).toBeInTheDocument();
    expect(screen.getByText(/能力目录归 Agent 所有/)).toBeInTheDocument();
  });

  it("installs a connector inside Hub through a reviewed Agent plan", async () => {
    const planId = "d".repeat(64);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/appliance/hub/catalog")) {
        return new Response(JSON.stringify(catalog([pendingApp])), {
          status: 200,
        });
      }
      if (url.includes("/api/appliance/hub/operations")) {
        return new Response(JSON.stringify(operationList()), { status: 200 });
      }
      if (url.includes("/api/appliance/agent-assets/catalog")) {
        return new Response(
          JSON.stringify({
            schema: "echo.agent-assets.v6",
            available: true,
            plugins: [
              {
                id: "wecom",
                plugin: "wecom",
                name_zh: "企业微信",
                description: "连接企业微信消息",
                source: "workbuddy",
                kind: "connector",
                version: "1.0.0",
                permissions: ["account.credentials", "network.remote"],
                authModes: ["token"],
                dependencies: [],
                runtimeDependencies: [],
                connectors: [],
              },
            ],
            skills: [],
            installed: { plugins: [], skills: [] },
            pluginStates: [
              {
                id: "wecom",
                catalogId: "wecom",
                kind: "connector",
                source: "cloud",
                state: "available",
                installed: false,
                enabled: false,
                rollbackAvailable: false,
                recoveryCount: 0,
                trustLevel: "catalog",
                integrityVerified: false,
                publisherVerified: false,
                compatibility: "compatible",
                permissions: ["account.credentials", "network.remote"],
                permissionsGranted: [],
                permissionReviewRequired: false,
                permissionActive: false,
                authModes: ["token"],
                dependencies: [],
                runtimeDependencies: [],
                connectors: [],
              },
            ],
            unavailableSources: [],
          }),
          { status: 200 },
        );
      }
      if (url.endsWith("/api/appliance/agent-capabilities/plans/install")) {
        return new Response(
          JSON.stringify({
            schema: "echo.capability_install_plan.v1",
            service_schema: "echo.capability-service.v1",
            capability_id: "wecom",
            plan_id: planId,
            can_install: true,
            permissions: ["account.credentials", "network.remote"],
            blockers: [],
            changes: ["verify_publisher_signature"],
          }),
          { status: 200 },
        );
      }
      if (url.endsWith("/api/appliance/approvals")) {
        return new Response(
          JSON.stringify({
            approvalToken: "agent-install-once",
            expiresIn: 90,
            action: "agent.capability.install",
            target: planId,
          }),
          { status: 200 },
        );
      }
      if (
        url.endsWith("/api/appliance/agent-capabilities/plans/install/apply")
      ) {
        return new Response(
          JSON.stringify({
            schema: "echo.capability-service.v1",
            operation: "install",
            capability: { id: "wecom", installed: true, enabled: false },
            result: { installed: true },
          }),
          { status: 200 },
        );
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<HubPanel open onClose={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "Agent 能力" }));
    await user.click(
      await screen.findByRole("button", { name: "查看“企业微信”详情" }),
    );
    const detail = screen.getByRole("dialog", { name: "企业微信 详情" });
    await user.click(
      within(detail).getByRole("button", { name: "安装到设备" }),
    );
    const approvalDialog = await screen.findByRole("alertdialog");
    expect(within(approvalDialog).getByText(/发布者签名/)).toBeInTheDocument();
    await user.type(
      within(approvalDialog).getByLabelText("设备管理员密码"),
      "install-agent-capability",
    );
    await user.click(
      within(approvalDialog).getByRole("button", { name: "确认安装" }),
    );

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith(
            "/api/appliance/agent-capabilities/plans/install/apply",
          ),
        ),
      ).toBe(true);
    });
    const approvalRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/api/appliance/approvals"),
    );
    const applyRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith(
        "/api/appliance/agent-capabilities/plans/install/apply",
      ),
    );
    expect(JSON.parse(String(approvalRequest?.[1]?.body))).toEqual({
      action: "agent.capability.install",
      target: planId,
      password: "install-agent-capability",
    });
    expect(applyRequest?.[1]?.headers).toEqual(
      expect.objectContaining({ "X-Echo-Approval": "agent-install-once" }),
    );
  });

  it("connects a principal-scoped Agent account directly inside Hub", async () => {
    const secret = "current-user-access-token";
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/appliance/hub/catalog")) {
        return new Response(JSON.stringify(catalog([pendingApp])), {
          status: 200,
        });
      }
      if (url.includes("/api/appliance/hub/operations")) {
        return new Response(JSON.stringify(operationList()), { status: 200 });
      }
      if (url.includes("/api/appliance/agent-assets/catalog")) {
        return new Response(
          JSON.stringify({
            schema: "echo.agent-assets.v6",
            available: true,
            plugins: [
              {
                id: "wecom",
                plugin: "wecom",
                name_zh: "企业微信",
                description: "连接企业微信消息",
                source: "workbuddy",
                kind: "connector",
                version: "1.0.0",
                permissions: ["account.credentials", "network.remote"],
                authModes: ["token"],
                dependencies: [],
                runtimeDependencies: [],
                connectors: [],
              },
            ],
            skills: [],
            installed: { plugins: ["wecom"], skills: [] },
            pluginStates: [
              {
                id: "wecom",
                catalogId: "wecom",
                kind: "connector",
                source: "cloud",
                state: "enabled",
                installed: true,
                enabled: true,
                rollbackAvailable: false,
                recoveryCount: 0,
                trustLevel: "publisher",
                integrityVerified: true,
                publisherVerified: true,
                compatibility: "compatible",
                permissions: ["account.credentials", "network.remote"],
                permissionsGranted: ["account.credentials", "network.remote"],
                permissionReviewRequired: false,
                permissionActive: true,
                authModes: ["token"],
                dependencies: [],
                runtimeDependencies: [],
                connectors: [],
              },
            ],
            unavailableSources: [],
          }),
          { status: 200 },
        );
      }
      if (url.endsWith("/wecom/connection-profile")) {
        return new Response(
          JSON.stringify({
            schema: "echo.capability-service.v1",
            capability_id: "wecom",
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
      if (url.endsWith("/api/appliance/agent-capabilities/connect")) {
        return new Response(
          JSON.stringify({
            schema: "echo.capability-service.v1",
            operation: "connect",
            capability: { id: "wecom", installed: true, enabled: true },
            result: { connected: true },
          }),
          { status: 200 },
        );
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<HubPanel open onClose={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "Agent 能力" }));
    await user.click(
      await screen.findByRole("button", { name: "查看“企业微信”详情" }),
    );
    const detail = screen.getByRole("dialog", { name: "企业微信 详情" });
    await user.click(within(detail).getByRole("button", { name: "连接账户" }));

    const connection = await screen.findByRole("dialog", {
      name: "企业微信 账户连接",
    });
    await user.type(within(connection).getByLabelText("访问令牌"), secret);
    await user.click(
      within(connection).getByRole("button", { name: "保存并连接" }),
    );

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "企业微信 账户连接" }),
      ).not.toBeInTheDocument();
    });
    const connectRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/api/appliance/agent-capabilities/connect"),
    );
    expect(JSON.parse(String(connectRequest?.[1]?.body))).toEqual({
      capabilityId: "wecom",
      tokens: { access_token: secret },
    });
  });
});
