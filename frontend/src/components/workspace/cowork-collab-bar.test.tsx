import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { PresenceDots, SearchHitList } from "./cowork-collab-bar";
import type {
  CoworkMemberPresence,
  CoworkSearchHit,
} from "@/core/cowork/types";

const t = {
  coworkCollab: {
    searchPlaceholder: "search",
    noResults: "No matches",
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
} as unknown as Parameters<typeof PresenceDots>[0]["t"];

function member(over: Partial<CoworkMemberPresence>): CoworkMemberPresence {
  return {
    member_id: "m",
    last_read: 0,
    last_seen_at: null,
    online: false,
    unread: 0,
    ...over,
  };
}

describe("PresenceDots", () => {
  it("renders nothing with no members", () => {
    const { container } = render(<PresenceDots members={[]} t={t} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows online count without aggregating member unread counters", () => {
    render(
      <PresenceDots
        members={[
          member({ member_id: "a", online: true, unread: 2 }),
          member({ member_id: "b", online: false, unread: 3 }),
        ]}
        t={t}
      />,
    );
    expect(screen.getByText("1 online")).toBeTruthy();
    expect(screen.queryByText("5 unread")).toBeNull();
    expect(screen.queryByTestId("cowork-unread-total")).toBeNull();
  });

  it("keeps the presence strip quiet when everything is read", () => {
    render(<PresenceDots members={[member({ online: true })]} t={t} />);
    expect(screen.queryByTestId("cowork-unread-total")).toBeNull();
  });
});

describe("SearchHitList", () => {
  const hit = (over: Partial<CoworkSearchHit>): CoworkSearchHit => ({
    kind: "blackboard",
    title: "decision",
    snippet: "ship the report",
    score: 1,
    actor: "alice",
    ts: null,
    ref: {},
    ...over,
  });

  it("shows an empty state when there are no hits", () => {
    render(<SearchHitList hits={[]} t={t} />);
    expect(screen.getByText("No matches")).toBeTruthy();
    expect(screen.queryByTestId("cowork-search-results")).toBeNull();
  });

  it("renders a row per hit with its kind label", () => {
    render(
      <SearchHitList
        hits={[
          hit({ title: "decision" }),
          hit({ kind: "task", title: "scan rivals" }),
        ]}
        t={t}
      />,
    );
    expect(screen.getByTestId("cowork-search-results")).toBeTruthy();
    expect(screen.getByText("decision")).toBeTruthy();
    expect(screen.getByText("scan rivals")).toBeTruthy();
    expect(screen.getByText("Blackboard")).toBeTruthy();
    expect(screen.getByText("Task")).toBeTruthy();
  });

  it("labels linked room transcript hits distinctly", () => {
    render(
      <SearchHitList
        hits={[
          hit({ kind: "room_message", title: "Planner", snippet: "room note" }),
        ]}
        t={t}
      />,
    );
    expect(screen.getByText("Room message")).toBeTruthy();
    expect(screen.getByText("Planner")).toBeTruthy();
  });

  it("labels linked room task hits distinctly", () => {
    render(
      <SearchHitList
        hits={[hit({ kind: "room_task", title: "Draft launch plan" })]}
        t={t}
      />,
    );
    expect(screen.getByText("Room task")).toBeTruthy();
    expect(screen.getByText("Draft launch plan")).toBeTruthy();
  });
});
