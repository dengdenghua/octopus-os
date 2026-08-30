"""System / runtime-config endpoints for the config router.

Pure structural split of ``_config_endpoints.py`` — no logic changes.
``_register_system`` attaches the feature-flags / smart-routing / ai-mode
endpoints to the injected router. These read from subsystems rather than
``custom_models_state``, so they only need the injected router.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Depends

if TYPE_CHECKING:
    from ._config_endpoints import _ConfigCtx


def _register_system(router: Any, ctx: _ConfigCtx) -> None:
    require_admin = ctx.require_admin
    # ─── Feature flags ─────────────────────────────────────
    #
    # Returns the live catalog so the frontend can gate
    # experimental panels (ambient suggestions, team cowork,
    # etc.) without each component reading env directly.

    @router.get("/api/feature-flags")
    def api_feature_flags_list() -> dict[str, Any]:
        """List every registered feature flag with its resolved value.

        Read-only; mutations happen via env vars or
        ``data/feature_flags.json``. Frontend should treat this as
        a one-shot fetch on app load (re-fetch on settings change).

        Response
        --------
        ``{ "flags": [ { name, value, source, default,
        description, experimental, primary_env, legacy_env }, ... ] }``
        """
        from runtime.platform import feature_flags as _ff

        return {"flags": _ff.describe()}

    @router.post("/api/feature-flags/reload", dependencies=[Depends(require_admin)])
    def api_feature_flags_reload() -> dict[str, Any]:
        """Force a re-resolve from env + override file.

        Useful after editing ``data/feature_flags.json`` while the
        server is running. Returns the same shape as the list
        endpoint.
        """
        from runtime.platform import feature_flags as _ff

        _ff.reload()
        return {"flags": _ff.describe()}

    @router.get("/api/smart-routing")
    def api_smart_routing_get() -> dict[str, Any]:
        """Inspect the three-tier smart routing config.

        Returns:
            {
              "enabled": bool,
              "tiers": {
                "local":       <model name | null>,
                "value":       <model name | null>,
                "performance": <model name | null>,
              },
              "env_keys": {
                "local":       "ECHO_MODEL_LOCAL",
                "value":       "ECHO_MODEL_VALUE",
                "performance": "ECHO_MODEL_PERFORMANCE",
              },
              "kill_switch_env": "ECHO_SMART_ROUTING",
            }

        The frontend Settings panel reads this to render the three
        tier slots so users can pick a model per tier.
        """
        from runtime.core.cerebrum.turn_complexity import (
            get_tier_config,
            is_smart_routing_enabled,
        )

        return {
            "enabled": is_smart_routing_enabled(),
            "tiers": get_tier_config(),
            "env_keys": {
                "local": "ECHO_MODEL_LOCAL",
                "value": "ECHO_MODEL_VALUE",
                "performance": "ECHO_MODEL_PERFORMANCE",
            },
            "kill_switch_env": "ECHO_SMART_ROUTING",
        }

    @router.get("/api/ai-mode")
    def api_ai_mode_get() -> dict[str, Any]:
        """Inspect AI mode (Marvis-style efficiency / privacy).

        Returns the current mode + a one-shot device-capability
        recommendation the UI can show as "推荐使用：效率模式".
        Detection is bounded (≤ a few seconds) so the call is safe
        from the settings panel.
        """
        from runtime.core.cerebrum.ai_mode import (
            current_ai_mode,
            detect_device_summary,
            recommend_mode,
        )

        summary = detect_device_summary()
        return {
            "mode": current_ai_mode(),
            "recommended": recommend_mode(summary),
            "device": summary.to_dict(),
            "modes": [
                {
                    "id": "efficiency",
                    "label": "效率模式",
                    "description": (
                        "融合端侧的极致响应与云端的强大算力，效果更好，速度更快，绝大多数用户的首选"
                    ),
                    "recommended_default": True,
                },
                {
                    "id": "privacy",
                    "label": "隐私模式",
                    "description": ("专为保密场景设计，使用本地模型，全部文件均在本地处理和分析"),
                    "recommended_default": False,
                },
            ],
        }

    @router.post("/api/ai-mode", dependencies=[Depends(require_admin)])
    def api_ai_mode_set(payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the user's AI mode choice."""
        from runtime.core.cerebrum.ai_mode import set_ai_mode

        try:
            canonical = set_ai_mode(payload.get("mode", ""))
        except ValueError as exc:
            from fastapi import HTTPException

            raise HTTPException(400, str(exc)) from exc
        return {"mode": canonical, "ok": True}
