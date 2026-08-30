"""HTTP router for tool-approval policy management.

REST surface for managing approval rules. The frontend's approval
tray uses these endpoints:

* ``GET  /api/permissions``                        — list current rules.
* ``POST /api/permissions/rules``                  — append a rule.
* ``DELETE /api/permissions/rules/{index}``        — drop the rule at that
                                                     0-based position.
* ``POST /api/permissions/rules/{index}/move``     — move a rule within
                                                     the list (order
                                                     matters · first
                                                     match wins).

The router takes a callable returning the storage path so tests can
isolate against ``tmp_path`` without touching the user's real
``ECHO_HOME`` directory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.safety.approval.approval_gate import ApprovalPolicy, ApprovalRule
from runtime.safety.approval.approval_policy_store import (
    append_rule,
    load_policy,
    move_rule,
    save_policy,
)


class _RuleIn(BaseModel):
    effect: Literal["allow", "deny"]
    tool: str = Field(default="*", min_length=1)
    args_contains: str = ""
    reason: str = ""


class _MoveIn(BaseModel):
    to: int = Field(ge=0)


class _RuleOut(BaseModel):
    effect: Literal["allow", "deny"]
    tool: str
    args_contains: str = ""
    reason: str = ""


class _PolicyOut(BaseModel):
    rules: list[_RuleOut]


def _to_out(rule: ApprovalRule) -> _RuleOut:
    return _RuleOut(
        effect=rule.effect,
        tool=rule.tool,
        args_contains=rule.args_contains,
        reason=rule.reason,
    )


def create_permissions_router(
    path_getter: Callable[[], Path] | None = None,
    *,
    identity_store: object | None = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Build the router. ``path_getter`` is called per-request so a
    test fixture can swap the underlying file by re-binding the
    closure rather than rebuilding the app."""
    if path_getter is None:
        from runtime.platform.process.paths import app_paths

        path_getter = lambda: app_paths().permissions_path  # noqa: E731 — single-expression closure is the right shape here

    def _auth_dep(request: Request) -> None:
        # Approval rules directly shape which tools may execute, so in
        # auth-on deployments this whole surface should reject
        # anonymous callers at the router boundary.
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(  # AUTH-OK: actor-agnostic
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _admin_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin",),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(
        prefix="/api/permissions",
        tags=["permissions"],
        dependencies=[Depends(_auth_dep)],
    )

    @router.get("", response_model=_PolicyOut)
    def list_rules() -> _PolicyOut:
        policy = load_policy(path_getter())
        return _PolicyOut(rules=[_to_out(r) for r in policy.rules])

    @router.post(
        "/rules",
        response_model=_PolicyOut,
        dependencies=[Depends(_admin_dep)],
    )
    def add_rule(rule: _RuleIn) -> _PolicyOut:
        new = ApprovalRule(
            effect=rule.effect,
            tool=rule.tool,
            args_contains=rule.args_contains,
            reason=rule.reason,
        )
        policy = append_rule(path_getter(), new)
        return _PolicyOut(rules=[_to_out(r) for r in policy.rules])

    @router.delete(
        "/rules/{index}",
        response_model=_PolicyOut,
        dependencies=[Depends(_admin_dep)],
    )
    def remove_rule(index: int) -> _PolicyOut:
        path = path_getter()
        policy = load_policy(path)
        if index < 0 or index >= len(policy.rules):
            raise HTTPException(status_code=404, detail="rule index out of range")
        next_rules = tuple(r for i, r in enumerate(policy.rules) if i != index)
        next_policy = ApprovalPolicy(rules=next_rules)
        save_policy(path, next_policy)
        return _PolicyOut(rules=[_to_out(r) for r in next_policy.rules])

    @router.post(
        "/rules/{index}/move",
        response_model=_PolicyOut,
        dependencies=[Depends(_admin_dep)],
    )
    def reorder_rule(index: int, body: _MoveIn) -> _PolicyOut:
        path = path_getter()
        try:
            policy = move_rule(path, index, body.to)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _PolicyOut(rules=[_to_out(r) for r in policy.rules])

    return router


__all__ = ["create_permissions_router"]
