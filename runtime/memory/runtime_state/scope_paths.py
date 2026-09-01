"""Filesystem paths for scoped long-term memory layers."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_PATH_SEGMENT_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')


def safe_path_segment(value: str, fallback: str) -> str:
    clean = _PATH_SEGMENT_RE.sub("_", value.strip())
    clean = re.sub(r"\s+", "-", clean).strip(" .")
    return clean or fallback


def global_memory_path() -> Path:
    echo_home = os.environ.get("ECHO_HOME")
    root = Path(echo_home).expanduser() if echo_home else Path.home() / ".echo"
    return root / "MEMORY.md"


def project_root_from_metadata(metadata: dict[str, Any] | None = None) -> Path:
    """Resolve the repo/project root from metadata or the discovered project root."""
    metadata = metadata or {}
    workspace_path = metadata.get("workspace_path")
    if isinstance(workspace_path, str) and workspace_path.strip():
        candidate = Path(workspace_path.strip()).expanduser()
        if candidate.is_absolute():
            return candidate.parent if candidate.exists() and candidate.is_file() else candidate
    from runtime.platform.process.paths import project_root

    return project_root()


def project_memory_path(metadata: dict[str, Any] | None = None) -> Path:
    return project_root_from_metadata(metadata) / ".echo" / "MEMORY.md"


def agent_memory_path(repo_root: Path, agent_id: str) -> Path:
    return repo_root / "agents" / safe_path_segment(agent_id, "agent") / "agent-core" / "MEMORY.md"


def team_memory_path(repo_root: Path, team_id: str) -> Path:
    return repo_root / "teams" / safe_path_segment(team_id, "team") / "team-core" / "MEMORY.md"


def team_agent_memory_path(repo_root: Path, team_id: str, agent_id: str) -> Path:
    return (
        repo_root
        / "teams"
        / safe_path_segment(team_id, "team")
        / "agents"
        / safe_path_segment(agent_id, "agent")
        / "MEMORY.md"
    )


def scoped_memory_path(
    scope: str,
    *,
    repo_root: Path,
    agent_id: str,
    metadata: dict[str, Any] | None = None,
) -> Path:
    metadata = metadata or {}
    normalized = normalize_memory_scope(scope)
    if normalized == "global":
        return global_memory_path()
    if normalized == "project":
        return project_memory_path(metadata)
    if normalized == "agent":
        return agent_memory_path(repo_root, agent_id)
    team_id = metadata.get("team_id")
    if not isinstance(team_id, str) or not team_id.strip():
        raise RuntimeError(f"memory scope {normalized!r} requires session.metadata.team_id")
    if normalized == "team":
        return team_memory_path(repo_root, team_id.strip())
    if normalized == "team_agent":
        return team_agent_memory_path(repo_root, team_id.strip(), agent_id)
    raise RuntimeError(f"unknown memory scope {scope!r}")


def normalize_memory_scope(scope: str | None) -> str:
    value = (scope or "agent").strip().lower().replace("-", "_")
    aliases = {
        "": "agent",
        "auto": "agent",
        "self": "agent",
        "member": "team_agent",
        "teamagent": "team_agent",
        "team_agent": "team_agent",
        "team_member": "team_agent",
        "repo": "project",
        "workspace": "project",
        "user": "global",
    }
    return aliases.get(value, value)


def visible_memory_tier_paths(
    *,
    repo_root: Path,
    agent_id: str,
    metadata: dict[str, Any] | None = None,
) -> list[tuple[str, Path]]:
    metadata = metadata or {}
    tiers: list[tuple[str, Path]] = [
        ("global", global_memory_path()),
        ("project", project_memory_path(metadata)),
    ]
    team_id = metadata.get("team_id")
    if isinstance(team_id, str) and team_id.strip():
        clean_team_id = team_id.strip()
        tiers.extend(
            [
                ("team", team_memory_path(repo_root, clean_team_id)),
                ("team-agent", team_agent_memory_path(repo_root, clean_team_id, agent_id)),
            ]
        )
    tiers.append(("agent", agent_memory_path(repo_root, agent_id)))
    return tiers
