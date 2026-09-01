import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

const mocks = vi.hoisted(() => ({
  inspect: vi.fn(),
  join: vi.fn(),
  getOwnRequest: vi.fn(),
  withdraw: vi.fn(),
  dispatchUpdated: vi.fn(),
  writePreferred: vi.fn(),
}));

vi.mock("@/core/teams", () => ({
  inspectTeamInvite: mocks.inspect,
  joinTeamInvite: mocks.join,
  getOwnTeamJoinRequest: mocks.getOwnRequest,
  withdrawOwnTeamJoinRequest: mocks.withdraw,
  dispatchTeamUpdated: mocks.dispatchUpdated,
  writePreferredTeam: mocks.writePreferred,
  readOrCreateTeamParticipantId: () => "browser-seat",
}));

import TeamJoinPage from "./page";

describe("TeamJoinPage project approval", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getOwnRequest.mockResolvedValue(null);
    mocks.inspect.mockResolvedValue({
      invite: {
        id: "invite-1",
        role: "member",
        status: "active",
        remaining_uses: 3,
      },
      team: {
        id: "room-1",
        name: "发布项目",
        member_count: 2,
        participant_count: 1,
      },
      join_policy: "apply_then_join",
      thread_id: null,
    });
    mocks.join.mockResolvedValue({
      ok: true,
      created: true,
      outcome: "pending_approval",
      join_policy: "apply_then_join",
      join_request: {
        id: "request-1",
        invite_id: "invite-1",
        team_id: "room-1",
        display_name: "Eve",
        role: "member",
        status: "pending",
        created_at: "2026-08-22T00:00:00Z",
        updated_at: "2026-08-22T00:00:00Z",
      },
      team: { id: "room-1", name: "发布项目" },
      thread_id: null,
    });
  });

  it("submits an approval request without exposing the project destination", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TeamJoinPage />, {
      initialRoute: "/workspace/team/join?token=secret",
      locale: "zh-CN",
    });

    expect(await screen.findByText("需要群主审批")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("你的显示名称"), "Eve");
    await user.click(screen.getByRole("button", { name: "申请加入" }));

    expect(await screen.findByText("申请已提交")).toBeInTheDocument();
    expect(mocks.join).toHaveBeenCalledWith("secret", {
      display_name: "Eve",
      participant_id: "browser-seat",
    });
    expect(mocks.writePreferred).not.toHaveBeenCalled();
    expect(mocks.dispatchUpdated).not.toHaveBeenCalled();
  });

  it("restores an existing pending request after refreshing the invite page", async () => {
    mocks.getOwnRequest.mockResolvedValue({
      outcome: "pending",
      join_policy: "apply_then_join",
      join_request: {
        id: "request-existing",
        invite_id: "invite-1",
        team_id: "room-1",
        display_name: "Eve",
        role: "member",
        status: "pending",
        created_at: "2026-08-22T00:00:00Z",
        updated_at: "2026-08-22T00:00:00Z",
      },
      team: { id: "room-1", name: "发布项目" },
      thread_id: null,
    });

    renderWithProviders(<TeamJoinPage />, {
      initialRoute: "/workspace/team/join?token=secret",
      locale: "zh-CN",
    });

    expect(await screen.findByText("申请已提交")).toBeInTheDocument();
    expect(mocks.getOwnRequest).toHaveBeenCalledWith("secret");
    expect(
      screen.queryByPlaceholderText("你的显示名称"),
    ).not.toBeInTheDocument();
  });
});
