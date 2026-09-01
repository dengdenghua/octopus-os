import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test-token" }),
  jsonAuthHeaders: () => ({
    Authorization: "Bearer test-token",
    "Content-Type": "application/json",
  }),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "https://api.example",
}));

import {
  approveTeamJoinRequest,
  createTeamInvite,
  getOwnTeamJoinRequest,
  getTeamJoinPolicy,
  inspectTeamInvite,
  joinTeamInvite,
  listTeamJoinRequests,
  listTeamInvites,
  rejectTeamJoinRequest,
  revokeTeamInvite,
  updateTeamJoinPolicy,
  withdrawOwnTeamJoinRequest,
} from "./api";

describe("team invite API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("creates invites on the plural endpoint with auth", async () => {
    const payload = {
      invite_id: "invite-1",
      team_id: "room-1",
      role: "viewer",
      invite_role: "viewer",
      invite_token: "secret",
      invite_path: "/workspace/team/join?token=secret",
      invite_hash_path: "/#/workspace/team/join?token=secret",
      created_at: "2026-08-22T00:00:00Z",
      use_count: 0,
      status: "active",
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(payload)));

    await expect(
      createTeamInvite("room/1", {
        role: "viewer",
        expires_in_seconds: 3600,
      }),
    ).resolves.toEqual(payload);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example/api/teams/room%2F1/invites",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer test-token",
        }),
        body: JSON.stringify({ role: "viewer", expires_in_seconds: 3600 }),
      }),
    );
  });

  it("lists, revokes and previews invites using authenticated requests", async () => {
    const record = {
      id: "invite-1",
      team_id: "room-1",
      role: "member",
      created_at: "2026-08-22T00:00:00Z",
      use_count: 0,
      status: "active",
    };
    const preview = {
      invite: {
        id: "invite-1",
        role: "member",
        status: "active",
        remaining_uses: null,
      },
      team: {
        id: "room-1",
        name: "Launch room",
        member_count: 2,
        participant_count: 1,
      },
      thread_id: "thread-1",
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ invites: [record], count: 1 })),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, invite: record })),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(preview)));

    await expect(listTeamInvites("room-1")).resolves.toEqual([record]);
    await expect(revokeTeamInvite("room-1", "invite-1")).resolves.toEqual(
      record,
    );
    await expect(inspectTeamInvite("secret/token")).resolves.toEqual(preview);

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "https://api.example/api/teams/room-1/invites/invite-1",
      expect.objectContaining({
        method: "DELETE",
        headers: { Authorization: "Bearer test-token" },
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "https://api.example/api/team-invites/secret%2Ftoken",
      { headers: { Authorization: "Bearer test-token" } },
    );
  });

  it("supports project approval policy and both sides of a join request", async () => {
    const policy = {
      team_id: "room-1",
      join_policy: "apply_then_join",
      is_project_group: true,
      project_id: "P-1",
      overridden: false,
    };
    const joinRequest = {
      id: "request-1",
      invite_id: "invite-1",
      team_id: "room-1",
      display_name: "Eve",
      role: "member",
      status: "pending",
      created_at: "2026-08-22T00:00:00Z",
      updated_at: "2026-08-22T00:00:00Z",
    };
    const pending = {
      ok: true,
      created: true,
      outcome: "pending_approval",
      join_policy: "apply_then_join",
      join_request: joinRequest,
      team: { id: "room-1", name: "Project room" },
      thread_id: null,
    };
    const joined = {
      outcome: "joined",
      join_policy: "apply_then_join",
      team: {
        id: "room-1",
        name: "Project room",
        members: [],
        leaderId: null,
      },
      participant: {
        id: "actor-eve",
        display_name: "Eve",
        role: "member",
        joined_at: "2026-08-22T00:01:00Z",
        status: "active",
      },
      join_request: { ...joinRequest, status: "approved" },
      thread_id: "thread-1",
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(policy)))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...policy,
            overridden: true,
            join_policy: "direct_join",
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ join_requests: [joinRequest], count: 1 }),
        ),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(joined)))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            changed: true,
            join_request: { ...joinRequest, status: "rejected" },
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(pending), { status: 202 }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(pending)))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: true,
            outcome: "withdrawn",
            join_request: { ...joinRequest, status: "withdrawn" },
          }),
        ),
      );

    await expect(getTeamJoinPolicy("room-1")).resolves.toEqual(policy);
    await expect(
      updateTeamJoinPolicy("room-1", "direct_join"),
    ).resolves.toEqual(expect.objectContaining({ join_policy: "direct_join" }));
    await expect(listTeamJoinRequests("room-1")).resolves.toEqual([
      joinRequest,
    ]);
    await expect(
      approveTeamJoinRequest("room-1", "request-1"),
    ).resolves.toEqual(joined);
    await expect(rejectTeamJoinRequest("room-1", "request-1")).resolves.toEqual(
      expect.objectContaining({
        join_request: expect.objectContaining({ status: "rejected" }),
      }),
    );
    await expect(
      joinTeamInvite("secret", { display_name: "Eve" }),
    ).resolves.toEqual(pending);
    await expect(getOwnTeamJoinRequest("secret")).resolves.toEqual(pending);
    await expect(withdrawOwnTeamJoinRequest("secret")).resolves.toEqual(
      expect.objectContaining({ outcome: "withdrawn" }),
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "https://api.example/api/teams/room-1/join-policy",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ join_policy: "direct_join" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      "https://api.example/api/team-invites/secret/join",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
