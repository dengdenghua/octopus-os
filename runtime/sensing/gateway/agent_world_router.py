# ruff: noqa: E402 — module-level imports below are intentionally late
"""Agent Market router · local agent marketplace.

Exposes the built-in agents under `agents/` as a browsable store and
persists install/uninstall state to a lightweight JSON file under the
runtime data directory. This makes the frontend Agent Market usable even
before a remote marketplace exists.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request
    from fastapi.responses import StreamingResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    StreamingResponse = None  # type: ignore[assignment, misc]

from runtime.execution.agents.loader import default_agents_root
from runtime.execution.misc.agent_avatar import pixel_agent_avatar_svg
from runtime.platform.io import atomic_write_json, atomic_write_text, read_json_with_backup
from runtime.platform.process.paths import app_paths, resources_root
from runtime.sensing._fastapi_guard import require_fastapi

# ── Re-exports from helper submodule ──────────────────────────────
# The following names are imported from ``_agent_world_helpers`` so that
# ``from runtime.sensing.gateway.agent_world_router import PublicName``
# continues to work.  Functions that are monkey-patched by tests
# (``_template_skill_catalog``) or that read the monkey-patched
# ``_INSTALL_STATE`` (``_read_install_state`` / ``_write_install_state``)
# remain defined in *this* module to preserve patch visibility.
from ._agent_world_helpers import (
    BUILTIN_TEMPLATES,
    _is_safe_agent_id,
    _list_local_agents,
    _parse_agent_markdown,
    _read_agent_private_skills,
    _read_agent_profile,
    _register_public_prompt_skills,
    _require_safe_agent_id,
    _require_safe_skill_name,
    _template_by_id,
    _template_private_skills,
    _template_source_root,
)

_INSTALL_STATE = app_paths().data_dir / "agents-installed.json"
_MARKET_INSTALL_SOURCE = "agent-market-template"


def _read_install_state() -> set[str]:
    data = read_json_with_backup(_INSTALL_STATE, default={})
    if not isinstance(data, dict):
        return set()
    raw = data.get("installed", [])
    if not isinstance(raw, list):
        return set()
    installed: set[str] = set()
    for item in raw:
        agent_id = str(item).strip()
        if _is_safe_agent_id(agent_id):
            installed.add(agent_id)
    return installed


def _write_install_state(installed: set[str]) -> None:
    safe_installed = sorted(agent_id for agent_id in installed if _is_safe_agent_id(agent_id))
    atomic_write_json(
        _INSTALL_STATE,
        {"installed": safe_installed},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _template_skill_catalog(template: dict[str, Any]) -> list[str]:
    raw = template.get("available_skills") or []
    if isinstance(raw, list) and raw:
        return list(dict.fromkeys(str(skill).strip() for skill in raw if str(skill).strip()))
    source_rel = template.get("skill_source_root")
    if not source_rel:
        return _template_private_skills(template)
    source_root = _template_source_root(template) / str(source_rel)
    if not source_root.is_dir():
        return _template_private_skills(template)
    names = [
        path.parent.name for path in sorted(source_root.rglob("SKILL.md")) if path.parent.is_dir()
    ]
    return list(dict.fromkeys(name for name in names if name))


def _copy_template_private_skills(
    template: dict[str, Any],
    skills_root: Path,
) -> dict[str, list[str]]:
    _template_private_skills(template)
    available_skills = _template_skill_catalog(template)
    source_rel = template.get("skill_source_root")
    result: dict[str, list[str]] = {"copied": [], "skipped": [], "missing": []}
    if not available_skills or not source_rel:
        return result
    source_root = _template_source_root(template) / str(source_rel)
    skills_root.mkdir(parents=True, exist_ok=True)
    for skill_name in available_skills:
        skill_name = _require_safe_skill_name(skill_name)
        source = source_root / skill_name
        if (
            source.is_symlink()
            or not source.is_dir()
            or not (source / "SKILL.md").is_file()
            or any(child.is_symlink() for child in source.rglob("*"))
        ):
            result["missing"].append(skill_name)
            continue
        target = skills_root / skill_name
        if target.exists() or target.is_symlink():
            result["skipped"].append(skill_name)
            continue
        shutil.copytree(source, target)
        result["copied"].append(skill_name)
    return result


def _is_market_managed_agent(
    agent_root: Path,
    agent_id: str,
    *,
    template: dict[str, Any] | None = None,
    installed: set[str] | None = None,
) -> bool:
    if agent_root.is_symlink() or not agent_root.is_dir():
        return False
    profile = _read_agent_profile(agent_root)
    if not profile:
        return False
    if str(profile.get("id") or agent_root.name).strip() != agent_id:
        return False
    if profile.get("source_kind") == _MARKET_INSTALL_SOURCE:
        return True
    if profile.get("managed_by") == "agent-market":
        return True

    # Backward compatibility for agents installed before the explicit
    # source marker existed: require both persisted install state and
    # catalog-identical metadata before treating the directory as managed.
    if not template or not installed or agent_id not in installed:
        return False
    return (
        str(profile.get("templateId") or "").strip() == agent_id
        and str(profile.get("creator") or "").strip() == str(template.get("author") or "").strip()
    )


def _cleanup_new_agent_root(agent_root: Path, *, created_new: bool) -> None:
    if created_new and agent_root.is_dir() and not agent_root.is_symlink():
        shutil.rmtree(agent_root, ignore_errors=True)


def _install_template_agent(
    agent_id: str,
    agents_root: Path,
    *,
    skills_root: Path | None = None,
) -> Path | None:
    agent_id = _require_safe_agent_id(agent_id)
    template = _template_by_id(agent_id)
    if not template:
        return None
    skills_root = skills_root or resources_root() / "skills" / "public"
    private_skills = _template_private_skills(template)
    available_skills = _template_skill_catalog(template)
    if agents_root.exists() and (agents_root.is_symlink() or not agents_root.is_dir()):
        raise ValueError("agents root must be a real directory")
    agents_root.mkdir(parents=True, exist_ok=True)
    agent_root = agents_root / agent_id
    created_new = not agent_root.exists() and not agent_root.is_symlink()
    if agent_root.exists() or agent_root.is_symlink():
        if not _is_market_managed_agent(
            agent_root,
            agent_id,
            template=template,
            installed=_read_install_state(),
        ):
            raise FileExistsError(
                f"agent directory already exists and is not market-managed: {agent_id}"
            )
        if agent_root.is_symlink() or not agent_root.is_dir():
            raise ValueError("agent directory must be a real directory")
    core = agent_root / "agent-core"
    try:
        core.mkdir(parents=True, exist_ok=True)
        if core.is_symlink() or not core.is_dir():
            raise ValueError("agent-core must be a real directory")
    except Exception:
        _cleanup_new_agent_root(agent_root, created_new=created_new)
        raise
    try:
        skill_bundle = _copy_template_private_skills(template, skills_root)
        from runtime.execution.agents.identity import (
            build_identity_profile,
            generate_identity_code,
        )

        identity_code = generate_identity_code(agents_root)
        profile = {
            "id": template["id"],
            "templateId": template["id"],
            "templateVersion": "1.0.0",
            "source_kind": _MARKET_INSTALL_SOURCE,
            "managed_by": "agent-market",
            "name": template["display_name"],
            "icon": template["icon"],
            "did": identity_code,
            "identity_code": identity_code,
            "identity": build_identity_profile(identity_code),
            "description": template["description"],
            "avatar": "avatar.svg",
            "category": template["category"],
            "tags": template["tags"],
            "model": {"provider": "auto", "name": "auto"},
            "runtime": "local",
            "creator": template["author"],
            "source": template.get("source_url"),
            "key_skills": private_skills,
            "available_skills": available_skills,
            "skill_bundle": skill_bundle,
        }
        atomic_write_json(agent_root / "profile.jsonc", profile, ensure_ascii=False, indent=2)
        atomic_write_text(
            agent_root / "avatar.svg",
            pixel_agent_avatar_svg(template["display_name"]),
            newline=None,
        )
        source_path = template.get("source_path")
        source_body = ""
        if source_path:
            try:
                _meta, source_body = _parse_agent_markdown(
                    _template_source_root(template) / str(source_path),
                )
            except OSError:
                source_body = ""
        soul = source_body or (
            f"You are {template['display_name']}.\n\n"
            f"Primary mission: {template['description']}\n\n"
            f"Specialties: {', '.join(template['tags'])}.\n"
            "Be concise, action-oriented, and precise."
        )
        if template.get("source_url"):
            soul = f"{soul}\n\n---\nSource: {template['source_url']}\n"
        atomic_write_text(core / "SOUL.md", soul, newline=None)
        atomic_write_text(
            core / "IDENTITY.md",
            f"- Name: {template['display_name']}\n- Role: {template['category']} specialist\n",
            newline=None,
        )
        atomic_write_json(
            core / "tool-registry.jsonc",
            {
                "arms": ["fs_writer", "git", "shell"],
                "extra_affinity": template["tags"],
                "private_skills": private_skills,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception:
        _cleanup_new_agent_root(agent_root, created_new=created_new)
        raise
    return agent_root


def _template_to_agent_dict(template: dict[str, Any], *, installed: set[str]) -> dict[str, Any]:
    """模板 → 与 ``_list_local_agents`` 同形状的 dict(供按 id 直查 / 安装用,
    不进入列表)。"""
    agent_id = template["id"]
    return {
        "id": agent_id,
        "name": agent_id,
        "display_name": template["display_name"],
        "description": template["description"],
        "author": template["author"],
        "category": template["category"],
        "tags": template["tags"],
        "icon": template["icon"],
        "avatar_url": None,
        "model": None,
        "tool_groups": ["fs_writer", "git", "shell"],
        "extra_affinity": list(template["tags"]),
        "private_skills": _template_private_skills(template),
        "capabilities": {},
        "version": "1.0.0",
        "downloads": 0,
        "rating": 4.5,
        "rating_count": 0,
        "is_featured": bool(template.get("featured")),
        "is_official": template["author"] == "echo",
        "is_installed": agent_id in installed,
        "source_kind": _MARKET_INSTALL_SOURCE,
        "created_at": "0",
        "source_url": template.get("source_url"),
        "key_skills": _template_private_skills(template),
        "available_skills": _template_skill_catalog(template),
    }


def create_agent_world_router(
    *,
    registry: Any = None,
    runtime: Any = None,
    skill_registry: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    allow_local_user_plugin_lifecycle: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    def _auth_dep(request: Request) -> None:
        # Agent market reads templates and can also install/uninstall local
        # agents. Keep dev mode unchanged; in auth-on deployments gate the
        # whole market surface at the router level.
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
        """Protect shared mutations while allowing the trusted local desktop.

        The desktop server already computes this posture from the explicit
        ``execution.deployment_mode=local`` + loopback bind contract.  Reuse
        it here so the legacy cloud catalog has the same local lifecycle
        semantics as ``/api/capabilities``.  The default remains admin-only,
        which preserves the shared/server security boundary for direct router
        users and deployments that do not opt into the local posture.
        """
        if allow_local_user_plugin_lifecycle:
            return
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

    router = APIRouter(tags=["agent-market"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/agent-market/store")
    def api_agent_market_store(
        category: str | None = None,
        search: str | None = None,
        sort: str = "downloads",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=500),
    ) -> dict[str, Any]:
        agents = _list_local_agents()
        if category:
            agents = [a for a in agents if a["category"] == category]
        if search:
            q = search.lower()
            agents = [
                a
                for a in agents
                if q in a["display_name"].lower()
                or q in a["description"].lower()
                or any(q in t for t in a["tags"])
            ]
        if sort == "rating":
            agents.sort(key=lambda a: a["rating"], reverse=True)
        elif sort == "created_at":
            agents.sort(key=lambda a: a["created_at"], reverse=True)
        elif sort == "name":
            agents.sort(key=lambda a: a["display_name"].lower())
        else:
            agents.sort(
                key=lambda a: (a["downloads"], a["is_featured"], a["is_official"]), reverse=True
            )
        total = len(agents)
        paged = agents[offset : offset + limit]
        page = offset // limit + 1
        return {"agents": paged, "total": total, "page": page, "page_size": limit}

    @router.get("/api/agent-market/store/featured")
    def api_agent_market_featured(limit: int = Query(default=20, ge=1, le=500)) -> dict[str, Any]:
        agents = [a for a in _list_local_agents() if a["is_featured"]]
        agents.sort(key=lambda a: (a["is_official"], a["display_name"].lower()), reverse=True)
        return {"agents": agents[:limit], "total": len(agents), "page": 1, "page_size": limit}

    @router.get("/api/agent-market/store/{agent_id}")
    def api_agent_market_detail(agent_id: str) -> dict[str, Any]:
        try:
            agent_id = _require_safe_agent_id(agent_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        for agent in _list_local_agents():
            if agent["id"] == agent_id:
                return agent
        # 不在本地列表(模板目录已不再列出,见 _list_local_agents)时按 id 直查——
        # 保留旧模板 id 的可解析性(供 install 等既有调用方使用),只是不再列出。
        template = _template_by_id(agent_id)
        if template:
            return _template_to_agent_dict(template, installed=_read_install_state())
        raise HTTPException(404, f"agent not found: {agent_id}")

    @router.post(
        "/api/agent-market/store/{agent_id}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def api_agent_market_install(agent_id: str) -> dict[str, Any]:
        try:
            agent_id = _require_safe_agent_id(agent_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        template = _template_by_id(agent_id)
        if not template:
            agents = _list_local_agents()
            if any(a["id"] == agent_id for a in agents):
                raise HTTPException(400, f"agent is already local: {agent_id}")
            raise HTTPException(404, f"agent not found: {agent_id}")
        agents_root = default_agents_root()
        skills_root = resources_root() / "skills" / "public"
        preexisting_agent_root = agents_root / agent_id
        had_agent_root = preexisting_agent_root.exists() or preexisting_agent_root.is_symlink()
        try:
            agent_root = _install_template_agent(agent_id, agents_root, skills_root=skills_root)
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                500, f"failed to install market agent: {type(exc).__name__}: {exc}"
            ) from exc
        if agent_root is None:
            raise HTTPException(404, f"agent template not found: {agent_id}")
        registered_skills = _register_public_prompt_skills(skill_registry, skills_root)
        installed = _read_install_state()
        installed.add(agent_id)
        try:
            _write_install_state(installed)
        except OSError as exc:
            if not had_agent_root and _is_market_managed_agent(
                agent_root, agent_id, template=template, installed=installed
            ):
                shutil.rmtree(agent_root, ignore_errors=True)
            raise HTTPException(
                500, f"failed to persist market install state: {type(exc).__name__}: {exc}"
            ) from exc
        if registry is not None and runtime is not None:
            from runtime.execution.agents.loader import load_agent

            loaded = load_agent(agent_root, runtime, agents_root / "_shared")
            if hasattr(registry, "replace") and registry.has(agent_id):
                registry.replace(loaded)
            elif not registry.has(agent_id):
                registry.register(loaded)
        tool_registry_path = agent_root / "agent-core" / "tool-registry.jsonc"
        return {
            "installed": True,
            "agent_id": agent_id,
            "key_skills": _read_agent_private_skills(agent_root),
            "available_skills": _template_skill_catalog(template),
            "registered_skills": registered_skills,
            "tool_registry": str(tool_registry_path),
        }

    @router.delete(
        "/api/agent-market/store/{agent_id}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def api_agent_market_uninstall(agent_id: str) -> dict[str, Any]:
        try:
            agent_id = _require_safe_agent_id(agent_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        template = _template_by_id(agent_id)
        if not template:
            raise HTTPException(
                400, f"agent is local and cannot be uninstalled from market: {agent_id}"
            )
        installed = _read_install_state()
        agent_root = default_agents_root() / agent_id
        if agent_root.exists() or agent_root.is_symlink():
            if not _is_market_managed_agent(
                agent_root,
                agent_id,
                template=template,
                installed=installed,
            ):
                raise HTTPException(
                    409,
                    f"agent directory is not market-managed and will not be removed: {agent_id}",
                )
            try:
                shutil.rmtree(agent_root)
            except OSError as exc:
                raise HTTPException(
                    500, f"failed to remove market agent: {type(exc).__name__}: {exc}"
                ) from exc
        installed.discard(agent_id)
        try:
            _write_install_state(installed)
        except OSError as exc:
            raise HTTPException(
                500, f"failed to persist market install state: {type(exc).__name__}: {exc}"
            ) from exc
        if registry is not None and hasattr(registry, "remove"):
            registry.remove(agent_id)
        return {"installed": False, "agent_id": agent_id}

    @router.get("/api/agent-market/profile/{agent_name}")
    def api_agent_market_profile(agent_name: str) -> dict[str, Any]:
        for agent in _list_local_agents():
            if agent["name"] == agent_name or agent["id"] == agent_name:
                return {
                    "agent_name": agent["name"],
                    "display_name": agent["display_name"],
                    "avatar_url": agent["avatar_url"],
                    "bio": agent["description"],
                    "category": agent["category"],
                    "tags": agent["tags"],
                    "stats": {
                        "total_conversations": 0,
                        "total_messages": 0,
                        "satisfaction_rate": agent["rating"] / 5 if agent["rating"] else 0,
                        "avg_response_time_ms": 0,
                        "tasks_completed": 0,
                    },
                    "capabilities": agent["tags"],
                    "last_active": None,
                }
        raise HTTPException(404, f"agent not found: {agent_name}")

    @router.get("/api/agent-market/memory/{agent_name}")
    def api_agent_market_memory(agent_name: str) -> dict[str, Any]:
        return {"memories": []}

    @router.get("/api/agent-market/store/{agent_id}/ratings")
    def api_agent_market_ratings(agent_id: str) -> dict[str, Any]:
        return {"ratings": []}

    @router.get("/api/agent-market/social/{agent_name}/relationships")
    def api_agent_market_social(agent_name: str) -> dict[str, Any]:
        return {"relationships": []}

    # ── WorkBuddy 专家商城 · 云端源 ──────────────────────────────
    # 数据来自发布到 GitHub Pages 的 expert-store.json(421 位专家/专家团,
    # 见 extensions/workbuddy-experts + scripts/publish-cloud.py)。
    def _cloud_store() -> Any:
        from runtime.platform.plugins.cloud_expert_store import CloudExpertStore

        return CloudExpertStore()

    @router.get("/api/agent-market/cloud/store")
    def api_agent_market_cloud_store(
        category: str | None = None,
        search: str | None = None,
        sort: str = "updated",
        refresh: int = 0,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=500),
    ) -> dict[str, Any]:
        store = _cloud_store()
        if refresh:
            store.refresh()
        return store.list_experts(
            category=category, search=search, sort=sort, offset=offset, limit=limit
        )

    @router.get("/api/agent-market/cloud/store/categories")
    def api_agent_market_cloud_categories() -> dict[str, Any]:
        store = _cloud_store()
        return {"categories": store.categories(), "meta": store.meta()}

    @router.get("/api/agent-market/cloud/store/{expert_id}")
    def api_agent_market_cloud_detail(expert_id: str) -> dict[str, Any]:
        store = _cloud_store()
        e = store.get(expert_id)
        if not e:
            raise HTTPException(404, f"cloud expert not found: {expert_id}")
        installed = store._installed_set()
        agent = store.to_agent_dict(e, installed=installed)
        agent["bundle_url"] = e.get("bundleUrl") or ""
        agent["quick_prompts"] = [
            p.get("zh") or p.get("en") or "" for p in (e.get("quickPrompts") or [])
        ]
        agent["prompt_file"] = e.get("promptFile") or ""
        return agent

    @router.post(
        "/api/agent-market/cloud/store/{expert_id}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def api_agent_market_cloud_install(expert_id: str) -> dict[str, Any]:
        from runtime.execution.misc.agent_packs import AgentPackAgentNotFound
        from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

        if immutable_prompt_catalog_required():
            raise HTTPException(
                403,
                "remote expert installation is disabled in shared/commercial deployments; "
                "ship expert prompts in a reviewed release artifact",
            )

        store = _cloud_store()
        try:
            return store.install_expert(
                expert_id,
                agents_root=default_agents_root(),
                skills_root=resources_root() / "skills" / "public",
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except AgentPackAgentNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc

    # ── 云商城插件 / 技能目录(发布到 Pages 的 plugin-store.json / skill-registry.json) ──
    def _cloud_catalog(kind: str) -> Any:
        from runtime.platform.plugins.cloud_catalog import CloudCatalog

        return CloudCatalog(kind)

    @router.get("/api/agent-market/cloud/plugins")
    def api_agent_market_cloud_plugins(
        search: str | None = None,
        kind: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
        refresh: int = Query(default=0, ge=0, le=1),
    ) -> dict[str, Any]:
        cat = _cloud_catalog("plugins")
        if refresh:
            cat.refresh()
        out = cat.list(search=search, kind=kind, offset=offset, limit=limit)
        out["meta"] = cat.meta()
        return out

    @router.get("/api/agent-market/cloud/skills")
    def api_agent_market_cloud_skills(
        search: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=300, ge=1, le=500),
        refresh: int = Query(default=0, ge=0, le=1),
    ) -> dict[str, Any]:
        cat = _cloud_catalog("skills")
        if refresh:
            cat.refresh()
        out = cat.list(search=search, offset=offset, limit=limit)
        out["meta"] = cat.meta()
        return out

    # ── 云商城已安装状态(本地已落地哪些技能/插件) ─────────────
    @router.get("/api/agent-market/cloud/installed")
    def api_agent_market_cloud_installed() -> dict[str, Any]:
        cat = _cloud_catalog("skills")
        plugins = _cloud_catalog("plugins")
        return {
            "skills": cat.installed_skills(),
            "plugins": plugins.installed_plugins(),
            "plugin_states": plugins.plugin_statuses(),
        }

    # ── 云商城安装(下载内容包 → 解包落地) ─────────────────────
    @router.post(
        "/api/agent-market/cloud/skills/{name}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def api_agent_market_cloud_skill_install(name: str) -> dict[str, Any]:
        from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

        if immutable_prompt_catalog_required():
            raise HTTPException(
                403,
                "remote skill installation is disabled in shared/commercial deployments; "
                "ship skill prompts in a reviewed release artifact",
            )
        cat = _cloud_catalog("skills")
        try:
            return cat.install_skill(name)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post(
        "/api/agent-market/cloud/skills/{name}/install/stream",
        dependencies=[Depends(_admin_dep)],
    )
    async def api_agent_market_cloud_skill_install_stream(name: str) -> Any:
        """Stream observable install phases as newline-delimited JSON."""
        from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

        if immutable_prompt_catalog_required():
            raise HTTPException(
                403,
                "remote skill installation is disabled in shared/commercial deployments; "
                "ship skill prompts in a reviewed release artifact",
            )

        async def events():
            def line(payload: dict[str, Any]) -> str:
                return json.dumps(payload, ensure_ascii=False) + "\n"

            yield line({"phase": "resolving", "progress": 10, "message": "正在解析云端技能"})
            await asyncio.sleep(0)
            yield line({"phase": "installing", "progress": 45, "message": "正在下载并校验内容包"})
            try:
                result = await asyncio.to_thread(_cloud_catalog("skills").install_skill, name)
            except (KeyError, ValueError) as exc:
                yield line({"phase": "failed", "progress": 100, "message": str(exc)})
                return
            except Exception as exc:  # pragma: no cover - defensive stream boundary
                yield line({"phase": "failed", "progress": 100, "message": str(exc)})
                return
            yield line({"phase": "indexing", "progress": 85, "message": "正在写入本地技能目录"})
            await asyncio.sleep(0)
            yield line(
                {
                    "phase": "completed",
                    "progress": 100,
                    "message": "安装完成",
                    "result": result,
                }
            )

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post(
        "/api/agent-market/cloud/plugins/{plugin_id}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def api_agent_market_cloud_plugin_install(
        plugin_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

        cat = _cloud_catalog("plugins")
        item = next((i for i in cat.items() if i.get("id") == plugin_id), None)
        if item is None:
            raise HTTPException(404, f"cloud plugin not found: {plugin_id}")
        # 内容包目录:codex 插件 plugins/codex/<name>,连接器 plugins/connector/<id>;
        # 条目 id 带前缀(codex_/wb_),成员名取 item["plugin"]。
        item_kind = str(item.get("kind") or "connector")
        archive_kind = (
            "codex"
            if item_kind == "plugin"
            else "workbench"
            if item_kind == "workbench"
            else "connector"
        )
        member = str(item.get("plugin") or plugin_id)
        runtime_member = str(item.get("runtime_plugin") or member)
        factory_checker = getattr(cat, "is_factory_plugin", None)
        catalog_marks_factory = (
            bool(factory_checker(member)) if callable(factory_checker) else False
        )
        is_factory_workbench = (
            archive_kind == "workbench" and bool(item.get("factory_seed")) and catalog_marks_factory
        )
        if immutable_prompt_catalog_required() and not is_factory_workbench:
            raise HTTPException(
                403,
                "unsigned cloud plugin installation is disabled in shared/commercial "
                "deployments; ship a reviewed signed plugin release",
            )
        payload = body or {}
        enabled_value = payload.get("enabled", True)
        restore_value = payload.get("restore_data", False)
        recovery_value = payload.get("recovery_id")
        if not isinstance(enabled_value, bool):
            raise HTTPException(400, "enabled must be a boolean")
        if not isinstance(restore_value, bool):
            raise HTTPException(400, "restore_data must be a boolean")
        if recovery_value is not None and not isinstance(recovery_value, str):
            raise HTTPException(400, "recovery_id must be a string")
        try:
            hub = getattr(request.app.state, "plugin_hub", None)
            if is_factory_workbench and hub is not None:
                try:
                    return hub.install_plugin(
                        member,
                        enabled=enabled_value,
                        restore_data=restore_value,
                        recovery_id=recovery_value,
                    )
                except KeyError as exc:
                    # A source checkout may ship the runtime module with a
                    # ``delivery: remote`` manifest. PluginHub deliberately
                    # hides such bundled modules from factory discovery, but
                    # the cloud catalog can still materialize the reviewed
                    # workbench package. Fall through to that path when the
                    # hub reports only this discoverability mismatch.
                    if not str(exc).startswith("'factory plugin is unavailable:"):
                        raise
            if is_factory_workbench:
                return cat.install_plugin(
                    member,
                    plugin_kind=archive_kind,
                    enabled=enabled_value,
                    restore_data=restore_value,
                    recovery_id=recovery_value,
                )
            was_loaded = bool(
                archive_kind == "workbench"
                and hub is not None
                and hub.get_plugin(runtime_member) is not None
            )
            was_started = bool(was_loaded and hub is not None and hub.is_started(runtime_member))
            if was_loaded and hub is not None:
                hub.unload(runtime_member)
            try:
                install_options: dict[str, Any] = {"plugin_kind": archive_kind}
                if archive_kind == "workbench":
                    install_options.update(
                        enabled=enabled_value,
                        restore_data=restore_value,
                        recovery_id=recovery_value,
                    )
                result = cat.install_plugin(member, **install_options)
            except Exception:
                if was_loaded and hub is not None:
                    hub.load(runtime_member)
                    if was_started:
                        hub.start(runtime_member)
                raise
            if archive_kind == "workbench" and hub is not None:
                package_dir = Path(str(result.get("path") or ""))
                activated_dependencies: list[str] = []
                parent_has_runtime = (package_dir / "plugin.yaml").is_file()

                def activate_runtime(name: str) -> None:
                    if hasattr(hub, "enable_plugin"):
                        lifecycle = hub.enable_plugin(name)
                        if not lifecycle.get("loaded") or not lifecycle.get("started"):
                            raise RuntimeError(f"failed to activate plugin: {name}")
                    else:
                        loaded = hub.load(name)
                        if loaded is None or not hub.start(name):
                            raise RuntimeError(f"failed to activate plugin: {name}")

                try:
                    for dependency in result.get("installed_dependencies") or []:
                        dependency_runtime = str(dependency.get("runtime_plugin") or "").strip()
                        if dependency_runtime:
                            activate_runtime(dependency_runtime)
                            activated_dependencies.append(dependency_runtime)
                    if parent_has_runtime:
                        runtime_member = str(result.get("runtime_plugin") or runtime_member)
                        activate_runtime(runtime_member)
                except Exception:
                    if parent_has_runtime:
                        hub.unload(runtime_member)
                    for dependency_runtime in reversed(activated_dependencies):
                        hub.unload(dependency_runtime)
                    transaction_id = result.get("transaction_id")
                    if transaction_id:
                        cat.rollback_plugin(
                            member,
                            plugin_kind=archive_kind,
                            transaction_id=str(transaction_id),
                        )
                    else:
                        cat.uninstall_plugin(member, plugin_kind=archive_kind)
                    consumed: set[str] = set()
                    for dependency in reversed(result.get("installed_dependencies") or []):
                        dependency_transaction = str(dependency.get("transaction_id") or "")
                        if not dependency_transaction or dependency_transaction in consumed:
                            continue
                        cat.rollback_plugin(
                            str(dependency.get("plugin_id") or ""),
                            plugin_kind="workbench",
                            transaction_id=dependency_transaction,
                        )
                        consumed.add(dependency_transaction)
                    if was_loaded:
                        hub.load(runtime_member)
                        if was_started:
                            hub.start(runtime_member)
                    raise
                if parent_has_runtime:
                    result.update(
                        loaded=True,
                        started=True,
                        restart_required=False,
                    )
                if activated_dependencies:
                    result["activated_dependencies"] = activated_dependencies
            return result
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post(
        "/api/agent-market/cloud/plugins/{plugin_id}/rollback",
        dependencies=[Depends(_admin_dep)],
    )
    def api_agent_market_cloud_plugin_rollback(
        plugin_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cat = _cloud_catalog("plugins")
        item = next((i for i in cat.items() if i.get("id") == plugin_id), None)
        if item is None or item.get("kind") != "workbench":
            raise HTTPException(404, f"workbench plugin not found: {plugin_id}")
        member = str(item.get("plugin") or plugin_id)
        runtime_member = str(item.get("runtime_plugin") or member)
        payload = body or {}
        transaction_id = payload.get("transaction_id")
        if transaction_id is not None and not isinstance(transaction_id, str):
            raise HTTPException(400, "transaction_id must be a string")
        hub = getattr(request.app.state, "plugin_hub", None)
        was_loaded = bool(hub is not None and hub.get_plugin(runtime_member) is not None)
        was_started = bool(was_loaded and hub is not None and hub.is_started(runtime_member))
        if was_loaded and hub is not None:
            hub.unload(runtime_member)
        try:
            result = cat.rollback_plugin(
                member,
                plugin_kind="workbench",
                transaction_id=transaction_id,
            )
            restored_runtime = str(result.get("runtime_plugin") or runtime_member)
            if result.get("installed") and hub is not None:
                lifecycle = hub.enable_plugin(restored_runtime)
                if not lifecycle.get("started"):
                    raise RuntimeError(f"failed to activate rollback: {restored_runtime}")
                result.update(loaded=True, started=True, restart_required=False)
            else:
                result.update(loaded=False, started=False, restart_required=False)
            return result
        except (KeyError, ValueError, RuntimeError) as exc:
            if was_loaded and hub is not None and hub.get_plugin(runtime_member) is None:
                hub.load(runtime_member)
                if was_started:
                    hub.start(runtime_member)
            status = 404 if isinstance(exc, KeyError) else 409
            raise HTTPException(status, str(exc)) from exc

    @router.post(
        "/api/agent-market/cloud/plugins/{plugin_id}/{action}",
        dependencies=[Depends(_admin_dep)],
    )
    def api_agent_market_cloud_plugin_activation(
        plugin_id: str,
        action: str,
        request: Request,
    ) -> dict[str, Any]:
        if action not in {"enable", "disable"}:
            raise HTTPException(404, f"unsupported workbench action: {action}")
        cat = _cloud_catalog("plugins")
        item = next((i for i in cat.items() if i.get("id") == plugin_id), None)
        if item is None or item.get("kind") != "workbench":
            raise HTTPException(404, f"workbench plugin not found: {plugin_id}")
        member = str(item.get("plugin") or plugin_id)
        runtime_member = str(item.get("runtime_plugin") or "").strip()
        enabled = action == "enable"
        try:
            if runtime_member:
                hub = getattr(request.app.state, "plugin_hub", None)
                if hub is None:
                    raise RuntimeError("plugin runtime is unavailable")
                result = (
                    hub.enable_plugin(runtime_member)
                    if enabled
                    else hub.disable_plugin(runtime_member)
                )
                if bool(result.get("enabled")) != enabled:
                    raise RuntimeError(str(result.get("error") or "plugin state did not change"))
            package = cat.set_workbench_enabled(member, enabled)
            return {**package, **(result if runtime_member else {})}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete(
        "/api/agent-market/cloud/plugins/{plugin_id}/install",
        dependencies=[Depends(_admin_dep)],
    )
    def api_agent_market_cloud_plugin_uninstall(
        plugin_id: str,
        request: Request,
        data_policy: str = Query(default="keep", pattern="^(keep|trash)$"),
        confirm_data_move: bool = Query(default=False),
    ) -> dict[str, Any]:
        cat = _cloud_catalog("plugins")
        item = next((i for i in cat.items() if i.get("id") == plugin_id), None)
        if item is None:
            raise HTTPException(404, f"cloud plugin not found: {plugin_id}")
        item_kind = str(item.get("kind") or "connector")
        archive_kind = (
            "codex"
            if item_kind == "plugin"
            else "workbench"
            if item_kind == "workbench"
            else "connector"
        )
        member = str(item.get("plugin") or plugin_id)
        runtime_member = str(item.get("runtime_plugin") or member)
        factory_checker = getattr(cat, "is_factory_plugin", None)
        is_factory_workbench = (
            archive_kind == "workbench"
            and bool(item.get("factory_seed"))
            and callable(factory_checker)
            and bool(factory_checker(member))
        )
        try:
            hub = getattr(request.app.state, "plugin_hub", None)
            if is_factory_workbench and hub is not None:
                return hub.uninstall_plugin(
                    member,
                    data_policy=data_policy,
                    confirm_data_move=confirm_data_move,
                )
            if is_factory_workbench:
                return cat.uninstall_plugin(
                    member,
                    plugin_kind=archive_kind,
                    data_policy=data_policy,
                    confirm_data_move=confirm_data_move,
                )
            was_loaded = bool(hub is not None and hub.get_plugin(runtime_member) is not None)
            was_started = bool(was_loaded and hub is not None and hub.is_started(runtime_member))
            if was_loaded and hub is not None:
                hub.unload(runtime_member)
            try:
                if archive_kind == "workbench":
                    return cat.uninstall_plugin(
                        member,
                        plugin_kind=archive_kind,
                        data_policy=data_policy,
                        confirm_data_move=confirm_data_move,
                    )
                return cat.uninstall_plugin(member, plugin_kind=archive_kind)
            except Exception:
                if was_loaded and hub is not None:
                    hub.load(runtime_member)
                    if was_started:
                        hub.start(runtime_member)
                raise
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            status = 409 if "not installed" in str(exc) else 400
            raise HTTPException(status, str(exc)) from exc

    return router


__all__ = ["BUILTIN_TEMPLATES", "create_agent_world_router"]
