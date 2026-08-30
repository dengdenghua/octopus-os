"""parse_template / instantiate split in the agent loader.

``load_agent`` is now a thin composition of ``parse_template`` (pure —
reads the folder into a runtime-independent ``AgentTemplate``) and
``instantiate`` (builds the live arms against a ``GraphRuntime``). These
tests lock the split: parsing needs no runtime, the template captures the
profile faithfully, and the two halves compose back into the same result.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from runtime.execution.agents.loader import (
    AgentTemplate,
    load_agent,
    parse_template,
)


def _write_agent(root: Path, agent_id: str, profile: dict, tool_registry: dict | None = None):
    agent_dir = root / agent_id
    (agent_dir / "agent-core").mkdir(parents=True)
    (agent_dir / "profile.jsonc").write_text(json.dumps(profile), encoding="utf-8")
    if tool_registry is not None:
        (agent_dir / "agent-core" / "tool-registry.jsonc").write_text(
            json.dumps(tool_registry), encoding="utf-8"
        )
    return agent_dir


def test_parse_template_reads_fields_without_a_runtime(tmp_path: Path):
    agent_dir = _write_agent(
        tmp_path,
        "scout",
        {
            "id": "scout",
            "name": "Scout",
            "description": "recon agent",
            "icon": "🔭",
            "model": {"provider": "auto", "name": "auto"},
            "capabilities": {"web": True},
            "budget": {"max_tokens": 1000},
        },
        {"arms": ["read_arm"], "extra_affinity": ["search"], "private_skills": ["peek"]},
    )
    # No runtime argument — parsing is runtime-independent.
    tmpl = parse_template(agent_dir, tmp_path / "_shared")
    assert isinstance(tmpl, AgentTemplate)
    assert tmpl.agent_id == "scout"
    assert tmpl.display_name == "Scout"
    assert tmpl.description == "recon agent"
    assert tmpl.icon == "🔭"
    assert tmpl.model is None  # auto → no preference
    assert tmpl.arm_ids == ["read_arm"]
    assert tmpl.affinity == ["search"]
    assert tmpl.private_skills == ["peek"]
    assert tmpl.capabilities == {"web": True}
    assert tmpl.budget == {"max_tokens": 1000}
    assert isinstance(tmpl.soul, str)  # composed, even if empty


def test_parse_template_defaults_id_to_dir_name(tmp_path: Path):
    agent_dir = _write_agent(tmp_path, "fallback", {"name": "No Id Here"})
    tmpl = parse_template(agent_dir, tmp_path / "_shared")
    assert tmpl.agent_id == "fallback"  # falls back to the directory name
    assert tmpl.arm_ids == []  # no tool-registry.jsonc → empty arms


def test_parse_template_honors_concrete_model(tmp_path: Path):
    agent_dir = _write_agent(
        tmp_path,
        "picky",
        {"model": {"provider": "anthropic", "name": "claude-x"}},
    )
    tmpl = parse_template(agent_dir, tmp_path / "_shared")
    assert tmpl.model == "anthropic/claude-x"


def test_parse_template_missing_profile_raises(tmp_path: Path):
    (tmp_path / "ghost").mkdir()
    with pytest.raises(FileNotFoundError):
        parse_template(tmp_path / "ghost", tmp_path / "_shared")


def test_template_is_frozen(tmp_path: Path):
    agent_dir = _write_agent(tmp_path, "immutable", {"id": "immutable"})
    tmpl = parse_template(agent_dir, tmp_path / "_shared")
    with pytest.raises(dataclasses.FrozenInstanceError):
        tmpl.agent_id = "hacked"  # type: ignore[misc]


def test_load_agent_composes_parse_and_instantiate(tmp_path: Path, monkeypatch):
    # load_agent(dir, rt, shared) must equal instantiate(parse_template(dir,
    # shared), rt) — same parse, same build. We stub both halves to prove
    # the composition wiring without needing a real GraphRuntime.
    import runtime.execution.agents.loader as loader

    sentinel_template = object()
    sentinel_agent = object()
    sentinel_runtime = object()
    calls = {}

    def fake_parse(agent_dir, shared_dir):
        calls["parse"] = (agent_dir, shared_dir)
        return sentinel_template

    def fake_instantiate(template, runtime):
        calls["instantiate"] = (template, runtime)
        return sentinel_agent

    monkeypatch.setattr(loader, "parse_template", fake_parse)
    monkeypatch.setattr(loader, "instantiate", fake_instantiate)

    result = load_agent(Path("/a/dir"), sentinel_runtime, Path("/shared"))
    assert result is sentinel_agent
    assert calls["parse"] == (Path("/a/dir"), Path("/shared"))
    assert calls["instantiate"] == (sentinel_template, sentinel_runtime)

