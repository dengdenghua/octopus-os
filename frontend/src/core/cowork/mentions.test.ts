import { describe, expect, test } from "vitest";

import type { CollaborationSession } from "./types";
import {
  coworkSessionToMentionMembers,
  extractCoworkAgentMentions,
} from "./mentions";

const session: CollaborationSession = {
  session_id: "thread-1",
  room_id: "room-1",
  mode: "project",
  roster: [
    {
      id: "planner",
      kind: "agent",
      role: "participant",
      grant: { scope: "all" },
    },
  ],
  blackboard: {},
  tasks: [],
  presence: [],
  room_messages: [],
  room_participants: [
    { id: "planner", kind: "agent", display_name: "旧名称" },
    { id: "alice", kind: "human", display_name: "Alice" },
  ],
  room_tasks: [],
};

describe("cowork member mentions", () => {
  test("adapts roster members to stable @ tokens while keeping display names", () => {
    const members = coworkSessionToMentionMembers(session, [
      { name: "planner", display_name: "规划师", avatar_url: "/planner.png" },
    ]);

    expect(members).toEqual([
      expect.objectContaining({
        member_id: "planner",
        display_name: "规划师",
        mention_value: "agent:planner",
        avatar_url: "/planner.png",
      }),
      expect.objectContaining({
        member_id: "alice",
        display_name: "Alice",
        mention_value: "Alice",
      }),
    ]);
  });

  test("extracts unique stable agent ids for item assignment", () => {
    expect(
      extractCoworkAgentMentions(
        "请 @agent:planner 拆解，再交给 @agent:builder_1；@agent:planner 已知悉",
      ),
    ).toEqual(["planner", "builder_1"]);
  });
});
