import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  browserHttpOrigin,
  clearBrowserAgentAudit,
  getBrowserAgentPermission,
  listBrowserAgentAudit,
  listBrowserAgentPermissions,
  recordBrowserAgentAudit,
  setBrowserAgentPermission,
} from "./agent-permissions";

describe("browser agent permissions", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("normalizes http origins and rejects internal schemes", () => {
    expect(browserHttpOrigin("https://example.com/a?q=1")).toBe(
      "https://example.com",
    );
    expect(browserHttpOrigin("echo://home")).toBeNull();
  });

  it("defaults to ask and remembers per-origin choices", () => {
    expect(getBrowserAgentPermission("https://example.com/a")).toBe("ask");
    setBrowserAgentPermission("https://example.com/a", "allow");
    expect(getBrowserAgentPermission("https://example.com/b")).toBe("allow");
    expect(getBrowserAgentPermission("https://other.example/")).toBe("ask");
    expect(listBrowserAgentPermissions()).toHaveLength(1);
    setBrowserAgentPermission("https://example.com", "ask");
    expect(listBrowserAgentPermissions()).toHaveLength(0);
  });

  it("keeps a bounded, clearable audit trail", () => {
    recordBrowserAgentAudit({
      origin: "https://example.com",
      action: "click:#submit",
      outcome: "confirmed",
    });
    expect(listBrowserAgentAudit()).toMatchObject([
      {
        origin: "https://example.com",
        action: "click:#submit",
        outcome: "confirmed",
      },
    ]);
    clearBrowserAgentAudit();
    expect(listBrowserAgentAudit()).toEqual([]);
  });
});
