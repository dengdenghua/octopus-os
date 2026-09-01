from __future__ import annotations

import pytest

from appliance import lan_discovery_proxy as proxy


def _field(number: int, value: bytes) -> bytes:
    return proxy._encode_varint(number << 3 | 2) + proxy._encode_varint(len(value)) + value


def _announcement(*addresses: str, device_id: bytes = b"d" * 32) -> bytes:
    packet = bytearray(proxy.MAGIC)
    packet.extend(_field(1, device_id))
    for address in addresses:
        packet.extend(_field(2, address.encode()))
    packet.extend(proxy._encode_varint(3 << 3))
    packet.extend(proxy._encode_varint(42))
    return bytes(packet)


def test_lan_announcement_restores_original_peer_address() -> None:
    packet = _announcement(
        "tcp://0.0.0.0:22000",
        "quic://:22000",
        "tcp6://[::]:22000",
        "tcp://syncbox.local:22000",
        "relay://relay.example:22067/?id=abc",
    )

    transformed = proxy.transform_announcement(packet, "192.168.50.23")

    assert b"tcp://192.168.50.23:22000" in transformed
    assert b"quic://192.168.50.23:22000" in transformed
    assert b"tcp6://[::]:22000" not in transformed
    assert b"tcp://syncbox.local:22000" in transformed
    assert b"relay://relay.example:22067/?id=abc" in transformed
    assert b"0.0.0.0" not in transformed


def test_proxy_routes_only_between_docker_bridge_and_lan() -> None:
    packet = _announcement("tcp://0.0.0.0:22000")

    outbound = proxy.route_announcement(packet, "172.19.0.5", "br-0123456789ab")
    inbound = proxy.route_announcement(packet, "10.20.30.40", "enp2s0")

    assert outbound == proxy.RoutedAnnouncement("lan", packet)
    assert inbound.target == "container"
    assert b"tcp://10.20.30.40:22000" in inbound.payload
    with pytest.raises(proxy.DiscoveryPacketError, match="unsupported interface"):
        proxy.route_announcement(packet, "127.0.0.1", "lo")
    with pytest.raises(proxy.DiscoveryPacketError, match="unsupported interface"):
        proxy.route_announcement(packet, "100.64.0.2", "tailscale0")
    with pytest.raises(proxy.DiscoveryPacketError, match="unsupported interface"):
        proxy.route_announcement(packet, "192.168.122.1", "virbr0")
    bridged_lan = proxy.route_announcement(packet, "192.168.1.20", "br-lan")
    assert bridged_lan.target == "container"


def test_recent_forward_guard_is_bounded_and_expires() -> None:
    now = [100.0]
    recent = proxy.RecentForwards(ttl_seconds=2, maximum=2, clock=lambda: now[0])

    recent.remember(b"first")
    assert recent.seen(b"first") is True
    assert recent.seen(b"other") is False
    recent.remember(b"second")
    recent.remember(b"third")
    assert recent.seen(b"first") is False
    assert recent.seen(b"third") is True
    now[0] = 102.1
    assert recent.seen(b"third") is False


@pytest.mark.parametrize(
    "packet",
    [
        b"not-syncthing",
        _announcement("tcp://0.0.0.0:22000", device_id=b"short"),
        proxy.MAGIC + proxy._encode_varint(1 << 3 | 2) + b"\xff",
        _announcement(*("tcp://0.0.0.0:22000" for _ in range(proxy.MAX_ADDRESSES + 1))),
        b"x" * (proxy.MAX_PACKET_BYTES + 1),
    ],
)
def test_proxy_rejects_malformed_or_oversized_datagrams(packet: bytes) -> None:
    with pytest.raises(proxy.DiscoveryPacketError):
        proxy.transform_announcement(packet, "192.168.1.2")


@pytest.mark.parametrize("source", ["127.0.0.1", "0.0.0.0", "ff02::1", "not-an-ip"])
def test_proxy_rejects_unsafe_or_non_ipv4_sources(source: str) -> None:
    with pytest.raises(proxy.DiscoveryPacketError, match="source"):
        proxy.transform_announcement(_announcement("tcp://:22000"), source)


def test_proxy_rejects_ipv6_only_announcement_from_ipv4_lan() -> None:
    with pytest.raises(proxy.DiscoveryPacketError, match="usable addresses"):
        proxy.transform_announcement(
            _announcement("tcp6://[::]:22000"),
            "192.168.1.2",
        )


def test_module_check_does_not_open_a_socket() -> None:
    assert proxy.main(["--check"]) == 0


def test_network_check_requires_a_docker_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy, "broadcast_addresses", lambda **_kwargs: ())
    assert proxy.main(["--check-network"]) == 1
    monkeypatch.setattr(
        proxy,
        "broadcast_addresses",
        lambda *, docker, **_kwargs: ("172.18.255.255",) if docker else ("192.168.1.255",),
    )
    assert proxy.main(["--check-network"]) == 0
    monkeypatch.setattr(
        proxy,
        "broadcast_addresses",
        lambda *, docker, **_kwargs: ("172.18.255.255",) if docker else (),
    )
    assert proxy.main(["--check-network"]) == 1
