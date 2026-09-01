import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

const mocks = vi.hoisted(() => ({
  createInvite: vi.fn(),
  listInvites: vi.fn(),
  revokeInvite: vi.fn(),
  getPolicy: vi.fn(),
  updatePolicy: vi.fn(),
  listRequests: vi.fn(),
  approveRequest: vi.fn(),
  rejectRequest: vi.fn(),
}));

vi.mock("@/core/teams", () => ({
  createTeamInvite: mocks.createInvite,
  listTeamInvites: mocks.listInvites,
  revokeTeamInvite: mocks.revokeInvite,
  getTeamJoinPolicy: mocks.getPolicy,
  updateTeamJoinPolicy: mocks.updatePolicy,
  listTeamJoinRequests: mocks.listRequests,
  approveTeamJoinRequest: mocks.approveRequest,
  rejectTeamJoinRequest: mocks.rejectRequest,
}));

import { InviteDialog } from "./invite-dialog";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

describe("InviteDialog project approvals", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listInvites.mockResolvedValue([]);
    mocks.updatePolicy.mockResolvedValue({
      team_id: "room-1",
      join_policy: "direct_join",
      is_project_group: true,
      project_id: "P-1",
      overridden: true,
    });
    mocks.getPolicy.mockResolvedValue({
      team_id: "room-1",
      join_policy: "apply_then_join",
      is_project_group: true,
      project_id: "P-1",
      overridden: false,
    });
    mocks.listRequests.mockResolvedValue([
      {
        id: "request-1",
        invite_id: "invite-1",
        team_id: "room-1",
        actor_id: "actor-eve@example.test",
        display_name: "Eve",
        role: "member",
        status: "pending",
        created_at: "2026-08-22T00:00:00Z",
        updated_at: "2026-08-22T00:00:00Z",
      },
    ]);
    mocks.approveRequest.mockResolvedValue({ outcome: "joined" });
  });

  it("shows project join policy and lets the owner approve a pending person", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <InviteDialog
        open
        onOpenChange={vi.fn()}
        roomId="room-1"
        threadId="thread-1"
      />,
      { locale: "zh-CN" },
    );

    expect(await screen.findByText("加入方式")).toBeInTheDocument();
    expect(screen.getByText("申请后加入")).toBeInTheDocument();
    expect(await screen.findByText("Eve")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "同意" }));

    await waitFor(() =>
      expect(mocks.approveRequest).toHaveBeenCalledWith("room-1", "request-1"),
    );
    expect(screen.queryByText("Eve")).not.toBeInTheDocument();
  });

  it("requires an explicit confirmation before active links become direct join", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <InviteDialog
        open
        onOpenChange={vi.fn()}
        roomId="room-1"
        threadId="thread-1"
      />,
      { locale: "zh-CN" },
    );

    expect(
      await screen.findByText("actor-eve@example.test"),
    ).toBeInTheDocument();
    await user.click(screen.getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: "直接加入" }));

    expect(await screen.findByText("确认开放直接加入？")).toBeInTheDocument();
    expect(mocks.updatePolicy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "开放直接加入" }));

    await waitFor(() =>
      expect(mocks.updatePolicy).toHaveBeenCalledWith("room-1", "direct_join"),
    );
  });

  it("ignores a slow response from the previously opened room", async () => {
    const firstPolicy = deferred<Record<string, unknown>>();
    const secondPolicy = deferred<Record<string, unknown>>();
    mocks.getPolicy.mockImplementation((roomId: string) =>
      roomId === "room-1" ? firstPolicy.promise : secondPolicy.promise,
    );
    mocks.listRequests.mockImplementation((roomId: string) =>
      Promise.resolve([
        {
          id: `request-${roomId}`,
          invite_id: `invite-${roomId}`,
          team_id: roomId,
          actor_id: `actor-${roomId}`,
          display_name:
            roomId === "room-1" ? "Old room member" : "New room member",
          role: "member",
          status: "pending",
          created_at: "2026-08-22T00:00:00Z",
          updated_at: "2026-08-22T00:00:00Z",
        },
      ]),
    );

    const view = renderWithProviders(
      <InviteDialog open onOpenChange={vi.fn()} roomId="room-1" />,
      { locale: "zh-CN" },
    );
    view.rerender(<InviteDialog open onOpenChange={vi.fn()} roomId="room-2" />);

    await act(async () => {
      secondPolicy.resolve({
        team_id: "room-2",
        join_policy: "apply_then_join",
        is_project_group: true,
        project_id: "P-2",
        overridden: false,
      });
    });
    expect(await screen.findByText("New room member")).toBeInTheDocument();

    await act(async () => {
      firstPolicy.resolve({
        team_id: "room-1",
        join_policy: "apply_then_join",
        is_project_group: true,
        project_id: "P-1",
        overridden: false,
      });
    });
    await waitFor(() =>
      expect(screen.queryByText("Old room member")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("New room member")).toBeInTheDocument();
  });
});
