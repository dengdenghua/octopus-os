import { describe, expect, test } from "vitest";

import {
  collaborationRosterFromThread,
  hydrateCollaborationRoster,
} from "./thread-collaboration";

describe("thread collaboration recovery", () => {
  test("hydrates an emoji-only saved roster from current agent profiles", () => {
    expect(
      hydrateCollaborationRoster(
        [
          {
            agent_id: "echo_mira_voss",
            name: "echo_mira_voss",
            display_name: "Mira",
            avatar_url: null,
            icon: "🧪",
            role: "member",
          },
        ],
        [
          {
            name: "echo_mira_voss",
            display_name: "Mira Voss / Glass Vein",
            avatar_url: "/api/agents/echo_mira_voss/avatar?v=2",
            icon: "M",
          },
        ],
      ),
    ).toEqual([
      {
        agent_id: "echo_mira_voss",
        name: "echo_mira_voss",
        display_name: "Mira Voss / Glass Vein",
        avatar_url: "/api/agents/echo_mira_voss/avatar?v=2",
        icon: "M",
        role: "member",
      },
    ]);
  });

  test("preserves order and roles while matching legacy display names", () => {
    const roster = hydrateCollaborationRoster(
      [
        {
          agent_id: "Eve / Siren",
          name: "Eve / Siren",
          display_name: "Eve / Siren",
          role: "tl",
        },
        {
          agent_id: "unknown",
          name: "unknown",
          display_name: "Unknown",
          role: "member",
        },
      ],
      [
        {
          name: "echo_eve",
          display_name: "Eve / Siren",
          avatar_url: "/api/agents/echo_eve/avatar",
        },
      ],
    );

    expect(roster.map((entry) => [entry.agent_id, entry.role])).toEqual([
      ["Eve / Siren", "tl"],
      ["unknown", "member"],
    ]);
    expect(roster[0]).toMatchObject({
      name: "echo_eve",
      avatar_url: "/api/agents/echo_eve/avatar",
    });
    expect(roster[1]?.avatar_url).toBeUndefined();
  });

  test("recovers a saved agent roster from thread metadata context", () => {
    expect(
      collaborationRosterFromThread(
        {
          context: {
            agent_roster: [
              {
                agent_id: "general",
                display_name: "General",
                role: "tl",
              },
              {
                agent_id: "installed_code_reviewer",
                display_name: "Code Reviewer",
                avatar_url: "/avatar/reviewer.png",
                role: "member",
              },
            ],
          },
        },
        {},
        "general",
      ),
    ).toEqual([
      {
        agent_id: "general",
        avatar_url: null,
        display_name: "General",
        icon: null,
        name: "general",
        role: "tl",
      },
      {
        agent_id: "installed_code_reviewer",
        avatar_url: "/avatar/reviewer.png",
        display_name: "Code Reviewer",
        icon: null,
        name: "installed_code_reviewer",
        role: "member",
      },
    ]);
  });

  test("falls back to task agent refs for older collaboration threads", () => {
    expect(
      collaborationRosterFromThread(
        {},
        {
          task_agent_refs: [
            "general",
            "coder",
            "coder",
            "installed_code_reviewer",
          ],
        },
        "general",
      ),
    ).toEqual([
      {
        agent_id: "general",
        display_name: "general",
        name: "general",
        role: "tl",
      },
      {
        agent_id: "coder",
        display_name: "coder",
        name: "coder",
        role: "member",
      },
      {
        agent_id: "installed_code_reviewer",
        display_name: "installed_code_reviewer",
        name: "installed_code_reviewer",
        role: "member",
      },
    ]);
  });
});
