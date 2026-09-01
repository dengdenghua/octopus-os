"""
Tests for the three-tier memory loader (global / project / agent).

The loader stacks memory at three scopes, with higher tiers
overriding lower tiers by composition (newer / more specific content
takes priority for weak models):

    ~/.echo/MEMORY.md              · global
    <CWD>/.echo/MEMORY.md          · project
    agents/<id>/agent-core/MEMORY.md  · agent

These tests pin:

1. All three tiers stack when present (not replace)
2. Missing tiers are silently skipped (doesn't crash / empty-pollute)
3. Template-only content is skipped at every tier
4. ``$ECHO_HOME`` overrides the global root (test isolation)
5. Back-compat · existing agents with only agent-tier memory still work
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runtime.execution.agents.loader import (
    _compose_soul,
    _memory_tier_paths,
)

# ═══════════════════════════════════════════════════════════
# _memory_tier_paths · pure function
# ═══════════════════════════════════════════════════════════


class TestMemoryTierPaths:
    def test_returns_three_tiers_in_priority_order(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Priority order: global (lowest / recency-bias-weakest) →
        project → agent (highest / closest to LLM's output window)."""
        monkeypatch.setenv("ECHO_HOME", str(tmp_path / "user-home"))
        (tmp_path / "some-repo").mkdir()
        monkeypatch.chdir(tmp_path / "some-repo")

        agent_dir = tmp_path / "agents" / "test-agent"
        core = agent_dir / "agent-core"
        core.mkdir(parents=True)

        tiers = _memory_tier_paths(agent_dir, core)
        assert [t[0] for t in tiers] == ["global", "project", "agent"]

        # Each tier points at the expected MEMORY.md location
        assert tiers[0][1] == tmp_path / "user-home" / "MEMORY.md"
        assert tiers[1][1] == tmp_path / "some-repo" / ".echo" / "MEMORY.md"
        assert tiers[2][1] == core / "MEMORY.md"

    def test_echo_home_env_overrides_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """$ECHO_HOME is how tests keep the global tier
        isolated from the developer's real ~/.echo."""
        monkeypatch.setenv("ECHO_HOME", str(tmp_path / "custom"))
        tiers = _memory_tier_paths(tmp_path / "a", tmp_path / "a" / "c")
        global_path = tiers[0][1]
        assert global_path == tmp_path / "custom" / "MEMORY.md"

    def test_default_falls_back_to_home_dot_echo(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Without $ECHO_HOME · resolves to ``~/.echo``."""
        monkeypatch.delenv("ECHO_HOME", raising=False)
        tiers = _memory_tier_paths(tmp_path / "a", tmp_path / "a" / "c")
        global_path = tiers[0][1]
        assert ".echo" in str(global_path)
        assert "MEMORY.md" in str(global_path)


# ═══════════════════════════════════════════════════════════
# Three-tier stacking via _compose_soul
# ═══════════════════════════════════════════════════════════


def _setup_agent_dir(
    tmp_path: Path,
    *,
    soul: str = "I am a test agent.",
    agent_id: str = "test",
    display_name: str = "Test Agent",
) -> tuple[Path, Path]:
    """Minimal agent-dir layout · returns (agent_dir, core)."""
    agent_dir = tmp_path / "agents" / agent_id
    core = agent_dir / "agent-core"
    core.mkdir(parents=True)
    (agent_dir / "profile.jsonc").write_text(
        f'{{"id":"{agent_id}","name":"{display_name}"}}',
        encoding="utf-8",
    )
    (core / "SOUL.md").write_text(soul, encoding="utf-8")
    return agent_dir, core


def _setup_shared_dir(tmp_path: Path) -> Path:
    shared = tmp_path / "agents" / "_shared"
    shared.mkdir(parents=True)
    (shared / "IDENTITY_BANNER.md").write_text(
        "# HARD SYSTEM RULE\n\n(banner stub)\n\n---\n",
        encoding="utf-8",
    )
    return shared


class TestThreeTierStacking:
    def test_identity_banner_uses_agent_display_name(
        self,
        tmp_path: Path,
    ):
        agent_dir, _core = _setup_agent_dir(
            tmp_path,
            agent_id="coder",
            display_name="Coder",
        )
        shared = _setup_shared_dir(tmp_path)

        soul = _compose_soul(
            agent_dir,
            shared,
            profile={"id": "coder", "name": "Coder"},
        )

        assert "You are **Coder**" in soul
        assert '"我是 Coder"' in soul
        assert "I'm Coder" in soul
        assert '"我是 Echo"' not in soul
        assert "I'm Echo" not in soul

    def test_all_three_tiers_appear_when_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        # Populate all three tiers
        global_mem = tmp_path / "home" / "MEMORY.md"
        global_mem.parent.mkdir(parents=True)
        global_mem.write_text(
            "User prefers concise answers.",
            encoding="utf-8",
        )
        project_mem = repo / ".echo" / "MEMORY.md"
        project_mem.parent.mkdir()
        project_mem.write_text(
            "This repo uses pytest.",
            encoding="utf-8",
        )

        agent_dir, core = _setup_agent_dir(tmp_path)
        (core / "MEMORY.md").write_text(
            "The agent has learned to prefer absolute paths.",
            encoding="utf-8",
        )
        shared = _setup_shared_dir(tmp_path)

        soul = _compose_soul(agent_dir, shared)

        # All three tier labels appear · in priority order
        assert "Long-term Memory (global)" in soul
        assert "Long-term Memory (project)" in soul
        assert "Long-term Memory (agent)" in soul

        # Content present
        assert "concise answers" in soul
        assert "uses pytest" in soul
        assert "absolute paths" in soul

        # Order: global before project before agent · mirrors
        # the priority list so recency bias favors agent memory
        gi = soul.index("(global)")
        pi = soul.index("(project)")
        ai = soul.index("(agent)")
        assert gi < pi < ai

    def test_missing_tiers_silently_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Most common case: only agent-tier memory present ·
        nothing in ~/.echo or repo/.echo · everything still
        works and just the one tier shows up."""
        monkeypatch.setenv("ECHO_HOME", str(tmp_path / "nohome"))
        monkeypatch.chdir(tmp_path)

        agent_dir, core = _setup_agent_dir(tmp_path)
        (core / "MEMORY.md").write_text(
            "agent memory only",
            encoding="utf-8",
        )
        shared = _setup_shared_dir(tmp_path)

        soul = _compose_soul(agent_dir, shared)

        # agent tier appears · other two do not
        assert "Long-term Memory (agent)" in soul
        assert "Long-term Memory (global)" not in soul
        assert "Long-term Memory (project)" not in soul

    def test_template_only_tier_is_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A tier containing only the scaffold ("no memories yet"
        placeholder + html comments) must not inject a useless
        heading into the soul."""
        monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
        monkeypatch.chdir(tmp_path)

        # Global = real content · project = template scaffold
        global_mem = tmp_path / "home" / "MEMORY.md"
        global_mem.parent.mkdir(parents=True)
        global_mem.write_text("real global memory", encoding="utf-8")

        project_mem = tmp_path / ".echo" / "MEMORY.md"
        project_mem.parent.mkdir()
        project_mem.write_text(
            "# Memory\n\n<!-- auto-generated · do not edit -->\n_No memories yet._\n",
            encoding="utf-8",
        )

        agent_dir, core = _setup_agent_dir(tmp_path)
        shared = _setup_shared_dir(tmp_path)

        soul = _compose_soul(agent_dir, shared)
        assert "Long-term Memory (global)" in soul
        # Project skipped · template-only
        assert "Long-term Memory (project)" not in soul

    def test_include_memory_md_flag_disables_all_three(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """``profile.systemPrompt.includeMemoryMd: false`` must
        suppress ALL three tiers · not just the agent one.
        Opt-out is all-or-nothing by design · anyone who wants
        per-tier control can PR a new flag."""
        monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
        monkeypatch.chdir(tmp_path)

        global_mem = tmp_path / "home" / "MEMORY.md"
        global_mem.parent.mkdir(parents=True)
        global_mem.write_text("global", encoding="utf-8")

        agent_dir, core = _setup_agent_dir(tmp_path)
        (core / "MEMORY.md").write_text("agent", encoding="utf-8")
        shared = _setup_shared_dir(tmp_path)

        soul = _compose_soul(
            agent_dir,
            shared,
            profile={"systemPrompt": {"includeMemoryMd": False}},
        )
        assert "Long-term Memory" not in soul


# ═══════════════════════════════════════════════════════════
# Back-compat
# ═══════════════════════════════════════════════════════════


class TestBackCompat:
    def test_old_agent_with_only_agent_memory_still_works(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Agents that existed before this tier-expansion had only
        ``agent-core/MEMORY.md``. They should keep producing the
        same-shaped soul (just with a new ``(agent)`` suffix in
        the heading · acceptable drift)."""
        monkeypatch.setenv("ECHO_HOME", str(tmp_path / "never"))
        (tmp_path / "empty-repo").mkdir()
        monkeypatch.chdir(tmp_path / "empty-repo")

        agent_dir, core = _setup_agent_dir(tmp_path)
        (core / "MEMORY.md").write_text(
            "· learned X\n· learned Y\n",
            encoding="utf-8",
        )
        shared = _setup_shared_dir(tmp_path)

        soul = _compose_soul(agent_dir, shared)
        assert "learned X" in soul
        assert "learned Y" in soul
