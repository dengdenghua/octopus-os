"""Send mapped pet events to the local Godot sidecar."""

from __future__ import annotations

import json
import logging
import socket
from contextlib import suppress
from typing import Any

from .pet_state_map import PetEvent, map_tentacle_event

logger = logging.getLogger(__name__)


class PetUdpBridge:
    """Best-effort UDP adapter for TentaclePool lifecycle events."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        sock: socket.socket | None = None,
    ) -> None:
        self._address = (host, port)
        self._socket = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._owns_socket = sock is None

    async def on_pool_event(self, event: dict[str, Any]) -> None:
        """Map and send a TentaclePool event without affecting pool state."""
        if not isinstance(event, dict):
            return
        mapped = map_tentacle_event(event.get("event", ""), event)
        if mapped is not None:
            self.send(mapped)

    def send(self, event: PetEvent) -> bool:
        """Send one JSON-safe pet event and return whether it was accepted."""
        if not isinstance(event, PetEvent):
            return False
        try:
            payload = json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8")
            self._socket.sendto(payload, self._address)
            return True
        except (OSError, TypeError, ValueError) as exc:
            logger.debug("pet UDP bridge unavailable: %s", exc)
            return False

    def close(self) -> None:
        """Close only sockets owned by this bridge."""
        if self._owns_socket:
            with suppress(OSError):
                self._socket.close()


__all__ = ["PetUdpBridge"]
