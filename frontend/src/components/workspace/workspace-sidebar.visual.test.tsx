import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Collapsible } from "@/components/ui/collapsible";

import { __testing } from "./workspace-sidebar";

const ProjectGroupTrigger = __testing.ProjectGroupTrigger;
const SidebarTimestamp = __testing.SidebarTimestamp;

describe("workspace sidebar visual accessibility", () => {
  it("exposes a semantic tooltip for a truncated project name", async () => {
    const user = userEvent.setup();
    const project = "echo-agent-with-a-project-name-that-does-not-fit";

    render(
      <Collapsible>
        <ProjectGroupTrigger project={project} threadCount={12} deletable />
      </Collapsible>,
    );

    const trigger = screen.getByRole("button", { name: new RegExp(project) });
    expect(screen.getByText(project)).toHaveClass("truncate");

    await user.hover(trigger);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(project);
    expect(trigger).toHaveAttribute("aria-describedby", tooltip.id);
  });

  it("renders compact timestamps with the stronger sidebar text token", () => {
    render(<SidebarTimestamp updatedAt="2026-08-21T00:00:00Z" />);

    const timestamp = screen.getByText(/\S+/);
    expect(timestamp).toHaveClass("text-sidebar-foreground/70", "font-medium");
  });
});
