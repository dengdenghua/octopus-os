"""Pydantic wire models for ``agents_router``.

Extracted from ``agents_router.py`` (2026-06) to keep that file under
the god-file threshold. These are pure DTOs with no behavior — moving
them to a sibling module preserves the single source of truth without
disrupting FastAPI schema generation.

Module-level definitions matter: nested class definitions inside
``create_agents_router`` cause ``PydanticUndefinedAnnotation`` errors
when the router is instantiated multiple times in the same test
session (forward-ref resolution depends on stable module-level names).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None
    soul: str | None = None
    personality_anchors: dict[str, Any] | None = None


class UpdateAgentRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    model: str | None = None
    soul: str | None = None
    capabilities: dict[str, Any] | None = None
    personality_anchors: dict[str, Any] | None = None


class GenerateAgentVisualsRequest(BaseModel):
    provider: str | None = None
    style_prompt: str = ""
    reference_images: list[str] = Field(default_factory=list)


class AgentVisualsWire(BaseModel):
    agent_id: str
    provider: str
    prompt: str
    avatar_url: str | None = None
    visual_urls: dict[str, str] = Field(default_factory=dict)


class AgentWire(BaseModel):
    name: str
    display_name: str | None = None
    description: str = ""
    icon: str | None = None
    avatar_url: str | None = None
    visual_urls: dict[str, str] = Field(default_factory=dict)
    model: str | None = None
    tool_groups: list[str] | None = None
    soul: str | None = None
    # Capability flags for feature-gating on the client. Kept as a
    # free-form dict so adding a new capability server-side doesn't
    # require a typed wire migration. (The former ``code_mode_unlock``
    # flag was removed — code mode is available to every agent by
    # default.)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    identity_code: str | None = None
    identity_profile: dict[str, Any] = Field(default_factory=dict)


class ArmWire(BaseModel):
    arm_id: str
    display_name: str = ""
    description: str = ""
    affinity: list[str] = Field(default_factory=list)
    icon: str = ""


class AgentDetailWire(AgentWire):
    arms: list[ArmWire] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    skill_policy: dict[str, Any] = Field(default_factory=dict)
    extra_affinity: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)


class ArmOptionWire(BaseModel):
    arm_id: str
    display_name: str = ""
    description: str = ""
    affinity: list[str] = Field(default_factory=list)
    icon: str = ""
    skills: list[str] = Field(default_factory=list)


class ToolRegistryWire(BaseModel):
    arms: list[str] = Field(default_factory=list)
    extra_affinity: list[str] = Field(default_factory=list)
    private_skills: list[str] = Field(default_factory=list)


class CapabilitiesWire(BaseModel):
    browser_automation: bool = True
    desktop_automation: bool = True


class LocalPartnerWire(BaseModel):
    id: str
    agent_id: str
    name: str
    default_alias: str
    description: str
    detected: bool = False
    registered: bool = False
    status: str = "missing"
    command: str | None = None
    executable: str | None = None


class LocalPartnerRegisterItem(BaseModel):
    id: str
    alias: str | None = None


class LocalPartnerRegisterRequest(BaseModel):
    partners: list[LocalPartnerRegisterItem] = Field(default_factory=list)


class LocalPartnerRegisterResult(BaseModel):
    id: str
    agent_id: str
    status: str
    message: str = ""
    agent: AgentDetailWire | None = None


class LocalPartnerRegisterResponse(BaseModel):
    results: list[LocalPartnerRegisterResult] = Field(default_factory=list)
    registered_count: int = 0
    already_exists_count: int = 0
    skipped_count: int = 0


# Task control bodies. Defined at module level so FastAPI's Pydantic
# schema generation can resolve forward references reliably across
# imports — nested class definitions cause ``PydanticUndefinedAnnotation``
# when the router is instantiated multiple times in the same test session.
class PauseTaskBody(BaseModel):
    reason: str = "user_request"
    note: str = ""


class ResumeTaskBody(BaseModel):
    extra_iterations: int = 0
    extra_tokens: int = 0
    extra_usd: float = 0.0


class GroupCreate(BaseModel):
    group_id: str
    display_name: str = ""
    description: str = ""
    members: list[str] = Field(default_factory=list)


class GroupUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None


class GroupWire(BaseModel):
    group_id: str
    display_name: str = ""
    description: str = ""
    members: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
