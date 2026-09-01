"""
Pure helper functions for the agents router.

Split out of ``agents_router.py`` (pure structural refactor — no logic
changes). Imported back by ``agents_router.py`` and shared with the
endpoint-handler submodule ``_agents_endpoints.py``.

These are the stateless helpers: agent-id / path safety checks, soul /
avatar / visual-url resolution, and the wire-model converters
(``_to_wire`` / ``_to_detail_wire`` / ``_group_to_wire``). They touch no
closure state and no router state, so they can live at module level
without changing behavior.
"""

from __future__ import annotations

import contextlib
import re
import shutil
from pathlib import Path
from typing import Any

try:
    from fastapi import HTTPException

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    HTTPException = None  # type: ignore[assignment, misc]

from runtime.sensing.gateway.agents_models import (
    AgentDetailWire,
    AgentWire,
    ArmWire,
    GroupWire,
)

_PERSONA_RE = re.compile(
    r"^##\s+Persona\s*\n(.*?)(?=^##\s+|\Z)",
    re.DOTALL | re.MULTILINE,
)

_BUILTIN_AGENT_IDS = frozenset({"general", "coder", "admin", "desktop_operator"})
_SAFE_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _require_safe_agent_id(agent_id: str) -> str:
    value = str(agent_id or "").strip()
    if not _SAFE_AGENT_ID_RE.fullmatch(value):
        raise HTTPException(
            400,
            "invalid agent_id: only alphanumeric characters, hyphens, and underscores are allowed",
        )
    return value


def _agent_dir_for(root: Path, agent_id: str) -> Path:
    safe_id = _require_safe_agent_id(agent_id)
    return root / safe_id


def _require_real_agent_dir(root: Path, agent_id: str) -> Path:
    agent_dir = _agent_dir_for(root, agent_id)
    if agent_dir.is_symlink():
        raise HTTPException(409, f"agent path is not a real directory: {agent_id}")
    if not agent_dir.is_dir():
        raise HTTPException(404, f"agent folder not found: {agent_id}")
    return agent_dir


def _cleanup_created_agent_dir(agent_dir: Path, *, created: bool) -> None:
    if created and agent_dir.is_dir() and not agent_dir.is_symlink():
        shutil.rmtree(agent_dir, ignore_errors=True)


def _restore_text_file(path: Path, original: str | None) -> None:
    if original is None:
        with contextlib.suppress(OSError):
            path.unlink()
        return
    from runtime.platform.io import atomic_write_text

    atomic_write_text(path, original, newline=None)


def _soul_for_display(soul: str | None) -> str | None:
    """Return only the user-facing persona paragraph.

    Strips the HARD SYSTEM RULE banner, Identity, Working Rules,
    MEMORY/USER, and REMINDER sections — those are all internal
    LLM scaffolding and should never appear in user-facing UI.
    """
    if not soul:
        return None
    m = _PERSONA_RE.search(soul)
    if m:
        body = m.group(1).strip()
        return body or None
    # Legacy / hand-rolled soul without "## Persona" header:
    # strip the banner prefix and any REMINDER tail, return the rest.
    txt = soul
    if txt.lstrip().startswith("# HARD SYSTEM RULE"):
        # drop everything up to first "---" divider (banner ends with ---)
        parts = txt.split("\n---\n", 1)
        if len(parts) == 2:
            txt = parts[1]
    # drop REMINDER tail
    txt = re.split(r"^##\s+REMINDER\b", txt, maxsplit=1, flags=re.MULTILINE)[0]
    txt = txt.strip()
    return txt or None


def _avatar_url_for(agent_id: str) -> str | None:
    """Return a URL the UI can <img> load, or None if no avatar on disk.

    Convention: ``agents/<id>/avatar.{png,webp,jpg,svg}`` · served by the
    ``GET /api/agents/{id}/avatar`` route below. Keeping the check
    cheap (Path.is_file, no read) so every list-agents response
    doesn't hit the disk hard. If the file appears/disappears at
    runtime the next list call reflects it · no caching.
    """
    from runtime.execution.agents.loader import default_agents_root

    try:
        root = default_agents_root()
    except (OSError, ImportError):
        return None
    agent_dir = root / agent_id
    profile_path = agent_dir / "profile.jsonc"
    profile_data: dict[str, Any] = {}
    if profile_path.is_file():
        try:
            from runtime.platform.process.utils import parse_jsonc

            parsed_profile = parse_jsonc(profile_path.read_text(encoding="utf-8"))
            if isinstance(parsed_profile, dict):
                profile_data = parsed_profile
            if "avatar" in profile_data and (
                profile_data.get("avatar") is None or profile_data.get("avatar") is False
            ):
                return None
        except (OSError, ValueError, TypeError):  # noqa: BLE001 — best-effort profile parse; fall through to file-extension detection
            pass
    for ext in ("png", "webp", "jpg", "jpeg", "svg"):
        path = agent_dir / f"avatar.{ext}"
        if path.is_file():
            return f"/api/agents/{agent_id}/avatar?v={int(path.stat().st_mtime)}"
    return None


def _agent_visual_urls_for(agent_id: str) -> dict[str, str]:
    from runtime.execution.agents.loader import default_agents_root

    try:
        root = default_agents_root()
    except (OSError, ImportError):
        return {}

    visuals_dir = root / agent_id / "visuals"
    urls: dict[str, str] = {}
    for view in ("front", "side", "back"):
        for ext in ("png", "jpg", "jpeg", "webp", "svg"):
            path = visuals_dir / f"{view}.{ext}"
            if path.is_file():
                urls[view] = f"/api/agents/{agent_id}/visuals/{view}?v={int(path.stat().st_mtime)}"
                break

    reference = visuals_dir / "reference.png"
    if reference.is_file():
        version = int(reference.stat().st_mtime)
        for view in ("front", "side", "back"):
            urls.setdefault(
                view,
                f"/api/agents/{agent_id}/visuals/{view}?v={version}",
            )
    return urls


def _identity_metadata_for(agent_id: str) -> tuple[str | None, dict[str, Any]]:
    """Read public identity metadata without adding it to the runtime Agent."""
    from runtime.execution.agents.loader import default_agents_root
    from runtime.platform.process.utils import parse_jsonc

    profile_path = default_agents_root() / agent_id / "profile.jsonc"
    try:
        profile = parse_jsonc(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        profile = {}
    identity = profile.get("identity")
    identity_profile = dict(identity) if isinstance(identity, dict) else {}
    code = str(
        profile.get("identity_code") or identity_profile.get("code") or profile.get("did") or ""
    ).strip()
    if not code:
        from runtime.execution.agents.identity import build_identity_profile, legacy_identity_code

        code = legacy_identity_code(agent_id)
        identity_profile = build_identity_profile(code)
        identity_profile["derived_for_legacy_profile"] = True
    return code, identity_profile


def _to_wire(agent: Any, *, include_visuals: bool = True) -> AgentWire:
    tool_groups = [str(arm.arm_id) for arm in agent.arms]
    # The roster endpoint is latency-sensitive and may contain hundreds of
    # agents. Its compact mode avoids parsing every profile and probing up to
    # twenty visual files per agent. The stable avatar endpoint lets the UI
    # load only the handful of rows it actually renders and fall back on 404.
    avatar_url = (
        _avatar_url_for(agent.agent_id)
        if include_visuals
        else f"/api/agents/{agent.agent_id}/avatar"
    )
    identity_code, identity_profile = _identity_metadata_for(agent.agent_id)
    return AgentWire(
        name=agent.agent_id,
        display_name=agent.display_name or None,
        description=agent.description,
        icon=agent.icon or None,
        avatar_url=avatar_url,
        visual_urls=_agent_visual_urls_for(agent.agent_id) if include_visuals else {},
        model=agent.model,
        tool_groups=tool_groups or None,
        soul=_soul_for_display(agent.soul),
        capabilities=dict(getattr(agent, "capabilities", {}) or {}),
        identity_code=identity_code,
        identity_profile=identity_profile,
    )


def _to_detail_wire(agent: Any) -> AgentDetailWire:
    try:
        skill_policy_obj = agent.skill_policy()
        allowed_skills = skill_policy_obj.as_list()
        skill_policy = {
            "allowed": allowed_skills,
            "sources": {source: list(names) for source, names in skill_policy_obj.sources.items()},
            "reason_map": {
                name: list(sources) for name, sources in skill_policy_obj.reason_map.items()
            },
            "allow_all": skill_policy_obj.allow_all,
        }
    except (AttributeError, TypeError, ValueError):
        allowed_skills = agent.allowed_skill_union()
        skill_policy = {}
    arms_wire = [
        ArmWire(
            arm_id=str(arm.arm_id),
            display_name=getattr(arm, "display_name", "") or "",
            description=getattr(arm, "description", "") or "",
            affinity=list(arm.affinity),
            icon=getattr(arm, "icon", "") or "",
        )
        for arm in agent.arms
    ]
    identity_code, identity_profile = _identity_metadata_for(agent.agent_id)
    return AgentDetailWire(
        name=agent.agent_id,
        display_name=agent.display_name or None,
        description=agent.description,
        icon=agent.icon or None,
        avatar_url=_avatar_url_for(agent.agent_id),
        visual_urls=_agent_visual_urls_for(agent.agent_id),
        model=agent.model,
        tool_groups=[a.arm_id for a in arms_wire] or None,
        soul=_soul_for_display(agent.soul),
        capabilities=dict(getattr(agent, "capabilities", {}) or {}),
        arms=arms_wire,
        allowed_skills=allowed_skills,
        skill_policy=skill_policy,
        extra_affinity=list(agent.extra_affinity),
        budget=dict(getattr(agent, "budget", {}) or {}),
        identity_code=identity_code,
        identity_profile=identity_profile,
    )


def _group_to_wire(g: Any) -> GroupWire:
    return GroupWire(
        group_id=g.group_id,
        display_name=g.display_name,
        description=g.description,
        members=list(g.members),
        created_at=g.created_at.isoformat() if g.created_at else "",
        updated_at=g.updated_at.isoformat() if g.updated_at else "",
    )
