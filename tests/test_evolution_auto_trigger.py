from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from runtime.platform.ui._app_agents import mount_agents
from runtime.platform.ui._app_context import AppContext
from runtime.safety.evolution import auto_trigger
from runtime.safety.evolution.auto_trigger import (
    AutoTriggerConfig,
    EvolutionAutoTrigger,
    resolve_evolution_agent_ids,
)


class _Registry:
    def __init__(self, *agent_ids: str) -> None:
        self._agent_ids = list(agent_ids)

    def all_ids(self) -> list[str]:
        return list(self._agent_ids)


def _stack(name: str = "my-echo") -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(name=name))


def test_resolver_uses_all_scored_registered_agents_not_application_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scored = {"coder", "researcher"}
    monkeypatch.setattr(
        auto_trigger,
        "_has_score_history",
        lambda agent_id: agent_id in scored,
    )

    resolved = resolve_evolution_agent_ids(
        _stack(),
        agent_registry=_Registry("coder", "general", "researcher"),
    )

    assert resolved == ("coder", "researcher")
    assert "my-echo" not in resolved


def test_resolver_honours_explicit_allowlist_and_fails_closed_for_unsafe_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[str] = []

    def _has_history(agent_id: str) -> bool:
        checked.append(agent_id)
        return True

    monkeypatch.setattr(auto_trigger, "_has_score_history", _has_history)
    registry = _Registry("coder", "researcher", "../outside")

    assert resolve_evolution_agent_ids(
        _stack(),
        agent_registry=registry,
        configured_agent_ids=("researcher", "unknown", "../outside"),
    ) == ("researcher",)
    assert checked == ["researcher"]

    checked.clear()
    assert (
        resolve_evolution_agent_ids(
            _stack(),
            agent_registry=registry,
            configured_agent_ids=("../outside",),
        )
        == ()
    )
    assert checked == []

    # Registry identity is exact: whitespace must not be stripped into a
    # different, unregistered agent id.
    assert (
        resolve_evolution_agent_ids(
            _stack(),
            agent_registry=_Registry(" coder "),
        )
        == ()
    )
    assert checked == []


def test_resolver_preserves_score_backed_legacy_single_agent_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[str] = []

    def _has_history(agent_id: str) -> bool:
        checked.append(agent_id)
        return agent_id == "coder"

    monkeypatch.setattr(auto_trigger, "_has_score_history", _has_history)

    assert resolve_evolution_agent_ids(_stack("coder")) == ("coder",)
    assert resolve_evolution_agent_ids(_stack("my-echo")) == ()
    assert resolve_evolution_agent_ids(_stack("../../outside")) == ()
    assert checked == ["coder", "my-echo"]


def test_tick_checks_each_resolved_agent_and_evolves_only_low_fitness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.evolution import fitness

    monkeypatch.setattr(auto_trigger, "_has_score_history", lambda _agent_id: True)
    computed: list[tuple[str, int]] = []

    def _compute(
        agent_id: str,
        config: Any,
        *,
        publish_event: bool = True,
    ) -> SimpleNamespace:
        computed.append((agent_id, config.window))
        combined = 0.3 if agent_id == "coder" else 0.8
        assert publish_event is False
        # Even a mixed-version implementation that still emits synchronously
        # must not make the same low-fitness observation evolve twice.
        trigger._on_fitness_event(
            SimpleNamespace(agent_id=agent_id, combined_score=combined),
        )
        return SimpleNamespace(combined=combined)

    monkeypatch.setattr(fitness, "compute_fitness", _compute)

    trigger = EvolutionAutoTrigger()
    trigger._stack = _stack()
    trigger._agent_registry = _Registry("coder", "researcher")
    trigger._config = AutoTriggerConfig(
        fitness_threshold=0.5,
        fitness_window=7,
        drift_critical_auto_rollback=False,
    )
    trigger._active = True
    evolved: list[str] = []
    monkeypatch.setattr(trigger, "_trigger_evolve", evolved.append)

    trigger._tick()

    assert computed == [("coder", 7), ("researcher", 7)]
    assert evolved == ["coder"]
    assert trigger.status()["tick_count"] == 1


def test_tick_reuses_drift_monitor_so_second_tick_can_detect_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.evolution import drift_monitor, fitness

    monkeypatch.setattr(auto_trigger, "_has_score_history", lambda _agent_id: True)

    def _compute(
        _agent_id: str,
        _config: Any,
        *,
        publish_event: bool = True,
    ) -> SimpleNamespace:
        assert publish_event is False
        return SimpleNamespace(combined=0.9)

    monkeypatch.setattr(fitness, "compute_fitness", _compute)
    constructed: list[str] = []

    class _StatefulDriftMonitor:
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            self.check_count = 0
            constructed.append(agent_id)

        def check(self, *, publish_events: bool = True) -> SimpleNamespace:
            assert publish_events is False
            self.check_count += 1
            changed = self.check_count == 2
            events = [SimpleNamespace(kind="soul_change", severity="critical")] if changed else []
            return SimpleNamespace(
                has_drift=changed,
                max_severity="critical" if changed else "none",
                events=events,
            )

    monkeypatch.setattr(drift_monitor, "DriftMonitor", _StatefulDriftMonitor)

    trigger = EvolutionAutoTrigger()
    trigger._stack = _stack()
    trigger._agent_registry = _Registry("coder")
    trigger._config = AutoTriggerConfig(drift_critical_auto_rollback=True)
    trigger._active = True
    rollbacks: list[tuple[str, str]] = []
    monkeypatch.setattr(
        trigger,
        "_trigger_rollback",
        lambda agent_id, report: rollbacks.append((agent_id, report.max_severity)),
    )

    trigger._tick()
    assert rollbacks == []
    trigger._tick()

    assert constructed == ["coder"]
    assert trigger._drift_monitors["coder"].check_count == 2
    assert rollbacks == [("coder", "critical")]


def test_registry_rebind_and_stop_prune_stale_drift_monitors() -> None:
    trigger = EvolutionAutoTrigger()
    coder_monitor = object()
    trigger._config = AutoTriggerConfig(agent_ids=("coder",))
    trigger._drift_monitors = {
        "coder": coder_monitor,
        "researcher": object(),
        "retired": object(),
    }

    trigger.bind_agent_registry(_Registry("coder", "researcher"))

    assert trigger._drift_monitors == {"coder": coder_monitor}
    trigger.stop()
    assert trigger._drift_monitors == {}


def test_event_handlers_reject_unknown_and_path_like_agent_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger = EvolutionAutoTrigger()
    trigger._stack = _stack()
    trigger._agent_registry = _Registry("coder")
    trigger._config = AutoTriggerConfig(fitness_threshold=0.5)
    trigger._active = True
    evolved: list[str] = []
    rolled_back: list[str] = []
    monkeypatch.setattr(trigger, "_trigger_evolve", evolved.append)
    monkeypatch.setattr(
        trigger,
        "_trigger_rollback_from_event",
        lambda event: rolled_back.append(event.agent_id),
    )

    for agent_id in ("unknown", "../../outside", " coder ", 123):
        trigger._on_fitness_event(
            SimpleNamespace(agent_id=agent_id, combined_score=0.1),
        )
        trigger._on_drift_event(
            SimpleNamespace(agent_id=agent_id, severity="critical"),
        )

    trigger._on_fitness_event(
        SimpleNamespace(agent_id="coder", combined_score=0.1),
    )
    trigger._on_drift_event(
        SimpleNamespace(agent_id="coder", severity="critical"),
    )

    assert evolved == ["coder"]
    assert rolled_back == ["coder"]


def test_disabled_trigger_ignores_events_and_repeated_start_wires_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.platform import feature_flags
    from runtime.platform.process import eventbus

    class _Bus:
        def __init__(self) -> None:
            self.topics: list[str] = []
            self.unsubscribed: list[int] = []

        def subscribe(self, topic: str, _handler: Any) -> int:
            self.topics.append(topic)
            return len(self.topics)

        def unsubscribe(self, subscription_id: int) -> bool:
            self.unsubscribed.append(subscription_id)
            return True

    bus = _Bus()
    monkeypatch.setattr(eventbus, "get_eventbus", lambda: bus)
    monkeypatch.setattr(feature_flags, "is_on", lambda _name: True)

    trigger = EvolutionAutoTrigger()
    registry = _Registry("coder")
    config = AutoTriggerConfig(check_interval_sec=60)
    trigger.start(_stack(), config, agent_registry=registry)
    trigger.start(_stack(), config)

    assert bus.topics == ["fitness.computed", "drift.detected"]
    assert trigger._agent_registry is registry

    trigger.stop()
    assert bus.unsubscribed == [1, 2]

    evolved: list[str] = []
    rolled_back: list[str] = []
    monkeypatch.setattr(trigger, "_trigger_evolve", evolved.append)
    monkeypatch.setattr(
        trigger,
        "_trigger_rollback_from_event",
        lambda event: rolled_back.append(event.agent_id),
    )
    trigger.start(
        _stack(),
        AutoTriggerConfig(enabled=False),
        agent_registry=registry,
    )
    # Handlers independently re-check config even if a stale/racing active
    # flag were observed.
    trigger._active = True
    trigger._on_fitness_event(
        SimpleNamespace(agent_id="coder", combined_score=0.1),
    )
    trigger._on_drift_event(
        SimpleNamespace(agent_id="coder", severity="critical"),
    )

    assert evolved == []
    assert rolled_back == []
    assert bus.topics == ["fitness.computed", "drift.detected"]


def test_mount_agents_rebinds_registry_created_by_compatibility_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    from runtime.execution.agents import base, loader, presets

    agents_router = importlib.import_module("runtime.sensing.gateway.agents_router")

    class _AutoRegistry:
        def __init__(self) -> None:
            self._ids: list[str] = []

        def register(self, agent: Any) -> None:
            self._ids.append(agent.agent_id)

        def all_ids(self) -> list[str]:
            return sorted(self._ids)

    class _StopAfterBind(Exception):
        pass

    auto_rebound: list[Any] = []
    regeneration_rebound: list[Any] = []
    fake_trigger = SimpleNamespace(bind_agent_registry=auto_rebound.append)
    fake_scheduler = SimpleNamespace(bind_agent_registry=regeneration_rebound.append)
    from runtime.safety.recovery import scheduler

    monkeypatch.setattr(base, "AgentRegistry", _AutoRegistry)
    monkeypatch.setattr(
        loader,
        "load_all_agents",
        lambda _runtime: [
            SimpleNamespace(agent_id="coder"),
            SimpleNamespace(agent_id="researcher"),
        ],
    )
    monkeypatch.setattr(
        presets,
        "make_admin_agent",
        lambda _runtime: SimpleNamespace(agent_id="admin"),
    )
    monkeypatch.setattr(auto_trigger, "get_auto_trigger", lambda: fake_trigger)
    monkeypatch.setattr(scheduler, "get_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr(
        agents_router,
        "create_agents_router",
        lambda **_kwargs: (_ for _ in ()).throw(_StopAfterBind),
    )

    ctx = AppContext(
        app=SimpleNamespace(include_router=lambda _router: None),
        state=SimpleNamespace(journal=None),
        stack=SimpleNamespace(runtime=object(), is_llm_planner=True),
    )
    with pytest.raises(_StopAfterBind):
        mount_agents(ctx, agent_registry=None, group_registry=None)

    assert auto_rebound == [ctx.agent_registry]
    assert regeneration_rebound == [ctx.agent_registry]
    assert ctx.agent_registry.all_ids() == ["admin", "coder", "researcher"]

