"""Pydantic response models for the meta router.

Extracted from ``meta_router.py`` in the god-file split campaign so the
factory module stays under the 1000-line gate. These models shape the
frontend-facing payloads for feedback / skills / auth-provider listing.
"""

from __future__ import annotations

try:
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment, misc]


if FASTAPI_AVAILABLE:

    class FeedbackEntry(BaseModel):
        ts: float
        sentiment: str  # "liked" | "disliked"
        message_id: str | None = None
        thread_id: str | None = None
        agent_id: str | None = None
        content_preview: str | None = None
        reason: str | None = None
        actor: str | None = None

    class FeedbackPostResponse(BaseModel):
        ok: bool
        recorded: FeedbackEntry

    class FeedbackListResponse(BaseModel):
        entries: list[FeedbackEntry]

    class SkillWire(BaseModel):
        name: str
        description: str
        affinity: list[str]
        cost_profile: str | None = None
        trusted_source: str | None = None
        has_tests: bool = False
        enabled: bool = True
        surface: str = "skill"
        permission_group: str | None = None
        category: str = "other"
        group: str | None = None
        kind: str = "domain"
        market_visibility: str = "market"
        market_reason: str | None = None
        canonical_skill: str | None = None

    class SkillsResponse(BaseModel):
        skills: list[SkillWire]

    class CapabilityPermissionWire(BaseModel):
        id: str
        enabled: bool
        available: bool
        skill_names: list[str]

    class CapabilityPermissionsResponse(BaseModel):
        permissions: list[CapabilityPermissionWire]

    class SlashCommandWire(BaseModel):
        name: str
        description: str = ""
        argument_hint: str = ""
        allowed_tools: list[str] = []
        model: str = ""
        source: str = ""

    class SlashCommandsResponse(BaseModel):
        commands: list[SlashCommandWire]

    class AuthProvider(BaseModel):
        # Account-backed and local producers shape differently, so
        # stay permissive at the model level · the frontend checks
        # ``id`` and uses the id-specific fields it knows about.
        model_config = {"extra": "allow"}
        id: str
        label: str

    class AuthProvidersResponse(BaseModel):
        providers: list[AuthProvider]


__all__ = [
    "AuthProvider",
    "AuthProvidersResponse",
    "CapabilityPermissionWire",
    "CapabilityPermissionsResponse",
    "FeedbackEntry",
    "FeedbackListResponse",
    "FeedbackPostResponse",
    "SkillWire",
    "SkillsResponse",
    "SlashCommandWire",
    "SlashCommandsResponse",
]
