"""Exclusive-operator lease management for the computer-automation router.

Split out of the former ~1994-line computer_router.py. The lease is a
single-holder TTL lock (``state.lease``) that serializes desktop control
across concurrent callers — claim/release raise 409 on conflict with a
diagnostic + replay-evidence hint attached.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException

from .computer_diagnostics import _computer_diagnostic
from .computer_replay_evidence import _computer_replay_evidence
from .computer_router_state import ComputerRouterState

_LEASE_TTL_SECONDS = 30
_DEFAULT_LEASE_OWNER_ID = "default-computer-operator"
_DEFAULT_LEASE_OWNER_LABEL = "Default operator"


def _lease_from_body(body: dict[str, Any] | None) -> dict[str, str]:
    body = body or {}
    owner_id = str(
        body.get("lease_owner_id")
        or body.get("owner_id")
        or body.get("project_id")
        or _DEFAULT_LEASE_OWNER_ID
    ).strip()
    owner_label = str(
        body.get("lease_owner_label")
        or body.get("owner_label")
        or body.get("project_label")
        or _DEFAULT_LEASE_OWNER_LABEL
    ).strip()
    return {
        "owner_id": owner_id[:120] or _DEFAULT_LEASE_OWNER_ID,
        "owner_label": owner_label[:120] or _DEFAULT_LEASE_OWNER_LABEL,
    }


def _effective_owner(body: dict[str, Any] | None, actor: str | None) -> dict[str, str]:
    """Resolve the lease owner, binding it to the authenticated principal.

    The exclusive-operator lease serializes desktop control. When auth is on
    (``actor`` is not None), the lease owner *is* the authenticated actor: a
    caller cannot claim the lease under, or release/steal, another operator's
    identity by spoofing ``lease_owner_id`` in the request body. In
    single-user / dev mode (``actor`` is None) we fall back to the cooperative
    body-supplied owner, preserving the friction-free local behavior.
    """
    owner = _lease_from_body(body)
    if actor is None:
        return owner
    actor_id = actor.strip()[:120] or _DEFAULT_LEASE_OWNER_ID
    return {
        "owner_id": actor_id,
        # Keep any human-facing label the caller supplied, but the identity
        # that gates claim/release is the authenticated actor, not the label.
        "owner_label": owner["owner_label"],
    }


def _cleanup_lease(state: ComputerRouterState, now: float | None = None) -> None:
    with state.lease_lock:
        if not state.lease:
            return
        current = time.time() if now is None else now
        if float(state.lease.get("expires_at") or 0) <= current:
            state.lease.clear()


def _public_lease(state: ComputerRouterState, now: float | None = None) -> dict[str, Any]:
    with state.lease_lock:
        current = time.time() if now is None else now
        _cleanup_lease(state, current)
        if not state.lease:
            return {
                "held": False,
                "ttl_seconds": 0,
                "lease_ttl_seconds": _LEASE_TTL_SECONDS,
            }
        return {
            "held": True,
            "owner_id": state.lease.get("owner_id"),
            "owner_label": state.lease.get("owner_label"),
            "acquired_at": state.lease.get("acquired_at"),
            "updated_at": state.lease.get("updated_at"),
            "expires_at": state.lease.get("expires_at"),
            "ttl_seconds": max(0, int(round(float(state.lease["expires_at"]) - current))),
            "lease_ttl_seconds": _LEASE_TTL_SECONDS,
        }


def _claim_lease(state: ComputerRouterState, owner: dict[str, str]) -> dict[str, Any]:
    # Held across the whole check-then-act so two threadpool requests cannot
    # both pass the conflict check and both write the lease (which would let
    # two operators drive the desktop at once — the thing the lease prevents).
    with state.lease_lock:
        now = time.time()
        _cleanup_lease(state, now)
        owner_id = owner["owner_id"]
        if state.lease and state.lease.get("owner_id") != owner_id:
            lease_state = _public_lease(state, now)
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "computer lease is held by another operator",
                    "lease": lease_state,
                    "diagnostic": _computer_diagnostic(
                        "lease_conflict",
                        severity="warning",
                        message="Computer automation lease is held by another operator.",
                        recommended_action="wait_or_release_lease",
                        metadata={
                            "requested_owner_id": owner_id,
                            "current_owner_id": lease_state.get("owner_id"),
                            "ttl_seconds": lease_state.get("ttl_seconds"),
                        },
                    ),
                    "recommended_actions": ["wait_or_release_lease"],
                    "replay_evidence": _computer_replay_evidence(state),
                },
            )
        acquired_at = float(state.lease.get("acquired_at") or now) if state.lease else now
        state.lease.update(
            {
                "owner_id": owner_id,
                "owner_label": owner["owner_label"],
                "acquired_at": acquired_at,
                "updated_at": now,
                "expires_at": now + _LEASE_TTL_SECONDS,
            }
        )
        return _public_lease(state, now)


def _release_lease(
    state: ComputerRouterState, owner: dict[str, str], *, force: bool = False
) -> dict[str, Any]:
    with state.lease_lock:
        now = time.time()
        _cleanup_lease(state, now)
        if state.lease and not force and state.lease.get("owner_id") != owner["owner_id"]:
            lease_state = _public_lease(state, now)
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "computer lease is held by another operator",
                    "lease": lease_state,
                    "diagnostic": _computer_diagnostic(
                        "lease_release_conflict",
                        severity="warning",
                        message="Computer automation lease can only be released by its owner.",
                        recommended_action="release_with_owner_or_force",
                        metadata={
                            "requested_owner_id": owner["owner_id"],
                            "current_owner_id": lease_state.get("owner_id"),
                            "ttl_seconds": lease_state.get("ttl_seconds"),
                        },
                    ),
                    "recommended_actions": ["release_with_owner_or_force"],
                    "replay_evidence": _computer_replay_evidence(state),
                },
            )
        state.lease.clear()
        return _public_lease(state, now)


__all__: list[str] = []
