from __future__ import annotations

import logging
import threading
from typing import Any

from runtime.platform.process.eventbus import EventBus, get_eventbus

_LOG = logging.getLogger(__name__)
_BRIDGE_LOCK = threading.Lock()
_SIGNAL_BUSES: set[int] = set()
_TYPED_PAIRS: set[tuple[int, str]] = set()
_HOOK_REGISTRIES: set[int] = set()


def bridge_signal_bus_to_eventbus(signal_bus: Any) -> None:
    if signal_bus is None or not hasattr(signal_bus, "subscribe"):
        return

    with _BRIDGE_LOCK:
        key = id(signal_bus)
        if key in _SIGNAL_BUSES:
            return
        _SIGNAL_BUSES.add(key)

    bus = get_eventbus()

    def _on_signal(event: Any) -> None:
        topic = getattr(event, "topic", "unknown")
        publisher = getattr(event, "publisher", "")
        payload = getattr(event, "payload", {})
        bus.emit(
            f"signal.{topic}",
            agent_id=publisher,
            payload=payload,
        )

    signal_bus.subscribe("*", _on_signal)
    _LOG.debug("SignalBus → EventBus bridge established")


def bridge_typed_bus_to_eventbus(typed_bus: Any) -> None:
    if typed_bus is None or not hasattr(typed_bus, "subscribe"):
        return

    bus = get_eventbus()

    _base_cls = None
    try:
        from runtime.core.nerves.bus import NervesEvent

        _base_cls = NervesEvent
    except ImportError:  # noqa: BLE001 — nerves.bus optional; bridge runs without typed-base check
        pass

    for attr_name in dir(typed_bus):
        if attr_name.startswith("_"):
            continue
        attr = getattr(typed_bus, attr_name, None)
        if not isinstance(attr, type):
            continue
        if _base_cls is not None and issubclass(attr, _base_cls):
            _bridge_event_cls(typed_bus, attr, bus)


def _bridge_event_cls(typed_bus: Any, event_cls: type, bus: EventBus) -> None:
    cls_name = event_cls.__name__
    with _BRIDGE_LOCK:
        key = (id(typed_bus), cls_name)
        if key in _TYPED_PAIRS:
            return
        _TYPED_PAIRS.add(key)

    def _on_typed_event(nerves_event: Any) -> None:
        payload = {}
        for fld_name in getattr(nerves_event, "model_fields", {}):
            try:
                payload[fld_name] = getattr(nerves_event, fld_name)
            except Exception as exc:  # noqa: BLE001 — pydantic field access edge case; skip field, keep emitting
                _LOG.debug("event bridge field %s skipped: %s", fld_name, exc)
        bus.emit(f"nerves.{cls_name}", payload=payload)

    typed_bus.subscribe(event_cls, _on_typed_event)
    _LOG.debug("TypedEventBus[%s] → EventBus bridge established", cls_name)


def bridge_hook_registry_to_eventbus(hook_registry: Any) -> None:
    if hook_registry is None or not hasattr(hook_registry, "add_hook"):
        return

    with _BRIDGE_LOCK:
        key = id(hook_registry)
        if key in _HOOK_REGISTRIES:
            return
        _HOOK_REGISTRIES.add(key)

    bus = get_eventbus()

    from runtime.platform.plugins.plugins import HookPoint

    _HOOK_MAP = {  # noqa: N806
        HookPoint.ON_INIT: "hook.on_init",
        HookPoint.ON_PLAN: "hook.on_plan",
        HookPoint.ON_EXECUTE: "hook.on_execute",
        HookPoint.ON_REFLECT: "hook.on_reflect",
        HookPoint.ON_SHUTDOWN: "hook.on_shutdown",
        HookPoint.ON_ERROR: "hook.on_error",
        HookPoint.ON_SKILL_REGISTER: "hook.on_skill_register",
        HookPoint.ON_CONFIG_LOAD: "hook.on_config_load",
    }

    for hook_point, event_type in _HOOK_MAP.items():

        def _make_bridge(et: str) -> Any:
            def _bridge(ctx: Any) -> None:
                bus.emit(et, payload={"hook": et})

            return _bridge

        hook_registry.add_hook(hook_point, _make_bridge(event_type))

    _LOG.debug("HookRegistry → EventBus bridge established")


def install_all_bridges(
    *, signal_bus: Any = None, typed_bus: Any = None, hook_registry: Any = None
) -> None:
    if signal_bus is not None:
        bridge_signal_bus_to_eventbus(signal_bus)
    if typed_bus is not None:
        bridge_typed_bus_to_eventbus(typed_bus)
    if hook_registry is not None:
        bridge_hook_registry_to_eventbus(hook_registry)


__all__ = [
    "bridge_signal_bus_to_eventbus",
    "bridge_typed_bus_to_eventbus",
    "bridge_hook_registry_to_eventbus",
    "install_all_bridges",
]
