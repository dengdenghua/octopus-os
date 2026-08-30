"""Agent CRUD + visual + reload endpoints for the agents router.

Pure structural split of ``_agents_endpoints.py`` — no logic changes.
``_register_agents_crud`` attaches the agent lifecycle endpoints (list /
create / get / update / delete / reload / avatar / visuals) to the injected
router, reading shared state through the injected ``_AgentsCtx`` and the
``_AuthActions`` bundle.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

try:
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse, Response

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    FileResponse = None  # type: ignore[assignment, misc]
    Response = None  # type: ignore[assignment, misc]

from runtime.execution.misc.agent_avatar import pixel_agent_avatar_svg

from ._agents_endpoints_shared import _AuthActions
from ._agents_helpers import (
    _BUILTIN_AGENT_IDS,
    _agent_dir_for,
    _agent_visual_urls_for,
    _avatar_url_for,
    _cleanup_created_agent_dir,
    _require_real_agent_dir,
    _require_safe_agent_id,
    _restore_text_file,
    _to_detail_wire,
    _to_wire,
)
from .agents_models import (
    AgentDetailWire,
    AgentVisualsWire,
    AgentWire,
    CreateAgentRequest,
    GenerateAgentVisualsRequest,
    UpdateAgentRequest,
)

if TYPE_CHECKING:
    from ._agents_endpoints import _AgentsCtx


def _register_agents_crud(router: Any, ctx: _AgentsCtx, auth: _AuthActions) -> None:
    registry = ctx.registry
    runtime = ctx.runtime
    _auth = auth.auth
    _require_admin = auth.require_admin

    # Agent IDs that should not appear in the agent gallery/picker.
    # ``admin`` is system-level (code-mode privileged persona) and has its
    # own dedicated entry point. ``desktop_operator`` IS user-facing since
    # #22 (CUA productization) — the Raven persona shows in the picker.
    _AGENT_GALLERY_SKIP_IDS = frozenset({"admin"})  # noqa: N806

    # The roster is read on every workspace bootstrap and can contain well over
    # one hundred personas.  Building the wire payload repeatedly is wasteful;
    # the visual variant also probes profile/avatar files for every agent.  A
    # per-router cache keeps the hot path allocation-free and the lock folds a
    # burst of identical cold requests into one build.  Registry mutations
    # publish replacement Agent objects, so tuple identity/equality is a cheap
    # and reliable invalidation key.  Visual metadata additionally expires to
    # preserve the existing "new avatar appears without reload" behaviour.
    _list_cache_lock = threading.Lock()
    _list_cache: dict[bool, tuple[tuple[Any, ...], float, tuple[AgentWire, ...]]] = {}
    _visual_cache_ttl_seconds = 2.0

    def _cached_agent_wires(*, include_visuals: bool) -> list[AgentWire]:
        snapshot = tuple(
            agent
            for agent in registry.all_agents()
            if agent.agent_id not in _AGENT_GALLERY_SKIP_IDS
        )
        now = time.monotonic()
        with _list_cache_lock:
            cached = _list_cache.get(include_visuals)
            if cached is not None:
                cached_snapshot, expires_at, cached_wires = cached
                if cached_snapshot == snapshot and now < expires_at:
                    return list(cached_wires)

            wires = tuple(_to_wire(agent, include_visuals=include_visuals) for agent in snapshot)
            expires_at = now + _visual_cache_ttl_seconds if include_visuals else float("inf")
            _list_cache[include_visuals] = (snapshot, expires_at, wires)
            return list(wires)

    @router.get("/api/agents")
    def list_agents(request: Request, include_visuals: bool = True) -> list[AgentWire]:
        _auth(request)  # AUTH-OK: actor-agnostic — agent registry is server-global
        return _cached_agent_wires(include_visuals=include_visuals)

    @router.post("/api/agents", status_code=201)
    def create_agent(request: Request, body: CreateAgentRequest) -> AgentDetailWire:
        """Create a new agent from scratch.

        Creates the agent directory structure, writes profile.jsonc and
        agent-core/SOUL.md, then hot-loads the agent into the registry.
        """
        _require_admin(request)  # Mutation: writes to global agents/ dir + loads code
        if runtime is None:
            raise HTTPException(503, "agent creation needs a GraphRuntime in this router")

        agent_id = body.name.strip()
        if not agent_id:
            raise HTTPException(400, "agent name is required")
        agent_id = _require_safe_agent_id(agent_id)

        # Check for reserved names
        if agent_id in _BUILTIN_AGENT_IDS:
            raise HTTPException(400, f"agent_id '{agent_id}' is reserved")

        from runtime.execution.agents.loader import (
            default_agents_root,
            load_agent,
        )

        root = default_agents_root().resolve()
        agent_dir = _agent_dir_for(root, agent_id)

        # Check if agent already exists
        if agent_dir.exists() or agent_dir.is_symlink():
            raise HTTPException(409, f"agent already exists: {agent_id}")
        if registry.has(agent_id):
            raise HTTPException(409, f"agent already registered: {agent_id}")

        # Create directory structure
        created_agent_dir = False
        try:
            agent_dir.mkdir(parents=True)
            created_agent_dir = True
            (agent_dir / "agent-core").mkdir()
            (agent_dir / "agent-core" / ".soul_history").mkdir()
            (agent_dir / "agent-core" / "diary").mkdir()
            (agent_dir / "agent-core" / "skills").mkdir()
            (agent_dir / "memory").mkdir()
            (agent_dir / "permissions").mkdir()
            (agent_dir / "project").mkdir()
            (agent_dir / "runtime").mkdir()
            (agent_dir / "sessions").mkdir()
            (agent_dir / "skills").mkdir()
        except OSError as exc:
            _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
            raise HTTPException(
                500, f"failed to create agent directories: {type(exc).__name__}: {exc}"
            ) from exc

        # Generate one immutable identity code. Display names and professions
        # may change; this code must not.
        from runtime.execution.agents.identity import (
            build_identity_profile,
            generate_identity_code,
        )

        identity_code = generate_identity_code(root)

        # Build profile.jsonc
        import json

        from runtime.platform.io import atomic_write_text

        profile: dict[str, Any] = {
            "id": agent_id,
            "templateId": agent_id,
            "templateVersion": "1.0.0",
            "name": agent_id.replace("-", " ").replace("_", " ").title(),
            "icon": "🤖",
            "did": identity_code,
            "identity_code": identity_code,
            "identity": build_identity_profile(identity_code, body.personality_anchors),
            "description": body.description or f"A custom agent named {agent_id}.",
            "avatar": "avatar.svg",
            "model": {"provider": "auto", "name": body.model or "auto"},
            "runtime": "local",
            "creator": "user",
            "defaultProject": {"dir": "project"},
            "capabilities": {},
        }

        profile_path = agent_dir / "profile.jsonc"
        try:
            profile_text = (
                f"// Echo Agent profile · {agent_id}\n"
                "// Created by user via API\n\n" + json.dumps(profile, ensure_ascii=False, indent=2)
            )
            atomic_write_text(profile_path, profile_text)
        except OSError as exc:
            _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
            raise HTTPException(
                500, f"failed to write profile.jsonc: {type(exc).__name__}: {exc}"
            ) from exc

        # Write SOUL.md
        soul_content = (
            body.soul
            or f"""# Soul

You are {agent_id}, a helpful AI assistant.

## Personality

- Friendly and professional.
- Clear and concise in communication.
- Adaptable to various tasks.

## Values

- Be helpful while respecting user autonomy.
- Provide accurate information.
- Acknowledge limitations when uncertain.

---

_This file is yours to evolve. As you learn who you are, update it._
"""
        )
        soul_path = agent_dir / "agent-core" / "SOUL.md"
        try:
            atomic_write_text(soul_path, soul_content, newline=None)
        except OSError as exc:
            _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
            raise HTTPException(
                500, f"failed to write SOUL.md: {type(exc).__name__}: {exc}"
            ) from exc

        # Write default IDENTITY.md
        identity_content = f"""# Identity

- **Name**: {agent_id}
- **Role**: Custom AI assistant

## Communication Style

- Clear and professional.
- Matches the user's language.
- Provides helpful, accurate information.

## Available arms

- Configurable via tool-registry.jsonc
"""
        identity_path = agent_dir / "agent-core" / "IDENTITY.md"
        try:
            atomic_write_text(identity_path, identity_content, newline=None)
        except OSError as exc:
            _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
            raise HTTPException(
                500, f"failed to write IDENTITY.md: {type(exc).__name__}: {exc}"
            ) from exc

        # Write default AGENTS.md
        agents_md_content = """# Working rules (shared by all Echo agents)

## Project context discovery

Before starting any task, automatically discover the project context.

## Following conventions

When making changes, first read the surrounding code.

## Security

- Never introduce code that exposes or logs secrets.
- Never commit secrets or keys to the repository.
"""
        agents_md_path = agent_dir / "agent-core" / "AGENTS.md"
        try:
            atomic_write_text(agents_md_path, agents_md_content, newline=None)
        except OSError as exc:
            _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
            raise HTTPException(
                500, f"failed to write AGENTS.md: {type(exc).__name__}: {exc}"
            ) from exc

        # Write default avatar.svg
        avatar_svg = pixel_agent_avatar_svg(profile["name"])
        avatar_path = agent_dir / "avatar.svg"
        try:
            atomic_write_text(avatar_path, avatar_svg, newline=None)
        except OSError as exc:
            _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
            raise HTTPException(
                500, f"failed to write avatar.svg: {type(exc).__name__}: {exc}"
            ) from exc

        # Write tool-registry.jsonc if tool_groups provided
        if body.tool_groups:
            tool_registry = {
                "arms": list(body.tool_groups),
                "extra_affinity": [],
                "private_skills": [],
            }
            tool_registry_path = agent_dir / "agent-core" / "tool-registry.jsonc"
            try:
                tool_text = "// Tool registry for this agent\n\n" + json.dumps(
                    tool_registry, ensure_ascii=False, indent=2
                )
                atomic_write_text(tool_registry_path, tool_text)
            except OSError as exc:
                _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
                raise HTTPException(
                    500, f"failed to write tool-registry.jsonc: {type(exc).__name__}: {exc}"
                ) from exc

        # Hot-load the new agent
        try:
            new_agent = load_agent(agent_dir, runtime, root / "_shared")
        except (OSError, ValueError, TypeError) as exc:
            _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
            raise HTTPException(
                500, f"agent load failed after creation: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            registry.register(new_agent)
        except (ValueError, TypeError) as exc:
            _cleanup_created_agent_dir(agent_dir, created=created_agent_dir)
            raise HTTPException(
                500, f"agent registry update failed after creation: {type(exc).__name__}: {exc}"
            ) from exc
        return _to_detail_wire(new_agent)

    @router.put("/api/agents/{agent_id}")
    def update_agent(request: Request, agent_id: str, body: UpdateAgentRequest) -> AgentDetailWire:
        _require_admin(request)  # Mutation: rewrites profile.jsonc, reloads agent into registry
        if runtime is None:
            raise HTTPException(503, "agent update needs a GraphRuntime in this router")
        agent_id = _require_safe_agent_id(agent_id)

        from runtime.execution.agents.loader import (
            default_agents_root,
            load_agent,
        )
        from runtime.platform.io import atomic_write_text
        from runtime.platform.process.utils import parse_jsonc

        root = default_agents_root().resolve()
        agent_dir = _require_real_agent_dir(root, agent_id)
        profile_path = agent_dir / "profile.jsonc"
        if profile_path.is_symlink():
            raise HTTPException(409, f"agent profile is not a real file: {agent_id}")
        if not profile_path.is_file():
            raise HTTPException(404, f"agent not found: {agent_id}")

        try:
            original_profile_text = profile_path.read_text(encoding="utf-8")
            profile = parse_jsonc(original_profile_text)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                500, f"failed to read profile.jsonc: {type(exc).__name__}: {exc}"
            ) from exc

        provided_fields = set(
            getattr(body, "model_fields_set", set()) or getattr(body, "__fields_set__", set())
        )
        display_name = (body.display_name or "").strip()
        if "display_name" in provided_fields:
            if not display_name:
                raise HTTPException(400, "display_name cannot be empty")
            profile["name"] = display_name
        if "description" in provided_fields:
            profile["description"] = body.description or ""
        if "model" in provided_fields:
            profile["model"] = {"provider": "auto", "name": (body.model or "").strip() or "auto"}
        if "capabilities" in provided_fields:
            profile["capabilities"] = body.capabilities or {}
        if "personality_anchors" in provided_fields:
            from runtime.execution.agents.identity import build_identity_profile

            identity_code = str(profile.get("identity_code") or profile.get("did") or "").strip()
            if not identity_code:
                from runtime.execution.agents.identity import generate_identity_code

                identity_code = generate_identity_code(root)
                profile["identity_code"] = identity_code
                profile["did"] = identity_code
            profile["identity"] = build_identity_profile(identity_code, body.personality_anchors)

        try:
            profile_text = f"// Echo Agent profile · {agent_id}\n\n" + json.dumps(
                profile, ensure_ascii=False, indent=2
            )
            atomic_write_text(profile_path, profile_text)
        except OSError as exc:
            raise HTTPException(
                500, f"failed to write profile.jsonc: {type(exc).__name__}: {exc}"
            ) from exc

        soul_path = agent_dir / "agent-core" / "SOUL.md"
        original_soul_text: str | None = None
        if "soul" in provided_fields:
            core_dir = soul_path.parent
            if core_dir.is_symlink() or not core_dir.is_dir():
                _restore_text_file(profile_path, original_profile_text)
                raise HTTPException(409, f"agent-core is not a real directory: {agent_id}")
            if soul_path.is_symlink():
                _restore_text_file(profile_path, original_profile_text)
                raise HTTPException(409, f"SOUL.md is not a real file: {agent_id}")
            try:
                if soul_path.is_file():
                    original_soul_text = soul_path.read_text(encoding="utf-8")
                atomic_write_text(soul_path, body.soul or "", newline=None)
            except OSError as exc:
                _restore_text_file(profile_path, original_profile_text)
                _restore_text_file(soul_path, original_soul_text)
                raise HTTPException(
                    500, f"failed to write SOUL.md: {type(exc).__name__}: {exc}"
                ) from exc

        try:
            updated_agent = load_agent(agent_dir, runtime, root / "_shared")
        except (OSError, ValueError, TypeError) as exc:
            _restore_text_file(profile_path, original_profile_text)
            if "soul" in provided_fields:
                _restore_text_file(soul_path, original_soul_text)
            raise HTTPException(
                500, f"agent load failed after update: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            if hasattr(registry, "replace"):
                registry.replace(updated_agent)
            else:
                if registry.has(agent_id) and hasattr(registry, "remove"):
                    registry.remove(agent_id)
                registry.register(updated_agent)
        except (ValueError, TypeError) as exc:
            _restore_text_file(profile_path, original_profile_text)
            if "soul" in provided_fields:
                _restore_text_file(soul_path, original_soul_text)
            raise HTTPException(
                500, f"agent registry update failed after update: {type(exc).__name__}: {exc}"
            ) from exc
        return _to_detail_wire(updated_agent)

    @router.get("/api/agents/{agent_id}")
    def get_agent(request: Request, agent_id: str) -> AgentDetailWire:
        _auth(request)  # AUTH-OK: actor-agnostic — agents are server-global
        if not registry.has(agent_id):
            raise HTTPException(404, f"agent not found: {agent_id}")
        return _to_detail_wire(registry.get(agent_id))

    @router.post("/api/agents/{agent_id}/visuals/generate")
    def generate_agent_visuals_route(
        request: Request,
        agent_id: str,
        body: GenerateAgentVisualsRequest | None = None,
    ) -> AgentVisualsWire:
        _require_admin(request)  # Mutation: regenerates avatar via LLM, writes to disk
        agent_id = _require_safe_agent_id(agent_id)

        from runtime.execution.agents.loader import default_agents_root
        from runtime.execution.misc.image_generation import generate_agent_visuals

        try:
            root = default_agents_root().resolve()
        except OSError as exc:
            raise HTTPException(500, f"agents root unavailable: {exc}") from exc

        agent_dir = _require_real_agent_dir(root, agent_id)
        visuals_dir = agent_dir / "visuals"
        if visuals_dir.is_symlink():
            raise HTTPException(409, f"agent visuals path is not a real directory: {agent_id}")

        display_name = agent_id
        description = ""
        if registry.has(agent_id):
            agent = registry.get(agent_id)
            display_name = agent.display_name or agent_id
            description = agent.description or ""
        else:
            profile_path = agent_dir / "profile.jsonc"
            if not profile_path.is_file():
                raise HTTPException(404, f"agent profile not found: {agent_id}")
            try:
                from runtime.platform.process.utils import parse_jsonc

                profile = parse_jsonc(profile_path.read_text(encoding="utf-8"))
                display_name = str(
                    profile.get("name")
                    or profile.get("display_name")
                    or profile.get("id")
                    or agent_id
                )
                description = str(profile.get("description") or "")
            except (OSError, ValueError, TypeError) as exc:
                raise HTTPException(
                    500,
                    f"agent profile read failed: {type(exc).__name__}: {exc}",
                ) from exc

        try:
            result = generate_agent_visuals(
                agent_id=agent_id,
                display_name=display_name,
                description=description,
                output_dir=visuals_dir,
                style_prompt=body.style_prompt if body else "",
                reference_images=body.reference_images if body else [],
                provider=body.provider if body else None,
            )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(
                500,
                f"agent visual generation failed: {type(exc).__name__}: {exc}",
            ) from exc
        return AgentVisualsWire(
            agent_id=agent_id,
            provider=result.provider,
            prompt=result.prompt,
            avatar_url=_avatar_url_for(agent_id),
            visual_urls=_agent_visual_urls_for(agent_id),
        )

    @router.get("/api/agents/{agent_id}/visuals/{view}", include_in_schema=False)
    def get_agent_visual(request: Request, agent_id: str, view: str) -> Any:
        from runtime.execution.agents.loader import default_agents_root

        if "/" in agent_id or "\\" in agent_id or agent_id in ("", ".", ".."):
            raise HTTPException(400, "invalid agent_id")
        if view not in {"front", "side", "back"}:
            raise HTTPException(400, "invalid visual view")
        try:
            root = default_agents_root()
        except OSError as exc:
            raise HTTPException(500, f"agents root unavailable: {exc}") from exc

        visuals_dir = root / agent_id / "visuals"
        for ext, mime in (
            ("png", "image/png"),
            ("jpg", "image/jpeg"),
            ("jpeg", "image/jpeg"),
            ("webp", "image/webp"),
            ("svg", "image/svg+xml"),
        ):
            p = visuals_dir / f"{view}.{ext}"
            if p.is_file():
                return FileResponse(
                    str(p),
                    media_type=mime,
                    headers={"Cache-Control": "no-store"},
                )
        reference = visuals_dir / "reference.png"
        if reference.is_file():
            return FileResponse(
                str(reference),
                media_type="image/png",
                headers={"Cache-Control": "no-store"},
            )
        raise HTTPException(404, f"no {view} visual for agent: {agent_id}")

    @router.delete(
        "/api/agents/{agent_id}", status_code=204, response_class=Response, response_model=None
    )
    def delete_agent(request: Request, agent_id: str):
        _require_admin(request)  # Mutation: deletes agent directory + unloads from registry
        agent_id = _require_safe_agent_id(agent_id)
        if agent_id in _BUILTIN_AGENT_IDS:
            raise HTTPException(400, f"agent_id '{agent_id}' is reserved")

        from runtime.execution.agents.loader import default_agents_root

        try:
            root = default_agents_root().resolve()
        except OSError as exc:
            raise HTTPException(500, f"agents root unavailable: {exc}") from exc

        agent_dir = _agent_dir_for(root, agent_id)
        if agent_dir.is_symlink():
            raise HTTPException(409, f"agent path is not a real directory: {agent_id}")
        if agent_dir.exists() and not agent_dir.is_dir():
            raise HTTPException(409, f"agent path is not a directory: {agent_id}")

        exists_on_disk = agent_dir.is_dir()
        exists_in_registry = registry.has(agent_id)
        if not exists_on_disk and not exists_in_registry:
            raise HTTPException(404, f"agent not found: {agent_id}")

        if exists_on_disk:
            try:
                shutil.rmtree(agent_dir)
            except OSError as exc:
                raise HTTPException(
                    500,
                    f"failed to delete agent directory: {type(exc).__name__}: {exc}",
                ) from exc

        if hasattr(registry, "remove"):
            registry.remove(agent_id)
        return

    @router.get("/api/agents/{agent_id}/avatar", include_in_schema=False)
    def get_agent_avatar(request: Request, agent_id: str) -> Any:
        """Serve ``agents/<id>/avatar.{png,webp,jpg,svg}`` from disk.

        Not auth-gated · avatars are UI decoration rendered in <img>
        tags. Adding the ``_auth`` check here would force every
        image-load to re-do the actor resolution and JWT parse,
        which is wasteful for a public-by-nature asset. Content is
        still scoped to the agents this instance has on disk · an
        attacker can't enumerate paths outside ``agents/<id>/``.
        """
        from runtime.execution.agents.loader import default_agents_root

        try:
            root = default_agents_root()
        except OSError as exc:
            raise HTTPException(500, f"agents root unavailable: {exc}") from exc
        # Reject path-traversal attempts early · agent_id should be a
        # plain slug, not something that could climb out of ``root``.
        if "/" in agent_id or "\\" in agent_id or agent_id in ("", ".", ".."):
            raise HTTPException(400, "invalid agent_id")
        agent_dir = root / agent_id
        profile_path = agent_dir / "profile.jsonc"
        if profile_path.is_file():
            try:
                from runtime.platform.process.utils import parse_jsonc

                profile = parse_jsonc(profile_path.read_text(encoding="utf-8"))
                if "avatar" in profile and (
                    profile.get("avatar") is None or profile.get("avatar") is False
                ):
                    raise HTTPException(404, f"no avatar for agent: {agent_id}")
            except HTTPException:
                raise
            except (OSError, ValueError, TypeError):  # noqa: BLE001 — best-effort profile parse; fall through to file-extension detection
                pass
        for ext, mime in (
            ("png", "image/png"),
            ("webp", "image/webp"),
            ("jpg", "image/jpeg"),
            ("jpeg", "image/jpeg"),
            ("svg", "image/svg+xml"),
        ):
            p = agent_dir / f"avatar.{ext}"
            if p.is_file():
                return FileResponse(
                    str(p),
                    media_type=mime,
                    headers={"Cache-Control": "no-store"},
                )
        raise HTTPException(404, f"no avatar for agent: {agent_id}")

    @router.post("/api/agents/{agent_id}/reload")
    def reload_agent(request: Request, agent_id: str) -> dict[str, Any]:
        """Hot-reload one agent from disk.

        Re-reads ``agents/<agent_id>/`` (profile / soul / identity /
        tool-registry / memory / etc.) and swaps the in-memory Agent.
        Previously-issued Agent references used by in-flight turns keep
        their old soul by Python object identity — no torn state.

        Returns the new Agent wire view. 404 if the folder is gone;
        503 if hot-reload isn't available (runtime missing)."""
        _require_admin(request)  # Mutation: hot-reloads agent code into registry
        if runtime is None:
            raise HTTPException(
                503,
                "hot reload needs a GraphRuntime · pass runtime=... to create_agents_router",
            )
        from runtime.execution.agents.loader import (
            default_agents_root,
            load_agent,
        )

        root = default_agents_root()
        agent_dir = root / agent_id
        if not agent_dir.is_dir() or not (agent_dir / "profile.jsonc").exists():
            raise HTTPException(404, f"agent folder not found: {agent_id}")
        try:
            new_agent = load_agent(agent_dir, runtime, root / "_shared")
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(
                400,
                f"agent rebuild failed: {type(exc).__name__}: {exc}",
            ) from exc
        prev = registry.replace(new_agent)
        return {
            "ok": True,
            "agent": _to_wire(new_agent).model_dump(),
            "replaced": prev is not None,
        }

    @router.post("/api/agents/reload")
    def reload_all_agents(request: Request) -> dict[str, Any]:
        """Hot-reload every agent folder under ``agents/``.

        Drops-in-place-replaces each registered agent. Agents that are
        in the filesystem but not in the registry get registered. Agents
        in the registry but no longer on disk are left alone (would
        need an explicit delete endpoint to remove).
        """
        _require_admin(request)  # Mutation: bulk hot-reload of all agents
        if runtime is None:
            raise HTTPException(
                503,
                "hot reload needs a GraphRuntime in this router",
            )
        from runtime.execution.agents.loader import load_all_agents

        try:
            rebuilt = load_all_agents(runtime)
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(
                400,
                f"agent scan failed: {type(exc).__name__}: {exc}",
            ) from exc
        replaced = 0
        added = 0
        for agent in rebuilt:
            prev = registry.replace(agent)
            if prev is None:
                added += 1
            else:
                replaced += 1
        return {
            "ok": True,
            "replaced": replaced,
            "added": added,
            "total": len(rebuilt),
        }
