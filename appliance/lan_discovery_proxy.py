"""Bounded Syncthing IPv4 local-discovery bridge for appliance Docker hosts.

Syncthing correctly replaces unspecified announcement addresses with the UDP
source address. A generic UDP relay would therefore make peers dial the Docker
bridge gateway instead of the NAS. This process accepts only the current
Syncthing announcement protobuf, rewrites unspecified addresses received from
the LAN to the original peer address, and forwards between physical-LAN and
Docker-bridge broadcasts.

The full Syncthing process remains on an ordinary bridge network. Only this
small, unprivileged parser runs in the host network namespace.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import logging
import re
import socket
import struct
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

MAGIC = b"\x2e\xa7\xd9\x0b"
LISTEN_PORT = 21027
MAX_PACKET_BYTES = 8192
MAX_ADDRESSES = 32
MAX_ADDRESS_BYTES = 512
IP_PKTINFO = 8
_DOCKER_BRIDGE = re.compile(r"^br-[0-9a-f]{12,64}$")
_LAN_INTERFACE_PREFIXES = ("eth", "en", "wl", "bond", "team", "br0", "br-lan", "lan")
_DIRECT_SCHEMES = frozenset({"tcp", "tcp4", "tcp6", "quic", "quic4", "quic6"})
_SIOCGIFFLAGS = 0x8913
_SIOCGIFBRDADDR = 0x8919
_IFF_UP = 0x1
_IFF_BROADCAST = 0x2
_IFF_LOOPBACK = 0x8

logger = logging.getLogger("echo.lan-discovery")


class DiscoveryPacketError(ValueError):
    """The datagram is not one bounded Syncthing local announcement."""


@dataclass(frozen=True)
class RoutedAnnouncement:
    target: str
    payload: bytes


class RecentForwards:
    """Bounded loop guard for broadcasts reflected back to the sending socket."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 2.0,
        maximum: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or not 1 <= maximum <= 4096:
            raise ValueError("recent-forward bounds are invalid")
        self._ttl_seconds = ttl_seconds
        self._maximum = maximum
        self._clock = clock
        self._entries: OrderedDict[bytes, float] = OrderedDict()

    @staticmethod
    def _key(packet: bytes) -> bytes:
        return hashlib.blake2s(packet, digest_size=16).digest()

    def _expire(self, now: float) -> None:
        while self._entries:
            _key, expiry = next(iter(self._entries.items()))
            if expiry > now:
                break
            self._entries.popitem(last=False)

    def seen(self, packet: bytes) -> bool:
        now = self._clock()
        self._expire(now)
        expiry = self._entries.get(self._key(packet))
        return expiry is not None and expiry > now

    def remember(self, packet: bytes) -> None:
        now = self._clock()
        self._expire(now)
        key = self._key(packet)
        self._entries.pop(key, None)
        self._entries[key] = now + self._ttl_seconds
        while len(self._entries) > self._maximum:
            self._entries.popitem(last=False)


def _is_docker_interface(name: str) -> bool:
    return (
        name == "docker0" or name.startswith("veth") or _DOCKER_BRIDGE.fullmatch(name) is not None
    )


def _is_lan_interface(name: str) -> bool:
    return name.startswith(_LAN_INTERFACE_PREFIXES) and not _is_docker_interface(name)


def _read_varint(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for offset in range(10):
        if position >= len(data):
            raise DiscoveryPacketError("truncated protobuf varint")
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << (offset * 7)
        if byte < 0x80:
            return value, position
    raise DiscoveryPacketError("oversized protobuf varint")


def _encode_varint(value: int) -> bytes:
    if not 0 <= value < 1 << 64:
        raise DiscoveryPacketError("protobuf varint is out of range")
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _rewrite_address(value: bytes, source: ipaddress.IPv4Address | None) -> bytes | None:
    if not 1 <= len(value) <= MAX_ADDRESS_BYTES:
        raise DiscoveryPacketError("announcement address is out of bounds")
    try:
        text = value.decode("utf-8")
        parsed = urlsplit(text)
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise DiscoveryPacketError("announcement address is invalid") from exc
    if parsed.scheme not in _DIRECT_SCHEMES:
        return value
    if port is None or parsed.username is not None or parsed.password is not None:
        raise DiscoveryPacketError("direct announcement address is invalid")
    hostname = parsed.hostname
    if hostname not in {None, "", "0.0.0.0", "::"}:
        return value
    if source is None:
        return value
    if parsed.scheme.endswith("6"):
        return None
    rewritten = urlunsplit(
        (parsed.scheme, f"{source.compressed}:{port}", parsed.path, parsed.query, parsed.fragment)
    ).encode("utf-8")
    if len(rewritten) > MAX_ADDRESS_BYTES:
        raise DiscoveryPacketError("rewritten announcement address is oversized")
    return rewritten


def transform_announcement(packet: bytes, source_ip: str | None = None) -> bytes:
    """Validate an announcement and optionally restore its original IPv4 source."""

    if not isinstance(packet, bytes) or not 5 <= len(packet) <= MAX_PACKET_BYTES:
        raise DiscoveryPacketError("announcement size is invalid")
    if packet[:4] != MAGIC:
        raise DiscoveryPacketError("announcement magic is invalid")
    source: ipaddress.IPv4Address | None = None
    if source_ip is not None:
        try:
            parsed_source = ipaddress.ip_address(source_ip)
        except ValueError as exc:
            raise DiscoveryPacketError("announcement source is invalid") from exc
        if (
            not isinstance(parsed_source, ipaddress.IPv4Address)
            or parsed_source.is_unspecified
            or parsed_source.is_multicast
            or parsed_source.is_loopback
        ):
            raise DiscoveryPacketError("announcement source is invalid")
        source = parsed_source

    position = 4
    output = bytearray(MAGIC)
    device_ids = 0
    addresses = 0
    emitted_addresses = 0
    instances = 0
    while position < len(packet):
        field_start = position
        key, position = _read_varint(packet, position)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number == 0 or wire_type in {3, 4}:
            raise DiscoveryPacketError("announcement protobuf field is invalid")
        if wire_type == 0:
            _value, position = _read_varint(packet, position)
            raw = packet[field_start:position]
            if field_number == 3:
                instances += 1
            output.extend(raw)
            continue
        if wire_type == 1:
            position += 8
        elif wire_type == 2:
            length, value_start = _read_varint(packet, position)
            position = value_start + length
        elif wire_type == 5:
            position += 4
        else:
            raise DiscoveryPacketError("announcement protobuf wire type is unsupported")
        if position > len(packet):
            raise DiscoveryPacketError("announcement protobuf field is truncated")
        if wire_type != 2:
            output.extend(packet[field_start:position])
            continue
        value = packet[value_start:position]
        if field_number == 1:
            device_ids += 1
            if len(value) != 32:
                raise DiscoveryPacketError("announcement device id is invalid")
            output.extend(packet[field_start:position])
            continue
        if field_number == 2:
            addresses += 1
            if addresses > MAX_ADDRESSES:
                raise DiscoveryPacketError("announcement has too many addresses")
            rewritten = _rewrite_address(value, source)
            if rewritten is not None:
                emitted_addresses += 1
                output.extend(_encode_varint(key))
                output.extend(_encode_varint(len(rewritten)))
                output.extend(rewritten)
            continue
        output.extend(packet[field_start:position])

    if device_ids != 1 or not 1 <= addresses <= MAX_ADDRESSES or instances > 1:
        raise DiscoveryPacketError("announcement identity is incomplete or ambiguous")
    if emitted_addresses == 0:
        raise DiscoveryPacketError("announcement has no usable addresses")
    return bytes(output)


def route_announcement(packet: bytes, source_ip: str, interface_name: str) -> RoutedAnnouncement:
    """Choose the sole allowed forwarding direction for one valid datagram."""

    if not interface_name or interface_name == "lo":
        raise DiscoveryPacketError("announcement arrived on an unsupported interface")
    if _is_docker_interface(interface_name):
        return RoutedAnnouncement("lan", transform_announcement(packet))
    if _is_lan_interface(interface_name):
        return RoutedAnnouncement("container", transform_announcement(packet, source_ip))
    raise DiscoveryPacketError("announcement arrived on an unsupported interface")


def _interface_request(name: str) -> bytes:
    encoded = name.encode("ascii", "strict")
    if not encoded or len(encoded) >= 16:
        raise OSError("interface name is invalid")
    return struct.pack("256s", encoded)


def broadcast_addresses(*, docker: bool, control: socket.socket | None = None) -> tuple[str, ...]:
    """Return broadcasts for either Docker bridges or physical LAN interfaces."""

    owned = control is None
    descriptor = control or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addresses: set[str] = set()
    try:
        for _index, name in socket.if_nameindex():
            eligible = _is_docker_interface(name) if docker else _is_lan_interface(name)
            if not eligible:
                continue
            try:
                request = _interface_request(name)
                flags_raw = fcntl.ioctl(descriptor.fileno(), _SIOCGIFFLAGS, request)
                flags = struct.unpack_from("H", flags_raw, 16)[0]
                if flags & (_IFF_UP | _IFF_BROADCAST) != (_IFF_UP | _IFF_BROADCAST):
                    continue
                if flags & _IFF_LOOPBACK:
                    continue
                broadcast_raw = fcntl.ioctl(descriptor.fileno(), _SIOCGIFBRDADDR, request)
                address = socket.inet_ntoa(broadcast_raw[20:24])
                parsed = ipaddress.ip_address(address)
                if isinstance(parsed, ipaddress.IPv4Address) and not parsed.is_unspecified:
                    addresses.add(parsed.compressed)
            except (OSError, UnicodeError, ValueError):
                continue
    finally:
        if owned:
            descriptor.close()
    return tuple(sorted(addresses))


def _packet_interface(ancillary: list[tuple[int, int, bytes]]) -> str:
    for level, kind, data in ancillary:
        if level == socket.IPPROTO_IP and kind == IP_PKTINFO and len(data) >= 4:
            index = struct.unpack_from("I", data)[0]
            try:
                return socket.if_indextoname(index)
            except OSError as exc:
                raise DiscoveryPacketError("announcement interface is unavailable") from exc
    raise DiscoveryPacketError("announcement interface metadata is missing")


def run() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    listener.setsockopt(socket.IPPROTO_IP, IP_PKTINFO, 1)
    listener.bind(("0.0.0.0", LISTEN_PORT))  # nosec B104 - bounded UDP protocol
    lan_broadcasts = broadcast_addresses(docker=False, control=listener)
    docker_broadcasts = broadcast_addresses(docker=True, control=listener)
    recent = RecentForwards()
    refreshed = time.monotonic()
    logger.info(
        "Syncthing discovery bridge ready on UDP %d with %d LAN broadcast target(s)",
        LISTEN_PORT,
        len(lan_broadcasts),
    )
    while True:
        packet, ancillary, _flags, source = listener.recvmsg(MAX_PACKET_BYTES + 1, 128)
        if len(packet) > MAX_PACKET_BYTES or recent.seen(packet):
            continue
        try:
            routed = route_announcement(packet, source[0], _packet_interface(ancillary))
        except DiscoveryPacketError:
            continue
        if time.monotonic() - refreshed >= 60:
            lan_broadcasts = broadcast_addresses(docker=False, control=listener)
            docker_broadcasts = broadcast_addresses(docker=True, control=listener)
            refreshed = time.monotonic()
        targets = docker_broadcasts if routed.target == "container" else lan_broadcasts
        if not targets:
            continue
        recent.remember(routed.payload)
        for broadcast in targets:
            listener.sendto(routed.payload, (broadcast, LISTEN_PORT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Echo bounded Syncthing LAN discovery bridge")
    parser.add_argument("--check", action="store_true", help="validate module availability only")
    parser.add_argument(
        "--check-network",
        action="store_true",
        help="require at least one active Docker bridge",
    )
    arguments = parser.parse_args(argv)
    if arguments.check:
        return 0
    if arguments.check_network:
        return 0 if broadcast_addresses(docker=True) and broadcast_addresses(docker=False) else 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LISTEN_PORT",
    "DiscoveryPacketError",
    "RoutedAnnouncement",
    "RecentForwards",
    "route_announcement",
    "transform_announcement",
]
