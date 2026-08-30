"""Security & safety config endpoints for the config router.

Pure structural split of ``_config_endpoints.py`` — no logic changes.
``_register_security`` attaches the identity-lock / constitution-profile /
llm-judge / path-denylist endpoints to the injected router, reading shared
state through the injected ``_ConfigCtx``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException

from runtime.sensing.gateway._config_models import (
    ConstitutionProfileResponse,
    IdentityLockResponse,
)

if TYPE_CHECKING:
    from ._config_endpoints import _ConfigCtx


def _register_security(router: Any, ctx: _ConfigCtx) -> None:
    require_admin = ctx.require_admin
    stack = ctx.stack

    # ─── Identity lock ────────────────────────────────────────
    # Process-wide privacy filter. The GET/PUT pair report and
    # toggle the runtime override; ``null`` defers to the env var /
    # default. Authentication is enforced once at the router level.

    @router.get(
        "/api/config/identity-lock",
        response_model=IdentityLockResponse,
    )
    def api_identity_lock_get() -> dict[str, Any]:
        """Report current identity-lock state.

        Fields
        ------
        locked : bool
            True if vendor/model names are being scrubbed from LLM
            replies in the current process.
        source : str
            ``"runtime"`` (admin toggled via PUT), ``"env"``
            (``ECHO_IDENTITY_LOCK`` set), or ``"default"``.
        unlock_paths : list[str]
            Ways the filter can be bypassed even when locked.
        """
        from runtime.platform import identity_filter as _idf

        rt = _idf.get_runtime_lock()
        if rt is not None:
            source = "runtime"
            locked = rt
        elif os.environ.get("ECHO_IDENTITY_LOCK"):
            source = "env"
            locked = _idf._env_lock_enabled()
        else:
            source = "default"
            locked = True
        return {
            "locked": locked,
            "source": source,
            "unlock_paths": [
                "env ECHO_IDENTITY_LOCK=0",
                "user prompt starts with /raw",
                "body.context.raw_identity=true on thread run",
                "PUT /api/config/identity-lock { locked: false }",
            ],
        }

    @router.put(
        "/api/config/identity-lock",
        response_model=IdentityLockResponse,
        dependencies=[Depends(require_admin)],
    )
    def api_identity_lock_put(body: dict[str, Any]) -> dict[str, Any]:
        """Admin · toggle identity lock at runtime.

        ``null`` clears the runtime override and defers to the env var /
        default. Authentication, when enabled, is enforced once at the
        router level so every config endpoint stays aligned.
        """
        from runtime.platform import identity_filter as _idf

        raw = body.get("locked") if isinstance(body, dict) else None
        if raw is None:
            _idf.set_runtime_lock(None)
        elif isinstance(raw, bool):
            _idf.set_runtime_lock(raw)
        else:
            raise HTTPException(
                400,
                "body.locked must be true / false / null",
            )
        return api_identity_lock_get()

    # ─── Constitution profile ────────────────────────────────
    #
    # Process-wide enforcement profile · strict / normal / lax.
    # Spec in docs/constitution.md §8 · ADR-008 explains the
    # downgrade rules. UI lives at the Privacy settings page.

    @router.get(
        "/api/safety/constitution-profile",
        response_model=ConstitutionProfileResponse,
    )
    def api_constitution_profile_get() -> dict[str, Any]:
        try:
            from runtime.safety.validation import get_profile
        except ImportError:
            return {"profile": "strict", "available": ["strict", "normal", "lax"]}
        return {
            "profile": get_profile(),
            "available": ["strict", "normal", "lax"],
        }

    @router.put(
        "/api/safety/constitution-profile",
        response_model=ConstitutionProfileResponse,
        dependencies=[Depends(require_admin)],
    )
    def api_constitution_profile_put(body: dict[str, Any]) -> dict[str, Any]:
        from runtime.safety.validation import get_profile, set_profile

        raw = body.get("profile") if isinstance(body, dict) else None
        if not isinstance(raw, str):
            raise HTTPException(400, "body.profile must be a string")
        try:
            set_profile(raw)  # type: ignore[arg-type]
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {
            "profile": get_profile(),
            "available": ["strict", "normal", "lax"],
        }

    # ─── LLM semantic-safety judge · runtime toggle ──────────
    #
    # The constitution's judge tier (gate Pass 3) — per-message semantic
    # review (PRIV/LAWF/DGNT). Off by default (one model call per unique
    # outbound message). ``safety.enable_llm_judge`` wires it at boot;
    # this lets the Privacy settings page flip it at RUNTIME via set_judge
    # (no restart). The profile (above) still decides whether a block
    # verdict hard-enforces (strict) or is audit-only (normal/lax).
    # ``available`` is false when there's no model router (static planner).

    def _judge_router() -> Any:
        return getattr(getattr(stack, "planner", None), "router", None)

    def _judge_state() -> dict[str, Any]:
        from runtime.safety.validation.judge import get_judge, null_judge

        return {
            "enabled": get_judge() is not null_judge,
            "available": _judge_router() is not None,
        }

    @router.get("/api/safety/llm-judge")
    def api_llm_judge_get() -> dict[str, Any]:
        return _judge_state()

    @router.put("/api/safety/llm-judge", dependencies=[Depends(require_admin)])
    def api_llm_judge_put(body: dict[str, Any]) -> dict[str, Any]:
        from runtime.safety.validation.judge import set_judge

        want = bool(body.get("enabled")) if isinstance(body, dict) else False
        if want:
            router_obj = _judge_router()
            if router_obj is None:
                raise HTTPException(
                    400,
                    "no model router available (static planner?) — cannot enable judge",
                )
            from runtime.safety.validation.llm_judge import build_judge_from_router

            set_judge(build_judge_from_router(router_obj))
        else:
            set_judge(None)  # back to null_judge (allow-all)
        return _judge_state()

    # ─── Path denylist ───────────────────────────────────────
    # Privacy & security panel · user-managed denied paths. Built-in
    # defaults are policy, not surfaced here.

    @router.get("/api/path-denylist")
    def api_path_denylist_get() -> dict[str, Any]:
        """List user-defined denied paths (privacy & security panel).

        Defaults shipped with the product (``.vscode``, ``AppData``,
        ``.cache``, etc.) are NOT returned — they're built-in policy.
        Only the user-managed list is surfaced; UI shows them with a
        "新增 / 删除" affordance.
        """
        from runtime.safety.auth.path_denylist import get_user_denylist

        return {"paths": get_user_denylist()}

    @router.post("/api/path-denylist", dependencies=[Depends(require_admin)])
    def api_path_denylist_add(payload: dict[str, Any]) -> dict[str, Any]:
        """Append a path to the user denylist."""
        from runtime.safety.auth.path_denylist import add_user_denylist_entry

        path = payload.get("path", "")
        if not isinstance(path, str) or not path.strip():
            from fastapi import HTTPException

            raise HTTPException(400, "path must be a non-empty string")
        return {"paths": add_user_denylist_entry(path), "ok": True}

    @router.delete("/api/path-denylist", dependencies=[Depends(require_admin)])
    def api_path_denylist_remove(payload: dict[str, Any]) -> dict[str, Any]:
        """Remove a path from the user denylist."""
        from runtime.safety.auth.path_denylist import remove_user_denylist_entry

        path = payload.get("path", "")
        if not isinstance(path, str) or not path.strip():
            from fastapi import HTTPException

            raise HTTPException(400, "path must be a non-empty string")
        return {"paths": remove_user_denylist_entry(path), "ok": True}
