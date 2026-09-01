"""
Pydantic response models for the config router.

Split out of config_router.py (pure structural refactor — no logic
changes). Imported back by config_router.py.

These give openapi.json real shape info for the config surface
(CustomModel / IdentityLock / Provider / Constitution endpoints).
"""

from __future__ import annotations

from typing import Literal

try:
    from pydantic import BaseModel, ConfigDict, Field, SecretStr

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment, misc]
    ConfigDict = None  # type: ignore[assignment, misc]
    Field = None  # type: ignore[assignment, misc]
    SecretStr = str  # type: ignore[assignment, misc]

if FASTAPI_AVAILABLE:

    class CustomModelEntry(BaseModel):
        """One custom-model row as shown to the UI. ``api_key`` is
        intentionally absent · ``has_api_key`` reports presence
        without exposing the secret.

        ``models`` is the open-ended list of upstream model IDs that
        this entry can dispatch to. The first item is the picker
        default; the last item is what Auto mode uses for
        performance-tier turns (long / multi-step / code / research).
        Operators can have any number of items · the smart router
        cycles through them in order when a tier escalates, so 1
        item, 2 items, or N items all work the same way. See
        ``turn_complexity._resolve_tier_value`` for the index
        selection rules."""

        # ``model`` and ``model_performance`` both start with the
        # ``model_`` pydantic-protected namespace, hence the opt-out
        # below. The fields themselves are intentional — the protected
        # namespace is meant to stop *us* from shadowing pydantic
        # internals like ``model_dump()``, and these are LLM model
        # names, not pydantic machinery.
        model_config = {"protected_namespaces": ()}

        id: str
        name: str
        provider: str
        base_url: str
        # Open-ended list of model IDs this entry can route to. Order
        # matters · index 0 is the picker default, index -1 is the
        # strongest tier for Auto mode's performance verdict.
        models: list[str] = Field(default_factory=list)
        selection_ids: list[str] = Field(default_factory=list)
        display_name: str
        has_api_key: bool
        managed_by_plugin: str | None = None
        max_tokens: int | None = None
        context_window: int | None = None
        enable_1m_context: bool = False
        supports_thinking: bool | None = None
        default_reasoning_effort: str | None = None
        supports_vision: bool | None = None
        supports_tool_use: bool | None = None
        omit_sampling_parameters: bool | None = None
        compat_profile: str | None = None
        thinking_request_style: str | None = None
        reasoning_efforts: list[str] | None = None
        drop_tool_choice: bool | None = None
        strict_tool_schema: bool | None = None
        max_temperature: float | None = None
        unsupported_request_fields: list[str] | None = None
        codex_wire_api: Literal["responses"] | None = None
        default_header_names: list[str] = Field(default_factory=list)
        has_default_headers: bool = False

    class CustomModelsList(BaseModel):
        models: list[CustomModelEntry]

    class CustomModelUpsertStatus(BaseModel):
        model_config = {"protected_namespaces": ()}

        ok: bool
        model_id: str | None = None
        error: str | None = None

    class CustomModelUpsertResponse(BaseModel):
        # ``model`` conflicts with pydantic's ``model_`` namespace and
        # the nested ``_status`` key needs an alias · but mixing both
        # with populate_by_name tripped PydanticUserError. Dropped
        # this model · the upsert endpoint stays un-annotated for now
        # (returns ``dict[str, Any]``) so the OpenAPI row is generic.
        # Proper typing for it is follow-up work.
        pass

    class CustomModelDeleteResponse(BaseModel):
        ok: bool
        removed: bool

    class CustomModelTestResponse(BaseModel):
        ok: bool
        provider: str
        model: str
        latency_ms: int | None = None
        message: str | None = None
        error: str | None = None
        # Vision auto-detection · ``True`` when the image canary was
        # accepted, ``False`` when the model rejected image input, and
        # ``None`` when the probe was inconclusive (no info). The UI
        # uses ``False`` to lock the vision toggle off.
        supports_vision: bool | None = None

    class IdentityLockResponse(BaseModel):
        locked: bool
        source: str  # "runtime" | "env" | "default"
        unlock_paths: list[str]

    class IdentityLockPutBody(BaseModel):
        locked: bool | None

    class ProviderCapabilitiesWire(BaseModel):
        name: str
        supports_vision: bool
        supports_tool_use: bool
        supports_streaming: bool
        supports_prompt_cache: bool
        supports_structured_output: bool
        default_model: str | None = None
        pricing_hint: str | None = None

    class ProvidersResponse(BaseModel):
        providers: list[ProviderCapabilitiesWire]

    class ConstitutionProfileResponse(BaseModel):
        profile: str  # "strict" | "normal" | "lax"
        available: list[str]

    class ConstitutionProfilePutBody(BaseModel):
        profile: str

    class CodexAccountWire(BaseModel):
        type: Literal["apiKey", "chatgpt", "amazonBedrock"]
        email: str | None = None
        plan_type: str | None = None

    class CodexAccountResponse(BaseModel):
        account: CodexAccountWire | None = None
        requires_openai_auth: bool
        login_pending: bool
        login_id: str | None = None
        login_error: str | None = None

    class CodexLoginBody(BaseModel):
        """One-shot secret envelope; never used as a response or persisted."""

        # The login route parses this model behind a fixed-error dependency so
        # Pydantic validation details (which include the raw input object) are
        # never returned by FastAPI.
        model_config = ConfigDict(extra="forbid")

        type: Literal["chatgpt", "chatgptDeviceCode", "apiKey"]
        api_key: SecretStr | None = Field(default=None, repr=False)

    class CodexLoginResponse(BaseModel):
        type: Literal["chatgpt", "chatgptDeviceCode", "apiKey"]
        login_id: str | None = None
        auth_url: str | None = None
        verification_url: str | None = None
        user_code: str | None = None

    class CodexCancelLoginResponse(BaseModel):
        cancelled: bool
        login_id: str
        reason: str | None = None

    class CodexLogoutResponse(BaseModel):
        logged_out: bool

    class CodexModelWire(BaseModel):
        model_config = ConfigDict(protected_namespaces=())

        id: str
        display_name: str
        description: str = ""
        reasoning_efforts: list[str] = Field(default_factory=list)
        default_reasoning_effort: str | None = None
        hidden: bool = False
        is_default: bool = False
        input_modalities: list[str] = Field(default_factory=list)

    class CodexModelsResponse(BaseModel):
        models: list[CodexModelWire]
        source: Literal["codex_account"] = "codex_account"

    class CodexRateLimitWindow(BaseModel):
        used_percent: float
        remaining_percent: float
        window_duration_mins: int
        resets_at: int

    class CodexRateLimitBucket(BaseModel):
        limit_id: str
        limit_name: str | None = None
        primary: CodexRateLimitWindow | None = None
        secondary: CodexRateLimitWindow | None = None
        plan_type: str | None = None
        rate_limit_reached_type: str | None = None

    class CodexRateLimitsResponse(BaseModel):
        buckets: list[CodexRateLimitBucket] = Field(default_factory=list)
        reset_credits_available: int | None = None

    class CodexUsageSummary(BaseModel):
        lifetime_tokens: int | None = None
        peak_daily_tokens: int | None = None
        longest_running_turn_sec: int | None = None
        current_streak_days: int | None = None
        longest_streak_days: int | None = None

    class CodexDailyUsageBucket(BaseModel):
        start_date: str
        tokens: int | None = None

    class CodexUsageResponse(BaseModel):
        summary: CodexUsageSummary
        daily_usage_buckets: list[CodexDailyUsageBucket] = Field(default_factory=list)

    class CodexAppWire(BaseModel):
        id: str
        name: str
        description: str = ""
        logo_url: str | None = None
        install_url: str | None = None
        is_accessible: bool
        is_enabled: bool
        selected: bool = False

    class CodexAppsResponse(BaseModel):
        apps: list[CodexAppWire] = Field(default_factory=list)

    class CodexAppsPutBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        app_ids: list[str] = Field(default_factory=list, max_length=32)

    class CodexUpdateApprovalBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        version: str = Field(min_length=1, max_length=64)

    class CodexUpdateStatusResponse(BaseModel):
        package: str
        current_version: str
        latest_version: str | None = None
        update_available: bool = False
        checked_at: str | None = None
        source_url: str
        release_url: str
        integrity: str | None = None
        tarball_url: str | None = None
        approval_status: Literal["none", "pending", "approved_for_next_release"] = "none"
        approved_version: str | None = None
        approved_at: str | None = None
        error: str | None = None

    class CodexModelProfilePutBody(BaseModel):
        """Full replacement of the principal's Coder model preference."""

        model_config = ConfigDict(extra="forbid", protected_namespaces=())

        mode: Literal["follow_system", "chatgpt"] = "follow_system"
        model: str | None = None
        reasoning_effort: str | None = None

    class CodexModelProfileResponse(BaseModel):
        model_config = ConfigDict(protected_namespaces=())

        mode: Literal["follow_system", "chatgpt"]
        selected_model: str | None = None
        effective_model: str | None = None
        system_model: str | None = None
        reasoning_effort: str | None = None
        model_source: Literal["turn", "role", "system", "codex_default"]
        provider: str
        compatible: bool
        compatibility_reason: str | None = None
        proxy_required: bool = False


__all__ = [
    "CodexAccountResponse",
    "CodexAccountWire",
    "CodexAppsPutBody",
    "CodexAppsResponse",
    "CodexUpdateApprovalBody",
    "CodexUpdateStatusResponse",
    "CodexAppWire",
    "CodexCancelLoginResponse",
    "CodexLoginBody",
    "CodexLoginResponse",
    "CodexLogoutResponse",
    "CodexModelProfilePutBody",
    "CodexModelProfileResponse",
    "CodexModelsResponse",
    "CodexModelWire",
    "CodexRateLimitBucket",
    "CodexRateLimitsResponse",
    "CodexRateLimitWindow",
    "CodexDailyUsageBucket",
    "CodexUsageResponse",
    "CodexUsageSummary",
    "ConstitutionProfilePutBody",
    "ConstitutionProfileResponse",
    "CustomModelDeleteResponse",
    "CustomModelEntry",
    "CustomModelsList",
    "CustomModelTestResponse",
    "CustomModelUpsertResponse",
    "CustomModelUpsertStatus",
    "FASTAPI_AVAILABLE",
    "IdentityLockPutBody",
    "IdentityLockResponse",
    "ProviderCapabilitiesWire",
    "ProvidersResponse",
]
