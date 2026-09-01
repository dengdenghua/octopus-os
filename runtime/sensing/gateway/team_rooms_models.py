"""Wire and request models shared by the Team Rooms HTTP and WS surfaces."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TeamMemberWire(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    display_name: str | None = None
    description: str = ""
    icon: str | None = None
    avatar_url: str | None = None
    model: str | None = None
    tool_groups: list[str] | None = None


class TeamParticipantWire(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    display_name: str
    role: str = "guest"
    actor_id: str | None = None
    joined_at: str
    last_seen_at: str | None = None
    status: str = "active"
    # Governance: an admin-imposed per-member mute. Applies on top of any
    # room ``speaker_policy`` — a muted member cannot broadcast messages
    # regardless of the policy. Only the team owner can set it.
    muted: bool = False
    # Delegated speaking (the bound person's OWN opt-in — the owner cannot
    # impose it, which would be impersonation):
    #   speak_mode    — "manual" (the human speaks), "twin" (a bound agent
    #                    speaks for them), or "hosted" (a human host does)
    #   twin_agent_id — the digital-twin agent authorized when speak_mode=twin
    #   host_id       — the human host authorized when speak_mode=hosted
    speak_mode: str = "manual"
    twin_agent_id: str | None = None
    host_id: str | None = None


class TeamRoomWire(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    members: list[TeamMemberWire] = Field(default_factory=list)
    leaderId: str | None = None  # noqa: N815 — frontend wire field uses camelCase
    owner_id: str | None = None
    tenant_id: str = "local"
    thread_id: str | None = None
    # ``None`` means "use the server default": ordinary rooms join directly,
    # while rooms whose canonical thread is bound in ProjectStore require an
    # owner/admin approval.  Keeping the override nullable is important: it
    # lets a room become a project room later without freezing the old
    # ordinary-room default into its persisted snapshot.
    join_policy_override: Literal["direct_join", "apply_then_join"] | None = None
    created_at: str
    updated_at: str
    participants: list[TeamParticipantWire] = Field(default_factory=list)
    # Governance: who may speak in the room. ``free`` = anyone not
    # individually muted; ``admin_only`` = only the owner/admins (a
    # whole-room mute); ``round_robin`` / ``roll_call`` / ``moderated`` are
    # turn-based — only the participant holding the floor may speak.
    speaker_policy: str = "free"
    # Turn-engine floor state (only meaningful in turn-based policies):
    #   current_speaker_id — participant id holding the floor (None = open)
    #   moderator_id       — who controls the floor in roll_call/moderated
    #                        (defaults to the owner on mode entry)
    #   floor_requests     — raised-hands queue (participant ids) for moderated
    current_speaker_id: str | None = None
    moderator_id: str | None = None
    floor_requests: list[str] = Field(default_factory=list)


class CreateTeamRoomRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str
    members: list[TeamMemberWire] = Field(default_factory=list)
    leaderId: str | None = None  # noqa: N815 — frontend wire field uses camelCase
    thread_id: str | None = None


class JoinInviteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str | None = None
    participant_id: str | None = None


class CreateTeamInviteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["member", "viewer"] = "member"
    expires_in_seconds: int = Field(default=7 * 24 * 60 * 60, ge=1, le=30 * 24 * 60 * 60)
    max_uses: int = Field(default=100, ge=1, le=1000)


class UpdateTeamJoinPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    join_policy: Literal["direct_join", "apply_then_join"]


class RejectTeamJoinRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str = Field(default="", max_length=1000)


class UpdateTeamParticipantRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str | None = None
    role: str | None = None
    status: str | None = None
    muted: bool | None = None


class UpdateSpeakerPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    speaker_policy: str


class UpdateDelegationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    speak_mode: str
    twin_agent_id: str | None = None
    host_id: str | None = None


__all__ = [
    "CreateTeamInviteRequest",
    "CreateTeamRoomRequest",
    "JoinInviteRequest",
    "RejectTeamJoinRequest",
    "TeamMemberWire",
    "TeamParticipantWire",
    "TeamRoomWire",
    "UpdateDelegationRequest",
    "UpdateTeamJoinPolicyRequest",
    "UpdateSpeakerPolicyRequest",
    "UpdateTeamParticipantRequest",
]
