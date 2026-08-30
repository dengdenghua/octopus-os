import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { RightPanelMenu, type RightPanelPage } from "./right-panel-menu";

function renderMenu(activePage: RightPanelPage | null = null) {
  const actions = {
    onClosePanel: vi.fn(),
    onOpenAgent: vi.fn(),
    onOpenArtifacts: vi.fn(),
    onOpenPlan: vi.fn(),
    onOpenPreview: vi.fn(),
    onOpenResearch: vi.fn(),
    onOpenResearchHistory: vi.fn(),
  };

  renderWithProviders(
    <RightPanelMenu
      activePage={activePage}
      artifactCount={0}
      hasAgentWorkbench
      hasPlan={false}
      hasPreview={false}
      hasResearch={false}
      hasResearchHistory={false}
      {...actions}
    />,
    { locale: "zh-CN" },
  );

  return actions;
}

describe("RightPanelMenu header toggle", () => {
  it("exposes the inactive workbench toggle as unpressed and opens the default panel", async () => {
    const user = userEvent.setup();
    const actions = renderMenu();

    const toggle = screen.getByRole("button", { name: "打开右侧窗口" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveAttribute("data-state", "closed");

    await user.click(toggle);
    expect(actions.onOpenAgent).toHaveBeenCalledTimes(1);
    expect(actions.onClosePanel).not.toHaveBeenCalled();
  });

  it("exposes an open workbench as pressed and closes the active panel", async () => {
    const user = userEvent.setup();
    const actions = renderMenu("agent");

    const toggle = screen.getByRole("button", { name: "关闭右侧窗口" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(toggle).toHaveAttribute("data-state", "open");

    await user.click(toggle);
    expect(actions.onClosePanel).toHaveBeenCalledTimes(1);
    expect(actions.onOpenAgent).not.toHaveBeenCalled();
  });
});
