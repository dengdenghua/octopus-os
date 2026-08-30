import { describe, expect, it, vi } from "vitest";

import {
  detachGroupProjectCapability,
  resolveGroupProjectCapabilityAction,
} from "./group-project-capability";

describe("resolveGroupProjectCapabilityAction", () => {
  it("opens the same thread workbench whenever project capability is bound", () => {
    expect(
      resolveGroupProjectCapabilityAction({
        isNewThread: false,
        isGroupConversation: true,
        hasBoundProject: true,
        canManageGroup: false,
      }),
    ).toBe("open");
  });

  it("lets only an existing group owner create the durable project plan", () => {
    expect(
      resolveGroupProjectCapabilityAction({
        isNewThread: false,
        isGroupConversation: true,
        hasBoundProject: false,
        canManageGroup: true,
      }),
    ).toBe("create");
    expect(
      resolveGroupProjectCapabilityAction({
        isNewThread: false,
        isGroupConversation: true,
        hasBoundProject: false,
        canManageGroup: false,
      }),
    ).toBeNull();
  });

  it("does not turn private or unsaved chats into project-capability actions", () => {
    expect(
      resolveGroupProjectCapabilityAction({
        isNewThread: false,
        isGroupConversation: false,
        hasBoundProject: false,
        canManageGroup: true,
      }),
    ).toBeNull();
    expect(
      resolveGroupProjectCapabilityAction({
        isNewThread: true,
        isGroupConversation: true,
        hasBoundProject: false,
        canManageGroup: true,
      }),
    ).toBeNull();
  });
});

describe("detachGroupProjectCapability", () => {
  it("uses one guarded non-force request for a completed project", async () => {
    const requestDetach = vi.fn().mockResolvedValue({ detached: true });
    const confirmForce = vi.fn();

    await expect(
      detachGroupProjectCapability({
        expectedProjectId: "P-completed",
        requestDetach,
        confirmForce,
      }),
    ).resolves.toBe("detached");
    expect(requestDetach).toHaveBeenCalledWith({
      expectedProjectId: "P-completed",
      force: false,
    });
    expect(confirmForce).not.toHaveBeenCalled();
  });

  it("requires a second confirmation before force-detaching active work", async () => {
    const activeError = Object.assign(new Error("active"), {
      status: 409,
      code: "PROJECT_ACTIVE",
    });
    const requestDetach = vi
      .fn()
      .mockRejectedValueOnce(activeError)
      .mockResolvedValueOnce({ detached: true });

    await expect(
      detachGroupProjectCapability({
        expectedProjectId: "P-running",
        requestDetach,
        confirmForce: vi.fn().mockResolvedValue(true),
      }),
    ).resolves.toBe("detached");
    expect(requestDetach).toHaveBeenNthCalledWith(1, {
      expectedProjectId: "P-running",
      force: false,
    });
    expect(requestDetach).toHaveBeenNthCalledWith(2, {
      expectedProjectId: "P-running",
      force: true,
    });
  });

  it("keeps active work attached when the stronger confirmation is cancelled", async () => {
    const requestDetach = vi.fn().mockRejectedValue(
      Object.assign(new Error("active"), {
        status: 409,
        code: "PROJECT_ACTIVE",
      }),
    );

    await expect(
      detachGroupProjectCapability({
        expectedProjectId: "P-running",
        requestDetach,
        confirmForce: vi.fn().mockResolvedValue(false),
      }),
    ).resolves.toBe("cancelled");
    expect(requestDetach).toHaveBeenCalledTimes(1);
  });

  it("never force-detaches a concurrently changed binding", async () => {
    const requestDetach = vi.fn().mockRejectedValue(
      Object.assign(new Error("changed"), {
        status: 409,
        code: "PROJECT_BINDING_CHANGED",
      }),
    );
    const confirmForce = vi.fn();

    await expect(
      detachGroupProjectCapability({
        expectedProjectId: "P-stale",
        requestDetach,
        confirmForce,
      }),
    ).resolves.toBe("binding-changed");
    expect(confirmForce).not.toHaveBeenCalled();
    expect(requestDetach).toHaveBeenCalledTimes(1);
  });
});
