import { describe, expect, test } from "vitest";

import type {
  CollaborationSession,
  CoworkMessageProjectActionResponse,
} from "./types";
import {
  collabSessionRefetchInterval,
  mergeCoworkProjectActionIntoSession,
} from "./hooks";

const session: CollaborationSession = {
  session_id: "thread-1",
  room_id: "room-1",
  mode: "project",
  roster: [],
  blackboard: {},
  tasks: [],
  presence: [],
  room_messages: [{ seq: 3, text: "原始消息", metadata: {} }],
  room_participants: [],
  room_tasks: [],
};

describe("cowork project action cache merge", () => {
  test("replaces the enriched source and appends the system card in sequence", () => {
    const response: CoworkMessageProjectActionResponse = {
      ok: true,
      replayed: false,
      created: true,
      action_id: "MA-1",
      action: "record_decision",
      project_id: "P-1",
      target: { kind: "decision", id: "EV-1" },
      receipt: {
        id: "MA-1",
        action: "record_decision",
        project_id: "P-1",
        target: { kind: "decision", id: "EV-1" },
      },
      source_message: {
        seq: 3,
        text: "原始消息",
        metadata: {
          entity_refs: [{ kind: "decision", id: "EV-1" }],
        },
      },
      system_card_message: {
        seq: 4,
        text: "已记录项目决策",
        metadata: { message_type: "system_card" },
      },
    };

    const merged = mergeCoworkProjectActionIntoSession(session, response);

    expect(merged?.room_messages.map((message) => message.seq)).toEqual([3, 4]);
    expect(merged?.room_messages[0].metadata?.entity_refs?.[0].id).toBe("EV-1");
  });
});

describe("collaboration session polling", () => {
  test("does not continuously poll an ordinary direct chat", () => {
    const directSession: CollaborationSession = {
      ...session,
      room_id: null,
      mode: "chat",
      roster: [],
    };

    expect(collabSessionRefetchInterval(directSession)).toBe(false);
  });

  test("keeps linked rooms and visible collaboration surfaces live", () => {
    expect(collabSessionRefetchInterval(session)).toBe(5_000);
    expect(
      collabSessionRefetchInterval({
        ...session,
        room_id: null,
        mode: "cluster",
      }),
    ).toBe(5_000);
    expect(
      collabSessionRefetchInterval(undefined, {
        live: true,
        refetchInterval: 1_000,
      }),
    ).toBe(1_000);
  });

  test("respects an explicitly disabled query", () => {
    expect(
      collabSessionRefetchInterval(session, { enabled: false, live: true }),
    ).toBe(false);
  });
});
