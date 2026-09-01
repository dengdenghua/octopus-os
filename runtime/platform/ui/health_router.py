"""Health and capability endpoints for the UI app."""

from __future__ import annotations

import contextlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from runtime import __version__
from runtime.platform.process.paths import app_paths, project_root

from ._health_checks import (
    _api_surface_info,
    _automation_surface_info,
    _model_compat_info,
    _orchestration_surface_info,
    _run_evidence_surface_info,
    _webui_static_info,
)
from ._health_helpers import (
    _canonical_backend_host,
    _clean_host,
    _coerce_port,
    _frontend_runtime_info,
    _frontend_version,
    _journal_source_detail,
    _journal_source_usable,
    _loopback_aliases,
    _process_info,
    _project_version,
)

_PROCESS_STARTED_AT = datetime.now(UTC)
_CLEAN_SOURCE_ID = re.compile(r"[0-9a-f]{40}")


def _runtime_identity() -> dict[str, Any]:
    """Return a public-safe build identity for desktop compatibility checks.

    A source revision is reported as verified only when a trusted launcher has
    explicitly asserted that it validated the immutable runtime bundle.  The
    native Echo OS entrypoint owns that assertion after checking the wheel,
    WebUI, resources and Codex manifests.
    """

    identity: dict[str, Any] = {
        "name": "echo-agent-runtime",
        "version": __version__,
        "verifiedBundle": False,
    }
    source_id = os.environ.get("ECHO_RUNTIME_SOURCE_ID", "").strip()
    verified = os.environ.get("ECHO_RUNTIME_BUNDLE_VERIFIED") == "1"
    if verified and _CLEAN_SOURCE_ID.fullmatch(source_id):
        identity["sourceId"] = source_id
        identity["verifiedBundle"] = True
    return identity


def _lifecycle_generation() -> dict[str, Any]:
    """Identify the loaded lifecycle build and detect stale dev processes."""

    root = project_root(Path(__file__))
    files = (
        Path(__file__),
        root / "runtime/protocol/items.py",
        root / "runtime/sensing/gateway/realtime_turn_lifecycle.py",
        root / "runtime/core/cerebrum/pause_control.py",
    )
    source_mtime_ns = max(
        (path.stat().st_mtime_ns for path in files if path.exists()),
        default=0,
    )
    started_ns = int(_PROCESS_STARTED_AT.timestamp() * 1_000_000_000)
    return {
        "processStartedAt": _PROCESS_STARTED_AT.isoformat(),
        "sourceMtimeNs": source_mtime_ns,
        "restartRequired": source_mtime_ns > started_ns,
    }


def create_health_router(
    *,
    state: Any,
    agent_registry: Any = None,
    channel_manager: Any = None,
    group_registry: Any = None,
    server_host: str | None = None,
    server_port: int | None = None,
    frontend_host: str | None = None,
    frontend_port: int | None = None,
    frontend_proxy_target: str | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Create ``/api/health`` and ``/api/status`` endpoints."""
    router = APIRouter(tags=["health"])

    def _capability_auth(request: Request) -> None:
        from runtime.safety.auth.principal import require_operator

        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    @router.get("/api/health")
    def api_health() -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": "ok",
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "runtime": _runtime_identity(),
            "skills": len(state.registry),
            "journal_events": -1,
            "agents": 0,
            "channels": [],
            "groups": 0,
            "lifecycle": _lifecycle_generation(),
        }
        trace_store = getattr(state, "trace_store", None)
        if trace_store is None:
            trace_store_path = getattr(state, "trace_store_path", None)
            if trace_store_path is not None:
                try:
                    from runtime.memory.diagnostics.trace_store import AgentTraceStore

                    trace_store = AgentTraceStore(trace_store_path)
                except Exception as exc:  # noqa: BLE001
                    out["status"] = "degraded"
                    out["lifecycle"]["traceStore"] = {
                        "ready": False,
                        "error": str(exc)[:240],
                    }
        if trace_store is not None and hasattr(trace_store, "schema_status"):
            try:
                out["lifecycle"]["traceStore"] = trace_store.schema_status()
                if not out["lifecycle"]["traceStore"].get("ready", False):
                    out["status"] = "degraded"
            except Exception as exc:  # noqa: BLE001
                out["status"] = "degraded"
                out["lifecycle"]["traceStore"] = {
                    "ready": False,
                    "error": str(exc)[:240],
                }
        try:
            out["journal_events"] = len(state.journal.read_all())
        except (OSError, ImportError, AttributeError):
            out["journal_events"] = -1
        if agent_registry is not None:
            with contextlib.suppress(Exception):
                out["agents"] = len(agent_registry)
        if channel_manager is not None:
            with contextlib.suppress(Exception):
                out["channels"] = list(channel_manager.channel_ids())
        if group_registry is not None:
            with contextlib.suppress(Exception):
                out["groups"] = len(group_registry)
        return out

    @router.get("/api/storage/status")
    def api_storage_status() -> dict[str, Any]:
        """Liveness of the echo-storage sibling (本地数据库 / File Agent), as the
        co-launch heartbeat last observed it. ``up=false`` means search_documents
        degrades; the heartbeat relaunches it when autostart owns its lifecycle."""
        from runtime.sensing.gateway.storage_supervisor import storage_status

        with contextlib.suppress(Exception):
            return storage_status()
        return {"up": False, "heartbeat": False, "error": "unavailable"}

    @router.get("/api/status")
    def api_status() -> dict[str, Any]:
        from runtime.adapters.instrumentation import OTEL_AVAILABLE
        from runtime.adapters.mcp_client.client import STDIO_AVAILABLE

        def _has(mod: str) -> bool:
            try:
                __import__(mod)
                return True
            except ImportError:
                return False

        return {
            "version": __version__,
            "tagline": "biomimetic self-evolving agent OS",
            "skill_count": len(state.registry),
            "journal_source": (str(state.journal_path) if state.journal_path else "in-memory"),
            "capabilities": {
                "opentelemetry": OTEL_AVAILABLE,
                "mcp": STDIO_AVAILABLE,
                "httpx": _has("httpx"),
                "anthropic": _has("anthropic"),
                "yaml": _has("yaml"),
                "playwright": _has("playwright"),
                "fastapi": _has("fastapi"),
            },
        }

    @router.get("/api/runtime/self-check")
    def api_runtime_self_check(request: Request) -> dict[str, Any]:
        return build_runtime_self_check(
            request=request,
            state=state,
            server_host=server_host,
            server_port=server_port,
            frontend_host=frontend_host,
            frontend_port=frontend_port,
            frontend_proxy_target=frontend_proxy_target,
        )

    @router.post("/api/capabilities/enable", dependencies=[Depends(_capability_auth)])
    def api_capabilities_enable(payload: dict[str, Any]) -> dict[str, Any]:
        """Hot-load a skill group that was excluded at startup.

        Body: ``{"group": "web"}`` — registers the named group into the
        live ``SkillRegistry`` so subsequent tool calls succeed without a
        backend restart. Triggered by the UI's one-click "enable" prompt
        when the model tries to call a config-disabled tool (e.g.
        ``web_search`` under ``enable_web_skills=False``).
        """
        from fastapi import HTTPException

        from runtime.execution.all_skills import WEB_ONLY_GROUPS, register_group

        group = str(payload.get("group") or "").strip()
        if not group:
            raise HTTPException(status_code=400, detail="missing 'group' field")
        if group not in WEB_ONLY_GROUPS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"group '{group}' is not a toggleable web-only group; "
                    f"allowed: {sorted(WEB_ONLY_GROUPS)}"
                ),
            )
        newly = register_group(state.registry, group)
        return {
            "ok": True,
            "group": group,
            "newly_registered": newly,
            "skill_count": len(state.registry),
        }

    return router


def build_runtime_self_check(
    *,
    request: Request | None,
    state: Any,
    server_host: str | None = None,
    server_port: int | None = None,
    frontend_host: str | None = None,
    frontend_port: int | None = None,
    frontend_proxy_target: str | None = None,
) -> dict[str, Any]:
    root = project_root(Path(__file__))
    paths = app_paths()
    pyproject_version = _project_version(root)
    frontend_version = _frontend_version(root)
    request_url = str(getattr(request, "url", "") or "")
    request_host = ""
    request_port: int | None = None
    request_scheme = "http"
    if request is not None:
        request_scheme = str(getattr(getattr(request, "url", None), "scheme", "") or "http")
        request_host = str(getattr(getattr(request, "url", None), "hostname", "") or "")
        request_port = getattr(getattr(request, "url", None), "port", None)
    env_port = _coerce_port(
        os.environ.get("ECHO_BACKEND_PORT")
        or os.environ.get("GATEWAY_PORT")
        or os.environ.get("PORT")
    )
    observed_port = request_port or _coerce_port(server_port) or env_port or 8000
    observed_host = _clean_host(server_host or request_host or "127.0.0.1")
    canonical_host = _canonical_backend_host(observed_host)
    canonical_base_url = f"{request_scheme}://{canonical_host}:{observed_port}"
    request_origin_base_url = (
        f"{request_scheme}://{request_host}:{observed_port}" if request_host else canonical_base_url
    )
    frontend = _frontend_runtime_info(
        request=request,
        request_scheme=request_scheme,
        backend_canonical_base_url=canonical_base_url,
        frontend_host=frontend_host,
        frontend_port=frontend_port,
        frontend_proxy_target=frontend_proxy_target,
    )
    version_sources = {
        "runtime": __version__,
        "pyproject": pyproject_version,
        "frontend_package": frontend_version,
    }
    drift = {
        "runtime_matches_pyproject": __version__ == pyproject_version,
        "frontend_matches_runtime": (frontend_version in {"", __version__}),
        "version_sources": version_sources,
    }
    aliases = _loopback_aliases(observed_host, observed_port, request_scheme)
    process = _process_info()
    api_surface = _api_surface_info(request)
    webui = _webui_static_info(root)
    model_compat = _model_compat_info()
    orchestration = _orchestration_surface_info(request)
    run_evidence = _run_evidence_surface_info(request)
    automation = _automation_surface_info(request)
    checks = [
        {
            "id": "runtime_version",
            "severity": "error",
            "passed": drift["runtime_matches_pyproject"],
            "detail": f"runtime={__version__} pyproject={pyproject_version}",
        },
        {
            "id": "frontend_version",
            "severity": "error",
            "passed": drift["frontend_matches_runtime"],
            "detail": f"frontend={frontend_version or 'missing'} runtime={__version__}",
        },
        {
            "id": "loopback_aliases",
            "severity": "error",
            "passed": bool(aliases["same_loopback_family"]),
            "detail": (
                "localhost and 127.0.0.1 are treated as equivalent local aliases"
                if aliases["same_loopback_family"]
                else "request host is not a recognized loopback alias"
            ),
        },
        {
            "id": "backend_base_url",
            "severity": "error",
            "passed": bool(canonical_base_url),
            "detail": canonical_base_url,
        },
        {
            "id": "frontend_origin",
            "severity": "error",
            "passed": bool(frontend["origin_normalized"]),
            "detail": (
                f"origin={frontend['observed_origin'] or 'missing'} "
                f"canonical={frontend['canonical_origin']}"
            ),
        },
        {
            "id": "vite_proxy_target",
            "severity": "error",
            "passed": bool(frontend["proxy_targets_backend"]),
            "detail": (f"proxy_target={frontend['proxy_target']} backend={canonical_base_url}"),
        },
        {
            "id": "api_surface",
            "severity": "error",
            "passed": bool(api_surface["required_routes_present"]),
            "detail": (
                "missing=" + ",".join(api_surface["missing_required_routes"])
                if api_surface["missing_required_routes"]
                else f"routes={api_surface['route_count']}"
            ),
        },
        {
            "id": "journal_path",
            "severity": "error",
            "passed": bool(_journal_source_usable(state)),
            "detail": _journal_source_detail(state),
        },
        {
            "id": "webui_dist",
            "severity": "warn",
            "passed": bool(
                not webui["env_dist_invalid"]
                and (webui["available"] or webui["dev_fallback_expected"])
            ),
            "detail": webui["detail"],
        },
        {
            "id": "openai_compat_profiles",
            "severity": "error",
            "passed": bool(model_compat["required_profiles_present"]),
            "detail": (
                f"profiles={model_compat['profile_count']} "
                f"missing={','.join(model_compat['missing_required_profile_ids']) or 'none'}"
            ),
        },
        {
            "id": "orchestration_surface",
            "severity": "error",
            "passed": bool(orchestration["ready"]),
            "detail": (
                f"routes={orchestration['route_count']} "
                f"missing={','.join(orchestration['missing_required_routes']) or 'none'} "
                f"models={len(orchestration['model_contracts'])}"
            ),
        },
        {
            "id": "run_evidence_surface",
            "severity": "error",
            "passed": bool(run_evidence["ready"]),
            "detail": (
                f"routes={run_evidence['route_count']} "
                f"missing={','.join(run_evidence['missing_required_routes']) or 'none'} "
                f"contracts={len(run_evidence['method_contracts'])}"
            ),
        },
        {
            "id": "automation_surface",
            "severity": "error",
            "passed": bool(automation["ready"]),
            "detail": (
                f"routes={automation['route_count']} "
                f"missing={','.join(automation['missing_required_routes']) or 'none'} "
                f"contracts={len(automation['method_contracts'])}"
            ),
        },
    ]
    ready = all(bool(row["passed"]) or row.get("severity") == "warn" for row in checks)
    warning_count = sum(
        1 for row in checks if row.get("severity") == "warn" and not bool(row["passed"])
    )
    return {
        "schema": "echo.runtime_self_check.v1",
        "ready": ready,
        "status": "ok" if ready and warning_count == 0 else "degraded",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "version": __version__,
        "version_drift": drift,
        "process": process,
        "backend": {
            "canonical_base_url": canonical_base_url,
            "request_origin_base_url": request_origin_base_url,
            "request_url": request_url,
            "host": observed_host,
            "canonical_host": canonical_host,
            "port": observed_port,
            "env_port": env_port,
            "server_host": server_host or "",
            "server_port": server_port,
        },
        "frontend": frontend,
        "webui": webui,
        "model_compat": model_compat,
        "orchestration": orchestration,
        "run_evidence": run_evidence,
        "automation": automation,
        "api_surface": api_surface,
        "loopback_aliases": aliases,
        "paths": {
            "project_root": str(root),
            "runtime_root": str(paths.root),
            "data_dir": str(paths.data_dir),
            "echo_home_env": os.environ.get("ECHO_HOME") or "",
            "echo_data_dir_env": os.environ.get("ECHO_DATA_DIR") or "",
            "journal_source": (
                str(state.journal_path) if getattr(state, "journal_path", None) else "in-memory"
            ),
        },
        "checks": checks,
        "next_actions": [
            str(row["detail"])
            for row in checks
            if not bool(row["passed"]) and row.get("severity") != "warn"
        ],
        "warnings": [
            str(row["detail"])
            for row in checks
            if not bool(row["passed"]) and row.get("severity") == "warn"
        ],
    }
