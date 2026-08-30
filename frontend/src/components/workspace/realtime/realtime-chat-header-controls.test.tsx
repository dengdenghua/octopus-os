import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import {
  RealtimeChatHeaderActions,
  RealtimeChatHeaderMemberSurface,
} from "./realtime-chat-header-controls";

describe("realtime chat header controls", () => {
  it("presents one member trigger in the header", () => {
    renderWithProviders(
      <RealtimeChatHeaderMemberSurface
        aiMembers={<button type="button">Members 1</button>}
      />,
      { locale: "en-US" },
    );

    const memberSurface = screen.getByRole("group", {
      name: "Collaboration",
    });
    expect(
      within(memberSurface).getByRole("button", { name: "Members 1" }),
    ).toBeInTheDocument();
    expect(within(memberSurface).getAllByRole("button")).toHaveLength(1);
    expect(memberSurface).toHaveAttribute(
      "data-slot",
      "realtime-header-members",
    );
  });

  it("keeps recording, workbench, and sharing in a stable order", () => {
    renderWithProviders(
      <RealtimeChatHeaderActions
        recording={<button type="button">REC active</button>}
        workbench={<button type="button">Workbench</button>}
        share={<button type="button">Share</button>}
      />,
      { locale: "en-US" },
    );

    const actions = screen
      .getByText("REC active")
      .closest('[data-slot="realtime-header-actions"]');
    expect(actions).not.toBeNull();
    expect(
      within(actions as HTMLElement)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["REC active", "Workbench", "Share"]);
    expect(
      within(actions as HTMLElement).getByRole("group", {
        name: "View controls",
      }),
    ).toHaveAttribute("data-slot", "realtime-header-view-actions");
  });
});
