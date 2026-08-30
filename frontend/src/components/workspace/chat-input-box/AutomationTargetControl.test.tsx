import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { AutomationTargetControl } from "./AutomationTargetControl";

const getRelayStatusMock = vi.fn();
const listComputerTargetsMock = vi.fn();

vi.mock("@/core/browser/api", () => ({
  getRelayStatus: (...args: unknown[]) => getRelayStatusMock(...args),
}));

vi.mock("@/core/computer/api", () => ({
  listComputerTargets: (...args: unknown[]) => listComputerTargetsMock(...args),
}));

describe("<AutomationTargetControl />", () => {
  beforeEach(() => {
    getRelayStatusMock.mockReset().mockResolvedValue({
      connected: true,
      extension_version: "1.0.0",
      pending_commands: 0,
      active_tab: {
        id: 42,
        title: "Echo dashboard",
        url: "https://echo.test/dashboard",
      },
    });
    listComputerTargetsMock.mockReset().mockResolvedValue({
      schema: "echo.automation_targets.v1",
      targets: [
        {
          kind: "desktop_window",
          source: "computer",
          id: "window-7",
          title: "Project notes",
          app_id: "com.apple.Notes",
          app_name: "Notes",
          frontmost: true,
        },
      ],
      count: 1,
      backend: "macos-native",
    });
  });

  it("lists the live browser tab and desktop windows as stable targets", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<AutomationTargetControl onChange={onChange} />);

    await user.click(
      screen.getByRole("button", {
        name: "Choose a browser or desktop window",
      }),
    );

    await user.click(
      await screen.findByRole("menuitemradio", {
        name: /Echo dashboard/,
      }),
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "browser_tab",
        source: "browser_relay",
        id: "42",
        url: "https://echo.test/dashboard",
      }),
    );
  });

  it("can clear a pinned target", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(
      <AutomationTargetControl
        value={{
          kind: "desktop_window",
          source: "computer",
          id: "window-7",
          title: "Project notes",
          app_name: "Notes",
        }}
        onChange={onChange}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: "Choose a browser or desktop window",
      }),
    );
    await user.click(
      screen.getByRole("menuitem", { name: "Clear pinned target" }),
    );
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("can live inside the composer's plus menu without its own button", async () => {
    const user = userEvent.setup();
    const onAddCurrentWindow = vi.fn();
    renderWithProviders(
      <DropdownMenu>
        <DropdownMenuTrigger>Insert</DropdownMenuTrigger>
        <DropdownMenuContent>
          <AutomationTargetControl
            placement="submenu"
            onChange={vi.fn()}
            onAddCurrentWindow={onAddCurrentWindow}
          />
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    await user.click(screen.getByRole("button", { name: "Insert" }));

    expect(
      await screen.findByTestId("automation-target-submenu-trigger"),
    ).toHaveTextContent("Window");
    expect(
      screen.queryByTestId("automation-target-trigger"),
    ).not.toBeInTheDocument();

    await user.hover(screen.getByTestId("automation-target-submenu-trigger"));
    fireEvent.click(
      await screen.findByRole("menuitem", {
        name: /Attach current window snapshot/,
      }),
    );
    expect(onAddCurrentWindow).toHaveBeenCalledTimes(1);
  });
});
