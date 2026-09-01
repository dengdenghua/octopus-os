from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from runtime.platform.models import now_utc

_LOG = logging.getLogger("echo.platform.eventbus")

E = TypeVar("E", bound="DomainEvent")

# Wire-protocol version of the typed event envelope (P4 · protocol versioning).
# Bump on incompatible envelope changes; consumers must reject events from a
# NEWER protocol instead of misreading them. Mirrors the journal / block
# manifest ``schema_version`` pattern.
CURRENT_EVENT_PROTOCOL_VERSION = 1


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_version: int = Field(
        default=CURRENT_EVENT_PROTOCOL_VERSION,
        ge=1,
        description="Event envelope wire-protocol version.",
    )
    event_id: str = Field(default="")
    event_type: str = Field(default="")
    agent_id: str = ""
    ts: datetime = Field(default_factory=now_utc)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("protocol_version")
    @classmethod
    def _protocol_must_be_supported(cls, value: int) -> int:
        if value > CURRENT_EVENT_PROTOCOL_VERSION:
            raise ValueError(
                f"event protocol v{value} is not supported by this runtime "
                f"(supports <= v{CURRENT_EVENT_PROTOCOL_VERSION}); upgrade the "
                "runtime or reject the upstream event"
            )
        return value


class FitnessComputed(DomainEvent):
    event_type: str = "fitness.computed"
    combined_score: float = 0.0
    verdict: str = ""
    trend: str = ""
    tenant_id: str = ""
    owner_actor_id: str = ""
    scope_mode: str = "legacy"


class DriftDetected(DomainEvent):
    event_type: str = "drift.detected"
    drift_kind: str = ""
    severity: str = ""
    detail: str = ""
    tenant_id: str = ""
    owner_actor_id: str = ""
    scope_mode: str = "legacy"


class SkillUsed(DomainEvent):
    event_type: str = "skill.used"
    skill_name: str = ""
    success: bool = True
    duration_sec: float = 0.0


class EvolutionTriggered(DomainEvent):
    event_type: str = "evolution.triggered"
    trigger_reason: str = ""
    dry_run: bool = True


class EvolutionCompleted(DomainEvent):
    event_type: str = "evolution.completed"
    rounds_run: int = 0
    applied_count: int = 0
    ok: bool = True


class GenomeRolledBack(DomainEvent):
    event_type: str = "genome.rolled_back"
    from_version: int = 0
    to_version: int = 0


class CanaryStageChanged(DomainEvent):
    event_type: str = "canary.stage_changed"
    candidate_id: str = ""
    old_stage: str = ""
    new_stage: str = ""


class ProposalCreated(DomainEvent):
    event_type: str = "proposal.created"
    proposal_id: str = ""
    mutation_type: str = ""


class BudgetPressureEvent(DomainEvent):
    event_type: str = "budget.pressure"
    percent_used: float = 0.0
    remaining: float = 0.0


class IterationExtended(DomainEvent):
    event_type: str = "iteration.extended"
    count: int = 0
    new_limit: int = 0
    reason: str = ""


class ToolCallBlocked(DomainEvent):
    event_type: str = "tool_call.blocked"
    tool_name: str = ""
    reason: str = ""


class FileWriteBlocked(DomainEvent):
    event_type: str = "file_write.blocked"
    path: str = ""
    reason: str = ""


class PluginLoadedEvent(DomainEvent):
    event_type: str = "plugin.loaded"
    plugin_name: str = ""
    plugin_version: str = ""


class StateChanged(DomainEvent):
    event_type: str = "state.changed"
    key: str = ""
    old_value: Any = None
    new_value: Any = None


EVOLUTION_EVENTS: frozenset[type[DomainEvent]] = frozenset(
    {
        FitnessComputed,
        DriftDetected,
        SkillUsed,
        EvolutionTriggered,
        EvolutionCompleted,
        GenomeRolledBack,
        CanaryStageChanged,
        ProposalCreated,
    }
)

SAFETY_EVENTS: frozenset[type[DomainEvent]] = frozenset(
    {
        BudgetPressureEvent,
        IterationExtended,
        ToolCallBlocked,
        FileWriteBlocked,
    }
)

PLATFORM_EVENTS: frozenset[type[DomainEvent]] = frozenset(
    {
        PluginLoadedEvent,
        StateChanged,
    }
)

ALL_DOMAIN_EVENTS: frozenset[type[DomainEvent]] = EVOLUTION_EVENTS | SAFETY_EVENTS | PLATFORM_EVENTS


@dataclass
class Subscription:
    sub_id: int
    event_type: str
    handler: Callable[[DomainEvent], None]
    filter_fn: Callable[[DomainEvent], bool] | None = None
    priority: int = 0
    once: bool = False


@dataclass
class EventBusConfig:
    crash_resilient: bool = True
    max_history: int = 1000
    log_events: bool = True
    log_level: str = "DEBUG"


class EventBus:
    _instance: EventBus | None = None
    _cls_lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls) -> EventBus:
        if cls._instance is None:
            with cls._cls_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._cls_lock:
            if cls._instance is not None:
                cls._instance.clear()
            cls._instance = None

    def __init__(self, config: EventBusConfig | None = None) -> None:
        self._config = config or EventBusConfig()
        self._subs: dict[str, list[Subscription]] = defaultdict(list)
        self._wildcard_subs: list[Subscription] = []
        self._next_id: int = 1
        self._lock = threading.RLock()
        self._history: list[DomainEvent] = []
        self._errors: list[str] = []
        self._typed_bridges: dict[str, list[Callable]] = {}

    def subscribe(
        self,
        event_type: str | type[DomainEvent],
        handler: Callable[[DomainEvent], None],
        *,
        filter_fn: Callable[[DomainEvent], bool] | None = None,
        priority: int = 0,
        once: bool = False,
    ) -> int:
        if isinstance(event_type, type) and issubclass(event_type, DomainEvent):
            et_field = event_type.model_fields.get("event_type")
            event_type_str = (
                et_field.default if et_field and et_field.default else event_type.__name__
            )
        else:
            event_type_str = str(event_type)

        if not callable(handler):
            raise TypeError(f"handler must be callable, got {type(handler).__name__}")

        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            sub = Subscription(
                sub_id=sub_id,
                event_type=event_type_str,
                handler=handler,
                filter_fn=filter_fn,
                priority=priority,
                once=once,
            )

            if event_type_str == "*":
                self._wildcard_subs.append(sub)
            else:
                self._subs[event_type_str].append(sub)
                self._subs[event_type_str].sort(key=lambda s: s.priority, reverse=True)

        return sub_id

    def unsubscribe(self, sub_id: int) -> bool:
        with self._lock:
            for type_name, subs in list(self._subs.items()):
                for i, sub in enumerate(subs):
                    if sub.sub_id == sub_id:
                        subs.pop(i)
                        if not subs:
                            del self._subs[type_name]
                        return True

            for i, sub in enumerate(self._wildcard_subs):
                if sub.sub_id == sub_id:
                    self._wildcard_subs.pop(i)
                    return True

        return False

    def publish(self, event: DomainEvent) -> int:
        if not isinstance(event, DomainEvent):
            raise TypeError(f"event must be DomainEvent, got {type(event).__name__}")

        event_type = event.event_type or type(event).__name__

        if self._config.log_events:
            log_fn = getattr(_LOG, self._config.log_level.lower(), _LOG.debug)
            log_fn("event published: %s agent=%s", event_type, event.agent_id)

        with self._lock:
            self._history.append(event)
            if len(self._history) > self._config.max_history:
                self._history = self._history[-self._config.max_history :]

            typed_subs = list(self._subs.get(event_type, []))
            wildcard_subs = list(self._wildcard_subs)
            to_remove: list[int] = []

        all_subs = sorted(
            typed_subs + wildcard_subs,
            key=lambda s: s.priority,
            reverse=True,
        )

        called = 0
        for sub in all_subs:
            if sub.filter_fn is not None:
                try:
                    if not sub.filter_fn(event):
                        continue
                except Exception:
                    continue

            try:
                sub.handler(event)
                called += 1
            except Exception as exc:
                err_text = (
                    f"handler sub_id={sub.sub_id} on {event_type}: {type(exc).__name__}: {exc}"
                )
                with self._lock:
                    self._errors.append(err_text)
                if not self._config.crash_resilient:
                    raise

            if sub.once:
                to_remove.append(sub.sub_id)

        for sid in to_remove:
            self.unsubscribe(sid)

        self._bridge_to_legacy(event)

        return called

    def emit(
        self,
        event_type: str,
        *,
        agent_id: str = "",
        **kwargs: Any,
    ) -> int:
        event = DomainEvent(
            event_type=event_type,
            agent_id=agent_id,
            payload=kwargs,
            ts=now_utc(),
        )
        return self.publish(event)

    def once(
        self,
        event_type: str | type[DomainEvent],
        handler: Callable[[DomainEvent], None],
    ) -> int:
        return self.subscribe(event_type, handler, once=True)

    def wait_for(
        self,
        event_type: str,
        timeout: float = 30.0,
    ) -> DomainEvent | None:
        result: DomainEvent | None = None
        evt = threading.Event()

        def _handler(e: DomainEvent) -> None:
            nonlocal result
            result = e
            evt.set()

        sub_id = self.subscribe(event_type, _handler, once=True)
        evt.wait(timeout=timeout)
        self.unsubscribe(sub_id)
        return result

    def bridge_typed_bus(self, typed_bus: Any) -> None:
        if typed_bus is None or not hasattr(typed_bus, "subscribe"):
            return

        def _make_bridge(cls_name: str) -> Callable:
            def _bridge(nerves_event: Any) -> None:
                payload = {}
                for fld_name in getattr(nerves_event, "model_fields", {}):
                    try:  # noqa: SIM105
                        payload[fld_name] = getattr(nerves_event, fld_name)
                    except Exception:  # noqa: BLE001 — pydantic field access edge case; skip field, keep emitting
                        pass
                self.emit(f"nerves.{cls_name}", payload=payload)

            return _bridge

        _base_cls = None
        try:
            from runtime.core.nerves.bus import NervesEvent

            _base_cls = NervesEvent
        except ImportError:  # noqa: BLE001 — nerves.bus optional; bridge skipped on this platform
            pass

        for attr_name in dir(typed_bus):
            if attr_name.startswith("_"):
                continue
            attr = getattr(typed_bus, attr_name, None)
            if not isinstance(attr, type):
                continue
            if _base_cls is not None and issubclass(attr, _base_cls):
                bridge_fn = _make_bridge(attr.__name__)
                typed_bus.subscribe(attr, bridge_fn)
                self._typed_bridges[attr.__name__] = [bridge_fn]

    def bridge_signal_bus(self, signal_bus: Any) -> None:
        if signal_bus is None or not hasattr(signal_bus, "subscribe"):
            return

        def _signal_bridge(signal_event: Any) -> None:
            self.emit(
                f"signal.{getattr(signal_event, 'topic', 'unknown')}",
                agent_id=getattr(signal_event, "publisher", ""),
                payload=getattr(signal_event, "payload", {}),
            )

        signal_bus.subscribe("*", _signal_bridge)

    def history(
        self,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[DomainEvent]:
        with self._lock:
            events = list(self._history)
        if event_type:
            events = [e for e in events if (e.event_type or type(e).__name__) == event_type]
        return events[-limit:]

    @property
    def errors(self) -> list[str]:
        with self._lock:
            return list(self._errors)

    def subscriber_count(self, event_type: str | None = None) -> int:
        with self._lock:
            if event_type:
                return len(self._subs.get(event_type, []))
            total = sum(len(subs) for subs in self._subs.values())
            total += len(self._wildcard_subs)
            return total

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()
            self._wildcard_subs.clear()
            self._history.clear()
            self._errors.clear()
            self._typed_bridges.clear()

    def _bridge_to_legacy(self, event: DomainEvent) -> None:
        event_type = event.event_type or type(event).__name__
        try:
            from runtime.platform.process.service_provider import get_provider

            provider = get_provider()

            signal_bus = provider.get("signal_bus")
            if signal_bus is not None and hasattr(signal_bus, "publish"):
                signal_bus.publish(
                    topic=f"eventbus.{event_type}",
                    payload=event.payload,
                    publisher="eventbus",
                )
        except Exception as _exc:
            _LOG.debug("legacy bridge failed: %s", _exc)


def publish_event(event: DomainEvent, *, logger: logging.Logger | None = None) -> int:
    """Publish an event to the global EventBus with safe error handling.

    This is the recommended way to publish events from evolution/safety/budget
    modules. It replaces the repeated try/except pattern in each module.
    """
    log = logger or _LOG
    try:
        bus = EventBus.get()
        return bus.publish(event)
    except Exception as _exc:
        log.debug("event publish failed [%s]: %s", event.event_type, _exc)
        return 0


def get_eventbus() -> EventBus:
    return EventBus.get()


__all__ = [
    "DomainEvent",
    "EventBus",
    "EventBusConfig",
    "Subscription",
    "FitnessComputed",
    "DriftDetected",
    "SkillUsed",
    "EvolutionTriggered",
    "EvolutionCompleted",
    "GenomeRolledBack",
    "CanaryStageChanged",
    "ProposalCreated",
    "BudgetPressureEvent",
    "IterationExtended",
    "ToolCallBlocked",
    "FileWriteBlocked",
    "PluginLoadedEvent",
    "StateChanged",
    "EVOLUTION_EVENTS",
    "SAFETY_EVENTS",
    "PLATFORM_EVENTS",
    "ALL_DOMAIN_EVENTS",
    "get_eventbus",
    "publish_event",
]
