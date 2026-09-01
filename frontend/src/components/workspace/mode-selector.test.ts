import { afterEach, beforeEach, describe, expect, test } from "vitest";

import {
  modeFromProjectKind,
  readStoredAuditIntensity,
  readStoredModeOverride,
  writeStoredAuditIntensity,
  writeStoredModeOverride,
} from "./mode-selector";

const STORAGE_KEY = "echo:modeOverride";

describe("modeFromProjectKind", () => {
  test("maps builder to develop", () => {
    expect(modeFromProjectKind("builder")).toBe("develop");
  });

  test("maps coder to develop", () => {
    expect(modeFromProjectKind("coder")).toBe("develop");
  });

  test("maps architect to audit", () => {
    expect(modeFromProjectKind("architect")).toBe("audit");
  });
});

describe("readStoredModeOverride / writeStoredModeOverride", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  test("returns null when nothing is stored", () => {
    expect(readStoredModeOverride("/workspace/a")).toBeNull();
  });

  test("round-trips a stored override and keeps the map structure", () => {
    writeStoredModeOverride("/workspace/a", "audit");
    writeStoredModeOverride("/workspace/b", "uxui");

    expect(readStoredModeOverride("/workspace/a")).toBe("audit");
    expect(readStoredModeOverride("/workspace/b")).toBe("uxui");

    const raw = window.localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!) as Record<string, unknown>;
    expect(parsed).toEqual({
      "/workspace/a": { mode: "audit" },
      "/workspace/b": { mode: "uxui" },
    });
  });

  test("reads a legacy flat-string override (backward compatible)", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ "/workspace/a": "audit" }),
    );
    expect(readStoredModeOverride("/workspace/a")).toBe("audit");
    expect(readStoredAuditIntensity("/workspace/a")).toBeNull();
  });

  test("overwrites the override for an existing workspace path", () => {
    writeStoredModeOverride("/workspace/a", "develop");
    writeStoredModeOverride("/workspace/a", "uxui");

    expect(readStoredModeOverride("/workspace/a")).toBe("uxui");
  });

  test("returns null for an invalid stored value", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ "/workspace/a": "invalid-mode" }),
    );
    expect(readStoredModeOverride("/workspace/a")).toBeNull();
  });

  test("returns null when stored JSON is malformed", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not valid json");
    expect(readStoredModeOverride("/workspace/a")).toBeNull();
  });

  test("no-ops on the SSR branch (window undefined)", () => {
    const originalWindow = globalThis.window;
    // Simulate server-side rendering where window is missing.
    (globalThis as { window?: unknown }).window = undefined;

    expect(readStoredModeOverride("/workspace/a")).toBeNull();
    expect(() =>
      writeStoredModeOverride("/workspace/a", "audit"),
    ).not.toThrow();

    (globalThis as { window?: unknown }).window = originalWindow;
    expect(readStoredModeOverride("/workspace/a")).toBeNull();
  });
});
describe("readStoredAuditIntensity / writeStoredAuditIntensity", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  test("returns null when nothing is stored", () => {
    expect(readStoredAuditIntensity("/workspace/a")).toBeNull();
  });

  test("round-trips the audit intensity with the mode", () => {
    writeStoredModeOverride("/workspace/a", "audit");
    writeStoredAuditIntensity("/workspace/a", "max");

    expect(readStoredModeOverride("/workspace/a")).toBe("audit");
    expect(readStoredAuditIntensity("/workspace/a")).toBe("max");

    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = JSON.parse(raw!) as Record<string, unknown>;
    expect(parsed["/workspace/a"]).toEqual({
      mode: "audit",
      auditIntensity: "max",
    });
  });

  test("writing the mode preserves an existing intensity", () => {
    writeStoredAuditIntensity("/workspace/a", "max");
    writeStoredModeOverride("/workspace/a", "audit");

    expect(readStoredAuditIntensity("/workspace/a")).toBe("max");
    expect(readStoredModeOverride("/workspace/a")).toBe("audit");
  });

  test("writing the intensity preserves an existing mode", () => {
    writeStoredModeOverride("/workspace/a", "audit");
    writeStoredAuditIntensity("/workspace/a", "max");

    expect(readStoredModeOverride("/workspace/a")).toBe("audit");
    expect(readStoredAuditIntensity("/workspace/a")).toBe("max");
  });

  test("ignores an invalid stored intensity", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        "/workspace/a": { mode: "audit", auditIntensity: "ultra" },
      }),
    );
    expect(readStoredAuditIntensity("/workspace/a")).toBeNull();
    // The mode is still read independently of the intensity.
    expect(readStoredModeOverride("/workspace/a")).toBe("audit");
  });

  test("migrates a legacy flat-string row when the intensity is written", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ "/workspace/a": "audit" }),
    );
    writeStoredAuditIntensity("/workspace/a", "max");

    expect(readStoredModeOverride("/workspace/a")).toBe("audit");
    expect(readStoredAuditIntensity("/workspace/a")).toBe("max");
  });
});
