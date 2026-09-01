import { describe, expect, test } from "vitest";

import { createEchoBrowserSessionIdentity } from "./api";

describe("browser session identity", () => {
  test("binds browser sessions to an Echo workspace path", () => {
    const first = createEchoBrowserSessionIdentity({
      threadId: "thread-1",
      workspacePath: "F:\\work\\echo-agent",
    });
    const second = createEchoBrowserSessionIdentity({
      threadId: "thread-2",
      workspacePath: "F:\\work\\echo-agent",
    });

    expect(first).toEqual(second);
    expect(first.scope).toBe("workspace");
    expect(first.displayName).toBe("echo-agent");
    expect(first.projectId).toBe("echo-workspace:F:\\work\\echo-agent");
    expect(first.sessionId).toMatch(/^echo-workspace-echo-agent-/);
    expect(first.profileId).toBe(first.sessionId);
  });

  test("keeps same-name workspace folders isolated", () => {
    const alpha = createEchoBrowserSessionIdentity({
      workspacePath: "F:\\alpha\\echo-agent",
    });
    const beta = createEchoBrowserSessionIdentity({
      workspacePath: "F:\\beta\\echo-agent",
    });

    expect(alpha.displayName).toBe(beta.displayName);
    expect(alpha.sessionId).not.toBe(beta.sessionId);
    expect(alpha.profileId).not.toBe(beta.profileId);
  });

  test("falls back to thread scope when no workspace is active", () => {
    const identity = createEchoBrowserSessionIdentity({
      threadId: "thread-123456789",
    });

    expect(identity.scope).toBe("thread");
    expect(identity.displayName).toBe("thread/thread-1");
    expect(identity.projectId).toBe("echo-thread:thread-123456789");
    expect(identity.sessionId).toMatch(/^echo-thread-thread-thread-1-/);
  });
});
