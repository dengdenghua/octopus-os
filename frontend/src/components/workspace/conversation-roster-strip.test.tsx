import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ConversationRosterStrip } from "./conversation-roster-strip";

describe("ConversationRosterStrip", () => {
  it("places the leader first and preserves member click access", () => {
    const onMemberClick = vi.fn();
    renderWithProviders(
      <ConversationRosterStrip
        seats={[
          { id: "local", name: "Local", role: "群主", kind: "human" },
          { id: "zero", name: "Zero", role: "member", kind: "agent" },
          { id: "kane", name: "Kane", role: "tl", kind: "agent" },
        ]}
        onMemberClick={onMemberClick}
      />,
      { locale: "zh-CN" },
    );

    const strip = screen.getByTestId("conversation-roster-strip");
    const seats = strip.querySelectorAll("button");
    expect(seats[0]).toHaveAccessibleName("Kane · 群主 · 在场");
    expect(seats[1]).toHaveAccessibleName("Zero · 协作 · 在场");
    expect(screen.queryByText("Local")).toBeNull();
    expect(screen.getByText("★")).toBeInTheDocument();
    expect(screen.queryByText("群主")).toBeNull();

    fireEvent.click(seats[1]!);
    expect(onMemberClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "zero" }),
    );
  });
});
