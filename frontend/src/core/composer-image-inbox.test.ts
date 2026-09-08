import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "actor-one" }));
vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import {
  clearComposerImageEntries,
  consumeComposerImageEntries,
  queueComposerImageEntry,
  readLastComposerTarget,
  rememberLastComposerTarget,
} from "./composer-image-inbox";

describe("composer-image-inbox", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    identity.actor = "actor-one";
  });

  it("round-trips a queued image for its target thread", () => {
    queueComposerImageEntry({
      threadId: "thread-a",
      dataUrl: "data:image/png;base64,AA==",
      filename: "shot.png",
    });
    expect(consumeComposerImageEntries("thread-b")).toEqual([]);
    expect(consumeComposerImageEntries("thread-a")).toMatchObject([
      { threadId: "thread-a", filename: "shot.png" },
    ]);
    expect(consumeComposerImageEntries("thread-a")).toEqual([]);
  });

  it("isolates queued images and target hints by actor", () => {
    queueComposerImageEntry({
      threadId: "thread-a",
      dataUrl: "data:image/png;base64,AA==",
      filename: "private.png",
    });
    rememberLastComposerTarget("browser-private");
    identity.actor = "actor-two";
    expect(consumeComposerImageEntries("thread-a")).toEqual([]);
    expect(readLastComposerTarget()).toBeNull();
  });

  it("clears all actor-scoped image state at a session boundary", () => {
    queueComposerImageEntry({
      threadId: "thread-a",
      dataUrl: "data:image/png;base64,AA==",
      filename: "private.png",
    });
    rememberLastComposerTarget("browser-private");
    clearComposerImageEntries();
    expect(consumeComposerImageEntries("thread-a")).toEqual([]);
    expect(readLastComposerTarget()).toBeNull();
  });
});
