import { describe, expect, it } from "vitest";

import {
  normalizePermissionMode,
  permissionRuntimeConfig,
} from "./permissions";

describe("permissionRuntimeConfig", () => {
  it("defaults to the product default mode with network DENIED", () => {
    // Network access is an independent user-controlled axis; the sandbox
    // default is network denied (matches backend TurnParams default).
    expect(permissionRuntimeConfig(undefined)).toEqual({
      mode: "default",
      approvalPolicy: "on-request",
      sandboxPolicy: {
        type: "workspaceWrite",
        networkAccess: false,
      },
      execution_environment: "sandbox",
      sandbox_mode: "sandbox",
      planningMode: false,
    });
  });

  it("maps bypassPermissions to local execution with auto approval", () => {
    expect(permissionRuntimeConfig("bypassPermissions")).toEqual({
      mode: "bypassPermissions",
      approvalPolicy: "never",
      sandboxPolicy: {
        type: "dangerFullAccess",
        // Full access is the one mode that defaults to network allowed.
        networkAccess: true,
      },
      execution_environment: "local",
      sandbox_mode: "full",
      planningMode: false,
    });
  });

  it("maps plan to the confirm-on-request sandbox with planning flagged", () => {
    expect(permissionRuntimeConfig("plan")).toEqual({
      mode: "plan",
      approvalPolicy: "on-request",
      sandboxPolicy: {
        type: "workspaceWrite",
        networkAccess: false,
      },
      execution_environment: "sandbox",
      sandbox_mode: "sandbox",
      planningMode: true,
    });
  });

  it("maps acceptEdits to local execution with confirm-on-request", () => {
    expect(permissionRuntimeConfig("acceptEdits")).toEqual({
      mode: "acceptEdits",
      approvalPolicy: "on-request",
      sandboxPolicy: {
        type: "workspaceWrite",
        networkAccess: false,
      },
      execution_environment: "local",
      sandbox_mode: "full",
      planningMode: false,
    });
  });

  it("honors an explicit networkAccess opt-in", () => {
    expect(permissionRuntimeConfig("default", true).sandboxPolicy).toEqual({
      type: "workspaceWrite",
      networkAccess: true,
    });
    expect(permissionRuntimeConfig("acceptEdits", true).sandboxPolicy).toEqual({
      type: "workspaceWrite",
      networkAccess: true,
    });
    expect(
      permissionRuntimeConfig("bypassPermissions", true).sandboxPolicy,
    ).toEqual({
      type: "dangerFullAccess",
      networkAccess: true,
    });
  });

  it("maps the common-domains tier to networkAccess=false + egressAllowCommon", () => {
    expect(permissionRuntimeConfig("default", "common").sandboxPolicy).toEqual({
      type: "workspaceWrite",
      networkAccess: false,
      egressAllowCommon: true,
    });
    expect(
      permissionRuntimeConfig("bypassPermissions", "common").sandboxPolicy,
    ).toEqual({
      type: "dangerFullAccess",
      networkAccess: false,
      egressAllowCommon: true,
    });
  });

  it("maps the deny tier explicitly and normalizes legacy booleans", () => {
    expect(permissionRuntimeConfig("default", "deny").sandboxPolicy).toEqual({
      type: "workspaceWrite",
      networkAccess: false,
    });
    // Legacy boolean storage: true -> full, false -> deny.
    expect(
      permissionRuntimeConfig("default", false).sandboxPolicy,
    ).toEqual({
      type: "workspaceWrite",
      networkAccess: false,
    });
    expect(
      permissionRuntimeConfig("bypassPermissions", false).sandboxPolicy,
    ).toEqual({
      type: "dangerFullAccess",
      networkAccess: false,
    });
  });

  it("honors an explicit networkAccess opt-out even for full access", () => {
    expect(
      permissionRuntimeConfig("bypassPermissions", false).sandboxPolicy,
    ).toEqual({
      type: "dangerFullAccess",
      networkAccess: false,
    });
  });

  it("keeps legacy sandbox/full settings compatible", () => {
    expect(normalizePermissionMode("sandbox")).toBe("default");
    expect(normalizePermissionMode("full")).toBe("bypassPermissions");
  });
});
