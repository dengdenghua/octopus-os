import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { TeamModePicker, type TeamResponseMode } from "./team-mode-picker";

function ControlledPicker({
  disabledModes = [],
}: {
  disabledModes?: TeamResponseMode[];
}) {
  const [value, setValue] = useState<TeamResponseMode>("chat");
  return (
    <TeamModePicker
      value={value}
      onChange={setValue}
      ariaLabel="Conversation type"
      disabledModes={disabledModes}
      disabledReason="Add an AI member"
      compact
    />
  );
}

describe("<TeamModePicker />", () => {
  it("opens a compact menu with the three response strategies", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ControlledPicker />);

    const trigger = screen.getByRole("button", {
      name: "Conversation type: On demand",
    });
    expect(screen.queryByRole("menuitemradio")).toBeNull();
    await user.click(trigger);

    const solo = screen.getByRole("menuitemradio", { name: /On demand/ });
    const cluster = screen.getByRole("menuitemradio", { name: /Coordinated/ });
    const swarm = screen.getByRole("menuitemradio", { name: /Parallel/ });
    expect(solo).toHaveAttribute("aria-checked", "true");
    expect(cluster).toBeInTheDocument();

    await user.click(swarm);

    expect(
      screen.getByRole("button", { name: "Conversation type: Parallel" }),
    ).toBeInTheDocument();
  });

  it("keeps unavailable strategies disabled and keyboard-selects an available one", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ControlledPicker disabledModes={["cluster"]} />);

    await user.click(
      screen.getByRole("button", { name: "Conversation type: On demand" }),
    );
    const solo = screen.getByRole("menuitemradio", { name: /On demand/ });
    const cluster = screen.getByRole("menuitemradio", { name: /Coordinated/ });
    solo.focus();

    await user.keyboard("{ArrowDown}{Enter}");

    expect(cluster).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByRole("button", { name: "Conversation type: Parallel" }),
    ).toBeInTheDocument();
  });

  it("normalizes legacy project mode to chat and omits project as a turn type", () => {
    renderWithProviders(
      <TeamModePicker
        value="project"
        onChange={vi.fn()}
        ariaLabel="Conversation type"
      />,
    );

    expect(
      screen.getByRole("button", { name: "Conversation type: On demand" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Project")).toBeNull();
  });
});
