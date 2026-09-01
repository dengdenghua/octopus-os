import { describe, expect, test } from "vitest";

import {
  threadCollaborationLink,
  threadCollaborationRoute,
} from "./thread-collaboration-link";

describe("thread collaboration links", () => {
  test("uses the unified realtime route for new and existing rooms", () => {
    expect(threadCollaborationRoute("ignored", true)).toBe(
      "/workspace/realtime/new",
    );
    expect(threadCollaborationRoute("room-1", false)).toBe(
      "/workspace/realtime/room-1",
    );
  });

  test("encodes room ids and preserves the current shell URL", () => {
    expect(threadCollaborationRoute("room / 中文", false)).toBe(
      "/workspace/realtime/room%20%2F%20%E4%B8%AD%E6%96%87",
    );
    expect(
      threadCollaborationLink({
        threadId: "room-1",
        isNewThread: false,
        origin: "http://localhost:3000",
        pathname: "/",
      }),
    ).toBe("http://localhost:3000/#/workspace/realtime/room-1");
  });
});
