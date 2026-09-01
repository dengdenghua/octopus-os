"""
Meta router · feedback / skills / auth-provider listing.

Extracted from the monolithic ``runtime/platform/ui/app.py`` in the
app.py-split campaign. Houses the "reflective" endpoints that don't
belong to any one feature — feedback on replies, the registered
skill catalog the UI browses, and the list of configured login
methods.

Endpoints
---------

    POST /api/feedback          · record 👍/👎 on a reply
    GET  /api/feedback          · admin read-back with limit + filter
    GET  /api/skills            · registered skill catalog
    GET  /api/auth/providers    · login methods available to the UI

Design notes
------------

* **Stateless factory injection.** All per-app state (skill registry,
  feedback-log path, auth configs, identity store) flows in via
  ``create_meta_router`` kwargs. The function itself owns nothing
  beyond the local closures, matching the pattern in
  ``config_router.py``.
* **Actor resolution stays lazy.** The feedback handler wants an
  optional actor tag. Rather than bind the auth stack at factory
  time, we import ``_resolve_actor`` inside the handler — keeps this
  module light and lets the big openai_gateway module import without
  a circular.

Structural split
----------------

The support code was extracted into sibling ``_``-prefixed submodules
in the god-file split campaign; this module keeps the ``create_meta_router``
closure factory (which must stay here so the ``meta_router._dynamic_plugin_skill_names``
binding the tests monkeypatch stays the one the factory resolves):

* ``_meta_models.py``        · Pydantic response models
* ``_meta_skill_install.py`` · skills/public install + uninstall helpers
* ``_meta_skill_metadata.py``· skill-market classification + catalog parse
* ``_meta_mentions.py``      · @-mention autocomplete builder
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query, Request, Response

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    Response = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi
from runtime.sensing.gateway._meta_mentions import _build_mentions_autocomplete
from runtime.sensing.gateway._meta_models import (
    AuthProvidersResponse,
    CapabilityPermissionsResponse,
    CapabilityPermissionWire,
    FeedbackListResponse,
    FeedbackPostResponse,
    SkillsResponse,
    SlashCommandsResponse,
)
from runtime.sensing.gateway._meta_skill_install import (
    _install_public_skill_dir,
    _require_safe_skill_install_name,
    _uninstall_public_skill_dir,
)
from runtime.sensing.gateway._meta_skill_metadata import (
    SKILL_CATEGORIES,
    _default_skill_library_dir,
    _derive_skill_category,
    _dynamic_plugin_skill_names,
    _is_hidden_skill_catalog_entry,
    _load_file_skill_catalog,
    _permission_group_for_skill,
    _resolve_thread_active_agents,
    _skill_group_for,
    _skill_kind,
    _skill_market_profile,
)

# ═══════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════


def create_meta_router(
    *,
    registry: Any,
    tool_registry: Any = None,
    mobile_skills_root: Path | str | None = None,
    feedback_path: Path | str = "data/feedback.jsonl",
    skill_library_dirs: Sequence[Path | str] | None = None,
    include_default_skill_library: bool = False,
    oct_config: Any = None,
    local_auth_config: Any = None,
    identity_store: Any = None,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    require_auth: bool = False,
) -> Any:
    """Build the FastAPI router.

    ╔════════════════════════════════════════════════════════════════════╗
    ║ meta_router.py · navigation map (closure factory).                 ║
    ║                                                                    ║
    ║   §1 Pydantic models + enums                     (submodule)       ║
    ║   §2 create_meta_router(...) factory                below          ║
    ║       §2.1 feedback endpoints (POST + GET)                          ║
    ║       §2.2 skills browser + enable/disable                          ║
    ║       §2.3 skills install/uninstall                                 ║
    ║       §2.4 user profile (GET + PUT)                                 ║
    ║       §2.5 auth status + usage audit                                ║
    ║       §2.6 architecture docs endpoints                              ║
    ║   §3 helper functions (skill metadata)      → _meta_skill_metadata  ║
    ╚════════════════════════════════════════════════════════════════════╝

    Parameters
    ----------
    registry :
        SkillRegistry — ``/api/skills`` iterates its contents.
    feedback_path :
        JSONL where ``/api/feedback`` appends. Kept as a parameter
        (default preserves the pre-split location) so tests can
        redirect it with ``tmp_path``.
    skill_library_dirs / include_default_skill_library :
        Optional file-backed SKILL.md catalogs to merge into the
        skill browser. These are read-only catalog entries; they do
        not register executable handlers in ``SkillRegistry``.
    oct_config / local_auth_config :
        Optional auth configs. Each is probed by the
        ``auth_providers`` handler for ``enabled`` and the fields
        it surfaces to the UI.
    identity_store / jwt_secret :
        Passed through to ``_resolve_actor`` when tagging feedback
        with the submitting user. Both being ``None`` means
        anonymous feedback still records (tagged ``actor=None``).
    """
    require_fastapi(__name__)

    router = APIRouter(tags=["meta"])
    _feedback_path = Path(feedback_path)
    _skill_library_dirs = [Path(p) for p in (skill_library_dirs or [])]
    if include_default_skill_library:
        default_library = _default_skill_library_dir()
        if default_library not in _skill_library_dirs:
            _skill_library_dirs.append(default_library)

    def _require_admin(request: Request, *, purpose: str) -> None:
        """Require an authenticated admin actor for high-risk operations."""
        try:
            from runtime.sensing.gateway.openai_gateway import _resolve_actor

            actor = _resolve_actor(
                request,
                identity_store,
                True,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                401,
                "auth required",
                headers={"X-Echo-Auth-Expired": "1"},
            ) from exc

        if identity_store is None or not actor:
            raise HTTPException(
                401,
                "auth required",
                headers={"X-Echo-Auth-Expired": "1"},
            )

        auth_header = request.headers.get("Authorization") or ""
        if not auth_header.lower().startswith("bearer "):
            raise HTTPException(
                401,
                "missing Authorization: Bearer <token>",
                headers={"X-Echo-Auth-Expired": "1"},
            )

        token = auth_header[7:].strip()
        identity = None
        if jwt_secret and token.count(".") == 2:
            with suppress(Exception):
                identity = identity_store.verify_jwt(
                    token,
                    secret=jwt_secret,
                    required_issuer=jwt_issuer,
                    required_audience=jwt_audience,
                )
        if identity is None:
            with suppress(Exception):
                identity = identity_store.verify_api_key(token)

        roles = getattr(identity, "roles", ()) or ()
        if "admin" not in {str(r).lower() for r in roles}:
            raise HTTPException(403, f"admin role required to {purpose}")

    # ─── Feedback ───────────────────────────────────────────

    @router.post("/api/feedback", response_model=FeedbackPostResponse)
    def api_feedback(
        body: dict[str, Any],
        request: Request,
    ) -> dict[str, Any]:
        sentiment = str(body.get("sentiment") or "").strip().lower()
        if sentiment not in ("liked", "disliked"):
            raise HTTPException(
                400,
                "sentiment must be 'liked' or 'disliked'",
            )
        actor: str | None = None
        try:
            from runtime.sensing.gateway.openai_gateway import _resolve_actor

            actor = (
                _resolve_actor(  # AUTH-OK: actor-agnostic — optional attribution for feedback log
                    request,
                    identity_store,
                    False,
                    jwt_secret=jwt_secret,
                    jwt_issuer=jwt_issuer,
                    jwt_audience=jwt_audience,
                )
            )
        except Exception as exc:
            import logging as _logging

            _logging.getLogger(__name__).debug("auth resolution failed: %s", exc)

        entry = {
            "ts": time.time(),
            "sentiment": sentiment,
            "message_id": str(body.get("message_id") or "") or None,
            "thread_id": str(body.get("thread_id") or "") or None,
            "agent_id": str(body.get("agent_id") or "") or None,
            "content_preview": str(body.get("content_preview") or "")[:400] or None,
            "reason": str(body.get("reason") or "")[:200] or None,
            "actor": actor,
        }
        try:
            _feedback_path.parent.mkdir(parents=True, exist_ok=True)
            with _feedback_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise HTTPException(
                500,
                f"failed to record feedback: {exc}",
            ) from exc
        return {"ok": True, "recorded": entry}

    @router.get("/api/feedback", response_model=FeedbackListResponse)
    def api_feedback_list(
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
        thread_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Scan recent feedback entries from the end of the JSONL.

        Performance note: full file read per request · fine for the
        admin-dashboard use case (small log, single reader). If
        this ever becomes a hot path, replace with tail-read +
        LRU cache.
        """
        _require_admin(request, purpose="read feedback")
        if not _feedback_path.exists():
            return {"entries": []}
        try:
            lines = _feedback_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {"entries": []}
        out: list[dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Single malformed row shouldn't void the whole list
                # — skip and continue. Consider alerting if this
                # happens in prod (corrupted writer).
                continue
            if thread_id and rec.get("thread_id") != thread_id:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return {"entries": out}

    # ─── Skills ─────────────────────────────────────────────

    @router.get("/api/skills", response_model=SkillsResponse)
    def api_skills() -> dict[str, Any]:
        skills_by_name: dict[str, dict[str, Any]] = {}
        dynamic_plugin_skills = _dynamic_plugin_skill_names()
        seen_sources: set[str] = set()
        try:
            registry_names = list(registry.all_names())
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("api_skills: registry.all_names failed: %s", exc)
            registry_names = []
        for name in registry_names:
            try:
                s = registry.get(name)
                if _is_hidden_skill_catalog_entry(
                    str(s.name),
                    str(s.trusted_source or ""),
                    dynamic_plugin_skills,
                    seen_sources,
                ):
                    continue
                affinity = list(s.affinity)
                permission_group = _permission_group_for_skill(str(s.name))
                is_enabled = registry.is_enabled(s.name)
                group = _skill_group_for(str(s.name))
                kind = _skill_kind(group, str(s.name))
                market_profile = _skill_market_profile(
                    name=str(s.name),
                    description=str(s.description or ""),
                    trusted_source=str(s.trusted_source or ""),
                    group=group,
                    kind=kind,
                )
                skills_by_name[s.name] = {
                    "name": s.name,
                    "description": s.description,
                    "affinity": affinity,
                    "cost_profile": s.cost_profile,
                    "trusted_source": s.trusted_source,
                    "has_tests": s.has_tests,
                    "enabled": is_enabled,
                    "surface": "permission" if permission_group else "skill",
                    "permission_group": permission_group,
                    "category": _derive_skill_category(s.name, affinity),
                    "group": group,
                    "kind": kind,
                    **market_profile,
                }
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("api_skills: skipping registry skill %s: %s", name, exc)
                continue
        for skill in _load_file_skill_catalog(_skill_library_dirs):
            # Runtime-registered skills win because those entries are
            # executable and carry their real trust/test metadata.
            try:
                # Dynamic plugin names hide executable all_skills registry
                # entries so plugin-owned tools do not appear twice. Do not
                # apply that rule to the read-only file catalog: bundled
                # public SKILL.md entries (for example pdf) are the fallback
                # surface when the executable registry entry was hidden.
                if _is_hidden_skill_catalog_entry(
                    str(skill.get("name") or ""),
                    str(skill.get("trusted_source") or ""),
                    set(),
                    seen_sources,
                ):
                    continue
                skills_by_name.setdefault(skill["name"], skill)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("api_skills: skipping file skill %s: %s", skill, exc)
                continue
        skills = sorted(
            skills_by_name.values(),
            key=lambda item: str(item.get("name", "")).lower(),
        )
        return {"skills": skills}

    @router.get("/api/capability-catalog")
    def api_capability_catalog(
        q: str | None = Query(default=None),
        source: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        risk_level: str | None = Query(default=None),
        permission_group: str | None = Query(default=None),
        available_only: bool = Query(default=False),
        limit: int = Query(default=500, ge=1, le=2000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        from runtime.execution.misc.capability_catalog import (
            build_capability_catalog,
            filter_capability_entries,
        )

        catalog = build_capability_catalog(
            registry=registry,
            tool_registry=tool_registry,
            mobile_skills_root=mobile_skills_root,
        )
        filtered = filter_capability_entries(
            catalog["capabilities"],
            q=q,
            source=source,
            kind=kind,
            risk_level=risk_level,
            permission_group=permission_group,
            available_only=available_only,
            limit=limit,
            offset=offset,
        )
        filtered["summary"] = catalog["summary"]
        return filtered

    @router.post("/api/skills/{skill_name}/enable")
    def api_enable_skill(request: Request, skill_name: str) -> dict[str, Any]:
        _require_admin(request, purpose="modify skills")
        try:
            registry.enable(skill_name)
        except KeyError as exc:
            raise HTTPException(404, f"skill not found: {skill_name}") from exc
        return {"ok": True, "name": skill_name, "enabled": True}

    @router.post("/api/skills/{skill_name}/disable")
    def api_disable_skill(request: Request, skill_name: str) -> dict[str, Any]:
        _require_admin(request, purpose="modify skills")
        try:
            registry.disable(skill_name)
        except KeyError as exc:
            raise HTTPException(404, f"skill not found: {skill_name}") from exc
        return {"ok": True, "name": skill_name, "enabled": False}

    # ─── Skills-market enable / disable ────────────────────
    #
    #
    @router.post("/api/skills-market/{skill_id}/enable")
    def api_market_enable(request: Request, skill_id: str) -> dict[str, Any]:
        _require_admin(request, purpose="modify skills")
        if not registry.has(skill_id):
            from runtime.execution.suckers.market_skills import (
                load_single_market_skill,
            )

            loaded = load_single_market_skill(registry, skill_id)
            if not loaded:
                raise HTTPException(
                    404,
                    f"skill not found: {skill_id} (no SKILL.md in all_skills/)",
                )
        try:
            registry.enable(skill_id)
        except KeyError as exc:
            raise HTTPException(404, f"skill not found: {skill_id}") from exc
        return {"ok": True, "skill_id": skill_id, "enabled": True}

    @router.post("/api/skills-market/{skill_id}/disable")
    def api_market_disable(request: Request, skill_id: str) -> dict[str, Any]:
        _require_admin(request, purpose="modify skills")
        try:
            registry.disable(skill_id)
        except KeyError as exc:
            raise HTTPException(404, f"skill not found: {skill_id}") from exc
        return {"ok": True, "skill_id": skill_id, "enabled": False}

    # ─── Skill install / uninstall ─────────────────────────
    @router.post("/api/skills/install")
    async def api_install_skill(request: Request) -> dict[str, Any]:
        import tempfile

        from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

        # Auth: this endpoint mutates skills/public which is auto-loaded
        # as Python code on next boot. It's effectively arbitrary-code
        # installation, so we require BOTH:
        #   1. authenticated caller (require_auth=True regardless of
        #      router config)
        #   2. admin role
        _require_admin(request, purpose="install skills")
        if immutable_prompt_catalog_required():
            raise HTTPException(
                403,
                "URL-based prompt installation is disabled in shared/commercial deployments; "
                "ship prompt changes in a reviewed release artifact",
            )

        try:
            body = await request.json()
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(400, f"body: {exc}") from exc
        url = (body or {}).get("url", "")
        if not isinstance(url, str) or not url.startswith("http"):
            raise HTTPException(400, "url required (https://...)")
        name_override = (body or {}).get("name")

        # SSRF guard + DNS-rebinding-proof fetch. ``check_url`` alone
        # would re-resolve on connect; use safe_httpx_get which pins
        # the resolved IP as the connect target while keeping the
        # original host name in the Host header.
        from runtime.safety.auth.url_guard import check_url, safe_httpx_get

        verdict = check_url(url, allow_private=False)
        if not verdict.allow:
            raise HTTPException(400, f"url rejected: {verdict.reason}")

        # Validate the new name *now* before any network I/O so we
        # fail fast on hostile filenames (path traversal in
        # ``skills/public/<name>``).
        if name_override is not None:
            if not isinstance(name_override, str) or not name_override:
                raise HTTPException(400, "name must be a non-empty string")
            try:
                name_override = _require_safe_skill_install_name(name_override, label="name")
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        # The download + extract + install chain is all blocking
        # (sync httpx, zip extraction, directory copy). Offload it to a
        # worker thread so the event loop isn't frozen for the ~30s
        # network timeout. HTTPException propagates through to_thread.
        def _do_install_blocking() -> dict[str, Any]:
            try:
                import httpx as _httpx
            except ImportError as e:
                raise HTTPException(500, "httpx required for skill install") from e

            # follow_redirects=False on the transport; safe_httpx_get
            # re-validates the next hop through check_url if we ever
            # enable follow_redirects. We keep it off so the network
            # topology of an outbound skill install stays one hop, one
            # check.
            try:
                r = safe_httpx_get(url, timeout=30.0, follow_redirects=False)
                r.raise_for_status()
            except ValueError as exc:
                raise HTTPException(400, f"url rejected: {exc}") from exc
            except (_httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                raise HTTPException(502, f"download failed: {exc}") from exc
            if len(r.content) > 50 * 1024 * 1024:
                raise HTTPException(413, "archive too large (>50MB)")

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                archive = tmp / "skill.zip"
                archive.write_bytes(r.content)
                extract_dir = tmp / "extracted"
                extract_dir.mkdir(parents=True, exist_ok=True)
                # Use the hardened extractor (zip-slip + symlink-component +
                # size caps) instead of ``shutil.unpack_archive`` which has
                # none of those defenses.
                try:
                    from runtime.execution.suckers.hub.installer import (
                        ArchiveSafetyError,
                        safe_extract_zip,
                    )

                    safe_extract_zip(r.content, extract_dir)
                except ArchiveSafetyError as exc:
                    raise HTTPException(400, f"unsafe archive: {exc}") from exc
                except (OSError, ValueError) as exc:
                    raise HTTPException(400, f"unpack failed: {exc}") from exc

                skill_dirs = list(extract_dir.rglob("SKILL.md"))
                if not skill_dirs:
                    raise HTTPException(400, "no SKILL.md found in archive")
                skill_root = skill_dirs[0].parent
                skill_name = name_override or skill_root.name
                try:
                    target = _install_public_skill_dir(skill_root, skill_name)
                    skill_name = target.name
                except FileExistsError as exc:
                    raise HTTPException(409, str(exc)) from exc
                except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
                    raise HTTPException(400, str(exc)) from exc
                except OSError as exc:
                    raise HTTPException(
                        500,
                        f"skill install failed: {type(exc).__name__}: {exc}",
                    ) from exc

            return {"ok": True, "name": skill_name, "path": str(target)}

        return await asyncio.to_thread(_do_install_blocking)

    @router.delete("/api/skills/{skill_name}/uninstall")
    def api_uninstall_skill(request: Request, skill_name: str) -> dict[str, Any]:
        _require_admin(request, purpose="uninstall skills")
        try:
            _uninstall_public_skill_dir(skill_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, f"skill directory not found: {skill_name}") from exc
        except NotADirectoryError as exc:
            raise HTTPException(400, f"not a directory: {skill_name}") from exc
        except OSError as exc:
            raise HTTPException(
                500,
                f"skill uninstall failed: {type(exc).__name__}: {exc}",
            ) from exc
        return {"ok": True, "name": skill_name, "removed": True}

    @router.get(
        "/api/capability-permissions",
        response_model=CapabilityPermissionsResponse,
    )
    def api_capability_permissions() -> dict[str, Any]:
        from runtime.execution.misc.capability_permissions import (
            list_capability_permissions,
        )

        return {
            "permissions": list_capability_permissions(
                registered_skill_names=set(registry.all_names()),
            ),
        }

    @router.put(
        "/api/capability-permissions/{group}",
        response_model=CapabilityPermissionWire,
    )
    def api_capability_permission_update(
        request: Request,
        group: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        _require_admin(request, purpose="modify capability permissions")
        from runtime.execution.misc.capability_permissions import (
            list_capability_permissions,
            set_capability_group_enabled,
        )

        try:
            set_capability_group_enabled(group, bool(body.get("enabled")))
        except KeyError as exc:
            raise HTTPException(404, f"unknown capability group: {group}") from exc
        updated = list_capability_permissions(
            registered_skill_names=set(registry.all_names()),
        )
        return next(item for item in updated if item["id"] == group)

    # ─── Slash commands ─────────────────────────────────────

    @router.get(
        "/api/slash-commands",
        response_model=SlashCommandsResponse,
    )
    def api_slash_commands() -> dict[str, Any]:
        """Return the merged slash-command catalog (global ∪ project).

        Frontend `/` typeahead uses this to show the user what
        commands are available. Body is intentionally NOT sent — it
        can be long and the client doesn't need to render it until
        expansion time (server-side, via the run endpoint).
        """
        import os

        from runtime.execution.slash_commands import load_slash_commands

        project_dir = os.getcwd()
        try:
            cmds = load_slash_commands(project_dir=project_dir)
        except (OSError, KeyError, ValueError):
            cmds = []
        return {"commands": [c.as_dict() for c in cmds]}

    # ─── Auth status ────────────────────────────────────────

    _oct_enabled = bool(oct_config is not None and getattr(oct_config, "enabled", False))
    _local_auth_enabled = bool(
        local_auth_config is not None and getattr(local_auth_config, "enabled", False)
    )
    _any_auth_enabled = _oct_enabled or _local_auth_enabled

    @router.get("/api/auth/status")
    def auth_status() -> dict[str, Any]:
        has_jwt = _oct_enabled or _local_auth_enabled
        return {
            "enabled": _any_auth_enabled,
            "jwt_available": has_jwt,
            "allow_registration": False,
            "exempt_paths": [],
        }

    def auth_me(request: Request) -> dict[str, Any]:
        """Return the authenticated actor from the real identity store.

        This endpoint used to exist only in the optional stub router. With
        production auth enabled the frontend therefore made a guaranteed 404
        request on every reload even though the bearer token was valid.
        """
        if identity_store is None:
            raise HTTPException(
                401,
                "authentication required",
                headers={"X-Echo-Auth-Expired": "1"},
            )

        from runtime.sensing.gateway.openai_gateway import _resolve_actor

        actor = _resolve_actor(
            request,
            identity_store,
            True,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if not actor:
            raise HTTPException(
                401,
                "authentication required",
                headers={"X-Echo-Auth-Expired": "1"},
            )

        identity = identity_store.get(actor) if hasattr(identity_store, "get") else None
        metadata = dict(getattr(identity, "metadata", None) or {})
        roles = [str(role) for role in (getattr(identity, "roles", None) or ())]
        fallback_name = actor.split(":", 1)[-1] if ":" in actor else actor
        username = next(
            (
                str(metadata[key]).strip()
                for key in ("username", "display_name", "email", "mobile")
                if isinstance(metadata.get(key), str) and str(metadata[key]).strip()
            ),
            fallback_name,
        )
        permissions_raw = metadata.get("permissions")
        permissions = (
            [str(value) for value in permissions_raw]
            if isinstance(permissions_raw, (list, tuple))
            else []
        )
        response: dict[str, Any] = {
            "user_id": actor,
            "actor_id": actor,
            "username": username,
            "roles": roles,
            "permissions": permissions,
            "is_active": True,
        }
        for key in ("email", "mobile", "provider"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                response[key] = value.strip()
        return response

    def auth_logout(request: Request, response: Response) -> None:
        """Clear the durable browser session even when its JWT has expired."""

        from runtime.safety.auth.principal import clear_session_cookie

        clear_session_cookie(response, request)
        response.status_code = 204

    # Leave the route to the compatibility stub when auth is disabled
    # (require_auth=False). The stub returns anonymous on bad/missing JWT
    # instead of 401, which is the desired behavior in dev mode. Only
    # register the strict handler when the app is actually enforcing auth —
    # at that point stub_router is disabled, so there's no route conflict.
    if identity_store is not None and require_auth:
        router.add_api_route("/api/auth/me", auth_me, methods=["GET"])
        router.add_api_route(
            "/api/auth/logout",
            auth_logout,
            methods=["POST"],
            status_code=204,
        )

    # ─── Auth providers ─────────────────────────────────────

    @router.get(
        "/api/auth/providers",
        response_model=AuthProvidersResponse,
    )
    def auth_providers() -> dict[str, Any]:
        """Return the list of configured login methods.

        The frontend Login page hides tabs whose id isn't in the
        returned set — so an empty response means "no providers
        configured, don't show a broken form".
        """
        providers: list[dict[str, Any]] = []
        if oct_config is not None and getattr(oct_config, "enabled", False):
            providers.append(
                {
                    "id": "oct",
                    "label": "邮箱登录",
                    "mock_mode": bool(getattr(oct_config, "mock_mode", False)),
                    "endpoint_send": "/api/auth/oct/email/send",
                    "endpoint_verify": "/api/auth/oct/email/login",
                }
            )
        if local_auth_config is not None and getattr(
            local_auth_config,
            "enabled",
            False,
        ):
            pw_required = bool(getattr(local_auth_config, "users", {}))
            providers.append(
                {
                    "id": "local",
                    "label": "本地登录",
                    "allow_any_username": bool(
                        getattr(local_auth_config, "allow_any_username", True),
                    ),
                    "password_required": pw_required,
                    "endpoint": "/api/auth/local/login",
                }
            )
        return {"providers": providers}

    #
    #

    ARCHITECTURE_DOCS: dict[str, str] = {  # noqa: N806
        "readme": "docs/architecture/README.md",
        "core-path": "docs/architecture/core-path.md",
        "high-res-map": "docs/architecture/high-res-map.md",
        "high-res-mermaid": "docs/architecture/high-res-map.mermaid.md",
        "chat-modes": "docs/architecture/chat-modes.md",
        "react-self-evo": "docs/architecture/react-self-evolution.md",
        "organ-tiering": "docs/architecture/organ-tiering.md",
        "module-map": "docs/architecture/module-map.md",
        "organ-cerebrum": "docs/architecture/organs/cerebrum.md",
        "organ-ganglia": "docs/architecture/organs/ganglia.md",
        "organ-beak": "docs/architecture/organs/beak.md",
        "organ-hearts": "docs/architecture/organs/hearts.md",
        "organ-chromatophores": "docs/architecture/organs/chromatophores.md",
    }

    @router.get("/api/architecture/docs")
    def list_architecture_docs() -> dict[str, Any]:
        return {
            "docs": [{"id": k, "path": v} for k, v in ARCHITECTURE_DOCS.items()],
        }

    @router.get("/api/architecture/docs/{doc_id}")
    def read_architecture_doc(doc_id: str) -> dict[str, Any]:
        rel = ARCHITECTURE_DOCS.get(doc_id)
        if rel is None:
            raise HTTPException(404, f"unknown doc id: {doc_id!r}")
        p = Path(rel)
        if not p.exists():
            raise HTTPException(404, f"file missing on disk: {rel}")
        try:
            content = p.read_text(encoding="utf-8")
        except OSError as e:
            raise HTTPException(500, f"read failed: {e}") from e
        return {"id": doc_id, "path": rel, "content": content}

    @router.get("/api/mentions/autocomplete")
    def mentions_autocomplete(
        q: str = "",
        workspace: str = "",
        thread_id: str = "",
        actor: str = "",
        scope: str = "all",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Autocomplete suggestions for @-mentions in chat input."""
        return _build_mentions_autocomplete(
            registry=registry,
            q=q,
            workspace=workspace,
            thread_id=thread_id,
            actor=actor,
            scope=scope,
            limit=limit,
        )

    @router.get("/api/threads/{thread_id}/active-agents")
    def thread_active_agents(thread_id: str) -> dict[str, Any]:
        """List agents that have participated in the given thread.

        Pulled from team room membership when the thread is bound to a
        team room, otherwise from message senders in the thread history.
        """
        try:
            agent_ids = _resolve_thread_active_agents(thread_id, registry)
        except (AttributeError, KeyError, TypeError):
            agent_ids = set()
        results: list[dict[str, Any]] = []
        for agent_id in sorted(agent_ids):
            try:
                agent = registry.get(agent_id) if hasattr(registry, "get") else None
            except (AttributeError, KeyError):
                agent = None
            if agent is None:
                results.append(
                    {
                        "id": agent_id,
                        "display_name": agent_id,
                        "description": "",
                    }
                )
                continue
            results.append(
                {
                    "id": agent_id,
                    "display_name": str(getattr(agent, "display_name", "") or agent_id),
                    "description": str(getattr(agent, "description", "") or "")[:200],
                }
            )
        return {"agents": results, "count": len(results)}

    return router


__all__ = [
    "create_meta_router",
    "SKILL_CATEGORIES",
    "_derive_skill_category",
    "_resolve_thread_active_agents",
]
_LOG = logging.getLogger(__name__)
