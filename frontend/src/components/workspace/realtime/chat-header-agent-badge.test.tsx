import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ChatHeaderAgentBadge } from "./chat-header-agent-badge";

describe("ChatHeaderAgentBadge", () => {
  it("renders identity as static information without button-like hover styling", () => {
    renderWithProviders(
      <ChatHeaderAgentBadge agent={null} agentId="planner" />,
    );

    const identity = screen.getByTitle("planner");
    expect(identity).not.toHaveClass("hover:bg-muted/45");
    expect(identity).not.toHaveClass("transition-colors");
    expect(identity).not.toHaveAttribute("role", "button");
  });

  it("keeps multi-agent identity static too", () => {
    renderWithProviders(
      <ChatHeaderAgentBadge
        agent={null}
        agentId="planner"
        collaborators={[
          {
            agent_id: "planner",
            name: "planner",
            display_name: "小章",
            role: "tl",
          },
          {
            agent_id: "researcher",
            name: "researcher",
            display_name: "研究员",
            role: "member",
          },
        ]}
      />,
    );

    const identity = screen.getByTitle("小章、研究员");
    expect(identity).not.toHaveClass("hover:bg-muted/45");
    expect(identity).not.toHaveClass("transition-colors");
    expect(identity).not.toHaveAttribute("role", "button");
  });
});
