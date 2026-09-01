import json

import pytest

from runtime.pet import PetEvent, PetUdpBridge


class FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload: bytes, address: tuple[str, int]) -> None:
        if self.fail:
            raise OSError("sidecar unavailable")
        self.sent.append((payload, address))


@pytest.mark.asyncio
async def test_pool_event_maps_to_godot_udp_payload() -> None:
    sock = FakeSocket()
    bridge = PetUdpBridge(sock=sock)

    await bridge.on_pool_event({"event": "tentacle.registered", "tentacle_id": "phone-7"})

    payload, address = sock.sent[0]
    assert json.loads(payload) == {
        "type": "agent.presence",
        "online": True,
        "device_id": "phone-7",
    }
    assert address == ("127.0.0.1", 8765)


@pytest.mark.asyncio
async def test_unknown_pool_event_is_noop() -> None:
    sock = FakeSocket()
    bridge = PetUdpBridge(sock=sock)

    await bridge.on_pool_event({"event": "device.screen_changed"})

    assert sock.sent == []


def test_udp_failure_is_best_effort() -> None:
    bridge = PetUdpBridge(sock=FakeSocket(fail=True))

    assert bridge.send(PetEvent("agent.presence", {"online": False})) is False

