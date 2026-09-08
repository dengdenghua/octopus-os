import { describe, expect, it } from "vitest";

import {
  projectByThreadQueryKey,
  projectQueryKey,
  projectsQueryKey,
  threadMapQueryKey,
} from "./hooks";

describe("project query scopes", () => {
  it("keeps project lists, details and thread maps in the actor namespace", () => {
    expect(projectsQueryKey("alice")).toEqual(["projects", "alice"]);
    expect(projectQueryKey("project-1", "alice")).toEqual([
      "project",
      "alice",
      "project-1",
    ]);
    expect(projectByThreadQueryKey("thread-1", "alice")).toEqual([
      "project",
      "by-thread",
      "alice",
      "thread-1",
    ]);
    expect(threadMapQueryKey("alice")).toEqual(["thread-map", "alice"]);
    expect(projectsQueryKey("alice")).not.toEqual(projectsQueryKey("bob"));
  });
});
