from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from runtime.platform.plugins.codex_discovery import (  # re-exported
    _string,
    discover_codex_plugins,
    is_sensitive_plugin_asset_path,
    public_plugin_asset_paths,
)
from runtime.platform.plugins.plugin_lifecycle import (
    install_local_plugin,
    plugin_lifecycle_history,
    rollback_plugin_transaction,
)
from runtime.platform.plugins.plugin_registry import (
    discover_plugin_registry_updates,
    install_registry_plugin,
)
from runtime.platform.plugins.publisher_provenance import (
    resolve_publisher_trust_store_path,
)
from runtime.platform.plugins.publisher_trust import (
    inspect_publisher_trust_store,
    revoke_publisher_key,
    rotate_publisher_key,
)
from runtime.platform.process.paths import app_paths
from runtime.safety.evolution.governance_audit import append_governance_audit_event
from runtime.safety.evolution.plugin_migration_readiness import (
    compute_plugin_migration_readiness,
)
from runtime.safety.evolution.policy_review_rules import (
    build_plugin_permission_rule_drafts,
    install_policy_review_rule_draft,
    verify_policy_review_rule_draft,
)


def is_public_plugin_asset_request(
    method: str,
    path: str,
    *,
    plugins: list[dict[str, Any]],
) -> bool:
    if method.upper() not in {"GET", "HEAD"}:
        return False
    parts = path.split("/", 5)
    if not (
        len(parts) == 6
        and parts[0] == ""
        and parts[1] == "api"
        and parts[2] == "plugins"
        and bool(parts[3])
        and parts[4] == "assets"
        and bool(parts[5])
    ):
        return False
    plugin_id, asset_path = parts[3], parts[5]
    requested = Path(asset_path)
    if requested.is_absolute() or ".." in requested.parts:
        return False
    for plugin in plugins:
        if str(plugin.get("id") or "") != plugin_id:
            continue
        plugin_dir = Path(_string(plugin.get("path"))).resolve()
        manifest = _read_json(plugin_dir / ".codex-plugin" / "plugin.json") or {}
        return requested.as_posix() in public_plugin_asset_paths(plugin_dir, manifest)
    return False


def create_plugins_router(
    *,
    plugin_roots: list[Path] | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    approval_policy_path: Path | None = None,
    promotion_audit_path: Path | None = None,
    publisher_trust_store_path: Path | None = None,
    plugin_registry_path: Path | None = None,
) -> APIRouter:
    def _discover() -> list[dict[str, Any]]:
        return discover_codex_plugins(
            plugin_roots,
            publisher_trust_store_path=publisher_trust_store_path,
        )

    def _publisher_store_path() -> Path:
        resolved = resolve_publisher_trust_store_path(publisher_trust_store_path)
        if resolved is None:
            raise HTTPException(500, "publisher trust store path is unavailable")
        return resolved

    def _writable_plugin_root() -> Path:
        if plugin_roots:
            return Path(plugin_roots[0])
        from runtime.platform.process.paths import app_paths

        return app_paths().codex_plugins_path

    def _registry_path() -> Path:
        return plugin_registry_path or (_writable_plugin_root().parent / "registry.json")

    def _auth_dep(request: Request) -> None:
        path = str(getattr(getattr(request, "url", None), "path", "") or "")
        if is_public_plugin_asset_request(request.method, path, plugins=_discover()):
            return

        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
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

    router = APIRouter(tags=["plugins"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/plugins")
    def _plugins() -> list[dict[str, Any]]:
        return _discover()

    @router.get("/api/plugins/capabilities")
    def _plugin_caps(type: str | None = None) -> list[dict[str, Any]]:
        caps: list[dict[str, Any]] = []
        for plugin in _discover():
            for cap in plugin["capabilities"]:
                if type is None or cap.get("type") == type:
                    caps.append(cap)
        return caps

    @router.get("/api/plugins/smoke-summary")
    def _plugin_smoke_summary() -> dict[str, Any]:
        plugins = _discover()
        migration_readiness = compute_plugin_migration_readiness(plugins=plugins)
        failed: list[dict[str, Any]] = []
        review_required: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        publisher_provenance: list[dict[str, Any]] = []
        permission_resolutions: list[dict[str, Any]] = []
        permission_rule_drafts = build_plugin_permission_rule_drafts(
            plugins=plugins,
            limit=500,
        )
        surface_totals = {
            "capabilities": 0,
            "skills": 0,
            "apps": 0,
            "mcp": 0,
            "commands": 0,
        }
        for plugin in plugins:
            smoke = plugin.get("smoke")
            if not isinstance(smoke, dict):
                failed.append(
                    {
                        "plugin_id": plugin.get("id"),
                        "plugin_name": plugin.get("name"),
                        "issues": ["plugin smoke metadata missing"],
                    }
                )
                continue
            raw_surfaces = smoke.get("surfaces")
            surfaces = raw_surfaces if isinstance(raw_surfaces, dict) else {}
            for surface in surface_totals:
                if surfaces.get(surface):
                    surface_totals[surface] += 1
            raw_trust = smoke.get("trust")
            trust = raw_trust if isinstance(raw_trust, dict) else {}
            provenance = (
                smoke.get("publisher_provenance")
                if isinstance(smoke.get("publisher_provenance"), dict)
                else {}
            )
            if provenance:
                publisher_provenance.append(
                    {
                        "plugin_id": plugin.get("id"),
                        "plugin_name": plugin.get("name"),
                        **provenance,
                    }
                )
            permission_resolution = (
                smoke.get("permission_resolution")
                if isinstance(smoke.get("permission_resolution"), dict)
                else {}
            )
            if permission_resolution:
                permission_resolutions.append(
                    {
                        "plugin_id": plugin.get("id"),
                        "plugin_name": plugin.get("name"),
                        **permission_resolution,
                    }
                )
            if not smoke.get("ok"):
                failed.append(
                    {
                        "plugin_id": plugin.get("id"),
                        "plugin_name": plugin.get("name"),
                        "issues": smoke.get("issues")
                        if isinstance(smoke.get("issues"), list)
                        else [],
                    }
                )
            if str(trust.get("level") or "").endswith("review_required"):
                review_required.append(
                    {
                        "plugin_id": plugin.get("id"),
                        "plugin_name": plugin.get("name"),
                        "reason": trust.get("reason") or "plugin requires local review",
                    }
                )
            plugin_warnings = smoke.get("warnings")
            if isinstance(plugin_warnings, list) and plugin_warnings:
                warnings.append(
                    {
                        "plugin_id": plugin.get("id"),
                        "plugin_name": plugin.get("name"),
                        "warnings": plugin_warnings,
                    }
                )
        compatibility = _compatibility_summary(
            total=len(plugins),
            failed_count=len(failed),
            review_required_count=len(review_required),
            warning_count=sum(len(item["warnings"]) for item in warnings),
            surface_totals=surface_totals,
        )
        return {
            "schema": "echo.codex_plugin_smoke_summary.v1",
            "total": len(plugins),
            "ok_count": len(plugins) - len(failed),
            "failed_count": len(failed),
            "review_required_count": len(review_required),
            "warning_count": sum(len(item["warnings"]) for item in warnings),
            "publisher_verified_count": sum(
                1 for item in publisher_provenance if item.get("verified") is True
            ),
            "unsigned_count": sum(
                1 for item in publisher_provenance if item.get("status") == "unsigned"
            ),
            "invalid_signature_count": sum(
                1
                for item in publisher_provenance
                if item.get("present") is True and item.get("verified") is not True
            ),
            "failed": failed,
            "review_required": review_required,
            "warnings": warnings,
            "publisher_provenance": publisher_provenance,
            "permission_resolutions": permission_resolutions,
            "permission_rule_drafts": {
                "schema": permission_rule_drafts["schema"],
                "total": permission_rule_drafts["total"],
                "verified": sum(
                    1
                    for draft in permission_rule_drafts.get("drafts") or []
                    if verify_policy_review_rule_draft(draft).get("ok") is True
                ),
            },
            "compatibility": compatibility,
            "migration_readiness": {
                "schema": migration_readiness["schema"],
                "score": migration_readiness["score"],
                "ready": migration_readiness["ready"],
                "ready_count": migration_readiness["ready_count"],
                "total": migration_readiness["total"],
                "blocked_count": migration_readiness["blocked_count"],
                "review_required_count": migration_readiness["review_required_count"],
            },
        }

    @router.get("/api/plugins/migration-readiness")
    def _plugin_migration_readiness() -> dict[str, Any]:
        return compute_plugin_migration_readiness(
            plugins=_discover(),
        )

    @router.get("/api/plugins/lifecycle/history")
    def _plugin_lifecycle_history(limit: int = 100) -> dict[str, Any]:
        return plugin_lifecycle_history(
            plugin_root=_writable_plugin_root(),
            limit=limit,
        )

    @router.get("/api/plugins/registry/updates")
    def _plugin_registry_updates() -> dict[str, Any]:
        try:
            return discover_plugin_registry_updates(
                registry_path=_registry_path(),
                plugin_root=_writable_plugin_root(),
                publisher_trust_store_path=publisher_trust_store_path,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post(
        "/api/plugins/registry/install",
        dependencies=[Depends(_admin_dep)],
    )
    def _plugin_registry_install(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload or {}
        try:
            result = install_registry_plugin(
                str(body.get("plugin_id") or ""),
                registry_path=_registry_path(),
                plugin_root=_writable_plugin_root(),
                publisher_trust_store_path=publisher_trust_store_path,
                confirm_install=body.get("confirm_install") is True,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        append_governance_audit_event(
            event_type="plugin_registry_install",
            target=str(result["plugin_id"]),
            status=str(result["status"]),
            artifact=result,
            decision_context={
                "schema": "echo.plugin_registry_install_context.v1",
                "actor": _actor_from_request(request),
                "source": "plugins_router",
            },
            audit_path=promotion_audit_path or app_paths().promotion_audit_path,
        )
        return result

    @router.post(
        "/api/plugins/lifecycle/install",
        dependencies=[Depends(_admin_dep)],
    )
    def _plugin_lifecycle_install(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload or {}
        try:
            result = install_local_plugin(
                str(body.get("source_path") or ""),
                plugin_root=_writable_plugin_root(),
                publisher_trust_store_path=publisher_trust_store_path,
                confirm_install=body.get("confirm_install") is True,
                allow_downgrade=body.get("allow_downgrade") is True,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        append_governance_audit_event(
            event_type="plugin_lifecycle_install",
            target=str(result["plugin_id"]),
            status=str(result["status"]),
            artifact=result,
            decision_context={
                "schema": "echo.plugin_lifecycle_install_context.v1",
                "actor": _actor_from_request(request),
                "source": "plugins_router",
            },
            audit_path=promotion_audit_path or app_paths().promotion_audit_path,
        )
        return result

    @router.post(
        "/api/plugins/lifecycle/rollback",
        dependencies=[Depends(_admin_dep)],
    )
    def _plugin_lifecycle_rollback(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload or {}
        try:
            result = rollback_plugin_transaction(
                str(body.get("transaction_id") or ""),
                plugin_root=_writable_plugin_root(),
                confirm_rollback=body.get("confirm_rollback") is True,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        append_governance_audit_event(
            event_type="plugin_lifecycle_rollback",
            target=str(result["plugin_id"]),
            status=str(result["status"]),
            artifact=result,
            decision_context={
                "schema": "echo.plugin_lifecycle_rollback_context.v1",
                "actor": _actor_from_request(request),
                "source": "plugins_router",
            },
            audit_path=promotion_audit_path or app_paths().promotion_audit_path,
        )
        return result

    @router.get("/api/plugins/publisher-trust")
    def _plugin_publisher_trust() -> dict[str, Any]:
        try:
            return inspect_publisher_trust_store(_publisher_store_path())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post(
        "/api/plugins/publisher-trust/rotate",
        dependencies=[Depends(_admin_dep)],
    )
    def _plugin_publisher_key_rotate(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload or {}
        if body.get("confirm_rotation") is not True:
            raise HTTPException(400, "confirm_rotation=true is required")
        try:
            result = rotate_publisher_key(
                _publisher_store_path(),
                publisher_id=str(body.get("publisher_id") or ""),
                new_key_id=str(body.get("new_key_id") or ""),
                new_public_key=str(body.get("new_public_key") or ""),
                previous_key_id=str(body.get("previous_key_id") or "") or None,
                reason=str(body.get("reason") or "scheduled rotation"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        append_governance_audit_event(
            event_type="plugin_publisher_key_rotation",
            target=str(result["publisher_id"]),
            status="rotated",
            artifact={key: value for key, value in result.items() if key != "trust"},
            decision_context={
                "schema": "echo.plugin_publisher_key_rotation_context.v1",
                "actor": _actor_from_request(request),
                "source": "plugins_router",
            },
            audit_path=promotion_audit_path or app_paths().promotion_audit_path,
        )
        return result

    @router.post(
        "/api/plugins/publisher-trust/revoke",
        dependencies=[Depends(_admin_dep)],
    )
    def _plugin_publisher_key_revoke(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload or {}
        if body.get("confirm_revocation") is not True:
            raise HTTPException(400, "confirm_revocation=true is required")
        try:
            result = revoke_publisher_key(
                _publisher_store_path(),
                publisher_id=str(body.get("publisher_id") or ""),
                key_id=str(body.get("key_id") or ""),
                reason=str(body.get("reason") or ""),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        append_governance_audit_event(
            event_type="plugin_publisher_key_revocation",
            target=str(result["publisher_id"]),
            status="revoked",
            artifact={key: value for key, value in result.items() if key != "trust"},
            decision_context={
                "schema": "echo.plugin_publisher_key_revocation_context.v1",
                "actor": _actor_from_request(request),
                "source": "plugins_router",
            },
            audit_path=promotion_audit_path or app_paths().promotion_audit_path,
        )
        return result

    @router.get("/api/plugins/permission-rule-drafts")
    def _plugin_permission_rule_drafts() -> dict[str, Any]:
        report = build_plugin_permission_rule_drafts(
            plugins=_discover(),
            limit=500,
        )
        report["verified"] = sum(
            1
            for draft in report.get("drafts") or []
            if verify_policy_review_rule_draft(draft).get("ok") is True
        )
        return report

    @router.post(
        "/api/plugins/permission-rule-drafts/install",
        dependencies=[Depends(_admin_dep)],
    )
    def _plugin_permission_rule_draft_install(
        request: Request,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload or {}
        draft_id = str(body.get("draft_id") or "").strip()
        if not draft_id:
            raise HTTPException(400, "draft_id is required")
        report = build_plugin_permission_rule_drafts(
            plugins=_discover(),
            limit=int(body.get("limit") or 500),
        )
        draft = next(
            (
                item
                for item in report.get("drafts") or []
                if isinstance(item, dict) and str(item.get("draft_id") or "") == draft_id
            ),
            None,
        )
        if draft is None:
            raise HTTPException(404, "plugin permission rule draft not found")
        try:
            result = install_policy_review_rule_draft(
                draft,
                policy_path=approval_policy_path or app_paths().permissions_path,
                confirm_install=body.get("confirm_install") is True,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        append_governance_audit_event(
            event_type="plugin_permission_rule_install",
            target="approval_policy",
            status="installed",
            artifact=result,
            decision_context={
                "schema": "echo.plugin_permission_rule_install_context.v1",
                "actor": _actor_from_request(request),
                "draft_id": draft_id,
                "source": "plugins_router",
            },
            audit_path=promotion_audit_path or app_paths().promotion_audit_path,
        )
        return result

    @router.get("/api/plugins/{plugin_id}/smoke")
    def _plugin_smoke(plugin_id: str) -> dict[str, Any]:
        for plugin in _discover():
            if plugin["id"] == plugin_id:
                smoke = plugin.get("smoke")
                if isinstance(smoke, dict):
                    return smoke
                raise HTTPException(status_code=404, detail="plugin smoke not found")
        raise HTTPException(status_code=404, detail="plugin not found")

    @router.get("/api/plugins/{plugin_id}/runtime")
    def _plugin_runtime(plugin_id: str) -> dict[str, Any]:
        for plugin in _discover():
            if plugin["id"] != plugin_id:
                continue
            plugin_dir = Path(_string(plugin.get("path"))).resolve()
            manifest = _read_json(plugin_dir / ".codex-plugin" / "plugin.json") or {}
            runtime = _plugin_runtime_profile(plugin_dir, manifest)
            runtime["plugin_id"] = plugin["id"]
            runtime["plugin_name"] = plugin["name"]
            return runtime
        raise HTTPException(status_code=404, detail="plugin not found")

    @router.get("/api/plugins/{plugin_id}/assets/{asset_path:path}")
    def _plugin_asset(plugin_id: str, asset_path: str) -> FileResponse:
        for plugin in _discover():
            if plugin["id"] != plugin_id:
                continue
            plugin_dir = Path(_string(plugin.get("path"))).resolve()
            requested = Path(asset_path)
            if requested.is_absolute() or ".." in requested.parts:
                raise HTTPException(status_code=404, detail="asset not found")
            if is_sensitive_plugin_asset_path(requested):
                raise HTTPException(status_code=404, detail="asset not found")
            manifest = _read_json(plugin_dir / ".codex-plugin" / "plugin.json") or {}
            public_paths = public_plugin_asset_paths(plugin_dir, manifest)
            is_public = requested.as_posix() in public_paths
            is_private_static_asset = bool(requested.parts) and (
                requested.parts[0].casefold() == "assets"
            )
            if not is_public and not is_private_static_asset:
                raise HTTPException(status_code=404, detail="asset not found")
            candidate = (plugin_dir / requested).resolve()
            try:
                candidate.relative_to(plugin_dir)
            except ValueError:
                raise HTTPException(status_code=404, detail="asset not found") from None
            if not candidate.is_file():
                raise HTTPException(status_code=404, detail="asset not found")
            return FileResponse(
                candidate,
                headers={
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                    "Cross-Origin-Resource-Policy": "same-origin",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        raise HTTPException(status_code=404, detail="plugin not found")

    @router.get("/api/plugins/{plugin_id}")
    def _plugin_get(plugin_id: str) -> dict[str, Any]:
        for plugin in _discover():
            if plugin["id"] == plugin_id:
                return plugin
        raise HTTPException(status_code=404, detail="plugin not found")

    return router


def _actor_from_request(request: Request) -> str:
    return str(getattr(getattr(request, "state", None), "actor_id", "") or "local_operator")


def _compatibility_summary(
    *,
    total: int,
    failed_count: int,
    review_required_count: int,
    warning_count: int,
    surface_totals: dict[str, int],
) -> dict[str, Any]:
    requirements = [
        {
            "id": "plugins_discovered",
            "passed": total > 0,
            "detail": f"{total} plugin(s) discovered",
        },
        {
            "id": "no_smoke_failures",
            "passed": failed_count == 0,
            "detail": f"{failed_count} plugin smoke failure(s)",
        },
        {
            "id": "capability_surface_present",
            "passed": any(count > 0 for count in surface_totals.values()),
            "detail": "at least one plugin exposes a usable surface",
        },
        {
            "id": "review_state_visible",
            "passed": review_required_count > 0 or warning_count > 0 or failed_count == 0,
            "detail": (
                f"{review_required_count} review-required plugin(s), {warning_count} warning(s)"
            ),
        },
    ]
    passed = sum(1 for item in requirements if item["passed"])
    if failed_count > 0 or total <= 0:
        verdict = "fail"
    elif warning_count > 0 or review_required_count > 0:
        verdict = "review"
    else:
        verdict = "pass"
    return {
        "schema": "echo.codex_plugin_compatibility.v1",
        "verdict": verdict,
        "passed": passed,
        "total": len(requirements),
        "surface_totals": surface_totals,
        "requirements": requirements,
        "next_actions": _compatibility_next_actions(
            verdict=verdict,
            total=total,
            failed_count=failed_count,
            review_required_count=review_required_count,
            warning_count=warning_count,
            has_surface=any(count > 0 for count in surface_totals.values()),
        ),
    }


def _compatibility_next_actions(
    *,
    verdict: str,
    total: int,
    failed_count: int,
    review_required_count: int,
    warning_count: int,
    has_surface: bool,
) -> list[str]:
    if verdict == "pass":
        return []
    actions: list[str] = []
    if total <= 0:
        actions.append("Install or enable at least one local Codex-compatible plugin.")
    if failed_count > 0:
        actions.append("Fix plugins with failed local smoke checks.")
    if not has_surface:
        actions.append("Expose at least one plugin capability, skill, app, MCP server, or command.")
    if review_required_count > 0:
        actions.append("Resolve inferred plugin permission defaults or mark accepted risk.")
    if warning_count > 0:
        actions.append("Resolve plugin warnings or mark accepted risk.")
    return actions


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _plugin_runtime_profile(plugin_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    skills = _plugin_skill_entries(plugin_dir)
    apps = _plugin_app_entries(plugin_dir, manifest)
    mcp_servers = _plugin_mcp_entries(plugin_dir, manifest)
    commands = _plugin_command_entries(plugin_dir)
    capabilities = _plugin_capability_entries(manifest)
    return {
        "schema": "echo.codex_plugin_runtime.v1",
        "surfaces": {
            "capabilities": len(capabilities),
            "skills": len(skills),
            "apps": len(apps),
            "mcp_servers": len(mcp_servers),
            "commands": len(commands),
        },
        "capabilities": capabilities,
        "skills": skills,
        "apps": apps,
        "mcp_servers": mcp_servers,
        "commands": commands,
        "call_order": _plugin_call_order(
            has_mcp=bool(mcp_servers),
            has_apps=bool(apps),
            has_skills=bool(skills),
            has_commands=bool(commands),
        ),
    }


def _plugin_capability_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw_interface = manifest.get("interface")
    interface = raw_interface if isinstance(raw_interface, dict) else {}
    raw = interface.get("capabilities")
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            entries.append(
                {
                    "name": _string(item.get("name") or item.get("type"), "capability"),
                    "type": _string(item.get("type"), "codex"),
                    "description": _string(item.get("description")),
                }
            )
        elif isinstance(item, str) and item.strip():
            entries.append({"name": item.strip(), "type": "codex", "description": ""})
    return entries


def _plugin_skill_entries(plugin_dir: Path) -> list[dict[str, Any]]:
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        skill_dir = skill_path.parent
        description = _first_markdown_paragraph(skill_path)
        entries.append(
            {
                "id": skill_dir.name,
                "name": skill_dir.name.replace("-", " ").title(),
                "path": str(skill_path),
                "description": description,
                "scope": "plugin",
            }
        )
    return entries


def _plugin_app_entries(plugin_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    raw_apps = manifest.get("apps")
    if isinstance(raw_apps, list):
        for item in raw_apps:
            if isinstance(item, dict):
                entries.append(
                    {
                        "id": _string(item.get("id") or item.get("name"), "app"),
                        "name": _string(item.get("name") or item.get("title"), "App"),
                        "description": _string(item.get("description")),
                        "source": "manifest",
                    }
                )
            elif isinstance(item, str) and item.strip():
                entries.append(
                    {
                        "id": item.strip(),
                        "name": item.strip(),
                        "description": "",
                        "source": "manifest",
                    }
                )
    for name in (".app.json", "echo-app.jsonc", "app.json"):
        path = plugin_dir / name
        if path.is_file():
            app = _read_json(path) or {}
            entries.append(
                {
                    "id": _string(app.get("id") or app.get("name"), path.stem),
                    "name": _string(app.get("name") or app.get("title"), path.stem),
                    "description": _string(app.get("description")),
                    "source": name,
                }
            )
    return _dedupe_by_id(entries)


def _plugin_mcp_entries(plugin_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw_servers: dict[str, Any] = {}
    manifest_servers = manifest.get("mcpServers")
    if isinstance(manifest_servers, dict):
        raw_servers.update(manifest_servers)
    mcp_manifest = _read_json(plugin_dir / ".mcp.json") or {}
    file_servers = mcp_manifest.get("mcpServers")
    if isinstance(file_servers, dict):
        raw_servers.update(file_servers)

    entries: list[dict[str, Any]] = []
    for name, raw_entry in sorted(raw_servers.items()):
        if not isinstance(raw_entry, dict):
            continue
        cwd = _string(raw_entry.get("cwd"))
        resolved_cwd = (
            str((plugin_dir / cwd).resolve()) if cwd and not Path(cwd).is_absolute() else cwd
        )
        raw_env = raw_entry.get("env")
        env = raw_env if isinstance(raw_env, dict) else {}
        entries.append(
            {
                "name": _string(name, "mcp"),
                "type": _string(
                    raw_entry.get("type"), "stdio" if raw_entry.get("command") else "http"
                ),
                "title": _string(raw_entry.get("title")),
                "description": _string(raw_entry.get("description") or raw_entry.get("note")),
                "command": _string(raw_entry.get("command")),
                "args": raw_entry.get("args") if isinstance(raw_entry.get("args"), list) else [],
                "cwd": resolved_cwd or str(plugin_dir),
                "url": _string(raw_entry.get("url")),
                "env_keys": sorted(str(key) for key in env),
                "enabled": False,
                "scope": "plugin",
            }
        )
    return entries


def _plugin_command_entries(plugin_dir: Path) -> list[dict[str, Any]]:
    commands_dir = plugin_dir / "commands"
    if not commands_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in commands_dir.iterdir() if item.is_file()):
        entries.append(
            {
                "id": path.stem,
                "name": path.stem.replace("-", " ").title(),
                "path": str(path),
                "executable": path.stat().st_mode & 0o111 != 0,
            }
        )
    return entries


def _first_markdown_paragraph(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    paragraph: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if paragraph:
                break
            continue
        if line.startswith("- ") or line.startswith("* "):
            continue
        paragraph.append(line)
        if len(" ".join(paragraph)) > 240:
            break
    return " ".join(paragraph)[:280]


def _dedupe_by_id(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        out[_string(entry.get("id") or entry.get("name"), "item")] = entry
    return list(out.values())


def _plugin_call_order(
    *,
    has_mcp: bool,
    has_apps: bool,
    has_skills: bool,
    has_commands: bool,
) -> list[str]:
    order: list[str] = []
    if has_mcp:
        order.append("mcp_tool")
    if has_apps:
        order.append("app_entry")
    if has_commands:
        order.append("command")
    if has_skills:
        order.append("plugin_skill_context")
    return order


__all__ = ["create_plugins_router", "discover_codex_plugins"]
