import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import EvolutionPage from "./page";

vi.mock("@/components/workspace/dual-helix-evolution-panel", () => ({
  DualHelixEvolutionPanel: ({ view }: { view: string }) => (
    <div>helix-{view}</div>
  ),
}));

vi.mock("@/components/workspace/evolution-governance-panel", () => ({
  EvolutionGovernancePanel: () => <div>governance-panel</div>,
}));

vi.mock("@/components/workspace/workspace-container", () => ({
  WorkspaceContainer: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  WorkspaceBody: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}));

describe("EvolutionPage", () => {
  it("consolidates evolution into overview, experiments, candidates, deployments, and governance", async () => {
    const user = userEvent.setup();
    renderWithProviders(<EvolutionPage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/evolution",
    });

    expect(screen.getByText("helix-overview")).toBeInTheDocument();
    expect(screen.queryByText("游戏化")).not.toBeInTheDocument();
    expect(screen.queryByText("经典视图")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "实验" }));
    expect(screen.getByText("helix-experiments")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "候选" }));
    expect(screen.getByText("helix-candidates")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "部署" }));
    expect(screen.getByText("helix-deployments")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "安全治理" }));
    expect(await screen.findByText("governance-panel")).toBeInTheDocument();
  });
});
