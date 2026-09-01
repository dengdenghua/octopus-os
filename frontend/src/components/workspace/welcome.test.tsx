import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

vi.mock("@/core/agents", () => ({
  useAgents: () => ({ agents: [] }),
}));

vi.mock("@/core/agents/active", () => ({
  useActiveAgentId: () => null,
}));

import { Welcome } from "./welcome";

describe("Welcome heading semantics", () => {
  it("leaves the page-level heading to ChatPageLayout", () => {
    renderWithProviders(<Welcome agentName="echo" />);

    expect(screen.getByRole("heading", { level: 2 })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });
});
