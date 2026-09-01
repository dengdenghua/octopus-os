import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { PermissionIndicator } from "./permission-indicator";

function openPermissionMenu(trigger: HTMLElement) {
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
  fireEvent.click(trigger);
}

describe("<PermissionIndicator />", () => {
  it("renders a compact localized menu and changes the selected mode", async () => {
    const onModeChange = vi.fn();

    renderWithProviders(
      <PermissionIndicator
        mode="bypassPermissions"
        onModeChange={onModeChange}
      />,
    );

    const trigger = screen.getByTestId("permission-mode-trigger");
    expect(trigger).toBeInTheDocument();
    expect(trigger).toHaveAccessibleName("Permissions: Full access");
    expect(trigger).toHaveTextContent("Full access");
    expect(trigger.className).toContain("text-muted-foreground");
    expect(trigger.querySelector(".text-warning")).toBeInTheDocument();
    expect(trigger.className).not.toContain("border-amber");
    expect(trigger.className).not.toContain("bg-amber");

    openPermissionMenu(trigger);

    expect(
      await screen.findByTestId("permission-mode-menu"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("permission-mode-option-default"),
    ).toHaveTextContent("Default");
    expect(
      screen.getByTestId("permission-mode-option-acceptEdits"),
    ).toHaveTextContent("Accept edits");
    expect(screen.getByText("Accept edits")).toBeInTheDocument();
    expect(screen.getAllByText("Full access").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("Plan only")).not.toBeInTheDocument();

    expect(
      screen.getByText(
        "File changes run automatically; commands still ask for approval.",
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText("Accept edits"));

    await waitFor(() => {
      expect(onModeChange).toHaveBeenCalledWith("acceptEdits");
    });
  });

  it("warns before enabling full access", async () => {
    const onModeChange = vi.fn();
    renderWithProviders(
      <PermissionIndicator mode="default" onModeChange={onModeChange} />,
    );

    openPermissionMenu(screen.getByTestId("permission-mode-trigger"));
    fireEvent.click(
      await screen.findByTestId("permission-mode-option-bypassPermissions"),
    );

    expect(onModeChange).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("dialog", { name: "Switch to Full access?" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/skips routine confirmation for commands/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Enable Full access" }));

    await waitFor(() => {
      expect(onModeChange).toHaveBeenCalledWith("bypassPermissions");
    });
  });

  it("keeps legacy plan mode out of the menu", async () => {
    renderWithProviders(
      <PermissionIndicator mode="default" onModeChange={vi.fn()} />,
    );

    openPermissionMenu(
      screen.getByRole("button", { name: "Permissions: Default" }),
    );

    const confirmItem = await screen.findByTestId(
      "permission-mode-option-default",
    );
    expect(confirmItem).toBeInTheDocument();
    expect(
      screen.queryByTestId("permission-mode-option-plan"),
    ).not.toBeInTheDocument();

    expect(
      screen.getByText(
        "Any write or command asks for your approval first. Safest option.",
      ),
    ).toBeInTheDocument();
  });
});
