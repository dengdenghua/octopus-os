"""Principal-scoped Coder/Codex account and model control endpoints."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from pydantic import ValidationError

from runtime.execution.codex_backend.account import (
    CodexAccountCapacityError,
    CodexAccountConflict,
    CodexAccountLeaseError,
)
from runtime.execution.codex_backend.model_profile import (
    CodexModelPreference,
    codex_proxy_route_available,
    is_disallowed_coder_system_model,
    resolve_codex_execution_profile,
)
from runtime.execution.codex_backend.types import (
    CodexAppServerError,
    ConfigurationError,
)
from runtime.safety.auth.scope import scope_from_request
from runtime.sensing.gateway._config_models import (
    CodexAccountResponse,
    CodexAppsPutBody,
    CodexAppsResponse,
    CodexCancelLoginResponse,
    CodexLoginBody,
    CodexLoginResponse,
    CodexLogoutResponse,
    CodexModelProfilePutBody,
    CodexModelProfileResponse,
    CodexModelsResponse,
    CodexRateLimitsResponse,
    CodexUpdateApprovalBody,
    CodexUpdateStatusResponse,
    CodexUsageResponse,
)

if TYPE_CHECKING:
    from ._config_endpoints import _ConfigCtx


def _register_coder_codex(router: Any, ctx: _ConfigCtx) -> None:
    accounts = ctx.codex_accounts
    preferences = ctx.codex_preferences
    updates = ctx.codex_updates

    @router.get(
        "/api/coder/codex/upstream-update",
        response_model=CodexUpdateStatusResponse,
        tags=["coder"],
    )
    async def api_coder_codex_upstream_update() -> dict[str, object]:
        return updates.read().to_wire()

    @router.post(
        "/api/coder/codex/upstream-update/check",
        response_model=CodexUpdateStatusResponse,
        tags=["coder"],
    )
    async def api_check_coder_codex_upstream_update(
        request: Request,
    ) -> dict[str, object]:
        ctx.require_admin(request)
        return (await asyncio.to_thread(updates.check)).to_wire()

    @router.post(
        "/api/coder/codex/upstream-update/approve",
        response_model=CodexUpdateStatusResponse,
        tags=["coder"],
    )
    async def api_approve_coder_codex_upstream_update(
        request: Request,
        body: CodexUpdateApprovalBody,
    ) -> dict[str, object]:
        ctx.require_admin(request)
        try:
            return updates.approve(body.version).to_wire()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get(
        "/api/coder/codex/account",
        response_model=CodexAccountResponse,
        tags=["coder"],
    )
    async def api_coder_codex_account(request: Request) -> dict[str, object]:
        scope = scope_from_request(request)
        try:
            status = await accounts.run_on_runtime_loop(scope, lambda: accounts.read_account(scope))
        except Exception as exc:
            raise _account_http_error(exc, operation="read") from None
        return status.to_wire()

    @router.post(
        "/api/coder/codex/login",
        response_model=CodexLoginResponse,
        tags=["coder"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["type"],
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["chatgpt", "chatgptDeviceCode", "apiKey"],
                                },
                                "api_key": {
                                    "anyOf": [
                                        {
                                            "type": "string",
                                            "format": "password",
                                            "writeOnly": True,
                                        },
                                        {"type": "null"},
                                    ]
                                },
                            },
                        }
                    }
                },
            }
        },
    )
    async def api_coder_codex_login(request: Request) -> dict[str, object]:
        body = await _read_login_body(request)
        if body.type == "apiKey" and (
            body.api_key is None or not body.api_key.get_secret_value().strip()
        ):
            raise HTTPException(400, "Codex API key login request is invalid")
        if body.type != "apiKey" and body.api_key is not None:
            raise HTTPException(400, "Codex login request is invalid")
        api_key = body.api_key.get_secret_value() if body.api_key is not None else None
        try:
            scope = scope_from_request(request)
            return await accounts.run_on_runtime_loop(
                scope,
                lambda: accounts.login(
                    scope,
                    login_type=body.type,
                    api_key=api_key,
                ),
            )
        except Exception as exc:
            raise _account_http_error(exc, operation="login") from None
        finally:
            api_key = None

    @router.post(
        "/api/coder/codex/login/{login_id}/cancel",
        response_model=CodexCancelLoginResponse,
        tags=["coder"],
    )
    async def api_coder_codex_cancel_login(
        login_id: str,
        request: Request,
    ) -> dict[str, object]:
        try:
            scope = scope_from_request(request)
            return await accounts.run_on_runtime_loop(
                scope,
                lambda: accounts.cancel_login(scope, login_id=login_id),
            )
        except Exception as exc:
            raise _account_http_error(exc, operation="cancel") from None

    @router.post(
        "/api/coder/codex/logout",
        response_model=CodexLogoutResponse,
        tags=["coder"],
    )
    async def api_coder_codex_logout(request: Request) -> dict[str, object]:
        scope = scope_from_request(request)
        try:
            # Logout is one server-owned state transition.  Persisting the
            # safe follow-system policy first means a failed account logout
            # cannot leave a stale Codex-account model selected; conversely a
            # preference write failure aborts before authentication changes.
            preferences.write(scope, CodexModelPreference(mode="follow_system"))
            return await accounts.run_on_runtime_loop(scope, lambda: accounts.logout(scope))
        except Exception as exc:
            raise _account_http_error(exc, operation="logout") from None

    @router.get(
        "/api/coder/codex/models",
        response_model=CodexModelsResponse,
        tags=["coder"],
    )
    async def api_coder_codex_models(
        request: Request,
        include_hidden: bool = False,
    ) -> dict[str, object]:
        try:
            scope = scope_from_request(request)
            models = await accounts.run_on_runtime_loop(
                scope,
                lambda: accounts.list_models(scope, include_hidden=include_hidden),
            )
        except Exception as exc:
            raise _account_http_error(exc, operation="models") from None
        return {"models": models, "source": "codex_account"}

    @router.get(
        "/api/coder/codex/rate-limits",
        response_model=CodexRateLimitsResponse,
        tags=["coder"],
    )
    async def api_coder_codex_rate_limits(request: Request) -> dict[str, object]:
        try:
            scope = scope_from_request(request)
            return await accounts.run_on_runtime_loop(
                scope, lambda: accounts.read_rate_limits(scope)
            )
        except Exception as exc:
            raise _account_http_error(exc, operation="rate limits") from None

    @router.get(
        "/api/coder/codex/usage",
        response_model=CodexUsageResponse,
        tags=["coder"],
    )
    async def api_coder_codex_usage(request: Request) -> dict[str, object]:
        try:
            scope = scope_from_request(request)
            return await accounts.run_on_runtime_loop(scope, lambda: accounts.read_usage(scope))
        except Exception as exc:
            raise _account_http_error(exc, operation="usage") from None

    @router.get(
        "/api/coder/codex/apps",
        response_model=CodexAppsResponse,
        tags=["coder"],
    )
    async def api_coder_codex_apps(
        request: Request,
        force_refetch: bool = False,
    ) -> dict[str, object]:
        scope = scope_from_request(request)
        try:
            apps = await accounts.run_on_runtime_loop(
                scope,
                lambda: accounts.list_apps(scope, force_refetch=force_refetch),
            )
        except Exception as exc:
            raise _account_http_error(exc, operation="apps") from None
        selected = set(preferences.read(scope).app_ids)
        return {"apps": [{**app, "selected": app.get("id") in selected} for app in apps]}

    @router.put(
        "/api/coder/codex/apps",
        response_model=CodexAppsResponse,
        tags=["coder"],
    )
    async def api_put_coder_codex_apps(
        request: Request,
        body: CodexAppsPutBody,
    ) -> dict[str, object]:
        scope = scope_from_request(request)
        try:
            apps = await accounts.run_on_runtime_loop(
                scope, lambda: accounts.list_apps(scope, force_refetch=False)
            )
            accessible = {
                str(app["id"])
                for app in apps
                if app.get("is_accessible") is True and isinstance(app.get("id"), str)
            }
            requested = tuple(dict.fromkeys(body.app_ids))
            if any(app_id not in accessible for app_id in requested):
                raise HTTPException(400, "Selected Codex connector is not accessible")
            current = preferences.read(scope)
            updated = CodexModelPreference(
                mode=current.mode,
                model=current.model,
                reasoning_effort=current.reasoning_effort,
                app_ids=requested,
            )
            preferences.write(scope, updated)
        except HTTPException:
            raise
        except Exception as exc:
            raise _account_http_error(exc, operation="apps") from None
        selected = set(updated.app_ids)
        return {"apps": [{**app, "selected": app.get("id") in selected} for app in apps]}

    @router.get(
        "/api/coder/codex/model-profile",
        response_model=CodexModelProfileResponse,
        tags=["coder"],
    )
    async def api_coder_codex_model_profile(request: Request) -> dict[str, object]:
        scope = scope_from_request(request)
        return _resolved_profile(ctx, preferences.read(scope))

    @router.put(
        "/api/coder/codex/model-profile",
        response_model=CodexModelProfileResponse,
        tags=["coder"],
    )
    async def api_put_coder_codex_model_profile(
        request: Request,
        body: CodexModelProfilePutBody,
    ) -> dict[str, object]:
        scope = scope_from_request(request)
        if body.mode == "follow_system":
            if is_disallowed_coder_system_model(body.model):
                raise HTTPException(400, "mix is not available to the Coder engine")
            # The system and Codex-account model domains are disjoint, but
            # both are user-selectable.  Persist an optional system-side model
            # and effort here; a missing model still means inherit the Echo
            # default.  Resolution below validates the selected route without
            # ever exposing host provider credentials to the Codex sidecar.
            try:
                preference = CodexModelPreference(
                    mode="follow_system",
                    model=body.model,
                    reasoning_effort=body.reasoning_effort,
                    app_ids=preferences.read(scope).app_ids,
                )
            except ConfigurationError:
                raise HTTPException(400, "Coder model preference is invalid") from None
        else:
            try:
                preference = CodexModelPreference(
                    mode="chatgpt",
                    model=body.model,
                    reasoning_effort=body.reasoning_effort,
                    app_ids=preferences.read(scope).app_ids,
                )
            except ConfigurationError:
                raise HTTPException(400, "Coder model preference is invalid") from None
            if preference.model is not None or preference.reasoning_effort is not None:
                # Never make a user selection wait on App Server's model/list.
                # Validate against the account-session catalog when one is
                # already available; otherwise Codex performs the authoritative
                # validation when the selected model is used.
                models = accounts.cached_models(scope, include_hidden=False)
                if models is not None:
                    _validate_account_model_preference(preference, models)
        preferences.write(scope, preference)
        return _resolved_profile(ctx, preference)


def _resolved_profile(ctx: _ConfigCtx, preference: CodexModelPreference):
    router = getattr(getattr(ctx.stack, "planner", None), "router", None)
    profile = resolve_codex_execution_profile(
        preference=preference,
        system_model=_system_model(ctx.stack),
        custom_models=ctx.custom_models_snapshot(),
        proxy_available=router is not None and callable(getattr(router, "call", None)),
        proxy_route_available=lambda model: codex_proxy_route_available(router, model),
    )
    return {**profile.to_wire(), "selected_model": preference.model}


async def _read_login_body(request: Request) -> CodexLoginBody:
    """Parse the secret-bearing body without ever returning raw validation input."""

    try:
        payload = await request.json()
        return CodexLoginBody.model_validate(payload)
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(400, "Codex login request is invalid") from None


def _system_model(stack: Any) -> str | None:
    config_model = getattr(getattr(getattr(stack, "config", None), "planner", None), "model", None)
    if isinstance(config_model, str) and config_model.strip():
        return config_model.strip()
    planner = getattr(stack, "planner", None)
    planner_model = getattr(planner, "planner_model", None)
    if isinstance(planner_model, str) and planner_model.strip():
        return planner_model.strip()
    default_model = getattr(getattr(planner, "router", None), "default_model", None)
    if isinstance(default_model, str) and default_model.strip():
        return default_model.strip()
    return None


def _validate_account_model_preference(
    preference: CodexModelPreference,
    models: list[dict[str, object]],
) -> None:
    selected: dict[str, object] | None = None
    if preference.model is not None:
        selected = next((item for item in models if item.get("id") == preference.model), None)
        if selected is None:
            raise HTTPException(400, "Selected model is not available for this Codex account")
    elif preference.reasoning_effort is not None:
        selected = next((item for item in models if item.get("is_default") is True), None)
        if selected is None:
            raise HTTPException(400, "Codex default model could not be validated")
    if selected is not None and preference.reasoning_effort is not None:
        raw_efforts = selected.get("reasoning_efforts")
        efforts = raw_efforts if isinstance(raw_efforts, list) else []
        if preference.reasoning_effort not in efforts:
            raise HTTPException(400, "Reasoning effort is not supported by the selected model")


def _account_http_error(exc: Exception, *, operation: str) -> HTTPException:
    """Map failures to fixed, non-secret public messages."""

    if isinstance(exc, CodexAccountConflict):
        return HTTPException(409, "A Codex login is already pending")
    if isinstance(exc, CodexAccountCapacityError):
        return HTTPException(503, "Codex account service is temporarily at capacity")
    if isinstance(exc, CodexAccountLeaseError):
        return HTTPException(409, "Codex account is active on another server worker")
    if isinstance(exc, ConfigurationError):
        return HTTPException(400, f"Codex account {operation} request is invalid")
    if isinstance(exc, CodexAppServerError):
        return HTTPException(503, f"Codex account {operation} operation failed")
    return HTTPException(503, f"Codex account {operation} operation failed")


__all__ = ["_register_coder_codex"]
