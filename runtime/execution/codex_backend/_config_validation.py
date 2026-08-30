"""Focused validators for server-managed Codex config sections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from .types import CodexProviderProfile


class SidecarConfigContext(Protocol):
    @property
    def workspace(self) -> Path: ...

    @property
    def scratch_root(self) -> Path: ...

    @property
    def state_root(self) -> Path: ...

    @property
    def sandbox_mode(self) -> str: ...


def validate_provider_profile(
    config: Mapping[str, object],
    expected: CodexProviderProfile | None,
    errors: list[str],
) -> None:
    """Pin provider routing and prove no long-lived credential entered config."""

    if expected is None:
        return
    _expect_value(config, "model", expected.model, errors)
    _expect_value(config, "model_provider", expected.provider_id, errors)
    providers = _mapping_at(config, "model_providers", errors)
    if providers is None:
        return
    raw_provider = providers.get(expected.provider_id)
    if not isinstance(raw_provider, Mapping):
        errors.append(f"model_providers.{expected.provider_id} is missing")
        return
    provider = cast(Mapping[str, object], raw_provider)
    prefix = f"model_providers.{expected.provider_id}."
    _expect_value(provider, "name", expected.name, errors, prefix=prefix)
    _expect_value(provider, "base_url", expected.base_url, errors, prefix=prefix)
    _expect_value(provider, "wire_api", expected.wire_api, errors, prefix=prefix)
    _expect_value(
        provider,
        "requires_openai_auth",
        expected.requires_openai_auth,
        errors,
        prefix=prefix,
    )
    _expect_value(provider, "env_key", expected.auth_env_key, errors, prefix=prefix)
    for secret_field in (
        "experimental_bearer_token",
        "http_headers",
        "env_http_headers",
        "auth",
    ):
        if provider.get(secret_field) not in (None, {}):
            errors.append(f"{prefix}{secret_field} must be absent")


def validate_permission_profile(
    config: Mapping[str, object],
    context: SidecarConfigContext,
    errors: list[str],
    *,
    profile_name: str,
    protected_workspace_subpaths: Sequence[str],
) -> None:
    permissions = _mapping_at(config, "permissions", errors)
    if permissions is None:
        return
    raw_profile = permissions.get(profile_name)
    if not isinstance(raw_profile, Mapping):
        errors.append(f"permissions.{profile_name} must be an object")
        return
    profile = cast(Mapping[str, object], raw_profile)
    if profile.get("extends") is not None:
        errors.append(f"permissions.{profile_name}.extends must be absent")
    allowed_profile_keys = {
        "description",
        "extends",
        "filesystem",
        "network",
        "workspace_roots",
    }
    unexpected = sorted(str(key) for key in set(profile) - allowed_profile_keys)
    if unexpected:
        errors.append(f"permissions.{profile_name} has unexpected keys: {', '.join(unexpected)}")

    expected_roots = {str(context.workspace): True, str(context.scratch_root): True}
    raw_roots = profile.get("workspace_roots")
    effective_roots = (
        {} if raw_roots is None else dict(raw_roots) if isinstance(raw_roots, Mapping) else None
    )
    if effective_roots != expected_roots:
        errors.append(
            f"permissions.{profile_name}.workspace_roots must contain only workspace "
            "and task scratch"
        )

    expected_filesystem = {
        ":minimal": "read",
        str(context.workspace): ("write" if context.sandbox_mode == "workspace-write" else "read"),
        str(context.scratch_root): "write",
        **{str(context.workspace / subpath): "read" for subpath in protected_workspace_subpaths},
        ":tmpdir": "deny",
        ":slash_tmp": "deny",
        str(context.state_root): "deny",
    }
    if _non_null_items(profile.get("filesystem")) != expected_filesystem:
        errors.append(
            f"permissions.{profile_name}.filesystem must allow only minimal runtime, "
            "workspace, and task scratch access"
        )
    if _non_null_items(profile.get("network")) != {"enabled": False}:
        errors.append(f"permissions.{profile_name}.network must be fully disabled")


def validate_apps_config(
    config: Mapping[str, object],
    selected_app_ids: Sequence[str],
    errors: list[str],
) -> None:
    """Validate the exact server-selected Codex app capability set."""

    apps = config.get("apps")
    if apps is None:
        return
    if not isinstance(apps, Mapping):
        errors.append("apps must be an object when present")
        return
    if set(apps) != {"_default", *selected_app_ids}:
        errors.append("apps must contain exactly the server-selected connector policy")
        return
    default_app = apps.get("_default")
    expected_default = {
        "enabled": False,
        "destructive_enabled": False,
        "open_world_enabled": False,
        **(
            {
                "approvals_reviewer": "user",
                "default_tools_approval_mode": "prompt",
            }
            if selected_app_ids
            else {}
        ),
    }
    if not isinstance(default_app, Mapping):
        errors.append("apps._default must be an object")
    elif _non_null_items(default_app) != expected_default:
        errors.append("apps._default must use the locked approval policy")
    expected_selected = {
        "enabled": True,
        "destructive_enabled": False,
        "open_world_enabled": False,
        "approvals_reviewer": "user",
        "default_tools_approval_mode": "prompt",
    }
    for app_id in selected_app_ids:
        app_config = apps.get(app_id)
        if not isinstance(app_config, Mapping) or _non_null_items(app_config) != expected_selected:
            errors.append(f"apps.{app_id} must use the locked approval policy")


def _expect_value(
    config: Mapping[str, object],
    key: str,
    expected: object,
    errors: list[str],
    *,
    prefix: str = "",
) -> None:
    if config.get(key) != expected:
        errors.append(f"{prefix}{key} must equal {expected!r}")


def _mapping_at(
    config: Mapping[str, object],
    key: str,
    errors: list[str],
) -> Mapping[str, object] | None:
    value = config.get(key)
    if not isinstance(value, Mapping):
        errors.append(f"{key} must be an object")
        return None
    return cast(Mapping[str, object], value)


def _non_null_items(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items() if item is not None}


__all__ = [
    "validate_apps_config",
    "validate_permission_profile",
    "validate_provider_profile",
]
