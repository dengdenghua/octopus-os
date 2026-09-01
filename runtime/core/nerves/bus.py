from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class NervesEvent(BaseModel):
    model_config = ConfigDict(frozen=True)


E = TypeVar("E", bound=NervesEvent)


class SkillRegistered(NervesEvent):
    skill_name: str
    trusted_source: str = ""
    forged: bool = False
    candidate_id: str | None = None


class SkillRetired(NervesEvent):
    skill_name: str
    reason: str = ""


class AgentAdded(NervesEvent):
    agent_id: str
    display_name: str = ""


class AgentRemoved(NervesEvent):
    agent_id: str


class BudgetPressure(NervesEvent):
    task_id: str
    percent_used: float
    limit_tokens: int = 0
    limit_usd: float = 0.0


class ConversationOpened(NervesEvent):
    conversation_id: str
    agent_id: str | None = None
    channel_id: str | None = None


Subscription = Callable[[E], None]


class AbstractEventBus(ABC):
    @abstractmethod
    def subscribe(
        self,
        event_type: type[E],
        handler: Callable[[E], None],
    ) -> None: ...

    @abstractmethod
    def unsubscribe(
        self,
        event_type: type[E],
        handler: Callable[[E], None],
    ) -> bool: ...

    @abstractmethod
    def publish(self, event: NervesEvent) -> int: ...

    @abstractmethod
    def subscriber_count(self, event_type: type[E]) -> int: ...

    def on(self, event_type: type[E]):
        def _wrap(handler: Callable[[E], None]) -> Callable[[E], None]:
            self.subscribe(event_type, handler)
            return handler

        return _wrap


class TypedEventBus(AbstractEventBus):
    def __init__(self, *, crash_resilient: bool = True) -> None:
        self._subs: dict[type, list[Callable]] = {}
        self._lock = threading.RLock()
        self._crash_resilient = crash_resilient

    def subscribe(
        self,
        event_type: type[E],
        handler: Callable[[E], None],
    ) -> None:
        if not isinstance(event_type, type) or not issubclass(event_type, NervesEvent):
            raise TypeError(f"event_type must be subclass of NervesEvent · got {event_type!r}")
        if not callable(handler):
            raise TypeError(f"handler must be callable · got {handler!r}")

        with self._lock:
            subs = self._subs.setdefault(event_type, [])
            if handler not in subs:
                subs.append(handler)

    def unsubscribe(
        self,
        event_type: type[E],
        handler: Callable[[E], None],
    ) -> bool:
        with self._lock:
            subs = self._subs.get(event_type, [])
            if handler in subs:
                subs.remove(handler)
                if not subs:
                    del self._subs[event_type]
                return True
            return False

    def subscriber_count(self, event_type: type[E]) -> int:
        with self._lock:
            return len(self._subs.get(event_type, []))

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()

    def publish(self, event: NervesEvent) -> int:
        if not isinstance(event, NervesEvent):
            raise TypeError(
                f"event must be NervesEvent · got {type(event).__name__}",
            )
        with self._lock:
            subs = list(self._subs.get(type(event), []))

        called = 0
        for handler in subs:
            try:
                handler(event)
                called += 1
            except Exception as e:
                if self._crash_resilient:
                    logger.warning(
                        "subscriber %r raised on %s: %s",
                        handler,
                        type(event).__name__,
                        e,
                    )
                else:
                    raise
        return called

    def on(self, event_type: type[E]):
        def _wrap(handler: Callable[[E], None]) -> Callable[[E], None]:
            self.subscribe(event_type, handler)
            return handler

        return _wrap

    def __repr__(self) -> str:
        with self._lock:
            total = sum(len(v) for v in self._subs.values())
            types = len(self._subs)
        return f"TypedEventBus(event_types={types}, subscribers={total})"
