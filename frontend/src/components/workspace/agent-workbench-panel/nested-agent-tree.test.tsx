import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NestedAgentTree } from "./nested-agent-tree";
import type { AgentTile } from "../agent-workbench-utils";

function mockAgentTile(
  id: string,
  overrides: Partial<AgentTile> = {},
): AgentTile {
  return {
    id,
    name: `agent-${id}`,
    label: `Agent ${id}`,
    status: "done",
    task: `Task for ${id}`,
    blackboardWrites: [],
    filesTouched: [],
    eventCount: 1,
    startedAt: Date.now(),
    ...overrides,
  };
}

describe("NestedAgentTree", () => {
  it("renders empty when no agents provided", () => {
    const { container } = render(
      <NestedAgentTree
        agentTiles={[]}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={new Set()}
        onToggleExpand={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders flat list when no parent relationships", () => {
    const tiles = [
      mockAgentTile("a1", { codename: "Alpha-1", task: "Task A" }),
      mockAgentTile("a2", { codename: "Beta-2", task: "Task B" }),
    ];

    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={new Set()}
        onToggleExpand={vi.fn()}
      />,
    );

    expect(screen.getByText("Alpha-1")).toBeInTheDocument();
    expect(screen.getByText("Beta-2")).toBeInTheDocument();
    expect(screen.getByText("Task A")).toBeInTheDocument();
    expect(screen.getByText("Task B")).toBeInTheDocument();
  });

  it("builds hierarchy from parentToolUseId", () => {
    const tiles = [
      mockAgentTile("root", { codename: "RootAgent", task: "Root task", parentToolUseId: undefined }),
      mockAgentTile("child1", {
        codename: "ChildAlpha",
        task: "Alpha task",
        parentToolUseId: "root",
      }),
      mockAgentTile("child2", {
        codename: "ChildBeta",
        task: "Beta task",
        parentToolUseId: "root",
      }),
      mockAgentTile("grandchild", {
        codename: "GrandChildX",
        task: "GrandChild task",
        parentToolUseId: "child1",
      }),
    ];

    const expandedNodes = new Set(["root", "child1"]);
    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={expandedNodes}
        onToggleExpand={vi.fn()}
      />,
    );

    // All agents should be visible when expanded
    expect(screen.getAllByText("RootAgent").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ChildAlpha").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ChildBeta").length).toBeGreaterThan(0);
    expect(screen.getAllByText("GrandChildX").length).toBeGreaterThan(0);
  });

  it("hides children when node is collapsed", () => {
    const tiles = [
      mockAgentTile("root", { codename: "Root" }),
      mockAgentTile("child", { codename: "Child", parentToolUseId: "root" }),
    ];

    const expandedNodes = new Set<string>(); // root is collapsed
    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={expandedNodes}
        onToggleExpand={vi.fn()}
      />,
    );

    expect(screen.getByText("Root")).toBeInTheDocument();
    expect(screen.queryByText("Child")).not.toBeInTheDocument();
  });

  it("calls onSelectAgent when agent is clicked", async () => {
    const user = userEvent.setup();
    const onSelectAgent = vi.fn();
    const tiles = [mockAgentTile("a1", { codename: "Agent-1" })];

    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={onSelectAgent}
        expandedNodes={new Set()}
        onToggleExpand={vi.fn()}
      />,
    );

    await user.click(screen.getByText("Agent-1"));
    expect(onSelectAgent).toHaveBeenCalledWith("a1");
  });

  it("calls onToggleExpand when chevron is clicked", async () => {
    const user = userEvent.setup();
    const onToggleExpand = vi.fn();
    const tiles = [
      mockAgentTile("root", { codename: "Root" }),
      mockAgentTile("child", { codename: "Child", parentToolUseId: "root" }),
    ];

    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={new Set()}
        onToggleExpand={onToggleExpand}
      />,
    );

    // The root row is now a div with role="button", not a button element
    const rootText = screen.getByText("Root");
    const rootRow = rootText.closest('[role="button"]');
    expect(rootRow).toBeInTheDocument();

    // The chevron button is inside the root row
    const chevronButton = rootRow?.querySelector("button");
    expect(chevronButton).toBeInTheDocument();

    await user.click(chevronButton!);

    expect(onToggleExpand).toHaveBeenCalledWith("root");
  });

  it("shows correct status indicators", () => {
    const tiles = [
      mockAgentTile("running", { status: "running", codename: "Running" }),
      mockAgentTile("done", { status: "done", codename: "Done" }),
      mockAgentTile("error", { status: "error", codename: "Error" }),
      mockAgentTile("pending", { status: "pending", codename: "Pending" }),
      mockAgentTile("waiting", {
        status: "waiting_approval",
        codename: "Waiting",
      }),
    ];

    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={new Set()}
        onToggleExpand={vi.fn()}
      />,
    );

    expect(screen.getByText("●")).toBeInTheDocument(); // running
    expect(screen.getByText("✓")).toBeInTheDocument(); // done
    expect(screen.getByText("✗")).toBeInTheDocument(); // error
    expect(screen.getByText("○")).toBeInTheDocument(); // pending
    expect(screen.getByText("⏸")).toBeInTheDocument(); // waiting_approval
  });

  it("displays iteration count when available", () => {
    const tiles = [
      mockAgentTile("a1", { codename: "Agent", iterationCount: 5 }),
    ];

    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={new Set()}
        onToggleExpand={vi.fn()}
      />,
    );

    expect(screen.getByText("5 iter")).toBeInTheDocument();
  });

  it("sorts siblings by startedAt timestamp", () => {
    const tiles = [
      mockAgentTile("root", { codename: "Root", startedAt: 1000 }),
      mockAgentTile("child2", {
        codename: "Second",
        parentToolUseId: "root",
        startedAt: 2000,
      }),
      mockAgentTile("child1", {
        codename: "First-Child",
        parentToolUseId: "root",
        startedAt: 1500,
      }),
    ];

    const expandedNodes = new Set(["root"]);
    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={expandedNodes}
        onToggleExpand={vi.fn()}
      />,
    );

    // Check ordering by getting all buttons and verifying First-Child comes before Second
    const buttons = screen.getAllByRole("button");
    const buttonTexts = buttons.map((btn) => btn.textContent);
    const firstIndex = buttonTexts.findIndex((text) => text?.includes("First-Child"));
    const secondIndex = buttonTexts.findIndex((text) => text?.includes("Second"));

    expect(firstIndex).toBeGreaterThan(-1);
    expect(secondIndex).toBeGreaterThan(-1);
    expect(firstIndex).toBeLessThan(secondIndex);
  });

  it("handles deep nesting (3 levels)", () => {
    const tiles = [
      mockAgentTile("l0", { codename: "Level-0" }),
      mockAgentTile("l1", { codename: "Level-1", parentToolUseId: "l0" }),
      mockAgentTile("l2", { codename: "Level-2", parentToolUseId: "l1" }),
    ];

    const expandedNodes = new Set(["l0", "l1"]);
    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId={null}
        onSelectAgent={vi.fn()}
        expandedNodes={expandedNodes}
        onToggleExpand={vi.fn()}
      />,
    );

    // Query for each level by text and role together to ensure uniqueness
    const level0Buttons = screen.getAllByText((content, element) => {
      return element?.textContent?.includes("Level-0") ?? false;
    });
    const level1Buttons = screen.getAllByText((content, element) => {
      return element?.textContent?.includes("Level-1") ?? false;
    });
    const level2Buttons = screen.getAllByText((content, element) => {
      return element?.textContent?.includes("Level-2") ?? false;
    });

    expect(level0Buttons.length).toBeGreaterThan(0);
    expect(level1Buttons.length).toBeGreaterThan(0);
    expect(level2Buttons.length).toBeGreaterThan(0);
  });

  it("highlights selected agent", () => {
    const tiles = [
      mockAgentTile("a1", { codename: "Agent-1" }),
      mockAgentTile("a2", { codename: "Agent-2" }),
    ];

    render(
      <NestedAgentTree
        agentTiles={tiles}
        selectedAgentId="a1"
        onSelectAgent={vi.fn()}
        expandedNodes={new Set()}
        onToggleExpand={vi.fn()}
      />,
    );

    const agent1Text = screen.getByText("Agent-1");
    const agent2Text = screen.getByText("Agent-2");
    const agent1Row = agent1Text.closest('[role="button"]');
    const agent2Row = agent2Text.closest('[role="button"]');

    expect(agent1Row).toHaveClass("bg-accent/80");
    expect(agent2Row).not.toHaveClass("bg-accent/80");
  });
});
