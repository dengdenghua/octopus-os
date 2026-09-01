import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ensureDefaultPanels } from "./default-panels";
import { PanelHost } from "./panel-host";
import { registerPanel, resetPanelsForTests } from "./panel-manifest";

describe("PanelHost", () => {
  beforeEach(() => {
    resetPanelsForTests();
    ensureDefaultPanels();
  });

  it("renders nothing for an empty zone", () => {
    render(<PanelHost zone="settings" />);
    expect(screen.queryByTestId("panel-host-settings")).toBeNull();
  });

  it("renders the registered panels of a zone with context", () => {
    render(<PanelHost zone="workspace" context={{ threadId: "t-9" }} />);
    expect(screen.getByTestId("panel-workbench.system-status")).toBeTruthy();
    expect(screen.getByText("thread: t-9")).toBeTruthy();
  });

  it("only renders panels of the requested zone", () => {
    registerPanel({
      id: "workspace.extra",
      title: "Extra",
      zone: "workspace",
      component: () => <div>extra</div>,
    });
    render(<PanelHost zone="workbench" />);
    expect(screen.queryByText("extra")).toBeNull();
  });

  it("uses a custom header renderer when provided", () => {
    render(
      <PanelHost
        zone="workspace"
        renderHeader={(title) => <div data-testid="custom-header">{title}</div>}
      />,
    );
    expect(screen.getByTestId("custom-header")).toHaveTextContent(
      "System Status",
    );
  });
});
