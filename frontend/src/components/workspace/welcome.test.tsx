import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import type { Agent } from "@/core/agents";

const { roster } = vi.hoisted(() => ({
  roster: [{ name: "general", display_name: "Echo" }] as Agent[],
}));

vi.mock("@/core/agents", () => ({
  useAgents: () => ({ agents: roster }),
}));

vi.mock("@/core/agents/active", () => ({
  useActiveAgentId: () => null,
}));

import { Welcome } from "./welcome";

describe("Welcome heading semantics", () => {
  it("never labels the chosen persona with a stale role's profile", () => {
    renderWithProviders(
      <Welcome
        agentName="general"
        agent={{ name: "coder", display_name: "Coder" } as Agent}
      />,
      { locale: "zh-CN" },
    );
    expect(
      screen.getByRole("heading", { name: "你好，我是 Echo" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Coder/)).not.toBeInTheDocument();
  });

  it("leaves the page-level heading to ChatPageLayout", () => {
    renderWithProviders(<Welcome agentName="echo" />);

    expect(screen.getByRole("heading", { level: 2 })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });
});
