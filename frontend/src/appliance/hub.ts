import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type HubInstallBlocker =
  | "PACKAGE_NOT_PUBLISHED"
  | "ARCHITECTURE_UNSUPPORTED"
  | "DOCKER_RUNTIME_UNAVAILABLE"
  | "DOCKER_STORAGE_UNAVAILABLE"
  | "DOCKER_STORAGE_INSUFFICIENT"
  | "PORT_IN_USE"
  | "ALREADY_INSTALLED"
  | "NOT_INSTALLED"
  | "INSTALLATION_AMBIGUOUS"
  | "INSTALLATION_NOT_MANAGED"
  | "ALREADY_CURRENT"
  | "ALREADY_RUNNING"
  | "ALREADY_STOPPED"
  | "RUNTIME_STATE_UNAVAILABLE"
  | "UPGRADE_PATH_UNSUPPORTED"
  | "REQUIRED_PROVIDER_UNAVAILABLE";

export type HubPort = {
  container: number;
  host: number;
  protocol: "tcp" | "udp";
};

export type HubPackage = {
  schema: "echo.hub.docker-package.v1";
  image: string;
  architectures: string[];
  ports: HubPort[];
  volumes: Array<{
    source: "app-data" | "nas-root";
    name: string;
    target: string;
    readOnly: boolean;
  }>;
  environment: Record<string, string>;
  runtime: {
    memoryMiB: number;
    pids: number;
    shmSizeMiB: number;
    readOnlyRootfs: boolean;
  };
};

export type HubBundlePackage = {
  schema: "echo.hub.bundle-package.v1";
  architectures: string[];
  publicService: string;
  providers?: Array<"lan-discovery">;
  networks: Array<{ name: string; internal: boolean }>;
  volumes: Array<{
    name: string;
    source: "app-data" | "nas-data";
    relativePath: string | null;
    retention: "retain";
    snapshotOnUpdate: boolean;
  }>;
  secrets: Array<{
    name: string;
    generation: "random-base64url" | "random-alphanumeric";
    bytes: number;
    revealOnce: boolean;
  }>;
  services: Array<{
    id: string;
    role: "app" | "database" | "cache" | "worker";
    version: string;
    image: string;
    dependsOn: string[];
    networks: string[];
    networkMode?: "bridge" | "host";
    ports: HubPort[];
    mounts: Array<{ volume: string; target: string; readOnly: boolean }>;
    secrets: Array<{ secret: string; target: string }>;
    secretEnvironment: Record<string, string>;
    environment: Record<string, string>;
    entrypoint: string[];
    command: string[];
    healthcheck: {
      command: string[];
      intervalSeconds: number;
      timeoutSeconds: number;
      retries: number;
      startPeriodSeconds: number;
    } | null;
    runtime: {
      profile: "unprivileged" | "data-root-dropper" | "web-root-dropper";
      memoryMiB: number;
      pids: number;
      shmSizeMiB: number;
      readOnlyRootfs: boolean;
    };
  }>;
  upgradePolicy: {
    applicationVersion: string;
    maxMajorStep: 1;
    snapshotVolumes: string[];
    serviceOrder: string[];
  };
};

export type HubApp = {
  id: string;
  name: string;
  nameZh: string;
  version: string;
  summary: string;
  category: string;
  icon: string;
  sourceUrl: string;
  featured: boolean;
  imageStorage: {
    schema: "echo.hub.image-storage.v1";
    architectures: Record<string, { downloadBytes: number; blobCount: number }>;
  } | null;
  package: HubPackage | null;
  bundle?: HubBundlePackage | null;
  integrationStatus: "available" | "integration-pending";
  integrationNote: string;
  installation: {
    installed: boolean;
    containerId: string | null;
    state: string;
    status: string;
    image: string | null;
    version: string | null;
  };
  installable: boolean;
  installBlockers: HubInstallBlocker[];
  updateAvailable: boolean;
};

export type HubCatalogResponse = {
  schema: "echo.hub.catalog-response.v1";
  version: string;
  digest: string;
  publisher: { id: string; name: string };
  architecture: string;
  runtime: { available: boolean; error: string | null };
  total: number;
  apps: HubApp[];
};

export type HubResourcePreflight = {
  schema: "echo.hub.resource-preflight.v1";
  readyForInstall: boolean;
  blockingIssues: HubInstallBlocker[];
  checks: Array<{
    id:
      | "architecture"
      | "docker-runtime"
      | "docker-storage"
      | "ports"
      | "providers"
      | "nas-capacity";
    status:
      | "pass"
      | "fail"
      | "unavailable"
      | "mismatch"
      | "observed"
      | "not-requested";
    blocking: boolean;
  }>;
  runtime: {
    serviceCount: number;
    memoryLimitMiB: number;
    pidsLimit: number;
    shmLimitMiB: number;
    healthcheckedServices: number;
  };
  network: {
    mode: "bridge" | "host";
    ports: Array<HubPort & { status: "available" | "owned" | "conflict" }>;
    requiredProviders: Array<"lan-discovery">;
    providersReady: boolean;
  };
  storage: {
    appDataVolumes: number;
    nasVolumes: number;
    nasAccess: "none" | "read-only" | "read-write";
    snapshotVolumes: number;
    nasCapacity: {
      status: "observed" | "unavailable" | "not-requested";
      totalBytes: number | null;
      freeBytes: number | null;
      usedPercent: number | null;
    };
    imageStorage: {
      status: "sufficient" | "insufficient" | "unavailable" | "mismatch";
      downloadBytes: number | null;
      blobCount: number | null;
      requiredFreeBytes: number | null;
      reservePolicy: "compressed-times-three-or-plus-512MiB";
      capacity: {
        schema: "echo.hub.docker-storage.v1";
        status: "observed" | "unavailable" | "mismatch";
        totalBytes: number | null;
        freeBytes: number | null;
        usedPercent: number | null;
      };
    };
  };
  notices: Array<
    | "HOST_LAN"
    | "NAS_READ_WRITE"
    | "NAS_READ_ONLY"
    | "MULTI_SERVICE"
    | "ONE_TIME_CREDENTIALS"
  >;
};

export type HubAppRuntime = {
  schema: "echo.hub.runtime.v1";
  status:
    | "healthy"
    | "degraded"
    | "starting"
    | "stopped"
    | "not-installed"
    | "unavailable";
  summary: {
    serviceCount: number;
    runningServices: number;
    healthyServices: number;
    restartCount: number;
    cpuPercent: number | null;
    memoryUsageBytes: number | null;
    memoryLimitBytes: number | null;
    pids: number | null;
  };
  services: Array<{
    id: string;
    role: "app" | "database" | "cache" | "worker";
    public: boolean;
    state:
      | "created"
      | "running"
      | "paused"
      | "restarting"
      | "removing"
      | "exited"
      | "dead"
      | "unknown";
    health: "healthy" | "unhealthy" | "starting" | "not-configured" | "unknown";
    restartCount: number;
    oomKilled: boolean;
    exitCode: number | null;
    cpuPercent: number | null;
    memoryUsageBytes: number | null;
    memoryLimitBytes: number | null;
    pids: number | null;
  }>;
};

export type HubAppDetailResponse = {
  schema: "echo.hub.app-detail.v1";
  catalogDigest: string;
  architecture: string;
  runtime: { available: boolean; error: string | null };
  appRuntime: HubAppRuntime;
  diagnostics: HubDiagnostics;
  app: HubApp;
  resourcePreflight: HubResourcePreflight;
};

export type HubDiagnostics = {
  schema: "echo.hub.diagnostics.v1";
  status:
    | "ok"
    | "attention"
    | "observing"
    | "stopped"
    | "not-installed"
    | "unavailable";
  incidents: Array<{
    code:
      | "OOM_KILLED"
      | "HEALTHCHECK_FAILED"
      | "RESTART_LOOP"
      | "CRASHED"
      | "SERVICE_STOPPED"
      | "STATE_UNAVAILABLE";
    severity: "warning" | "error" | "critical";
    serviceId: string;
    recovery: "restart" | "inspect";
  }>;
};

export type HubInstallPlan = {
  schema: "echo.hub.install-plan.v1";
  planId: string;
  operation: "install";
  ready: boolean;
  requiresApproval: boolean;
  approvalAction: "hub.app.install" | null;
  approvalTarget: string | null;
  current: HubApp["installation"];
  desired: {
    appId: string;
    architecture: string;
    catalogDigest: string;
    package: HubPackage | null;
    bundle?: HubBundlePackage | null;
  };
  changes: Array<{ field: string; before: unknown; after: unknown }>;
  blockers: Array<{ code: HubInstallBlocker; message: string }>;
  resourcePreflight: HubResourcePreflight;
};

export type HubInstallResult = {
  schema: "echo.hub.install-result.v1";
  appId: string;
  planId: string;
  catalogDigest: string;
  containerId: string;
  state: string;
  image: string;
  serviceContainerIds?: Record<string, string>;
  revealedSecrets?: Record<string, string>;
};

export type HubUninstallPlan = {
  schema: "echo.hub.uninstall-plan.v1";
  planId: string;
  operation: "uninstall";
  ready: boolean;
  requiresApproval: boolean;
  approvalAction: "hub.app.uninstall" | null;
  approvalTarget: string | null;
  current: HubApp["installation"];
  desired: {
    appId: string;
    catalogDigest: string;
    containerRemoved: true;
    dataVolumesRetained: true;
    nasDataRetained: true;
  };
  changes: Array<{ field: string; before: unknown; after: unknown }>;
  blockers: Array<{ code: HubInstallBlocker; message: string }>;
};

export type HubUninstallResult = {
  schema: "echo.hub.uninstall-result.v1";
  appId: string;
  planId: string;
  catalogDigest: string;
  containerId: string;
  state: "not-installed";
  dataVolumesRetained: true;
  nasDataRetained: true;
};

export type HubUpdatePlan = {
  schema: "echo.hub.update-plan.v1";
  planId: string;
  operation: "update";
  ready: boolean;
  requiresApproval: boolean;
  approvalAction: "hub.app.update" | null;
  approvalTarget: string | null;
  current: HubApp["installation"];
  desired: {
    appId: string;
    architecture: string;
    catalogDigest: string;
    packageDigest: string | null;
    package: HubPackage | null;
    bundle?: HubBundlePackage | null;
    appDataVolumesRetained: true;
    nasDataRetained: true;
    runningStatePreserved: true;
  };
  changes: Array<{ field: string; before: unknown; after: unknown }>;
  blockers: Array<{ code: HubInstallBlocker; message: string }>;
};

export type HubUpdateResult = {
  schema: "echo.hub.update-result.v1";
  appId: string;
  planId: string;
  catalogDigest: string;
  previousContainerId: string;
  containerId: string;
  previousImage: string;
  image: string;
  state: "running" | "stopped";
  dataVolumesRetained: true;
  nasDataRetained: true;
};

export type HubControlOperation = "start" | "stop" | "restart";

export type HubControlPlan = {
  schema:
    | "echo.hub.start-plan.v1"
    | "echo.hub.stop-plan.v1"
    | "echo.hub.restart-plan.v1";
  planId: string;
  operation: HubControlOperation;
  ready: boolean;
  requiresApproval: boolean;
  approvalAction: "hub.app.start" | "hub.app.stop" | "hub.app.restart" | null;
  approvalTarget: string | null;
  current: {
    installation: Pick<
      HubApp["installation"],
      "installed" | "containerId" | "state" | "image" | "version"
    >;
    runtime: {
      status: HubAppRuntime["status"];
      services: Array<
        Pick<
          HubAppRuntime["services"][number],
          "id" | "state" | "health" | "restartCount" | "oomKilled" | "exitCode"
        >
      >;
    };
  };
  desired: {
    appId: string;
    catalogDigest: string;
    state: "running" | "stopped";
    serviceOrder: string[];
    dataVolumesRetained: true;
    nasDataRetained: true;
  };
  changes: Array<{ field: string; before: unknown; after: unknown }>;
  blockers: Array<{ code: HubInstallBlocker; message: string }>;
};

export type HubControlResult = {
  schema:
    | "echo.hub.start-result.v1"
    | "echo.hub.stop-result.v1"
    | "echo.hub.restart-result.v1";
  appId: string;
  planId: string;
  catalogDigest: string;
  containerId: string;
  state: "running" | "stopped";
  serviceCount: number;
  dataVolumesRetained: true;
  nasDataRetained: true;
};

export type HubOperationStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "interrupted";

export type HubOperationProgress = {
  schema: "echo.hub.progress.v1";
  stage:
    | "queued"
    | "validating"
    | "pulling"
    | "preparing"
    | "snapshotting"
    | "stopping"
    | "starting"
    | "verifying"
    | "switching"
    | "removing"
    | "rolling-back"
    | "completed"
    | "failed"
    | "interrupted";
  step: string;
  completed: number | null;
  total: number | null;
  unit: "layers" | "images" | "services" | "volumes" | null;
  item: number | null;
  items: number | null;
  sequence: number;
};

export type HubOperation = {
  schema: "echo.hub.operation.v1";
  operationId: string;
  operation: "install" | "update" | "uninstall" | "start" | "stop" | "restart";
  appId: string;
  planId: string;
  catalogDigest: string;
  status: HubOperationStatus;
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  error: {
    code: string;
    message: string;
    recoveryAction: string;
  } | null;
  warning: { code: string; message: string } | null;
  progress: HubOperationProgress;
  credentialsAvailable: boolean;
  result:
    | HubInstallResult
    | HubUpdateResult
    | HubUninstallResult
    | HubControlResult
    | null;
};

export type HubOperationsResponse = {
  schema: "echo.hub.operations.v1";
  operations: HubOperation[];
  total: number;
};

function isHubOperation(value: unknown): value is HubOperation {
  if (!isRecord(value)) return false;
  const validStatus = [
    "queued",
    "running",
    "succeeded",
    "failed",
    "interrupted",
  ].includes(String(value.status));
  const validOperation = [
    "install",
    "update",
    "uninstall",
    "start",
    "stop",
    "restart",
  ].includes(String(value.operation));
  const progress = value.progress;
  const validCounter = (counter: unknown) =>
    counter === null ||
    (typeof counter === "number" &&
      Number.isInteger(counter) &&
      counter >= 0 &&
      counter <= 4096);
  const validProgress =
    isRecord(progress) &&
    progress.schema === "echo.hub.progress.v1" &&
    [
      "queued",
      "validating",
      "pulling",
      "preparing",
      "snapshotting",
      "stopping",
      "starting",
      "verifying",
      "switching",
      "removing",
      "rolling-back",
      "completed",
      "failed",
      "interrupted",
    ].includes(String(progress.stage)) &&
    [
      "waiting",
      "checking-plan",
      "pulling-image",
      "creating-resources",
      "snapshotting-data",
      "stopping-services",
      "starting-services",
      "checking-health",
      "switching-services",
      "removing-services",
      "restoring-state",
      "finished",
      "operation-failed",
      "runtime-restarted",
    ].includes(String(progress.step)) &&
    validCounter(progress.completed) &&
    validCounter(progress.total) &&
    (progress.unit === null ||
      ["layers", "images", "services", "volumes"].includes(
        String(progress.unit),
      )) &&
    validCounter(progress.item) &&
    validCounter(progress.items) &&
    ((progress.completed === null && progress.total === null) ||
      (typeof progress.completed === "number" &&
        typeof progress.total === "number" &&
        progress.total > 0 &&
        progress.completed <= progress.total)) &&
    ((progress.item === null && progress.items === null) ||
      (typeof progress.item === "number" &&
        typeof progress.items === "number" &&
        progress.item > 0 &&
        progress.items > 0 &&
        progress.item <= progress.items)) &&
    typeof progress.sequence === "number" &&
    Number.isInteger(progress.sequence) &&
    progress.sequence >= 0;
  return (
    value.schema === "echo.hub.operation.v1" &&
    typeof value.operationId === "string" &&
    /^[0-9a-f]{32}$/.test(value.operationId) &&
    validOperation &&
    typeof value.appId === "string" &&
    typeof value.planId === "string" &&
    /^[0-9a-f]{64}$/.test(value.planId) &&
    typeof value.catalogDigest === "string" &&
    /^[0-9a-f]{64}$/.test(value.catalogDigest) &&
    validStatus &&
    typeof value.createdAt === "string" &&
    typeof value.updatedAt === "string" &&
    (value.startedAt === null || typeof value.startedAt === "string") &&
    (value.finishedAt === null || typeof value.finishedAt === "string") &&
    (value.error === null || isRecord(value.error)) &&
    (value.warning === null || isRecord(value.warning)) &&
    validProgress &&
    typeof value.credentialsAvailable === "boolean" &&
    (value.result === null || isRecord(value.result))
  );
}

async function hubError(response: Response, fallback: string): Promise<Error> {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (typeof detail === "string" && detail.trim()) return new Error(detail);
  if (detail && typeof detail === "object") {
    const message = detail.message;
    if (typeof message === "string" && message.trim())
      return new Error(message);
  }
  if (response.status === 401) return new Error("管理员会话已过期，请重新登录");
  return new Error(fallback);
}

export async function fetchHubCatalog(): Promise<HubCatalogResponse> {
  const response = await fetch("/api/appliance/hub/catalog", {
    headers: authHeader(),
  });
  if (!response.ok) throw await hubError(response, "Echo Hub 暂时无法连接");
  return (await response.json()) as HubCatalogResponse;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isBoundedInteger(value: unknown, maximum: number): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= maximum
  );
}

function isNullableCapacity(value: unknown): value is number | null {
  return (
    value === null ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0)
  );
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]) {
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => key in value);
}

function isNullableBoundedInteger(
  value: unknown,
  maximum: number,
): value is number | null {
  return value === null || isBoundedInteger(value, maximum);
}

function isRuntimeCpu(value: unknown): value is number | null {
  return (
    value === null ||
    (typeof value === "number" &&
      Number.isFinite(value) &&
      value >= 0 &&
      value <= 409_600)
  );
}

function isHubAppRuntime(value: unknown): value is HubAppRuntime {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["schema", "status", "summary", "services"]) ||
    value.schema !== "echo.hub.runtime.v1" ||
    ![
      "healthy",
      "degraded",
      "starting",
      "stopped",
      "not-installed",
      "unavailable",
    ].includes(String(value.status)) ||
    !isRecord(value.summary) ||
    !hasExactKeys(value.summary, [
      "serviceCount",
      "runningServices",
      "healthyServices",
      "restartCount",
      "cpuPercent",
      "memoryUsageBytes",
      "memoryLimitBytes",
      "pids",
    ]) ||
    !Array.isArray(value.services) ||
    value.services.length > 64
  )
    return false;
  const summary = value.summary;
  if (
    !isBoundedInteger(summary.serviceCount, 64) ||
    summary.serviceCount !== value.services.length ||
    !isBoundedInteger(summary.runningServices, summary.serviceCount) ||
    !isBoundedInteger(summary.healthyServices, summary.runningServices) ||
    !isBoundedInteger(summary.restartCount, 64_000_000) ||
    !isRuntimeCpu(summary.cpuPercent) ||
    !isNullableBoundedInteger(
      summary.memoryUsageBytes,
      Number.MAX_SAFE_INTEGER,
    ) ||
    !isNullableBoundedInteger(
      summary.memoryLimitBytes,
      Number.MAX_SAFE_INTEGER,
    ) ||
    !isNullableBoundedInteger(summary.pids, 67_108_864)
  )
    return false;
  if (
    ["not-installed", "unavailable"].includes(String(value.status)) &&
    value.services.length !== 0
  )
    return false;
  const serviceKeys = [
    "id",
    "role",
    "public",
    "state",
    "health",
    "restartCount",
    "oomKilled",
    "exitCode",
    "cpuPercent",
    "memoryUsageBytes",
    "memoryLimitBytes",
    "pids",
  ];
  return value.services.every(
    (service) =>
      isRecord(service) &&
      hasExactKeys(service, serviceKeys) &&
      typeof service.id === "string" &&
      /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(service.id) &&
      ["app", "database", "cache", "worker"].includes(String(service.role)) &&
      typeof service.public === "boolean" &&
      [
        "created",
        "running",
        "paused",
        "restarting",
        "removing",
        "exited",
        "dead",
        "unknown",
      ].includes(String(service.state)) &&
      [
        "healthy",
        "unhealthy",
        "starting",
        "not-configured",
        "unknown",
      ].includes(String(service.health)) &&
      isBoundedInteger(service.restartCount, 1_000_000) &&
      typeof service.oomKilled === "boolean" &&
      (service.exitCode === null ||
        (typeof service.exitCode === "number" &&
          Number.isInteger(service.exitCode) &&
          service.exitCode >= -2_147_483_648 &&
          service.exitCode <= 2_147_483_647)) &&
      isRuntimeCpu(service.cpuPercent) &&
      isNullableBoundedInteger(
        service.memoryUsageBytes,
        Number.MAX_SAFE_INTEGER,
      ) &&
      isNullableBoundedInteger(
        service.memoryLimitBytes,
        Number.MAX_SAFE_INTEGER,
      ) &&
      isNullableBoundedInteger(service.pids, 1_048_576),
  );
}

function isHubDiagnostics(value: unknown): value is HubDiagnostics {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["schema", "status", "incidents"]) ||
    value.schema !== "echo.hub.diagnostics.v1" ||
    ![
      "ok",
      "attention",
      "observing",
      "stopped",
      "not-installed",
      "unavailable",
    ].includes(String(value.status)) ||
    !Array.isArray(value.incidents) ||
    value.incidents.length > 64
  )
    return false;
  return value.incidents.every(
    (incident) =>
      isRecord(incident) &&
      hasExactKeys(incident, ["code", "severity", "serviceId", "recovery"]) &&
      [
        "OOM_KILLED",
        "HEALTHCHECK_FAILED",
        "RESTART_LOOP",
        "CRASHED",
        "SERVICE_STOPPED",
        "STATE_UNAVAILABLE",
      ].includes(String(incident.code)) &&
      ["warning", "error", "critical"].includes(String(incident.severity)) &&
      typeof incident.serviceId === "string" &&
      /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(incident.serviceId) &&
      ["restart", "inspect"].includes(String(incident.recovery)),
  );
}

function isHubResourcePreflight(value: unknown): value is HubResourcePreflight {
  if (!isRecord(value) || value.schema !== "echo.hub.resource-preflight.v1")
    return false;
  if (
    typeof value.readyForInstall !== "boolean" ||
    !Array.isArray(value.blockingIssues) ||
    value.blockingIssues.length > 16 ||
    !value.blockingIssues.every(
      (item) =>
        typeof item === "string" &&
        [
          "PACKAGE_NOT_PUBLISHED",
          "ARCHITECTURE_UNSUPPORTED",
          "DOCKER_RUNTIME_UNAVAILABLE",
          "DOCKER_STORAGE_UNAVAILABLE",
          "DOCKER_STORAGE_INSUFFICIENT",
          "PORT_IN_USE",
          "ALREADY_INSTALLED",
          "NOT_INSTALLED",
          "INSTALLATION_AMBIGUOUS",
          "INSTALLATION_NOT_MANAGED",
          "ALREADY_CURRENT",
          "UPGRADE_PATH_UNSUPPORTED",
          "REQUIRED_PROVIDER_UNAVAILABLE",
        ].includes(item),
    ) ||
    !Array.isArray(value.checks) ||
    value.checks.length > 8
  )
    return false;
  const checkIds = [
    "architecture",
    "docker-runtime",
    "docker-storage",
    "ports",
    "providers",
    "nas-capacity",
  ];
  const checkStatuses = [
    "pass",
    "fail",
    "unavailable",
    "mismatch",
    "observed",
    "not-requested",
  ];
  if (
    !value.checks.every(
      (check) =>
        isRecord(check) &&
        typeof check.id === "string" &&
        checkIds.includes(check.id) &&
        typeof check.status === "string" &&
        checkStatuses.includes(check.status) &&
        typeof check.blocking === "boolean",
    ) ||
    !isRecord(value.runtime) ||
    !isBoundedInteger(value.runtime.serviceCount, 64) ||
    !isBoundedInteger(value.runtime.memoryLimitMiB, 524288) ||
    !isBoundedInteger(value.runtime.pidsLimit, 131072) ||
    !isBoundedInteger(value.runtime.shmLimitMiB, 65536) ||
    !isBoundedInteger(value.runtime.healthcheckedServices, 64) ||
    !isRecord(value.network) ||
    !["bridge", "host"].includes(String(value.network.mode)) ||
    !Array.isArray(value.network.ports) ||
    value.network.ports.length > 32 ||
    !Array.isArray(value.network.requiredProviders) ||
    value.network.requiredProviders.length > 4 ||
    !value.network.requiredProviders.every(
      (item) => item === "lan-discovery",
    ) ||
    typeof value.network.providersReady !== "boolean"
  )
    return false;
  if (
    !value.network.ports.every(
      (port) =>
        isRecord(port) &&
        isBoundedInteger(port.container, 65535) &&
        port.container >= 1 &&
        isBoundedInteger(port.host, 65535) &&
        port.host >= 1 &&
        ["tcp", "udp"].includes(String(port.protocol)) &&
        ["available", "owned", "conflict"].includes(String(port.status)),
    ) ||
    !isRecord(value.storage) ||
    !isBoundedInteger(value.storage.appDataVolumes, 64) ||
    !isBoundedInteger(value.storage.nasVolumes, 64) ||
    !["none", "read-only", "read-write"].includes(
      String(value.storage.nasAccess),
    ) ||
    !isBoundedInteger(value.storage.snapshotVolumes, 64) ||
    !isRecord(value.storage.nasCapacity) ||
    !["observed", "unavailable", "not-requested"].includes(
      String(value.storage.nasCapacity.status),
    ) ||
    !isNullableCapacity(value.storage.nasCapacity.totalBytes) ||
    !isNullableCapacity(value.storage.nasCapacity.freeBytes) ||
    !isNullableCapacity(value.storage.nasCapacity.usedPercent) ||
    !isRecord(value.storage.imageStorage) ||
    !["sufficient", "insufficient", "unavailable", "mismatch"].includes(
      String(value.storage.imageStorage.status),
    ) ||
    !isNullableCapacity(value.storage.imageStorage.downloadBytes) ||
    !isNullableCapacity(value.storage.imageStorage.blobCount) ||
    !isNullableCapacity(value.storage.imageStorage.requiredFreeBytes) ||
    value.storage.imageStorage.reservePolicy !==
      "compressed-times-three-or-plus-512MiB" ||
    !Array.isArray(value.notices) ||
    value.notices.length > 8 ||
    !value.notices.every(
      (notice) =>
        typeof notice === "string" &&
        [
          "HOST_LAN",
          "NAS_READ_WRITE",
          "NAS_READ_ONLY",
          "MULTI_SERVICE",
          "ONE_TIME_CREDENTIALS",
        ].includes(notice),
    )
  )
    return false;
  const capacity = value.storage.nasCapacity;
  const imageStorage = value.storage.imageStorage;
  const dockerCapacity = imageStorage.capacity;
  if (
    !isRecord(dockerCapacity) ||
    dockerCapacity.schema !== "echo.hub.docker-storage.v1" ||
    !["observed", "unavailable", "mismatch"].includes(
      String(dockerCapacity.status),
    )
  )
    return false;
  if (
    (capacity.status === "observed" &&
      (!isBoundedInteger(capacity.totalBytes, Number.MAX_SAFE_INTEGER) ||
        capacity.totalBytes === 0 ||
        !isBoundedInteger(capacity.freeBytes, capacity.totalBytes) ||
        typeof capacity.usedPercent !== "number" ||
        !Number.isFinite(capacity.usedPercent) ||
        capacity.usedPercent < 0 ||
        capacity.usedPercent > 100)) ||
    (capacity.status !== "observed" &&
      (capacity.totalBytes !== null ||
        capacity.freeBytes !== null ||
        capacity.usedPercent !== null))
  )
    return false;
  if (
    (dockerCapacity.status === "observed" &&
      (!isBoundedInteger(dockerCapacity.totalBytes, Number.MAX_SAFE_INTEGER) ||
        dockerCapacity.totalBytes === 0 ||
        !isBoundedInteger(
          dockerCapacity.freeBytes,
          dockerCapacity.totalBytes,
        ) ||
        typeof dockerCapacity.usedPercent !== "number" ||
        !Number.isFinite(dockerCapacity.usedPercent) ||
        dockerCapacity.usedPercent < 0 ||
        dockerCapacity.usedPercent > 100)) ||
    (dockerCapacity.status !== "observed" &&
      (dockerCapacity.totalBytes !== null ||
        dockerCapacity.freeBytes !== null ||
        dockerCapacity.usedPercent !== null))
  )
    return false;
  const downloadBytes = imageStorage.downloadBytes;
  const blobCount = imageStorage.blobCount;
  const requiredFreeBytes = imageStorage.requiredFreeBytes;
  const hasImageAttestation = typeof downloadBytes === "number";
  if (
    (hasImageAttestation &&
      (!isBoundedInteger(downloadBytes, Number.MAX_SAFE_INTEGER) ||
        downloadBytes < 1024 ||
        !isBoundedInteger(blobCount, 4096) ||
        blobCount < 1 ||
        !isBoundedInteger(requiredFreeBytes, Number.MAX_SAFE_INTEGER) ||
        requiredFreeBytes < downloadBytes)) ||
    (!hasImageAttestation &&
      (blobCount !== null ||
        requiredFreeBytes !== null ||
        imageStorage.status !== "unavailable")) ||
    (imageStorage.status === "sufficient" &&
      (dockerCapacity.status !== "observed" ||
        typeof dockerCapacity.freeBytes !== "number" ||
        typeof requiredFreeBytes !== "number" ||
        dockerCapacity.freeBytes < requiredFreeBytes)) ||
    (imageStorage.status === "insufficient" &&
      (dockerCapacity.status !== "observed" ||
        typeof dockerCapacity.freeBytes !== "number" ||
        typeof requiredFreeBytes !== "number" ||
        dockerCapacity.freeBytes >= requiredFreeBytes)) ||
    (imageStorage.status === "mismatch" &&
      dockerCapacity.status !== "mismatch") ||
    (imageStorage.status === "unavailable" &&
      hasImageAttestation &&
      dockerCapacity.status !== "unavailable")
  )
    return false;
  return true;
}

export async function fetchHubAppDetail(
  appId: string,
): Promise<HubAppDetailResponse> {
  if (!/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(appId)) {
    throw new Error("应用标识无效");
  }
  const response = await fetch(
    `/api/appliance/hub/apps/${encodeURIComponent(appId)}`,
    { headers: authHeader() },
  );
  if (!response.ok) throw await hubError(response, "无法读取应用详情");
  const body: unknown = await response.json().catch(() => null);
  if (
    !isRecord(body) ||
    body.schema !== "echo.hub.app-detail.v1" ||
    typeof body.catalogDigest !== "string" ||
    !/^[0-9a-f]{64}$/.test(body.catalogDigest) ||
    typeof body.architecture !== "string" ||
    !/^[0-9A-Za-z_-]{1,32}$/.test(body.architecture) ||
    !isRecord(body.runtime) ||
    typeof body.runtime.available !== "boolean" ||
    !(
      body.runtime.error === null ||
      (typeof body.runtime.error === "string" &&
        body.runtime.error.length <= 512 &&
        !/[\u0000-\u001f\u007f]/.test(body.runtime.error))
    ) ||
    !isRecord(body.app) ||
    body.app.id !== appId ||
    !isHubAppRuntime(body.appRuntime) ||
    !isHubDiagnostics(body.diagnostics) ||
    !isHubResourcePreflight(body.resourcePreflight)
  ) {
    throw new Error("应用详情数据无效，请刷新后重试");
  }
  return body as HubAppDetailResponse;
}

export async function createHubInstallPlan(
  appId: string,
): Promise<HubInstallPlan> {
  const response = await fetch("/api/appliance/hub/plans/install", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ appId }),
  });
  if (!response.ok) throw await hubError(response, "无法生成安全安装计划");
  return (await response.json()) as HubInstallPlan;
}

export async function applyHubInstall(
  appId: string,
  planId: string,
  approvalToken: string,
): Promise<HubInstallResult> {
  const response = await fetch("/api/appliance/hub/plans/install/apply", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ appId, planId }),
  });
  if (!response.ok) {
    throw await hubError(response, "应用安装失败，设备状态没有被改变");
  }
  return (await response.json()) as HubInstallResult;
}

export async function createHubUpdatePlan(
  appId: string,
): Promise<HubUpdatePlan> {
  const response = await fetch("/api/appliance/hub/plans/update", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ appId }),
  });
  if (!response.ok) throw await hubError(response, "无法生成安全更新计划");
  return (await response.json()) as HubUpdatePlan;
}

export async function applyHubUpdate(
  appId: string,
  planId: string,
  approvalToken: string,
): Promise<HubUpdateResult> {
  const response = await fetch("/api/appliance/hub/plans/update/apply", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ appId, planId }),
  });
  if (!response.ok) {
    throw await hubError(response, "应用更新失败，旧版本已恢复");
  }
  return (await response.json()) as HubUpdateResult;
}

export async function createHubUninstallPlan(
  appId: string,
): Promise<HubUninstallPlan> {
  const response = await fetch("/api/appliance/hub/plans/uninstall", {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ appId }),
  });
  if (!response.ok) throw await hubError(response, "无法生成安全卸载计划");
  return (await response.json()) as HubUninstallPlan;
}

export async function createHubControlPlan(
  operation: HubControlOperation,
  appId: string,
): Promise<HubControlPlan> {
  const response = await fetch(`/api/appliance/hub/plans/${operation}`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ appId }),
  });
  if (!response.ok) {
    throw await hubError(response, "无法生成安全运行计划");
  }
  const body: unknown = await response.json().catch(() => null);
  if (
    !isRecord(body) ||
    body.schema !== `echo.hub.${operation}-plan.v1` ||
    body.operation !== operation ||
    typeof body.planId !== "string" ||
    !/^[0-9a-f]{64}$/.test(body.planId) ||
    typeof body.ready !== "boolean" ||
    !Array.isArray(body.blockers)
  ) {
    throw new Error("安全运行计划数据无效");
  }
  return body as HubControlPlan;
}

export async function applyHubUninstall(
  appId: string,
  planId: string,
  approvalToken: string,
): Promise<HubUninstallResult> {
  const response = await fetch("/api/appliance/hub/plans/uninstall/apply", {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ appId, planId }),
  });
  if (!response.ok) {
    throw await hubError(response, "应用卸载失败，设备状态没有被改变");
  }
  return (await response.json()) as HubUninstallResult;
}

async function queueHubOperation(
  operation: "install" | "update" | "uninstall" | "start" | "stop" | "restart",
  appId: string,
  planId: string,
  approvalToken: string,
): Promise<HubOperation> {
  const response = await fetch(`/api/appliance/hub/plans/${operation}/queue`, {
    method: "POST",
    headers: {
      ...authHeader(),
      ...approvalHeader(approvalToken),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ appId, planId }),
  });
  if (!response.ok) {
    throw await hubError(response, "无法创建后台应用任务");
  }
  const body: unknown = await response.json().catch(() => null);
  if (!isHubOperation(body)) throw new Error("后台应用任务数据无效");
  return body;
}

export function queueHubInstall(
  appId: string,
  planId: string,
  approvalToken: string,
): Promise<HubOperation> {
  return queueHubOperation("install", appId, planId, approvalToken);
}

export function queueHubUpdate(
  appId: string,
  planId: string,
  approvalToken: string,
): Promise<HubOperation> {
  return queueHubOperation("update", appId, planId, approvalToken);
}

export function queueHubUninstall(
  appId: string,
  planId: string,
  approvalToken: string,
): Promise<HubOperation> {
  return queueHubOperation("uninstall", appId, planId, approvalToken);
}

export function queueHubControl(
  operation: HubControlOperation,
  appId: string,
  planId: string,
  approvalToken: string,
): Promise<HubOperation> {
  return queueHubOperation(operation, appId, planId, approvalToken);
}

export async function fetchHubOperations(
  limit = 20,
): Promise<HubOperationsResponse> {
  const response = await fetch(
    `/api/appliance/hub/operations?limit=${encodeURIComponent(String(limit))}`,
    { headers: authHeader() },
  );
  if (!response.ok) throw await hubError(response, "无法读取后台应用任务");
  const body: unknown = await response.json().catch(() => null);
  if (
    !isRecord(body) ||
    body.schema !== "echo.hub.operations.v1" ||
    !Array.isArray(body.operations) ||
    !body.operations.every(isHubOperation) ||
    typeof body.total !== "number"
  ) {
    throw new Error("后台应用任务列表数据无效");
  }
  return body as HubOperationsResponse;
}

export async function claimHubOperationCredentials(
  operationId: string,
): Promise<Record<string, string>> {
  const response = await fetch(
    `/api/appliance/hub/operations/${encodeURIComponent(operationId)}/credentials/claim`,
    { method: "POST", headers: authHeader() },
  );
  if (!response.ok) throw await hubError(response, "无法领取应用初始凭据");
  const body: unknown = await response.json().catch(() => null);
  if (
    !isRecord(body) ||
    body.schema !== "echo.hub.operation-credentials.v1" ||
    body.operationId !== operationId ||
    !isRecord(body.credentials) ||
    !Object.values(body.credentials).every((value) => typeof value === "string")
  ) {
    throw new Error("应用初始凭据数据无效");
  }
  return body.credentials as Record<string, string>;
}
