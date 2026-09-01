import { authHeader } from "@/appliance/auth";
import { approvalHeader } from "@/appliance/approval";

export type AgentAssetKind = "plugin" | "connector" | "workbench" | "skill";
export type AgentAssetLifecycleState =
  | "available"
  | "enabled"
  | "disabled"
  | "update_available"
  | "broken";
export type AgentAssetTrustLevel =
  | "system"
  | "publisher"
  | "local_integrity"
  | "catalog"
  | "unverified";
export type AgentAssetCompatibility =
  | "compatible"
  | "incompatible"
  | "not_checked";

export type AgentHubAsset = {
  id: string;
  installId: string;
  name: string;
  description: string;
  kind: AgentAssetKind;
  source: string;
  author: string | null;
  version: string | null;
  availableVersion: string | null;
  installed: boolean;
  enabled: boolean;
  lifecycleState: AgentAssetLifecycleState;
  rollbackAvailable: boolean;
  recoveryCount: number;
  trustLevel: AgentAssetTrustLevel;
  integrityVerified: boolean;
  publisherVerified: boolean;
  verifiedPublisher: string | null;
  compatibility: AgentAssetCompatibility;
  hostApi: string | null;
  releaseSummary: string | null;
  permissions: string[];
  permissionsGranted: string[];
  permissionReviewRequired: boolean;
  permissionActive: boolean;
  authModes: string[];
  dependencies: string[];
  runtimeDependencies: string[];
  connectors: string[];
};

export type AgentHubCatalog = {
  available: boolean;
  assets: AgentHubAsset[];
  plugins: number;
  workbenches: number;
  connectors: number;
  skills: number;
  installed: number;
  updates: number;
  attention: number;
  error: string | null;
};

export type AgentCapabilityPlanOperation =
  | "install"
  | "authorize"
  | "uninstall"
  | "rollback";

export type AgentCapabilityPlan = {
  schema: string;
  serviceSchema: "echo.capability-service.v1";
  capabilityId: string;
  planId: string;
  operation: AgentCapabilityPlanOperation;
  ready: boolean;
  permissions: string[];
  blockers: string[];
  changes: string[];
};

export type AgentCapabilityOperationResult = {
  schema: "echo.capability-service.v1";
  operation: string;
  capabilityId: string;
  installed: boolean | null;
  enabled: boolean | null;
  connected: boolean | null;
};

export type AgentCapabilityCredentialField = {
  key: string;
  label: string;
  labelZh: string;
  secret: true;
  required: boolean;
};

export type AgentCapabilityConnectionProfile = {
  schema: "echo.capability-service.v1";
  capabilityId: string;
  authMode: string;
  mode:
    | "principal_credentials"
    | "no_credentials"
    | "agent_managed"
    | "unavailable";
  canConnect: boolean;
  connected: boolean;
  minimumCredentials: number;
  fields: AgentCapabilityCredentialField[];
  blockers: string[];
};

type CloudPlugin = {
  id: string;
  plugin: string;
  source?: string;
  name?: string;
  name_zh?: string;
  description?: string;
  version?: string;
  kind?: "plugin" | "connector" | "workbench";
  category?: string;
  author?: string;
  release_summary?: string;
  host_api?: string;
  permissions: string[];
  authModes: string[];
  dependencies: string[];
  runtimeDependencies: string[];
  connectors: string[];
};

type CloudSkill = {
  name: string;
  source?: string;
  author?: string;
  description?: string;
  version?: string;
};

type AgentAssetProjection = {
  schema: "echo.agent-assets.v6";
  available: boolean;
  plugins: CloudPlugin[];
  skills: CloudSkill[];
  installed: { plugins?: string[]; skills?: string[] };
  pluginStates: CloudPluginState[];
  unavailableSources: string[];
};

type CloudPluginState = {
  id: string;
  catalogId: string;
  kind: "plugin" | "connector" | "workbench";
  source: "factory" | "cloud";
  state: AgentAssetLifecycleState;
  installed: boolean;
  enabled: boolean;
  rollbackAvailable: boolean;
  recoveryCount: number;
  version?: string;
  availableVersion?: string;
  trustLevel: AgentAssetTrustLevel;
  integrityVerified: boolean;
  publisherVerified: boolean;
  publisher?: string;
  compatibility: AgentAssetCompatibility;
  hostApi?: string;
  releaseSummary?: string;
  permissions: string[];
  permissionsGranted: string[];
  permissionReviewRequired: boolean;
  permissionActive: boolean;
  authModes: string[];
  dependencies: string[];
  runtimeDependencies: string[];
  connectors: string[];
};

async function readJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: authHeader() });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as T;
}

async function responseError(response: Response, fallback: string) {
  const detail = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  if (isRecord(detail) && typeof detail.message === "string") {
    return new Error(detail.message);
  }
  if (typeof detail === "string") return new Error(detail);
  return new Error(fallback);
}

async function postAgentCapability(
  path: string,
  body: Record<string, unknown>,
  approvalToken?: string,
): Promise<unknown> {
  const response = await fetch(`/api/appliance/agent-capabilities${path}`, {
    method: "POST",
    headers: {
      ...authHeader(),
      ...(approvalToken ? approvalHeader(approvalToken) : {}),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw await responseError(response, "Agent 能力操作没有完成");
  }
  return response.json();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringList(value: unknown, maximum = 128): string[] | null {
  if (
    !Array.isArray(value) ||
    value.length > maximum ||
    !value.every((item) => typeof item === "string")
  )
    return null;
  return value;
}

function parseAgentCapabilityPlan(
  value: unknown,
  operation: AgentCapabilityPlanOperation,
): AgentCapabilityPlan {
  if (!isRecord(value)) throw new Error("Agent 返回了不兼容的操作计划");
  const capabilityId = value.capability_id;
  const planId = value.plan_id;
  const serviceSchema = value.service_schema;
  const schema = value.schema;
  const permissions = stringList(value.permissions ?? []) ?? null;
  const blockers = stringList(value.blockers ?? []) ?? null;
  const changes = stringList(value.changes ?? []) ?? null;
  const readyField =
    operation === "uninstall"
      ? "can_uninstall"
      : operation === "rollback"
        ? "can_rollback"
        : "can_install";
  if (
    typeof schema !== "string" ||
    serviceSchema !== "echo.capability-service.v1" ||
    typeof capabilityId !== "string" ||
    typeof planId !== "string" ||
    !/^[0-9a-f]{64}$/.test(planId) ||
    typeof value[readyField] !== "boolean" ||
    permissions === null ||
    blockers === null ||
    changes === null
  ) {
    throw new Error("Agent 返回了不兼容的操作计划");
  }
  return {
    schema,
    serviceSchema,
    capabilityId,
    planId,
    operation,
    ready: value[readyField] as boolean,
    permissions,
    blockers,
    changes,
  };
}

function parseAgentCapabilityResult(
  value: unknown,
): AgentCapabilityOperationResult {
  if (
    !isRecord(value) ||
    value.schema !== "echo.capability-service.v1" ||
    typeof value.operation !== "string" ||
    !isRecord(value.capability) ||
    !isRecord(value.result) ||
    typeof value.capability.id !== "string"
  ) {
    throw new Error("Agent 返回了不兼容的操作结果");
  }
  const capability = value.capability;
  const result = value.result;
  const optionalBoolean = (field: string): boolean | null => {
    const fromResult = result[field];
    const fromCapability = capability[field];
    const candidate = fromResult ?? fromCapability;
    return typeof candidate === "boolean" ? candidate : null;
  };
  return {
    schema: "echo.capability-service.v1",
    operation: value.operation as string,
    capabilityId: capability.id as string,
    installed: optionalBoolean("installed"),
    enabled: optionalBoolean("enabled"),
    connected: optionalBoolean("connected"),
  };
}

export async function createAgentCapabilityPlan(
  operation: AgentCapabilityPlanOperation,
  capabilityId: string,
): Promise<AgentCapabilityPlan> {
  const result = await postAgentCapability(`/plans/${operation}`, {
    capabilityId,
  });
  return parseAgentCapabilityPlan(result, operation);
}

export async function applyAgentCapabilityLifecycle(
  operation: "install" | "uninstall" | "rollback",
  capabilityId: string,
  planId: string,
  approvalToken: string,
): Promise<AgentCapabilityOperationResult> {
  const result = await postAgentCapability(
    `/plans/${operation}/apply`,
    { capabilityId, planId },
    approvalToken,
  );
  return parseAgentCapabilityResult(result);
}

export async function authorizeAgentCapability(
  capabilityId: string,
  planId: string,
  permissions: string[],
  approvalToken: string,
): Promise<AgentCapabilityOperationResult> {
  const result = await postAgentCapability(
    "/plans/authorize/apply",
    {
      capabilityId,
      planId,
      permissions,
      activate: true,
    },
    approvalToken,
  );
  return parseAgentCapabilityResult(result);
}

export async function disableAgentCapability(
  capabilityId: string,
): Promise<AgentCapabilityOperationResult> {
  const result = await postAgentCapability(
    `/${encodeURIComponent(capabilityId)}/disable`,
    {},
  );
  return parseAgentCapabilityResult(result);
}

function parseAgentCapabilityConnectionProfile(
  value: unknown,
): AgentCapabilityConnectionProfile {
  if (!isRecord(value)) throw new Error("Agent 返回了不兼容的连接说明");
  const modes = new Set([
    "principal_credentials",
    "no_credentials",
    "agent_managed",
    "unavailable",
  ]);
  const fields = value.fields;
  const blockers = stringList(value.blockers ?? [], 32);
  if (
    value.schema !== "echo.capability-service.v1" ||
    typeof value.capability_id !== "string" ||
    typeof value.auth_mode !== "string" ||
    typeof value.mode !== "string" ||
    !modes.has(value.mode) ||
    typeof value.can_connect !== "boolean" ||
    typeof value.connected !== "boolean" ||
    !Number.isInteger(value.minimum_credentials) ||
    (value.minimum_credentials as number) < 0 ||
    (value.minimum_credentials as number) > 32 ||
    !Array.isArray(fields) ||
    fields.length > 32 ||
    blockers === null
  ) {
    throw new Error("Agent 返回了不兼容的连接说明");
  }
  const parsedFields: AgentCapabilityCredentialField[] = [];
  for (const field of fields) {
    if (
      !isRecord(field) ||
      typeof field.key !== "string" ||
      !/^[A-Za-z][A-Za-z0-9_]{0,127}$/.test(field.key) ||
      typeof field.label !== "string" ||
      typeof field.label_zh !== "string" ||
      field.secret !== true ||
      typeof field.required !== "boolean"
    ) {
      throw new Error("Agent 返回了不兼容的连接说明");
    }
    parsedFields.push({
      key: field.key,
      label: field.label,
      labelZh: field.label_zh,
      secret: true,
      required: field.required,
    });
  }
  return {
    schema: "echo.capability-service.v1",
    capabilityId: value.capability_id as string,
    authMode: value.auth_mode as string,
    mode: value.mode as AgentCapabilityConnectionProfile["mode"],
    canConnect: value.can_connect as boolean,
    connected: value.connected as boolean,
    minimumCredentials: value.minimum_credentials as number,
    fields: parsedFields,
    blockers,
  };
}

export async function fetchAgentCapabilityConnectionProfile(
  capabilityId: string,
): Promise<AgentCapabilityConnectionProfile> {
  const result = await readJson<unknown>(
    `/api/appliance/agent-capabilities/${encodeURIComponent(capabilityId)}/connection-profile`,
  );
  return parseAgentCapabilityConnectionProfile(result);
}

export async function connectAgentCapability(
  capabilityId: string,
  tokens: Record<string, string>,
): Promise<AgentCapabilityOperationResult> {
  const result = await postAgentCapability("/connect", {
    capabilityId,
    ...(Object.keys(tokens).length ? { tokens } : {}),
  });
  return parseAgentCapabilityResult(result);
}

export async function disconnectAgentCapability(
  capabilityId: string,
): Promise<AgentCapabilityOperationResult> {
  const result = await postAgentCapability(
    `/${encodeURIComponent(capabilityId)}/disconnect`,
    {},
  );
  return parseAgentCapabilityResult(result);
}

function hasOptionalStrings(
  value: Record<string, unknown>,
  fields: string[],
): boolean {
  return fields.every(
    (field) => value[field] === undefined || typeof value[field] === "string",
  );
}

function hasOnlyFields(
  value: Record<string, unknown>,
  fields: string[],
): boolean {
  return Object.keys(value).every((field) => fields.includes(field));
}

const requirementFields = [
  "permissions",
  "authModes",
  "dependencies",
  "runtimeDependencies",
  "connectors",
];
const permissionValues = new Set([
  "account.credentials",
  "content.read",
  "content.write",
  "interaction.user",
  "network.remote",
  "process.local",
]);
const authModeValues = new Set([
  "connected-account",
  "mcp",
  "oauth",
  "oneid-token",
  "server-side",
  "token",
]);

function hasRequirementLists(value: Record<string, unknown>): boolean {
  const structurallyValid = requirementFields.every(
    (field) =>
      Array.isArray(value[field]) &&
      value[field].length <= 64 &&
      value[field].every((item) => typeof item === "string"),
  );
  return (
    structurallyValid &&
    (value.permissions as string[]).every((item) =>
      permissionValues.has(item),
    ) &&
    (value.authModes as string[]).every((item) => authModeValues.has(item))
  );
}

function isAgentAssetProjection(value: unknown): value is AgentAssetProjection {
  if (!isRecord(value) || value.schema !== "echo.agent-assets.v6") return false;
  if (
    !hasOnlyFields(value, [
      "schema",
      "available",
      "plugins",
      "skills",
      "installed",
      "pluginStates",
      "unavailableSources",
    ])
  )
    return false;
  if (
    typeof value.available !== "boolean" ||
    !Array.isArray(value.plugins) ||
    !Array.isArray(value.skills) ||
    !isRecord(value.installed) ||
    !Array.isArray(value.pluginStates) ||
    !Array.isArray(value.unavailableSources)
  )
    return false;
  if (
    !hasOnlyFields(value.installed, ["plugins", "skills"]) ||
    !Array.isArray(value.installed.plugins) ||
    !Array.isArray(value.installed.skills)
  )
    return false;
  const installedPlugins = value.installed.plugins;
  const installedSkills = value.installed.skills;
  const pluginFields = [
    "id",
    "plugin",
    "source",
    "name",
    "name_zh",
    "description",
    "version",
    "kind",
    "category",
    "author",
    "release_summary",
    "host_api",
    ...requirementFields,
  ];
  const pluginStringFields = pluginFields.filter(
    (field) => !requirementFields.includes(field),
  );
  const skillFields = ["name", "source", "author", "description", "version"];
  const valid =
    value.plugins.every(
      (item) =>
        isRecord(item) &&
        hasOnlyFields(item, pluginFields) &&
        (typeof item.id === "string" || typeof item.plugin === "string") &&
        hasOptionalStrings(item, pluginStringFields) &&
        hasRequirementLists(item) &&
        (item.kind === undefined ||
          ["plugin", "connector", "workbench"].includes(String(item.kind))),
    ) &&
    value.skills.every(
      (item) =>
        isRecord(item) &&
        hasOnlyFields(item, skillFields) &&
        typeof item.name === "string" &&
        hasOptionalStrings(item, skillFields),
    ) &&
    installedPlugins.every((item) => typeof item === "string") &&
    installedSkills.every((item) => typeof item === "string") &&
    value.pluginStates.every((item) => {
      if (!isRecord(item)) return false;
      const fields = [
        "id",
        "catalogId",
        "kind",
        "source",
        "state",
        "installed",
        "enabled",
        "rollbackAvailable",
        "recoveryCount",
        "version",
        "availableVersion",
        "trustLevel",
        "integrityVerified",
        "publisherVerified",
        "publisher",
        "compatibility",
        "hostApi",
        "releaseSummary",
        "permissionsGranted",
        "permissionReviewRequired",
        "permissionActive",
        ...requirementFields,
      ];
      return (
        hasOnlyFields(item, fields) &&
        hasRequirementLists(item) &&
        Array.isArray(item.permissionsGranted) &&
        item.permissionsGranted.length <= 64 &&
        item.permissionsGranted.every(
          (permission) =>
            typeof permission === "string" &&
            permissionValues.has(permission) &&
            (item.permissions as string[]).includes(permission),
        ) &&
        typeof item.permissionReviewRequired === "boolean" &&
        typeof item.permissionActive === "boolean" &&
        typeof item.id === "string" &&
        typeof item.catalogId === "string" &&
        ["plugin", "connector", "workbench"].includes(String(item.kind)) &&
        ["factory", "cloud"].includes(String(item.source)) &&
        [
          "available",
          "enabled",
          "disabled",
          "update_available",
          "broken",
        ].includes(String(item.state)) &&
        typeof item.installed === "boolean" &&
        typeof item.enabled === "boolean" &&
        (!item.enabled || item.installed) &&
        (!item.permissionActive ||
          (item.installed &&
            item.enabled &&
            item.permissionsGranted.length ===
              (item.permissions as string[]).length)) &&
        (!item.permissionReviewRequired ||
          (item.installed &&
            item.permissionsGranted.length !==
              (item.permissions as string[]).length)) &&
        item.installed === installedPlugins.includes(item.id) &&
        (item.state !== "available" || !item.installed) &&
        (!["enabled", "update_available"].includes(String(item.state)) ||
          (item.installed && item.enabled)) &&
        (item.state !== "disabled" || (item.installed && !item.enabled)) &&
        typeof item.rollbackAvailable === "boolean" &&
        typeof item.recoveryCount === "number" &&
        Number.isInteger(item.recoveryCount) &&
        item.recoveryCount >= 0 &&
        item.recoveryCount <= 1000 &&
        (item.version === undefined || typeof item.version === "string") &&
        (item.availableVersion === undefined ||
          typeof item.availableVersion === "string") &&
        [
          "system",
          "publisher",
          "local_integrity",
          "catalog",
          "unverified",
        ].includes(String(item.trustLevel)) &&
        typeof item.integrityVerified === "boolean" &&
        typeof item.publisherVerified === "boolean" &&
        (!item.publisherVerified ||
          (item.integrityVerified && item.trustLevel === "publisher")) &&
        (item.trustLevel !== "publisher" || item.publisherVerified) &&
        (item.trustLevel !== "local_integrity" ||
          (item.integrityVerified && !item.publisherVerified)) &&
        (!["catalog", "unverified"].includes(String(item.trustLevel)) ||
          (!item.integrityVerified && !item.publisherVerified)) &&
        (item.trustLevel !== "system" || item.source === "factory") &&
        (item.publisher === undefined ||
          (typeof item.publisher === "string" && item.publisherVerified)) &&
        ["compatible", "incompatible", "not_checked"].includes(
          String(item.compatibility),
        ) &&
        (item.compatibility !== "incompatible" ||
          (item.state === "broken" && !item.enabled)) &&
        (item.hostApi === undefined || typeof item.hostApi === "string") &&
        (item.releaseSummary === undefined ||
          typeof item.releaseSummary === "string")
      );
    }) &&
    value.unavailableSources.every(
      (item) =>
        typeof item === "string" &&
        ["plugins", "skills", "plugin-statuses"].includes(item),
    );
  if (!valid) return false;
  const pluginIds = value.plugins.map((item) => item.plugin || item.id);
  const skillIds = value.skills.map((item) => item.name);
  const stateIds = value.pluginStates.map((item) => item.id);
  return (
    new Set(pluginIds).size === pluginIds.length &&
    new Set(skillIds).size === skillIds.length &&
    new Set(stateIds).size === stateIds.length &&
    stateIds.every((identity) => pluginIds.includes(identity))
  );
}

function unavailableAgentCatalog(error: string): AgentHubCatalog {
  return {
    available: false,
    assets: [],
    plugins: 0,
    workbenches: 0,
    connectors: 0,
    skills: 0,
    installed: 0,
    updates: 0,
    attention: 0,
    error,
  };
}

export function agentAssetManagementRoute(asset: AgentHubAsset): string {
  if (asset.kind === "skill") {
    return "/workspace/agents?surface=chat&tab=skills";
  }
  return `/workspace/agents?surface=chat&tab=plugins&view=${asset.installed ? "installed" : "all"}`;
}

export function agentAssetWindowId(asset: AgentHubAsset): string {
  return asset.kind === "skill" ? "agent-assets:skill" : "agent-assets:plugin";
}

/**
 * Project the existing Agent cloud catalog into Echo Hub without copying its
 * install implementation or reading Agent's private SQLite files directly.
 * The Agent API remains the owner of plugin/skill state and security policy.
 */
export async function fetchAgentHubCatalog(): Promise<AgentHubCatalog> {
  let response: unknown;
  try {
    response = await readJson<unknown>(
      "/api/appliance/agent-assets/catalog?limit=80",
    );
  } catch {
    return unavailableAgentCatalog("Agent 能力目录尚未连接");
  }
  if (!isAgentAssetProjection(response)) {
    return unavailableAgentCatalog("Agent 能力目录返回了不兼容的数据");
  }
  const projection = response;
  if (!projection.available) {
    return unavailableAgentCatalog("Agent 能力目录尚未连接");
  }

  const plugins = projection.plugins ?? [];
  const skills = projection.skills ?? [];
  const installedPlugins = new Set(projection.installed?.plugins ?? []);
  const installedSkills = new Set(projection.installed?.skills ?? []);
  const pluginStates = new Map(
    projection.pluginStates.map((state) => [state.id, state]),
  );
  const assets: AgentHubAsset[] = [
    ...plugins.map((item): AgentHubAsset => {
      const installId = item.plugin || item.id;
      const lifecycle = pluginStates.get(installId);
      const installed =
        lifecycle?.installed ??
        (installedPlugins.has(installId) || installedPlugins.has(item.id));
      const kind = lifecycle?.kind || item.kind || "plugin";
      return {
        id: `${kind}:${item.id || installId}`,
        installId,
        name: item.name_zh || item.name || installId,
        description:
          item.description ||
          (kind === "workbench"
            ? "Agent 工作台"
            : kind === "connector"
              ? "Agent 连接器"
              : "Agent 插件"),
        kind,
        source: item.source || "Agent",
        author: item.author || null,
        version: lifecycle?.version || item.version || null,
        availableVersion: lifecycle?.availableVersion || null,
        installed,
        enabled: lifecycle?.enabled ?? installed,
        lifecycleState:
          lifecycle?.state ?? (installed ? "enabled" : "available"),
        rollbackAvailable: lifecycle?.rollbackAvailable ?? false,
        recoveryCount: lifecycle?.recoveryCount ?? 0,
        trustLevel: lifecycle?.trustLevel ?? "catalog",
        integrityVerified: lifecycle?.integrityVerified ?? false,
        publisherVerified: lifecycle?.publisherVerified ?? false,
        verifiedPublisher: lifecycle?.publisher ?? null,
        compatibility: lifecycle?.compatibility ?? "not_checked",
        hostApi: lifecycle?.hostApi ?? null,
        releaseSummary:
          lifecycle?.releaseSummary || item.release_summary || null,
        permissions: lifecycle?.permissions ?? item.permissions,
        permissionsGranted: lifecycle?.permissionsGranted ?? [],
        permissionReviewRequired: lifecycle?.permissionReviewRequired ?? false,
        permissionActive: lifecycle?.permissionActive ?? false,
        authModes: lifecycle?.authModes ?? item.authModes,
        dependencies: lifecycle?.dependencies ?? item.dependencies,
        runtimeDependencies:
          lifecycle?.runtimeDependencies ?? item.runtimeDependencies,
        connectors: lifecycle?.connectors ?? item.connectors,
      };
    }),
    ...skills.map(
      (item): AgentHubAsset => ({
        id: `skill:${item.name}`,
        installId: item.name,
        name: item.name,
        description: item.description || "Agent 技能",
        kind: "skill",
        source: item.source || item.author || "Agent",
        author: item.author || null,
        version: item.version || null,
        installed: installedSkills.has(item.name),
        availableVersion: null,
        enabled: installedSkills.has(item.name),
        lifecycleState: installedSkills.has(item.name)
          ? "enabled"
          : "available",
        rollbackAvailable: false,
        recoveryCount: 0,
        trustLevel: "catalog",
        integrityVerified: false,
        publisherVerified: false,
        verifiedPublisher: null,
        compatibility: "not_checked",
        hostApi: null,
        releaseSummary: null,
        permissions: [],
        permissionsGranted: [],
        permissionReviewRequired: false,
        permissionActive: installedSkills.has(item.name),
        authModes: [],
        dependencies: [],
        runtimeDependencies: [],
        connectors: [],
      }),
    ),
  ];

  return {
    available: true,
    assets,
    plugins: assets.filter((item) => item.kind === "plugin").length,
    workbenches: assets.filter((item) => item.kind === "workbench").length,
    connectors: assets.filter((item) => item.kind === "connector").length,
    skills: skills.length,
    installed: assets.filter((item) => item.installed).length,
    updates: assets.filter((item) => item.lifecycleState === "update_available")
      .length,
    attention: assets.filter(
      (item) =>
        item.lifecycleState === "broken" || item.permissionReviewRequired,
    ).length,
    error: null,
  };
}
