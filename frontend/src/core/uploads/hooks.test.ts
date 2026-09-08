import { beforeEach, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "alice" }));

vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import { uploadsQueryKey } from "./hooks";

beforeEach(() => {
  identity.actor = "alice";
});

it("keeps uploaded-file caches separate for the same thread id", () => {
  expect(uploadsQueryKey("thread-1")).toEqual([
    "uploads",
    "list",
    "alice",
    "thread-1",
  ]);

  identity.actor = "bob";
  expect(uploadsQueryKey("thread-1")).toEqual([
    "uploads",
    "list",
    "bob",
    "thread-1",
  ]);
});
