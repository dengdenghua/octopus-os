export type PermissionMode =
  | "default"
  | "acceptEdits"
  | "bypassPermissions"
  | "plan";
export type LegacyPermissionMode = "sandbox" | "full";
export type ApprovalPolicy = "never" | "on-request" | "untrusted";

/**
 * Network tiers the user picks from the sandbox settings page.
 * - "deny":   only model inference endpoints stay reachable.
 * - "common": additionally pre-allows bundled dev-tool hosts (npm / pip /
 *             git / apt / rust / go registries and CN mirrors).
 * - "full":   everything reachable.
 * Legacy boolean values are still accepted ("true" -> full, "false" -> deny)
 * so stale localStorage never breaks the mapping.
 */
export type NetworkAccessMode = "deny" | "common" | "full";

export type SandboxPolicy =
  | {
      type: "workspaceWrite";
      networkAccess: boolean;
      egressAllowCommon?: boolean;
    }
  | {
      type: "dangerFullAccess";
      networkAccess: boolean;
      egressAllowCommon?: boolean;
    };

export interface PermissionRuntimeConfig {
  mode: PermissionMode;
  approvalPolicy: ApprovalPolicy;
  sandboxPolicy: SandboxPolicy;
  execution_environment: "sandbox" | "local";
  sandbox_mode: LegacyPermissionMode;
  planningMode: boolean;
}

export function normalizePermissionMode(value: unknown): PermissionMode {
  const raw = typeof value === "string" ? value.trim() : "";
  const normalized = raw.toLowerCase();
  if (normalized === "acceptedits" || normalized === "accept-edits") {
    return "acceptEdits";
  }
  if (
    normalized === "bypasspermissions" ||
    normalized === "bypass-permissions" ||
    normalized === "bypass" ||
    normalized === "yolo" ||
    normalized === "full"
  ) {
    return "bypassPermissions";
  }
  if (normalized === "plan") return "plan";
  return "default";
}

export function normalizeNetworkAccess(value: unknown): NetworkAccessMode | undefined {
  if (value === true) return "full";
  if (value === false) return "deny";
  if (typeof value === "string") {
    const v = value.trim().toLowerCase();
    if (v === "deny" || v === "common" || v === "full") return v;
  }
  return undefined;
}

export function permissionRuntimeConfig(
  value: unknown,
  networkAccess?: NetworkAccessMode | boolean,
): PermissionRuntimeConfig {
  const mode = normalizePermissionMode(value);
  // Network access is an independent axis the user controls from the
  // sandbox settings page. When unset, follow the safest default: only
  // full-access (local, auto-approved) turns get network; everything else
  // stays network-denied — matching the backend's default
  // ``TurnParams.sandbox_policy`` (``networkAccess: false``).
  const effectiveNetwork =
    normalizeNetworkAccess(networkAccess) ??
    (mode === "bypassPermissions" ? "full" : "deny");
  const sandboxPolicy = (
    type: SandboxPolicy["type"],
  ): SandboxPolicy => {
    if (effectiveNetwork === "full") {
      return { type, networkAccess: true };
    }
    if (effectiveNetwork === "common") {
      return { type, networkAccess: false, egressAllowCommon: true };
    }
    return { type, networkAccess: false };
  };
  if (mode === "bypassPermissions") {
    return {
      mode,
      approvalPolicy: "never",
      sandboxPolicy: sandboxPolicy("dangerFullAccess"),
      execution_environment: "local",
      sandbox_mode: "full",
      planningMode: false,
    };
  }
  if (mode === "acceptEdits") {
    // "Accept edits" trusts the machine for file changes while commands still
    // ask for confirmation — so it runs locally, matching the sandbox
    // settings page's "Local execution" level. Keeping this in sync prevents
    // the composer shortcut and the settings page from overriding each other.
    return {
      mode,
      approvalPolicy: "on-request",
      sandboxPolicy: sandboxPolicy("workspaceWrite"),
      execution_environment: "local",
      sandbox_mode: "full",
      planningMode: false,
    };
  }
  return {
    mode,
    approvalPolicy: "on-request",
    sandboxPolicy: sandboxPolicy("workspaceWrite"),
    execution_environment: "sandbox",
    sandbox_mode: "sandbox",
    // Plan mode keeps the same confirm-on-request sandbox but flags the turn as
    // planning-only (the payload builder forwards planningMode to the backend).
    planningMode: mode === "plan",
  };
}
