from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute

_LOG = logging.getLogger(__name__)

_STUB_HEADER = "X-Echo-Stub"
_STUB_REASON_HEADER = "X-Echo-Stub-Reason"
_STUB_ROUTE_HEADER = "X-Echo-Stub-Route"
_STUB_SOURCE_HEADER = "X-Echo-Stub-Source"
_STUB_REASON = "compatibility_fallback"
_STUB_SOURCE = "runtime.sensing.gateway.stub_router"
_DISABLE_ENV = "ECHO_DISABLE_STUB_API"
_ALLOW_ENV = "ECHO_ALLOW_STUB_API"
_REAL_API_REQUIRED_ENV = "ECHO_REAL_API_REQUIRED"
_ENV_ENV = "ECHO_ENV"
_PRODUCTION_ENVS = {"prod", "production"}
_TRUTHY = {"1", "true", "yes", "on"}


def _envelope(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None, "meta": {}}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _stub_api_enabled(enabled: bool | None, *, require_auth: bool = False) -> bool:
    if enabled is not None:
        return enabled
    if _env_truthy(_DISABLE_ENV) or _env_truthy(_REAL_API_REQUIRED_ENV):
        return False
    if _env_truthy(_ALLOW_ENV):
        return True
    # Auth-on is the product's deploy signal: fake account/billing/usage
    # data must never be the silent default on a surface someone actually
    # exposed — opt back in with ECHO_ALLOW_STUB_API if truly wanted.
    if require_auth:
        return False
    env_name = os.environ.get(_ENV_ENV, "").strip().lower()
    return env_name not in _PRODUCTION_ENVS


class _StubRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()
        route_path = self.path

        async def tagging_handler(request: Request) -> Response:
            resp = await original(request)
            resp.headers[_STUB_HEADER] = "true"
            resp.headers[_STUB_REASON_HEADER] = _STUB_REASON
            resp.headers[_STUB_ROUTE_HEADER] = route_path
            resp.headers[_STUB_SOURCE_HEADER] = _STUB_SOURCE
            media = resp.headers.get("content-type", "")
            if "application/json" not in media:
                return resp
            body = getattr(resp, "body", b"")
            if not body:
                return resp
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, TypeError, ValueError):  # Implementation note.
                return resp
            if isinstance(data, dict):
                if "_stub" not in data:
                    data["_stub"] = True
                if "_stub_reason" not in data:
                    data["_stub_reason"] = _STUB_REASON
                if "_stub_route" not in data:
                    data["_stub_route"] = route_path
                if "_stub_source" not in data:
                    data["_stub_source"] = _STUB_SOURCE
            else:
                return resp
            new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            resp.body = new_body
            resp.headers["content-length"] = str(len(new_body))
            return resp

        return tagging_handler


def create_stub_router(
    *,
    enabled: bool | None = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    if not _stub_api_enabled(enabled, require_auth=require_auth):
        _LOG.info(
            "stub API disabled (auth=%s, %s to force-enable)",
            "on" if require_auth else "off",
            _ALLOW_ENV,
        )
        return APIRouter(tags=["stub-disabled"])

    router = APIRouter(tags=["stub"], route_class=_StubRoute)

    @router.get("/api/auth/status")
    def _auth_status() -> dict[str, Any]:
        return {
            "enabled": True,
            "jwt_available": True,
            "allow_registration": False,
            "exempt_paths": [],
        }

    @router.get("/api/auth/me")
    def _auth_me(request: Request) -> dict[str, Any]:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer ") and jwt_secret:
            token = auth[7:].strip()
            if token.count(".") == 2:
                try:
                    from runtime.safety.auth.identity import JWTError, verify_jwt_hs256

                    claims = verify_jwt_hs256(
                        token,
                        secret=jwt_secret,
                        required_issuer=jwt_issuer,
                        required_audience=jwt_audience,
                    )
                    sub = claims.get("sub", "unknown")
                    # Oct token: sub = "oct:<email>"
                    # Local token: sub = "local:<username>"
                    if sub.startswith("oct:"):
                        email = sub[4:]
                        return {
                            "user_id": sub,
                            "username": email,
                            "email": email,
                            "roles": ["user"],
                            "permissions": [],
                            "provider": "oct",
                        }
                    if sub.startswith("local:"):
                        username = sub[6:]
                        return {
                            "user_id": sub,
                            "username": username,
                            "email": None,
                            "roles": ["user", "local"],
                            "permissions": [],
                            "provider": "local",
                        }
                    return {
                        "user_id": sub,
                        "username": sub,
                        "email": None,
                        "roles": ["user"],
                        "permissions": [],
                    }
                except (ValueError, TypeError, AttributeError, JWTError):  # noqa: BLE001 — stub envelope wrap; pass through original on failure
                    pass

        return {
            "user_id": "anonymous",
            "username": "anonymous",
            "email": None,
            "roles": [],
            "permissions": [],
        }

    @router.post("/api/auth/refresh")
    def _auth_refresh() -> dict[str, Any]:
        return {"access_token": None, "expires_at": None}

    @router.post("/api/auth/logout")
    def _auth_logout() -> dict[str, Any]:
        return {"success": True}

    @router.get("/api/account/profile")
    def _profile() -> dict[str, Any]:
        return _envelope(
            {
                "user_id": "anonymous",
                "username": "anonymous",
                "display_name": "Guest",
                "email": None,
                "avatar_url": None,
                "bio": None,
                "timezone": "UTC",
                "language": "en",
                "linked_accounts": [],
                "privacy_mode": False,
                "data_collection_consent": False,
                "marketing_emails": False,
                "created_at": "1970-01-01T00:00:00Z",
                "updated_at": "1970-01-01T00:00:00Z",
            }
        )

    @router.patch("/api/account/profile")
    def _profile_patch(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return _envelope({"updated": True})

    @router.get("/api/account/privacy")
    def _privacy() -> dict[str, Any]:
        return _envelope(
            {
                "privacy_mode": False,
                "data_collection_consent": False,
                "share_usage_analytics": False,
                "allow_community_features": False,
                "public_profile": False,
            }
        )

    @router.patch("/api/account/privacy")
    def _privacy_patch(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return _envelope({"updated": True})

    @router.get("/api/account/subscription")
    def _subscription() -> dict[str, Any]:
        return _envelope(None)

    @router.get("/api/account/plans")
    def _plans() -> dict[str, Any]:
        return _envelope([])

    @router.post("/api/account/subscribe")
    def _subscribe(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return _envelope({"ok": True})

    @router.post("/api/account/cancel")
    def _cancel(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return _envelope({"ok": True})

    @router.get("/api/account/usage")
    def _usage() -> dict[str, Any]:
        return _envelope(None)

    @router.get("/api/account/usage/events")
    def _usage_events() -> dict[str, Any]:
        return _envelope(
            {
                "data": [],
                "pagination": {
                    "total": 0,
                    "page": 1,
                    "page_size": 20,
                    "total_pages": 0,
                },
            }
        )

    @router.get("/api/account/usage/summary")
    def _usage_summary() -> dict[str, Any]:
        return _envelope(
            {
                "user_id": "anonymous",
                "period": "month",
                "total_cost": "0",
                "total_requests": 0,
                "total_tokens": 0,
                "by_model": {},
                "by_event_type": {},
                "most_used_model": None,
            }
        )

    @router.get("/api/account/billing")
    def _billing() -> dict[str, Any]:
        return _envelope(
            {
                "user_id": "anonymous",
                "current_balance": "0",
                "bonus_balance": "0",
                "current_period_cost": "0",
                "last_invoice_date": None,
                "last_invoice_amount": None,
                "next_billing_date": None,
            }
        )

    @router.get("/api/account/billing/history")
    def _billing_history() -> dict[str, Any]:
        return _envelope(
            {
                "data": [],
                "pagination": {
                    "total": 0,
                    "page": 1,
                    "page_size": 20,
                    "total_pages": 0,
                },
            }
        )

    @router.get("/api/account/billing/invoices/{invoice_id}")
    def _invoice(invoice_id: str) -> dict[str, Any]:
        return _envelope(None)

    @router.get("/api/account/linked-accounts")
    def _linked_accounts() -> dict[str, Any]:
        return _envelope([])

    @router.post("/api/account/linked-accounts")
    def _link_account(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return _envelope({"linked": False})

    @router.delete("/api/account/linked-accounts/{provider}")
    def _unlink_account(provider: str) -> dict[str, Any]:
        return _envelope({"unlinked": True})

    @router.post("/api/account/avatar")
    def _avatar() -> dict[str, Any]:
        return {"success": True, "avatar_url": "", "data": {"avatar_url": ""}}

    @router.get("/api/account/overview")
    def _overview() -> dict[str, Any]:
        profile: dict[str, Any] = {
            "user_id": "anonymous",
            "username": "anonymous",
            "display_name": "Guest",
            "email": None,
            "avatar_url": None,
            "bio": None,
            "timezone": "UTC",
            "language": "en",
            "linked_accounts": [],
            "privacy_mode": False,
            "data_collection_consent": False,
            "marketing_emails": False,
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
        }
        billing = {
            "user_id": "anonymous",
            "current_balance": "0",
            "bonus_balance": "0",
            "current_period_cost": "0",
            "last_invoice_date": None,
            "last_invoice_amount": None,
            "next_billing_date": None,
        }
        return _envelope(
            {
                "profile": profile,
                "subscription": None,
                "usage": None,
                "billing_summary": billing,
                "recent_usage": [],
                "recent_billing": [],
            }
        )

    # ─── Memory ────────────────────────────────────────
    _empty_memory = {
        "version": 1,
        "lastUpdated": "1970-01-01T00:00:00Z",
        "user": {
            "workContext": "",
            "personalContext": "",
            "topOfMind": "",
        },
        "history": {
            "recentMonths": "",
            "earlierContext": "",
            "longTermBackground": "",
        },
        "facts": [],
    }

    @router.get("/api/memory")
    def _memory() -> dict[str, Any]:
        return _empty_memory

    @router.get("/api/memory/search")
    def _memory_search(q: str | None = None) -> list[Any]:
        return []

    @router.post("/api/memory/reload")
    def _memory_reload() -> dict[str, Any]:
        return _empty_memory

    @router.delete("/api/memory")
    def _memory_clear() -> dict[str, Any]:
        return _empty_memory

    @router.post("/api/memory/facts")
    def _memory_fact_create(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return _empty_memory

    @router.delete("/api/memory/facts/{fact_id}")
    def _memory_fact_delete(fact_id: str) -> dict[str, Any]:
        return _empty_memory

    @router.patch("/api/memory/facts/{fact_id}")
    def _memory_fact_update(fact_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return _empty_memory

    @router.get("/api/memory/config")
    def _memory_config() -> dict[str, Any]:
        return {
            "enabled": False,
            "storage_path": "",
            "debounce_seconds": 5,
            "max_facts": 500,
            "fact_confidence_threshold": 0.5,
            "injection_enabled": False,
            "max_injection_tokens": 2000,
        }

    @router.get("/api/memory/export")
    def _memory_export() -> dict[str, Any]:
        return _empty_memory

    @router.post("/api/memory/import")
    def _memory_import(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return _empty_memory

    # ─── Observability / Metrics / Alerts / Trace / Telemetry ──
    @router.get("/api/metrics")
    def _metrics() -> dict[str, Any]:
        return {}

    @router.get("/api/metrics/summary")
    def _metrics_summary() -> dict[str, Any]:
        return {"metrics": {}, "health": {}, "alerts": []}

    @router.get("/api/alerts")
    def _alerts() -> list[Any]:
        return []

    @router.get("/api/alerts/rules")
    def _alert_rules() -> list[Any]:
        return []

    @router.post("/api/alerts/rules")
    def _alert_rule_create(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True}

    @router.delete("/api/alerts/rules/{name}")
    def _alert_rule_delete(name: str) -> dict[str, Any]:
        return {"ok": True}

    @router.get("/api/trace/recent")
    def _trace_recent(limit: int = 50) -> list[Any]:
        return []

    @router.get("/api/trace/{trace_id}")
    def _trace_detail(trace_id: str) -> list[Any]:
        return []

    @router.get("/api/telemetry/stats")
    def _telemetry_stats() -> dict[str, Any]:
        return {
            "enabled": False,
            "metrics_exporter": None,
            "logs_exporter": None,
            "traces_exporter": None,
            "redact_prompts": True,
            "redact_tool_content": True,
        }

    @router.get("/api/observability/health")
    def _obs_health() -> dict[str, Any]:
        return {"status": "ok", "components": {}}

    # ─── Skills extras (market removed · meta-skills moved to /api/skills/packs) ──

    @router.get("/api/skills/custom")
    def _skills_custom() -> dict[str, Any]:
        return {"skills": []}

    @router.get("/api/skills/performance")
    def _skills_perf() -> list[Any]:
        return []

    @router.get("/api/skills/declining")
    def _skills_declining() -> list[Any]:
        return []

    # ─── Integrations auth ─────────────────────────────
    @router.get("/api/integrations/auth/providers")
    def _int_providers() -> list[Any]:
        return []

    @router.get("/api/integrations/auth/providers/types")
    def _int_provider_types() -> list[dict[str, str]]:
        return []

    @router.get("/api/integrations/auth")
    def _int_auth_list() -> list[Any]:
        return []

    @router.get("/api/integrations/auth/providers/{provider_id}")
    def _int_provider(provider_id: str) -> dict[str, Any]:
        return {"id": provider_id, "name": provider_id, "available": False}

    # ─── Plugins ───────────────────────────────────────
    # Fallbacks for the real plugins_router (mounted earlier, so it wins the
    # match in normal operation; these only serve if that mount raised). The
    # real handlers share these function names + paths, so give the stubs
    # explicit operation_ids to avoid duplicate-operation-id schema warnings.
    @router.get("/api/plugins", operation_id="stub_list_plugins")
    def _plugins() -> list[Any]:
        return []

    @router.get("/api/plugins/capabilities", operation_id="stub_plugin_capabilities")
    def _plugin_caps() -> list[Any]:
        return []

    @router.get("/api/plugins/{plugin_id}", operation_id="stub_get_plugin")
    def _plugin_get(plugin_id: str) -> dict[str, Any]:
        return {"id": plugin_id, "name": plugin_id, "enabled": False, "state": "stopped"}

    # ─── Task board ────────────────────────────────────
    @router.get("/api/task-board/all")
    def _tb_all() -> dict[str, Any]:
        return {"tasks": [], "total": 0}

    @router.get("/api/task-board/stats")
    def _tb_stats() -> dict[str, Any]:
        return {
            "total": 0,
            "by_status": {},
            "by_type": {},
            "avg_duration_ms": 0,
            "success_rate": 0.0,
            "running_count": 0,
            "queued_count": 0,
        }

    @router.get("/api/task-board/timeline")
    def _tb_timeline() -> dict[str, Any]:
        return {"tasks": [], "earliest_ms": 0, "latest_ms": 0}

    # ─── Arena ─────────────────────────────────────────
    @router.get("/api/arena/leaderboard")
    def _arena_lb() -> dict[str, Any]:
        return {"entries": []}

    @router.get("/api/arena/history")
    def _arena_hist() -> dict[str, Any]:
        return {"battles": []}

    @router.get("/api/arena/stats")
    def _arena_stats() -> dict[str, Any]:
        return {"total_battles": 0, "total_voted": 0, "total_models": 0}

    @router.post("/api/arena/battle")
    def _arena_battle(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"error": "arena not enabled"}

    @router.post("/api/arena/vote")
    def _arena_vote(body: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": False, "error": "arena not enabled"}

    # ─── Agent Market ──────────────────────────────────
    # Replaced by agent_world_router.py · stubs removed.

    @router.get("/api/intelligence/subscriptions")
    def _intel_subs() -> dict[str, Any]:
        return {"subscriptions": []}

    @router.post("/api/intelligence/subscriptions")
    def _intel_subs_create() -> dict[str, Any]:
        return {"id": "stub", "topic": "stub", "enabled": True}

    @router.patch("/api/intelligence/subscriptions/{sub_id}")
    def _intel_subs_update(sub_id: str) -> dict[str, Any]:
        return {"id": sub_id, "enabled": True}

    @router.delete("/api/intelligence/subscriptions/{sub_id}")
    def _intel_subs_delete(sub_id: str) -> dict[str, Any]:
        return {"ok": False, "id": sub_id, "error": "intelligence not enabled"}

    @router.post("/api/intelligence/subscriptions/{sub_id}/run")
    def _intel_subs_run(sub_id: str) -> dict[str, Any]:
        return {
            "ok": False,
            "subscription": {"id": sub_id, "enabled": False},
            "report": None,
            "error": "intelligence not enabled",
        }

    @router.post("/api/intelligence/run")
    def _intel_run_all() -> dict[str, Any]:
        return {
            "ok": False,
            "reports": [],
            "reports_count": 0,
            "subscriptions_checked": 0,
            "error": "intelligence not enabled",
        }

    @router.get("/api/intelligence/reports")
    def _intel_reports() -> dict[str, Any]:
        return {"reports": []}

    @router.get("/api/intelligence/reports/{report_id}")
    def _intel_report_detail(report_id: str) -> dict[str, Any]:
        return {"id": report_id, "error": "intelligence not enabled"}

    @router.get("/api/knowledge/graph")
    def _kg_graph() -> dict[str, Any]:
        return {"entities": [], "relationships": []}

    @router.get("/api/knowledge/stats")
    def _kg_stats() -> dict[str, Any]:
        return {
            "total_entities": 0,
            "total_relationships": 0,
            "entity_types": {},
        }

    @router.get("/api/knowledge/entities/{entity_id}")
    def _kg_entity(entity_id: str) -> dict[str, Any]:
        return {"id": entity_id, "name": "", "type": "", "description": ""}

    @router.get("/api/swarm/status")
    def _swarm_status() -> dict[str, Any]:
        return {"status": "idle", "tasks": []}

    @router.get("/api/swarm/tasks")
    def _swarm_tasks() -> dict[str, Any]:
        return {"tasks": []}

    @router.post("/api/swarm/tasks")
    def _swarm_tasks_create() -> dict[str, Any]:
        return {"id": "stub", "status": "pending"}

    _LOG.info(
        "stub API mounted: %d compatibility routes returning simulated "
        "account/billing/usage data (responses carry %s; disable with %s)",
        len(router.routes),
        _STUB_HEADER,
        _DISABLE_ENV,
    )
    return router
