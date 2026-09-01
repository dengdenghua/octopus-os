"""Device pool — manage ALL connected devices (phones, robots, cars, ...).

Generic device abstraction: any device that speaks JSON-RPC over WebSocket
can register itself.  Device-specific metadata lives in subclasses.

Architecture:
    Phone  ──WS──→ ┐
    Robot  ──WS──→ DevicePool ──→ *_operator_arm
    Car    ──WS──→ ┘                   ↓
                                  ToolRegistry (device.*)

Adding a new device type:
    1. Subclass ConnectedDevice (add your metadata fields)
    2. Add a SkillId list in presets.py (e.g. _ROBOT_OPS)
    3. Add a make_*_operator_arm() factory
    4. Optionally add a router in siphon/ (e.g. robot_router.py)

That's it. No changes to DevicePool, device_lock, or existing arms.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from runtime.core.nerves.bus import NervesEvent, TypedEventBus

# ── Device State ────────────────────────────────────────


class DeviceState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"


class DeviceKind(StrEnum):
    ANDROID = "android"
    ROBOT = "robot"
    CAR = "car"
    DESKTOP = "desktop"
    IOT = "iot"
    CUSTOM = "custom"


# ── Base Device ─────────────────────────────────────────


@dataclass
class ConnectedDevice:
    """Base class for any device connected via WebSocket + JSON-RPC.

    Subclass this to add device-specific metadata.
    Examples:
        - AndroidDevice(ConnectedDevice): adds android_version, sdk_version
        - RobotDevice(ConnectedDevice): adds joint_count, firmware_version
        - CarDevice(ConnectedDevice): adds vin, make, model_year
    """

    device_id: str
    kind: DeviceKind = DeviceKind.CUSTOM
    label: str = ""  # Human-readable name (e.g. "Pixel 8 Pro")
    state: DeviceState = DeviceState.ONLINE
    last_heartbeat: float = field(default_factory=time.time)
    connected_at: float = field(default_factory=time.time)
    ws: Any = None  # WebSocket handle
    pending_calls: dict[str, asyncio.Future] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)  # e.g. ["tap","swipe","browser_navigate"]
    metadata: dict[str, Any] = field(default_factory=dict)  # Extensible key-value store

    @property
    def is_online(self) -> bool:
        return self.state in (DeviceState.ONLINE, DeviceState.BUSY)

    def touch_heartbeat(self) -> None:
        self.last_heartbeat = time.time()

    def stale_seconds(self) -> float:
        return time.time() - self.last_heartbeat

    def supports(self, action: str) -> bool:
        """Check if this device supports a given action/capability."""
        if not self.capabilities:
            return True  # No capability list → assume all supported
        return action in self.capabilities


# ── Concrete Device Types ───────────────────────────────


@dataclass
class AndroidDevice(ConnectedDevice):
    """An Android phone running Echo Mobile."""

    kind: DeviceKind = DeviceKind.ANDROID
    model: str = ""  # Device model name, e.g. "Pixel 8 Pro"
    android_version: str = ""
    sdk_version: int = 0

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.model or (
                f"Android {self.android_version}"
                if self.android_version
                else f"Phone {self.device_id[:8]}"
            )


@dataclass
class RobotDevice(ConnectedDevice):
    """A robot (arm / drone / rover / humanoid)."""

    kind: DeviceKind = DeviceKind.ROBOT
    firmware_version: str = ""
    joint_count: int = 0
    robot_type: str = ""  # "arm" / "drone" / "rover" / "humanoid"

    def __post_init__(self) -> None:
        if not self.label:
            self.label = (
                f"Robot {self.robot_type} {self.device_id[:8]}"
                if self.robot_type
                else f"Robot {self.device_id[:8]}"
            )


@dataclass
class CarDevice(ConnectedDevice):
    """A connected vehicle."""

    kind: DeviceKind = DeviceKind.CAR
    vin: str = ""
    make: str = ""
    model_year: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"{self.make} {self.model_year}".strip() or f"Car {self.device_id[:8]}"


@dataclass
class DesktopDevice(ConnectedDevice):
    """A desktop computer (self-referencing for local desktop_operator)."""

    kind: DeviceKind = DeviceKind.DESKTOP
    os_name: str = ""
    hostname: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.hostname or f"Desktop {self.device_id[:8]}"


@dataclass
class IoTDevice(ConnectedDevice):
    """An IoT device (smart home / sensor / industrial controller)."""

    kind: DeviceKind = DeviceKind.IOT
    protocol: str = ""  # "zigbee" / "mqtt" / "ble" / "wifi"
    device_class: str = ""  # "light" / "sensor" / "switch" / "camera"

    def __post_init__(self) -> None:
        if not self.label:
            self.label = (
                f"IoT {self.device_class} {self.device_id[:8]}"
                if self.device_class
                else f"IoT {self.device_id[:8]}"
            )


# ── Events ──────────────────────────────────────────────


class DeviceOnlineEvent(NervesEvent):
    # NervesEvent is a frozen pydantic model — declare fields as
    # annotations (like the sibling events above) rather than assigning
    # ``self.x`` in ``__init__``, which raises on a frozen instance.
    device_id: str
    kind: str
    label: str


class DeviceOfflineEvent(NervesEvent):
    device_id: str
    kind: str
    reason: str


class DeviceHeartbeatEvent(NervesEvent):
    device_id: str


# ── Pool ────────────────────────────────────────────────


class DevicePool:
    """Registry of ALL connected devices, regardless of type.

    Device-type-agnostic: the pool doesn't care if it's a phone,
    robot, or car.  It only tracks online/offline, heartbeat,
    and routes JSON-RPC calls.
    """

    HEARTBEAT_TIMEOUT_S = 45.0

    def __init__(self, bus: TypedEventBus | None = None) -> None:
        self._devices: dict[str, ConnectedDevice] = {}
        self._bus = bus if bus is not None else TypedEventBus()

    @property
    def bus(self) -> TypedEventBus:
        """Event bus carrying device-lifecycle events (online/offline/heartbeat).

        Subscribe here to react to device changes, e.g.
        ``get_device_pool().bus.subscribe(DeviceOnlineEvent, handler)``.
        """
        return self._bus

    # ── CRUD ───────────────────────────────────────────

    def register(self, device: ConnectedDevice) -> None:
        self._devices[device.device_id] = device
        self._bus.publish(
            DeviceOnlineEvent(
                device_id=device.device_id,
                kind=device.kind.value,
                label=device.label,
            )
        )

    def unregister(self, device_id: str, reason: str = "disconnect") -> None:
        dev = self._devices.pop(device_id, None)
        if dev is not None:
            for fut in dev.pending_calls.values():
                if not fut.done():
                    fut.set_exception(ConnectionError(f"Device {device_id} disconnected"))
            dev.pending_calls.clear()
            self._bus.publish(
                DeviceOfflineEvent(
                    device_id=device_id,
                    kind=dev.kind.value,
                    reason=reason,
                )
            )

    def get(self, device_id: str) -> ConnectedDevice | None:
        return self._devices.get(device_id)

    def list_online(self) -> list[ConnectedDevice]:
        return [d for d in self._devices.values() if d.is_online]

    def list_all(self) -> list[ConnectedDevice]:
        return list(self._devices.values())

    def list_by_kind(self, kind: DeviceKind) -> list[ConnectedDevice]:
        """List all devices of a specific type."""
        return [d for d in self._devices.values() if d.kind == kind]

    # ── Heartbeat ──────────────────────────────────────

    def heartbeat(self, device_id: str) -> None:
        dev = self._devices.get(device_id)
        if dev is not None:
            dev.touch_heartbeat()
            self._bus.publish(DeviceHeartbeatEvent(device_id=device_id))

    async def prune_stale(self) -> list[str]:
        pruned: list[str] = []
        for did, dev in list(self._devices.items()):
            if dev.stale_seconds() > self.HEARTBEAT_TIMEOUT_S:
                self.unregister(did, reason="heartbeat_timeout")
                pruned.append(did)
        return pruned

    # ── Routing ────────────────────────────────────────

    def pick_device(
        self,
        preferred_id: str | None = None,
        kind: DeviceKind | None = None,
    ) -> ConnectedDevice | None:
        """Select a device for the next tool call.

        Strategy:
          1. If preferred_id given and online → use it
          2. Filter by kind if specified
          3. Pick first ONLINE device
          4. If all busy → pick least-loaded BUSY device
        """
        if preferred_id:
            dev = self._devices.get(preferred_id)
            if dev and dev.is_online:
                return dev

        candidates = list(self._devices.values())
        if kind:
            candidates = [d for d in candidates if d.kind == kind]

        online = [d for d in candidates if d.state == DeviceState.ONLINE]
        if online:
            return online[0]

        busy = [d for d in candidates if d.state == DeviceState.BUSY]
        if busy:
            return min(busy, key=lambda d: len(d.pending_calls))

        return None

    # ── JSON-RPC dispatch ──────────────────────────────

    async def call_tool(
        self,
        device_id: str | None,
        method: str,
        params: dict[str, Any],
        timeout_s: float = 30.0,
        kind: DeviceKind | None = None,
    ) -> Any:
        dev = self.pick_device(preferred_id=device_id, kind=kind)
        if dev is None:
            kind_hint = f" kind={kind.value}" if kind else ""
            raise RuntimeError(f"No online device available{kind_hint}")

        call_id = f"call-{int(time.time() * 1000)}-{id(dev)}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        dev.pending_calls[call_id] = fut

        try:
            if dev.ws is None:
                raise ConnectionError(f"Device {dev.device_id} has no WebSocket")

            request = {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": method,
                "params": params,
            }
            await dev.ws.send_json(request)
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except TimeoutError:
            raise TimeoutError(f"Tool call {method} timed out after {timeout_s}s") from None
        finally:
            dev.pending_calls.pop(call_id, None)

    def handle_response(self, device_id: str, msg_id: str, result: Any) -> None:
        dev = self._devices.get(device_id)
        if dev is None:
            return
        fut = dev.pending_calls.pop(msg_id, None)
        if fut and not fut.done():
            fut.set_result(result)

    def handle_error(self, device_id: str, msg_id: str, error: Any) -> None:
        dev = self._devices.get(device_id)
        if dev is None:
            return
        fut = dev.pending_calls.pop(msg_id, None)
        if fut and not fut.done():
            fut.set_exception(RuntimeError(str(error)))


# ── Global singleton ────────────────────────────────────

_pool: DevicePool | None = None


def get_device_pool() -> DevicePool:
    global _pool
    if _pool is None:
        _pool = DevicePool()
    return _pool
