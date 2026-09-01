import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ensureDefaultPanels } from "@/core/panels/default-panels";
import { resetPanelsForTests } from "@/core/panels/panel-manifest";
import { renderWithProviders } from "@/test/harness";

import IntelligencePage from "./page";

vi.mock("@/components/workspace/automation/automation-configured-tab", () => ({
  AutomationConfiguredTab: () => <div>configured-stub</div>,
}));

vi.mock("@/components/workspace/automation/automation-history-tab", () => ({
  AutomationHistoryTab: () => <div>history-stub</div>,
}));

vi.mock("@/components/workspace/automation/automation-templates-tab", () => ({
  AutomationTemplatesTab: () => <div>templates-stub</div>,
}));

vi.mock("@/components/workspace/automation/automation-create-dialog", () => ({
  AutomationCreateDialog: () => null,
}));

vi.mock("@/components/workspace/workspace-container", () => ({
  WorkspaceContainer: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  WorkspaceBody: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}));

describe("IntelligencePage", () => {
  beforeEach(() => {
    // The reference workspace panel is registered by default-panels.tsx
    // through the usePanels import chain; reset to a known state, then
    // re-register the defaults (the module-level ensure runs only once).
    resetPanelsForTests();
    ensureDefaultPanels();
  });

  it("renders the composition-layer PanelHost tab with registered panels", async () => {
    const user = userEvent.setup();
    renderWithProviders(<IntelligencePage />, {
      locale: "zh-CN",
      initialRoute: "/workspace/intelligence",
    });

    const panelTab = screen.getByRole("tab", { name: "面板" });
    await user.click(panelTab);

    // The reference workspace panel renders through PanelHost — the
    // "register-and-render" contract in a real page.
    expect(screen.getByTestId("panel-workbench.system-status")).toBeTruthy();
    // Both the host header and the panel's own title carry the name.
    expect(screen.getAllByText("System Status").length).toBeGreaterThan(0);
  });
});
