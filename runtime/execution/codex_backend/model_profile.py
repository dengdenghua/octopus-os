"""Server-owned model selection and provider resolution for the Coder role.

The public UI chooses a policy (follow Echo or use the Codex account) and
may choose a model id.  It never submits a provider URL or credential.  This
module resolves those choices against the live server catalog and produces the
only provider object the isolated Codex sidecar accepts.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from runtime.platform.io import (
    JsonMutation,
    TransactionalFileError,
    mutate_json_file,
    read_json_file,
)
from runtime.platform.models.custom_model_selection import (
    custom_model_upstreams,
    resolve_custom_model_selection,
)
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path

from .types import CodexProviderProfile, ConfigurationError

CodexModelMode = Literal["follow_system", "chatgpt"]
_MODEL_SENTINELS = frozenset({"", "auto", "default", "follow_system", "inherit"})
_MODEL_ID_RE = re.compile(r"^[^\x00\r\n]{1,256}$")
_EFFORT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class CodexModelCompatibilityError(ConfigurationError):
    """The selected Echo provider cannot safely run inside Codex."""


@dataclass(frozen=True, slots=True)
class CodexModelPreference:
    """Principal-scoped Coder preference; defaults to Echo inheritance."""

    mode: CodexModelMode = "follow_system"
    model: str | None = None
    reasoning_effort: str | None = None
    app_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"follow_system", "chatgpt"}:
            raise ConfigurationError("Codex model mode must be follow_system or chatgpt")
        object.__setattr__(self, "model", _normalize_model(self.model))
        object.__setattr__(self, "reasoning_effort", _normalize_effort(self.reasoning_effort))
        normalized_apps = tuple(dict.fromkeys(_normalize_app_id(item) for item in self.app_ids))
        if len(normalized_apps) > 32:
            raise ConfigurationError("Codex connector selection exceeds 32 apps")
        object.__setattr__(self, "app_ids", normalized_apps)


@dataclass(frozen=True, slots=True)
class ResolvedCodexExecutionProfile:
    """Effective model decision after all server-owned precedence rules."""

    mode: CodexModelMode
    effective_model: str | None
    system_model: str | None
    reasoning_effort: str | None
    model_source: Literal["turn", "role", "system", "codex_default"]
    provider: str
    provider_profile: CodexProviderProfile | None
    compatible: bool
    compatibility_reason: str | None = None
    proxy_required: bool = False

    def require_compatible(self) -> ResolvedCodexExecutionProfile:
        if not self.compatible:
            raise CodexModelCompatibilityError(
                self.compatibility_reason or "selected model is not compatible with Codex"
            )
        return self

    def to_wire(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "effective_model": self.effective_model,
            "system_model": self.system_model,
            "reasoning_effort": self.reasoning_effort,
            "model_source": self.model_source,
            "provider": self.provider,
            "compatible": self.compatible,
            "compatibility_reason": self.compatibility_reason,
            "proxy_required": self.proxy_required,
        }


class CodexModelPreferenceStore:
    """Atomic, tenant/principal-partitioned model preference storage."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser().resolve(strict=False)

    def path_for(self, scope: TenantScope | None) -> Path:
        return tenant_scoped_path(self._path, scope)

    def read(self, scope: TenantScope | None) -> CodexModelPreference:
        path = self.path_for(scope)
        try:
            payload = read_json_file(
                path,
                default_factory=dict,
                validate=_validate_preference_payload,
                mode=0o600,
            )
        except (TransactionalFileError, ConfigurationError, OSError, ValueError, TypeError):
            return CodexModelPreference()
        if not isinstance(payload, Mapping):
            return CodexModelPreference()
        try:
            mode = str(payload.get("mode") or "follow_system")
            model = _optional_string(payload.get("model"))
            if mode == "follow_system" and is_disallowed_coder_system_model(model):
                model = None
            return CodexModelPreference(
                mode=mode,  # type: ignore[arg-type]
                model=model,
                reasoning_effort=_optional_string(payload.get("reasoning_effort")),
                app_ids=tuple(payload.get("app_ids") or ()),
            )
        except ConfigurationError:
            return CodexModelPreference()

    def write(
        self,
        scope: TenantScope | None,
        preference: CodexModelPreference,
    ) -> CodexModelPreference:
        if preference.mode == "follow_system" and is_disallowed_coder_system_model(
            preference.model
        ):
            raise ConfigurationError("mix is not an executable Coder system model")
        path = self.path_for(scope)
        payload = asdict(preference)

        def _replace(current: Any) -> JsonMutation[CodexModelPreference]:
            if not isinstance(current, dict):
                raise ConfigurationError("Codex model preference state must be an object")
            current.clear()
            current.update(payload)
            return JsonMutation(preference)

        return mutate_json_file(
            path,
            default_factory=dict,
            validate=_validate_preference_payload,
            mutate=_replace,
            mode=0o600,
            indent=2,
        )


def is_disallowed_coder_system_model(model: str | None) -> bool:
    normalized = str(model or "").strip().lower()
    return normalized in {"mix", "echo-mix"}


def resolve_codex_execution_profile(
    *,
    preference: CodexModelPreference | None = None,
    turn_model: str | None = None,
    role_model: str | None = None,
    system_model: str | None = None,
    turn_effort: str | None = None,
    role_effort: str | None = None,
    system_effort: str | None = None,
    custom_models: Mapping[str, Mapping[str, Any]] | None = None,
    proxy_available: bool = False,
    proxy_route_available: Callable[[str], bool] | None = None,
) -> ResolvedCodexExecutionProfile:
    """Resolve ``turn > role > system > Codex default`` without client secrets."""

    selected_preference = preference or CodexModelPreference()
    explicit_role_model = _normalize_model(role_model)
    normalized_system = _normalize_model(system_model)
    candidates: tuple[tuple[str, str | None], ...]
    effort_candidates: tuple[str | None, ...]
    if selected_preference.mode == "chatgpt":
        # Account mode is its own provider domain.  Falling through to the
        # Echo system model could send a DeepSeek/Qwen/custom id to an
        # OpenAI account when no personal model was selected.
        candidates = (
            ("turn", _normalize_model(turn_model)),
            ("role", explicit_role_model or selected_preference.model),
        )
        effort_candidates = (
            _normalize_effort(turn_effort),
            _normalize_effort(role_effort) or selected_preference.reasoning_effort,
        )
    else:
        candidates = (
            ("turn", _normalize_model(turn_model)),
            ("role", explicit_role_model or selected_preference.model),
            ("system", normalized_system),
        )
        effort_candidates = (
            _normalize_effort(turn_effort),
            _normalize_effort(role_effort) or selected_preference.reasoning_effort,
            _normalize_effort(system_effort),
        )
    source: Literal["turn", "role", "system", "codex_default"] = "codex_default"
    selected_model: str | None = None
    for candidate_source, candidate in candidates:
        if candidate is not None:
            source = candidate_source  # type: ignore[assignment]
            selected_model = candidate
            break

    selected_effort = next(
        (effort for effort in effort_candidates if effort is not None),
        None,
    )
    if selected_preference.mode == "chatgpt":
        return ResolvedCodexExecutionProfile(
            mode="chatgpt",
            effective_model=selected_model,
            system_model=normalized_system,
            reasoning_effort=selected_effort,
            model_source=source,
            provider="codex_account",
            provider_profile=None,
            compatible=True,
        )

    # A missing Echo default deliberately means "let Codex choose".  It
    # stays in Codex's own account/default domain and needs no host proxy.
    if selected_model is None:
        return ResolvedCodexExecutionProfile(
            mode="follow_system",
            effective_model=None,
            system_model=normalized_system,
            reasoning_effort=selected_effort,
            model_source=source,
            provider="openai",
            provider_profile=None,
            compatible=True,
        )

    catalog = {str(key): dict(value) for key, value in (custom_models or {}).items()}
    resolved = _resolve_custom_entry(catalog, selected_model)
    effective_model = resolved[2] if resolved is not None else selected_model
    route_available = proxy_available and (
        proxy_route_available is None or proxy_route_available(effective_model)
    )
    return ResolvedCodexExecutionProfile(
        mode="follow_system",
        effective_model=effective_model,
        system_model=normalized_system,
        reasoning_effort=selected_effort,
        model_source=source,
        provider="echo_responses_proxy",
        provider_profile=None,
        compatible=route_available,
        compatibility_reason=(
            None
            if route_available
            else (
                "Echo Responses proxy is unavailable for the selected system model"
                if not proxy_available
                else "Selected system model has no exact Echo ModelRouter route"
            )
        ),
        proxy_required=True,
    )


def codex_proxy_route_available(router: Any, model: str) -> bool:
    """Prove a proxy request will not silently fall through to another model."""

    if not callable(getattr(router, "call", None)):
        return False
    has_route = getattr(router, "has", None)
    if not callable(has_route):
        # A direct provider router owns its whole model namespace.  Exact
        # registry checks apply only to dispatch routers exposing `has()`.
        return True
    try:
        if bool(has_route(model)):
            return True
    except Exception:  # noqa: BLE001 - routing probes must fail closed
        return False
    default_model = getattr(router, "default_model", None)
    return isinstance(default_model, str) and default_model.strip() == model


def _resolve_custom_entry(
    custom_models: dict[str, Any],
    selected_model: str | None,
) -> tuple[str, dict[str, Any], str] | None:
    if selected_model is None:
        return None
    selection = resolve_custom_model_selection(custom_models, selected_model)
    if selection is not None:
        return selection.entry_id, dict(selection.entry), selection.model

    exact_entry = custom_models.get(selected_model)
    if isinstance(exact_entry, Mapping):
        entry = dict(exact_entry)
        upstreams = custom_model_upstreams(entry, selected_model)
        return selected_model, entry, upstreams[0]

    matches: list[tuple[str, dict[str, Any], str]] = []
    for raw_entry_id, raw_entry in custom_models.items():
        if not isinstance(raw_entry, Mapping):
            continue
        entry = dict(raw_entry)
        entry_id = str(entry.get("id") or raw_entry_id).strip()
        for upstream in custom_model_upstreams(entry, entry_id):
            if upstream == selected_model or f"{upstream}::1m" == selected_model:
                matches.append((entry_id, entry, upstream))
    return matches[0] if len(matches) == 1 else None


def _normalize_model(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if normalized.casefold() in _MODEL_SENTINELS:
        return None
    if not _MODEL_ID_RE.fullmatch(normalized):
        raise ConfigurationError("model id must contain 1..256 safe characters")
    return normalized


def _normalize_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.casefold() in {"auto", "default", "inherit"}:
        return None
    if not _EFFORT_RE.fullmatch(normalized):
        raise ConfigurationError("reasoning effort must be a safe non-empty identifier")
    return normalized


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _normalize_app_id(value: str) -> str:
    if not isinstance(value, str):
        raise ConfigurationError("Codex connector id must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > 256 or any(c in normalized for c in "\x00\r\n"):
        raise ConfigurationError("Codex connector id must be a bounded safe identifier")
    return normalized


def _validate_preference_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ConfigurationError("Codex model preference state must be an object")
    if not payload:
        return
    allowed = {"mode", "model", "reasoning_effort", "app_ids"}
    if set(payload) - allowed:
        raise ConfigurationError("Codex model preference state has unknown fields")
    if not isinstance(payload.get("app_ids", []), (list, tuple)):
        raise ConfigurationError("Codex connector selection must be an array")
    CodexModelPreference(
        mode=str(payload.get("mode") or "follow_system"),  # type: ignore[arg-type]
        model=_optional_string(payload.get("model")),
        reasoning_effort=_optional_string(payload.get("reasoning_effort")),
        app_ids=tuple(payload.get("app_ids") or ()),
    )


__all__ = [
    "CodexModelCompatibilityError",
    "CodexModelMode",
    "CodexModelPreference",
    "CodexModelPreferenceStore",
    "ResolvedCodexExecutionProfile",
    "codex_proxy_route_available",
    "is_disallowed_coder_system_model",
    "resolve_codex_execution_profile",
]
