import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { GroupHumanInviteButton } from "./group-human-invite-button";

vi.mock("./invite-dialog", () => ({
  InviteDialog: ({ open, roomId }: { open: boolean; roomId: string }) =>
    open ? <div data-testid="human-invite-dialog">{roomId}</div> : null,
}));

describe("GroupHumanInviteButton", () => {
  beforeEach(() => vi.clearAllMocks());

  it("opens the invite dialog for an existing room", () => {
    renderWithProviders(<GroupHumanInviteButton roomId="room-1" />, {
      locale: "zh-CN",
    });

    fireEvent.click(screen.getByRole("button", { name: "邀请真人" }));
    expect(screen.getByTestId("human-invite-dialog")).toHaveTextContent(
      "room-1",
    );
  });

  it("can ensure a room before opening", async () => {
    const onEnsureRoom = vi.fn().mockResolvedValue("room-created");
    renderWithProviders(
      <GroupHumanInviteButton onEnsureRoom={onEnsureRoom} />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByRole("button", { name: "邀请真人" }));
    await waitFor(() => expect(onEnsureRoom).toHaveBeenCalledOnce());
    expect(await screen.findByTestId("human-invite-dialog")).toHaveTextContent(
      "room-created",
    );
  });

  it("reconciles an existing room before opening when an ensure callback is available", async () => {
    const onEnsureRoom = vi.fn().mockResolvedValue("room-1");
    renderWithProviders(
      <GroupHumanInviteButton roomId="room-1" onEnsureRoom={onEnsureRoom} />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByRole("button", { name: "邀请真人" }));
    await waitFor(() => expect(onEnsureRoom).toHaveBeenCalledOnce());
    expect(await screen.findByTestId("human-invite-dialog")).toHaveTextContent(
      "room-1",
    );
  });

  it("supports one controlled dialog shared by multiple invite entry points", () => {
    const onOpenChange = vi.fn();
    renderWithProviders(
      <GroupHumanInviteButton
        roomId="room-shared"
        open={false}
        onOpenChange={onOpenChange}
        iconOnly
      />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByRole("button", { name: "邀请真人" }));
    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(screen.queryByTestId("human-invite-dialog")).not.toBeInTheDocument();
  });
});
