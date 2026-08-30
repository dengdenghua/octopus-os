import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { CollaborationSessionView } from "./collaboration-session-view";
import type { CollaborationSession } from "@/core/cowork/types";

const t = {
  coworkCollab: {
    searchPlaceholder: "search",
    noResults: "none",
    online: "online",
    members: "Members",
    unread: (n: number) => `${n} unread`,
    kindBlackboard: "Blackboard",
    kindTask: "Task",
    kindEvent: "Event",
    kindRoomMessage: "Room message",
    kindRoomTask: "Room task",
    linkedRoom: "Linked room",
  },
  collab: {
    teamModes: [
      { id: "chat", label: "Solo", description: "One agent answers" },
      { id: "cluster", label: "Cluster", description: "Leader decomposes" },
      { id: "swarm", label: "Swarm", description: "Agents react in parallel" },
      { id: "project", label: "Project", description: "Milestone-driven" },
    ],
  },
} as unknown as Parameters<typeof CollaborationSessionView>[0]["t"];

function session(
  over: Partial<CollaborationSession> = {},
): CollaborationSession {
  return {
    session_id: "t1",
    room_id: null,
    mode: "swarm",
    roster: [{ id: "a" }, { id: "b" }] as CollaborationSession["roster"],
    blackboard: {},
    tasks: [{ task_id: "x" }],
    presence: [{ member_id: "a", online: true, unread: 0 } as never],
    room_messages: [],
    room_participants: [],
    room_tasks: [],
    ...over,
  };
}

describe("CollaborationSessionView", () => {
  it("renders the mode label and core stats", () => {
    render(<CollaborationSessionView session={session()} t={t} />);
    expect(screen.getByTestId("collab-session-view")).toBeTruthy();
    expect(screen.getByText("Swarm")).toBeTruthy(); // swarm mode label from teamModes
    expect(screen.getByText("2")).toBeTruthy(); // roster count
  });

  it("shows the linked-room section only when a room is linked", () => {
    const { rerender } = render(
      <CollaborationSessionView session={session()} t={t} />,
    );
    expect(screen.queryByTestId("collab-session-room")).toBeNull();

    rerender(
      <CollaborationSessionView
        session={session({
          room_id: "room-9",
          room_participants: [{ id: "p1" } as never],
          room_messages: [
            { text: "a" } as never,
            { text: "b" } as never,
            { text: "c" } as never,
          ],
          room_tasks: [{ id: "task-1" } as never, { id: "task-2" } as never],
        })}
        t={t}
      />,
    );
    const room = screen.getByTestId("collab-session-room");
    expect(room).toBeTruthy();
    expect(screen.getByText("room-9")).toBeTruthy();
    expect(room.textContent).toContain("3"); // room message count (unique to the room section)
    expect(room.textContent).toContain("2"); // room task count
  });
});
