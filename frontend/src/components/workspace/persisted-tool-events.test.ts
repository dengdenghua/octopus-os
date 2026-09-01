import { describe, expect, test } from "vitest";

import type { Message } from "@/core/api/types";

import { deriveAgentPhases, progressForPhases } from "./agent-phases";
import type { LiveToolEvent } from "./live-tool-timeline";
import {
  persistedTodoEventsFromMessages,
  restoredTodoEventsForDisplay,
} from "./persisted-tool-events";

function event(partial: Partial<LiveToolEvent>): LiveToolEvent {
  return {
    id: "event-1",
    name: "todo_write",
    status: "done",
    startedAt: 1000,
    iteration: 0,
    ...partial,
  };
}

describe("persisted tool events", () => {
  test("folds saved todo_write tool calls back into progress state", () => {
    const messages: Message[] = [
      {
        id: "assistant-1",
        type: "ai",
        content: "Done.",
        tool_calls: [
          {
            id: "call-1",
            name: "todo_write",
            args: {
              items: [
                { content: "Create plan", status: "completed" },
                { content: "Write report", status: "completed" },
              ],
            },
          },
        ],
      },
    ];
    const staleLiveTodo = event({
      id: "stale-todo",
      input: {
        items: [
          { content: "Create plan", status: "completed" },
          { content: "Write report", status: "in_progress" },
        ],
      },
    });

    const persisted = persistedTodoEventsFromMessages(messages);
    const state = deriveAgentPhases([staleLiveTodo, ...persisted], {
      hasAnswer: true,
      runSettled: true,
    });

    expect(persisted).toHaveLength(1);
    expect(persisted[0]?.status).toBe("done");
    expect(state.phases.map((phase) => phase.status)).toEqual(["done", "done"]);
    expect(progressForPhases(state.phases, state.currentPhase!)).toEqual({
      current: 2,
      total: 2,
    });
  });

  test("does not restore the previous todo plan while a new turn is loading", () => {
    const persisted = [
      event({
        id: "persisted-todo",
        input: {
          items: [
            { content: "Old plan", status: "completed" },
            { content: "Old follow-up", status: "completed" },
          ],
        },
      }),
    ];

    expect(
      restoredTodoEventsForDisplay({
        isLoading: true,
        lastTurnToolEvents: [],
        latestPersistedTodoEvents: persisted,
      }),
    ).toEqual([]);
    expect(
      restoredTodoEventsForDisplay({
        isLoading: false,
        lastTurnToolEvents: [],
        latestPersistedTodoEvents: persisted,
      }),
    ).toEqual(persisted);
    expect(
      restoredTodoEventsForDisplay({
        isLoading: false,
        lastTurnToolEvents: [event({ id: "live-todo" })],
        latestPersistedTodoEvents: persisted,
      }),
    ).toEqual([]);
  });
});
