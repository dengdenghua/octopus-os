import { describe, expect, test } from "vitest";

import {
  coworkGroupToCollaborationRoster,
  coworkSessionToCollaborationRoster,
} from "./roster";
import type { CollaborationSession, CoworkGroupResponse } from "./types";

function group(ids: string[]): CoworkGroupResponse {
  return {
    thread_id: "thread-1",
    state: {
      mode: "cluster",
      event_count: ids.length,
      is_one_to_one: ids.length <= 1,
      roster: ids.map((id) => ({
        id,
        kind: "agent",
        role: "participant",
        joined_at_message: null,
        grant: { scope: "all" },
        muted: false,
        invited_by: "user",
      })),
    },
    blackboard: {},
    events: [],
    responders: ids,
  };
}

function session(ids: string[]): CollaborationSession {
  return {
    session_id: "thread-1",
    room_id: null,
    mode: "cluster",
    roster: group(ids).state.roster,
    blackboard: {},
    tasks: [],
    presence: [],
    room_messages: [],
    room_participants: [],
    room_tasks: [],
  };
}

describe("cowork roster mapping", () => {
  test("keeps the current task agent as leader and enriches cowork members", () => {
    const roster = coworkGroupToCollaborationRoster(
      group(["general", "codex-cli"]),
      "general",
      [
        {
          name: "general",
          display_name: "Eve",
          avatar_url: "/api/agents/general/avatar",
        },
        { name: "codex-cli", display_name: "Codex CLI", icon: "C" },
      ],
    );

    expect(roster).toEqual([
      {
        agent_id: "general",
        name: "general",
        display_name: "Eve",
        avatar_url: "/api/agents/general/avatar",
        icon: null,
        role: "tl",
      },
      {
        agent_id: "codex-cli",
        name: "codex-cli",
        display_name: "Codex CLI",
        avatar_url: null,
        icon: "C",
        role: "member",
      },
    ]);
  });

  test("returns empty when the thread group has no agent members", () => {
    expect(
      coworkGroupToCollaborationRoster(
        { ...group([]), responders: [] },
        "general",
        [],
      ),
    ).toEqual([]);
  });

  test("maps unified collaboration session roster with the same semantics", () => {
    const roster = coworkSessionToCollaborationRoster(
      session(["general", "analyst"]),
      "general",
      [
        { name: "general", display_name: "General" },
        { name: "analyst", display_name: "Analyst" },
      ],
    );

    expect(roster.map((entry) => [entry.agent_id, entry.role])).toEqual([
      ["general", "tl"],
      ["analyst", "member"],
    ]);
  });
});
