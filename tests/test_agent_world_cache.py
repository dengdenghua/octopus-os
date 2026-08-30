"""Regression coverage for the mtime-aware local agent market cache."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from runtime.sensing.gateway import _agent_world_helpers as helpers


def _write_agent(root: Path, agent_id: str, display_name: str) -> Path:
    agent_dir = root / agent_id
    (agent_dir / "agent-core").mkdir(parents=True)
    profile = agent_dir / "profile.jsonc"
    profile.write_text(
        json.dumps(
            {
                "id": agent_id,
                "name": display_name,
                "description": f"{display_name} description",
                "tags": ["cached"],
            }
        ),
        "utf-8",
    )
    (agent_dir / "agent-core" / "tool-registry.jsonc").write_text(
        json.dumps({"private_skills": ["first-skill"]}),
        "utf-8",
    )
    return profile


def _advance_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


def test_local_agent_cache_reuses_parse_and_returns_isolated_results(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    _write_agent(agents_root, "alpha", "Alpha")
    monkeypatch.setattr(helpers, "default_agents_root", lambda: agents_root)

    parse_calls = 0
    real_parse = helpers._parse_jsonc

    def counted_parse(value: str) -> Any:
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(value)

    monkeypatch.setattr(helpers, "_parse_jsonc", counted_parse)

    first = helpers._list_local_agents()
    calls_after_first_scan = parse_calls
    first[0]["tags"].append("caller-mutation")

    second = helpers._list_local_agents()

    assert parse_calls == calls_after_first_scan
    assert second[0]["tags"] == ["cached"]


def test_local_agent_cache_invalidates_for_profile_tools_visuals_and_new_agent(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    profile = _write_agent(agents_root, "alpha", "Alpha")
    monkeypatch.setattr(helpers, "default_agents_root", lambda: agents_root)

    assert helpers._list_local_agents()[0]["display_name"] == "Alpha"

    profile.write_text(
        json.dumps({"id": "alpha", "name": "Alpha Updated", "tags": ["updated"]}),
        "utf-8",
    )
    _advance_mtime(profile)
    assert helpers._list_local_agents()[0]["display_name"] == "Alpha Updated"

    tool_registry = agents_root / "alpha" / "agent-core" / "tool-registry.jsonc"
    tool_registry.write_text(json.dumps({"private_skills": ["new-skill"]}), "utf-8")
    _advance_mtime(tool_registry)
    assert helpers._list_local_agents()[0]["private_skills"] == ["new-skill"]

    avatar = agents_root / "alpha" / "avatar.png"
    avatar.write_bytes(b"avatar")
    _advance_mtime(avatar)
    assert helpers._list_local_agents()[0]["avatar_url"] is not None

    visual = agents_root / "alpha" / "visuals" / "front.png"
    visual.parent.mkdir()
    visual.write_bytes(b"visual")
    _advance_mtime(visual)
    assert "front" in helpers._list_local_agents()[0]["visual_urls"]

    _write_agent(agents_root, "beta", "Beta")
    assert {agent["id"] for agent in helpers._list_local_agents()} == {"alpha", "beta"}

