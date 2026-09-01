import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ChatHeaderMenuButton } from "./chat-header-menu-button";

describe("ChatHeaderMenuButton", () => {
  it("identifies the searchable history drawer instead of duplicating the sidebar menu label", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();

    renderWithProviders(<ChatHeaderMenuButton onClick={onClick} />, {
      locale: "zh-CN",
    });

    const button = screen.getByRole("button", { name: "对话历史" });
    expect(button).toHaveAttribute("title", "对话历史");

    await user.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
