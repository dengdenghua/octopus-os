from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from runtime.core.nerves.bus import AbstractEventBus

from .events import EnvironmentPing, SensorEvent
from .sensor import EnvSensor, SensorStatus

logger = logging.getLogger(__name__)


class SensorManager:
    def __init__(
        self,
        *,
        bus: AbstractEventBus | None = None,
        publisher: Callable[[SensorEvent], None] | None = None,
    ) -> None:
        if bus is None and publisher is None:
            raise ValueError("either bus or publisher must be provided")
        self.bus = bus
        self._custom_publisher = publisher
        self._sensors: dict[str, EnvSensor] = {}
        self._lock = threading.RLock()

    def register(self, sensor: EnvSensor) -> None:
        if not sensor.sensor_id:
            raise ValueError(
                f"sensor {type(sensor).__name__} must set sensor_id before register",
            )
        with self._lock:
            if sensor.sensor_id in self._sensors:
                raise ValueError(f"duplicate sensor_id: {sensor.sensor_id!r}")
            sensor.bind_publisher(self._dispatch)
            self._sensors[sensor.sensor_id] = sensor

    def unregister(self, sensor_id: str) -> bool:
        with self._lock:
            sensor = self._sensors.pop(sensor_id, None)
        if sensor is None:
            return False
        try:
            sensor.stop()
        except (OSError, TypeError, ValueError, RuntimeError):  # noqa: BLE001
            logger.exception("sensor %r stop failed during unregister", sensor_id)
        return True

    def get(self, sensor_id: str) -> EnvSensor | None:
        with self._lock:
            return self._sensors.get(sensor_id)

    def sensor_ids(self) -> list[str]:
        with self._lock:
            return list(self._sensors.keys())

    # ─── lifecycle ────────────────────────────────

    def start_all(self) -> None:
        with self._lock:
            sensors = list(self._sensors.values())
        for s in sensors:
            try:
                s.start()
            except (OSError, TypeError, ValueError, RuntimeError):  # noqa: BLE001
                logger.exception("sensor %r start failed", s.sensor_id)

    def stop_all(self) -> None:
        with self._lock:
            sensors = list(self._sensors.values())
        for s in sensors:
            try:
                s.stop()
            except Exception as exc:
                logger.exception("sensor %r stop failed: %s", s.sensor_id, exc)

    def status_all(self) -> list[SensorStatus]:
        with self._lock:
            sensors = list(self._sensors.values())
        return [s.status() for s in sensors]

    def ping(self) -> EnvironmentPing:
        with self._lock:
            active = sum(1 for s in self._sensors.values() if s.status().running)
        evt = EnvironmentPing(
            sensor_id="__manager__",
            detected_at=datetime.now(UTC),
            active_sensor_count=active,
        )
        self._dispatch(evt)
        return evt

    def _dispatch(self, event: SensorEvent) -> None:
        if self._custom_publisher is not None:
            self._custom_publisher(event)
            return
        if self.bus is not None:
            self.bus.publish(event)
