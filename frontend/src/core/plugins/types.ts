export interface PluginInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  capabilities: Array<Record<string, unknown>>;
  dependencies: string[];
  enabled: boolean;
  state: string;
  error?: string;
  logo_url?: string | null;
  icon_url?: string | null;
  brand_color?: string | null;
  start_time?: string;
  smoke?: PluginSmoke;
}

export interface CapabilityInfo {
  name: string;
  type: string;
  description: string;
  version: string;
  requires: string[];
  provider?: string;
}

export interface PluginSmoke {
  schema: "echo.codex_plugin_smoke.v1" | string;
  ok: boolean;
  warnings?: string[];
  surfaces?: {
    capabilities?: boolean;
    skills?: boolean;
    apps?: boolean;
    mcp?: boolean;
    commands?: boolean;
  };
  publisher_provenance?: PluginPublisherProvenance;
}

export interface PluginPublisherProvenance {
  schema: "echo.plugin_publisher_provenance.v1" | string;
  present: boolean;
  verified: boolean;
  trusted: boolean;
  status:
    | "unsigned"
    | "verified"
    | "invalid"
    | "tampered"
    | "untrusted"
    | "revoked"
    | string;
  publisher_id: string;
  key_id: string;
  content_digest: string;
  signature_digest: string;
  reason: string;
}

export interface PluginSmokeSummaryItem {
  plugin_id?: string | null;
  plugin_name?: string | null;
  issues?: string[];
  warnings?: string[];
  reason?: string;
}

export interface PluginPermissionResolution {
  plugin_id?: string | null;
  plugin_name?: string | null;
  schema: "echo.codex_plugin_permission_resolution.v1" | string;
  status: "explicit" | "review_required" | "none" | string;
  review_required: boolean;
  accepted_risk: boolean;
  permissions: unknown[];
  reason: string;
}

export interface PluginSmokeSummary {
  schema: "echo.codex_plugin_smoke_summary.v1" | string;
  total: number;
  ok_count: number;
  failed_count: number;
  review_required_count: number;
  warning_count: number;
  publisher_verified_count?: number;
  unsigned_count?: number;
  invalid_signature_count?: number;
  failed: PluginSmokeSummaryItem[];
  review_required: PluginSmokeSummaryItem[];
  warnings: PluginSmokeSummaryItem[];
  publisher_provenance?: Array<
    PluginPublisherProvenance & {
      plugin_id?: string | null;
      plugin_name?: string | null;
    }
  >;
  permission_resolutions?: PluginPermissionResolution[];
  compatibility?: {
    schema: "echo.codex_plugin_compatibility.v1" | string;
    verdict: "pass" | "review" | "fail" | string;
    passed: number;
    total: number;
    surface_totals: Record<string, number>;
    requirements: Array<{
      id: string;
      passed: boolean;
      detail: string;
    }>;
    next_actions: string[];
  };
  migration_readiness?: {
    schema: "echo.plugin_migration_readiness.v1" | string;
    score: number;
    ready: boolean;
    ready_count: number;
    total: number;
    blocked_count: number;
    review_required_count: number;
  };
}

export interface PluginMigrationReadiness {
  schema: "echo.plugin_migration_readiness.v1" | string;
  total: number;
  ready_count: number;
  blocked_count: number;
  review_required_count: number;
  score: number;
  ready: boolean;
  central_contract?: {
    schema: "echo.plugin_migration_contract_index.v1" | string;
    path: string;
    present: boolean;
    covered_count: number;
    central_tests_present: boolean;
  };
  plugins: Array<Record<string, unknown>>;
  next_actions: string[];
}

export interface PluginPublisherTrustReport {
  schema: "echo.plugin_publisher_trust_report.v1" | string;
  path: string;
  exists: boolean;
  publisher_count: number;
  key_count: number;
  active_key_count: number;
  revoked_key_count: number;
  rotation_due_count: number;
  ready: boolean;
  publishers: Array<{
    publisher_id: string;
    display_name: string;
    active_key_count: number;
    rotation_due_count: number;
    keys: Array<{
      key_id: string;
      algorithm: string;
      status: string;
      public_key_fingerprint: string;
      created_at: string;
      age_days: number | null;
      rotation_due: boolean;
      replaces: string;
      replaced_by: string;
      retired_at: string;
      revoked_at: string;
      revocation_reason: string;
    }>;
  }>;
  next_actions: string[];
}

export interface PluginLifecycleTransaction {
  schema: "echo.plugin_lifecycle_transaction.v1" | string;
  ts: string;
  transaction_id: string;
  plugin_id: string;
  operation: "install" | "upgrade" | "rollback" | string;
  status: "committed" | "rolled_back" | string;
  previous_version?: string;
  version?: string;
  restored_version?: string;
  removed_version?: string;
  rollback_available?: boolean;
}

export interface PluginLifecycleHistory {
  schema: "echo.plugin_lifecycle_history.v1" | string;
  total: number;
  items: PluginLifecycleTransaction[];
}

export interface PluginRegistryEntry {
  id: string;
  version: string;
  installed_version: string;
  status:
    | "not_installed"
    | "update_available"
    | "current"
    | "installed_newer"
    | string;
  surfaces: string[];
  content_digest: string;
  publisher_verified: boolean;
  fixture_verified: boolean;
  installable: boolean;
  one_click_install: boolean;
  blockers: string[];
}

export interface PluginRegistryUpdates {
  schema: "echo.plugin_registry_updates.v1" | string;
  total: number;
  update_count: number;
  install_count: number;
  blocked_count: number;
  ready: boolean;
  plugins: PluginRegistryEntry[];
}

export interface PluginRuntimeProfile {
  schema: "echo.codex_plugin_runtime.v1" | string;
  plugin_id: string;
  plugin_name: string;
  surfaces: {
    capabilities: number;
    skills: number;
    apps: number;
    mcp_servers: number;
    commands: number;
  };
  capabilities: Array<{
    name: string;
    type: string;
    description: string;
  }>;
  skills: Array<{
    id: string;
    name: string;
    path: string;
    description: string;
    scope: "plugin" | string;
  }>;
  apps: Array<{
    id: string;
    name: string;
    description: string;
    source: string;
  }>;
  mcp_servers: Array<{
    name: string;
    type: string;
    title: string;
    description: string;
    command: string;
    args: unknown[];
    cwd: string;
    url: string;
    env_keys: string[];
    enabled: boolean;
    scope: "plugin" | string;
  }>;
  commands: Array<{
    id: string;
    name: string;
    path: string;
    executable: boolean;
  }>;
  call_order: string[];
}

// ── PluginHub (new pluggable module architecture) ──────────

/** Capability as returned by the PluginHub API. */
export interface HubCapability {
  type: "skill" | "channel" | "api" | "config_ui";
  name: string;
  description: string;
}

/** Full plugin info as returned by the PluginHub API. */
export interface HubPluginInfo {
  id: string;
  name: string;
  /** Optional localized display name (e.g. 中文插件名); falls back to `name`. */
  display_name?: string;
  version: string;
  description: string;
  author: string;
  capabilities: HubCapability[];
  config_schema?: Record<string, unknown>;
  config_ui?: string | null;
  loaded: boolean;
  enabled: boolean;
  error?: string | null;
  dir: string;
  dependencies: string[];
  state: string;
}

/** A discovered (not yet loaded) plugin candidate. */
export interface DiscoveredPlugin {
  id: string;
  name: string;
  display_name?: string;
  version: string;
  description: string;
  author: string;
  tags: string[];
  dir: string;
  loaded: boolean;
}
