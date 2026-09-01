"""Tests for the four mention-driven runtime behaviors:

1. @pack: parsing + skill expansion via capability_router.
2. plugin_auto_load — load+start workflow against a stub PluginHub.
3. agent_auto_delegate — heuristic-driven plan_auto_delegation.
4. mention_history — SQLite-backed cross-thread ranking store.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

# ── 1. @pack support ──────────────────────────────────────


def test_parse_input_mentions_extracts_pack() -> None:
    from runtime.core.cerebrum.input_mentions import parse_input_mentions

    result = parse_input_mentions("use @pack:research and also @skill:web_search")
    assert result.packs == ("research",)
    assert result.skills == ("web_search",)
    assert result.has_any


def test_capability_router_expands_pack_into_skills() -> None:
    """A @pack: mention should pull the pack's skills into priority list."""
    from runtime.core.cerebrum.capability_router import activate_capabilities

    class _Registry:
        def has(self, _name: str) -> bool:
            return True

        def is_enabled(self, _name: str) -> bool:
            return True

    activation = activate_capabilities(
        "kick off the work with @pack:research",
        registry=_Registry(),
    )
    # The "research" pack expands to web_search + fetch_url + bb_*
    assert "web_search" in activation.priority_skills
    assert "fetch_url" in activation.priority_skills
    assert activation.pinned_packs == ("research",)
    assert "pinned" in activation.labels


def test_capability_router_pack_render_includes_pack_name() -> None:
    from runtime.core.cerebrum.capability_router import activate_capabilities

    class _Registry:
        def has(self, _name: str) -> bool:
            return True

        def is_enabled(self, _name: str) -> bool:
            return True

    activation = activate_capabilities(
        "@pack:research",
        registry=_Registry(),
    )
    rendered = activation.render_prompt()
    assert "@pack" in rendered
    assert "`research`" in rendered


# ── 2. Plugin auto-load ───────────────────────────────────


@dataclass
class _StubPlugin:
    state: str = "loaded"


class _StubHub:
    """Pretends to be a PluginHub for the auto-load test."""

    def __init__(
        self, *, already_loaded: dict[str, str] | None = None, will_fail: set[str] | None = None
    ) -> None:
        self.loaded: dict[str, _StubPlugin] = {
            name: _StubPlugin(state=state) for name, state in (already_loaded or {}).items()
        }
        self.started: set[str] = {
            name
            for name, state in (already_loaded or {}).items()
            if state in {"started", "running"}
        }
        self.will_fail = will_fail or set()
        self.load_calls: list[str] = []
        self.start_calls: list[str] = []

    def get_plugin(self, name: str):
        return self.loaded.get(name)

    def load(self, name: str):
        self.load_calls.append(name)
        if name in self.will_fail:
            raise RuntimeError("stub load failure")
        plugin = _StubPlugin(state="loaded")
        self.loaded[name] = plugin
        return plugin

    def start(self, name: str) -> bool:
        self.start_calls.append(name)
        if name not in self.loaded:
            return False
        self.loaded[name].state = "started"
        self.started.add(name)
        return True


def test_auto_load_skips_already_started() -> None:
    from runtime.core.cerebrum.plugin_auto_load import auto_load_pinned_plugins

    hub = _StubHub(already_loaded={"web-search": "started"})
    report = auto_load_pinned_plugins(["web-search"], hub=hub)

    activation = report.activations[0]
    assert activation.was_already_started
    assert not activation.loaded_now
    assert not activation.started_now
    assert hub.load_calls == []
    assert hub.start_calls == []


def test_auto_load_loads_and_starts_new_plugin() -> None:
    from runtime.core.cerebrum.plugin_auto_load import auto_load_pinned_plugins

    hub = _StubHub()
    report = auto_load_pinned_plugins(["web-search"], hub=hub)

    activation = report.activations[0]
    assert not activation.was_already_loaded
    assert activation.loaded_now
    assert activation.started_now
    assert activation.is_available
    assert hub.load_calls == ["web-search"]
    assert hub.start_calls == ["web-search"]


def test_auto_load_captures_load_failure() -> None:
    from runtime.core.cerebrum.plugin_auto_load import auto_load_pinned_plugins

    hub = _StubHub(will_fail={"broken-plugin"})
    report = auto_load_pinned_plugins(["broken-plugin"], hub=hub)

    activation = report.activations[0]
    assert activation.error is not None
    assert "stub load failure" in activation.error
    assert not activation.is_available


def test_auto_load_observation_renders_summary() -> None:
    from runtime.core.cerebrum.plugin_auto_load import auto_load_pinned_plugins

    hub = _StubHub(already_loaded={"existing": "started"})
    report = auto_load_pinned_plugins(["existing", "fresh"], hub=hub)

    obs = report.render_observation()
    assert "Activated pinned plugins" in obs
    assert "`fresh`" in obs
    assert "Already running" in obs
    assert "`existing`" in obs


def test_auto_load_empty_input_short_circuits() -> None:
    from runtime.core.cerebrum.plugin_auto_load import auto_load_pinned_plugins

    report = auto_load_pinned_plugins([])
    assert report.activations == ()
    assert report.render_observation() == ""


# ── 3. Agent auto-delegate ────────────────────────────────


class _StubRegistry:
    def __init__(self, agent_ids: set[str]) -> None:
        self._agents = agent_ids

    def has(self, name: str) -> bool:
        return name in self._agents


def test_auto_delegate_picks_single_agent_with_substantive_prompt() -> None:
    from runtime.core.cerebrum.agent_auto_delegate import plan_auto_delegation

    plan = plan_auto_delegation(
        "@agent:researcher_v1 please look into the latest market data "
        "for our top three competitors",
        registry=_StubRegistry({"researcher_v1"}),
    )
    assert plan.should_delegate
    assert plan.target_agent == "researcher_v1"
    assert "@agent:researcher_v1" not in plan.cleaned_prompt


def test_auto_delegate_skips_when_multiple_agent_pins() -> None:
    from runtime.core.cerebrum.agent_auto_delegate import plan_auto_delegation

    plan = plan_auto_delegation(
        "@agent:a help and @agent:b also help",
        registry=_StubRegistry({"a", "b"}),
    )
    assert not plan.should_delegate
    assert "orchestration" in plan.reason


def test_auto_delegate_skips_when_agent_not_in_registry() -> None:
    from runtime.core.cerebrum.agent_auto_delegate import plan_auto_delegation

    plan = plan_auto_delegation(
        "@agent:nonexistent please draft a long detailed report",
        registry=_StubRegistry({"someone_else"}),
    )
    assert not plan.should_delegate
    assert "not in registry" in plan.reason


def test_auto_delegate_skips_when_competing_skill_pin() -> None:
    from runtime.core.cerebrum.agent_auto_delegate import plan_auto_delegation

    plan = plan_auto_delegation(
        "@agent:a please use @skill:deep-research to investigate the issue",
        registry=_StubRegistry({"a"}),
    )
    assert not plan.should_delegate
    assert "competing" in plan.reason


def test_auto_delegate_skips_for_fan_out_wording() -> None:
    from runtime.core.cerebrum.agent_auto_delegate import plan_auto_delegation

    plan = plan_auto_delegation(
        "@agent:a compare with the other agents and give me consensus",
        registry=_StubRegistry({"a"}),
    )
    assert not plan.should_delegate
    assert "fan-out" in plan.reason


def test_auto_delegate_skips_when_prompt_is_just_the_mention() -> None:
    from runtime.core.cerebrum.agent_auto_delegate import plan_auto_delegation

    plan = plan_auto_delegation(
        "@agent:a hi",  # trivially short
        registry=_StubRegistry({"a"}),
    )
    assert not plan.should_delegate


# ── 4. Mention history ────────────────────────────────────


@pytest.fixture
def history_store(tmp_path):
    from runtime.memory.users.mention_history import (
        MentionHistoryStore,
        reset_mention_history_store_for_tests,
    )

    reset_mention_history_store_for_tests()
    yield MentionHistoryStore(tmp_path / "history.sqlite")
    reset_mention_history_store_for_tests()


def test_mention_history_records_and_reads(history_store):
    history_store.record("user-1", "skill", "deep-research", ts=time.time())
    history_store.record("user-1", "skill", "deep-research", ts=time.time())
    history_store.record("user-1", "skill", "web_search", ts=time.time())

    top = history_store.top_for_actor("user-1", kind="skill")
    assert top[0].identifier == "deep-research"
    assert top[0].count == 2
    assert top[1].identifier == "web_search"
    assert top[1].count == 1


def test_mention_history_per_actor_isolation(history_store):
    history_store.record("alice", "agent", "researcher", ts=time.time())
    history_store.record("bob", "agent", "coder", ts=time.time())

    alice_top = history_store.top_for_actor("alice")
    bob_top = history_store.top_for_actor("bob")

    assert [s.identifier for s in alice_top] == ["researcher"]
    assert [s.identifier for s in bob_top] == ["coder"]


def test_mention_history_has_used(history_store):
    assert not history_store.has_used("u", "plugin", "p")
    history_store.record("u", "plugin", "p", ts=time.time())
    assert history_store.has_used("u", "plugin", "p")


def test_mention_history_normalizes_empty_actor(history_store):
    history_store.record("", "skill", "x", ts=time.time())
    top = history_store.top_for_actor("")
    assert len(top) == 1
    assert top[0].actor == "anonymous"


def test_mention_history_rejects_unknown_kind(history_store):
    history_store.record("u", "weird-kind", "x", ts=time.time())
    assert history_store.top_for_actor("u") == []


def test_mention_history_record_batch(history_store):
    ts = time.time()
    history_store.record_batch(
        "u",
        [
            ("skill", "a"),
            ("skill", "b"),
            ("agent", "c"),
            ("pack", "d"),
        ],
        ts=ts,
    )
    top = history_store.top_for_actor("u", limit=10)
    identifiers = {stat.identifier for stat in top}
    assert identifiers == {"a", "b", "c", "d"}
