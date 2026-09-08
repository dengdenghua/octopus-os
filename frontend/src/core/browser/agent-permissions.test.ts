import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));
vi.mock("@/core/auth/api", () => ({ currentActorId: () => identity.actor }));

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
    identity.actor = "account-a";
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

  it("isolates permissions and audit history between actors", () => {
    setBrowserAgentPermission("https://example.com", "allow");
    recordBrowserAgentAudit({
      origin: "https://example.com",
      action: "click:#submit",
      outcome: "allowed",
    });

    identity.actor = "account-b";
    expect(getBrowserAgentPermission("https://example.com")).toBe("ask");
    expect(listBrowserAgentAudit()).toEqual([]);
  });
});
