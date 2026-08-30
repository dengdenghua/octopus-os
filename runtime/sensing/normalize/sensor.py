from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .events import SensorEvent


@dataclass(frozen=True)
class SensorStatus:
    sensor_id: str
    running: bool
    events_emitted: int = 0
    last_emit_at: datetime | None = None
    last_error: str = ""
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


class EnvSensor(ABC):
    sensor_id: str = ""

    def __init__(self) -> None:
        self._publisher: Callable[[SensorEvent], None] | None = None
        self._events_emitted: int = 0
        self._last_emit_at: datetime | None = None
        self._last_error: str = ""
        self._running: bool = False
        self._lock = threading.RLock()

    def bind_publisher(self, publisher: Callable[[SensorEvent], None]) -> None:
        self._publisher = publisher

    def _publish(self, event: SensorEvent) -> None:
        if self._publisher is None:
            raise RuntimeError(
                f"sensor {self.sensor_id!r} has no publisher · register to SensorManager first",
            )
        if not event.sensor_id:
            event = event.model_copy(update={"sensor_id": self.sensor_id})
        self._publisher(event)
        with self._lock:
            self._events_emitted += 1
            self._last_emit_at = datetime.now(UTC)

    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    def status(self) -> SensorStatus:
        with self._lock:
            return SensorStatus(
                sensor_id=self.sensor_id,
                running=self._running,
                events_emitted=self._events_emitted,
                last_emit_at=self._last_emit_at,
                last_error=self._last_error,
            )

    def __repr__(self) -> str:  # pragma: no cover
        return f"{type(self).__name__}(sensor_id={self.sensor_id!r})"
