"""Tentacle join router · ``/api/tentacle/join-info``.

Gives the team UI a scan-to-join 口令 + QR for pulling a phone into the group,
WeChat-style: the desktop shows a connect string, the phone pastes/scans it to
fill its gateway URL + token and connect — no manual IP typing.
"""

from __future__ import annotations

import socket
from typing import Any
from urllib.parse import quote


def _lan_ip() -> str:
    """Best-effort LAN IP of this host (the address a phone on the same Wi-Fi
    should dial). Falls back to loopback if offline."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is actually sent for a UDP connect; it just picks the
        # outbound interface so getsockname() yields the LAN address.
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def create_tentacle_join_router(*, ws_port: int, auth_token: str | None) -> Any:
    """Build the router. ``ws_port``/``auth_token`` come from the coordinator the
    main app started."""
    try:
        from fastapi import APIRouter
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("fastapi not installed") from exc

    router = APIRouter(tags=["tentacle"])

    @router.get("/api/tentacle/join-info")
    def join_info() -> dict[str, Any]:
        ip = _lan_ip()
        ws_url = f"ws://{ip}:{ws_port}"
        token = auth_token or ""
        # Compact, parseable on the phone: echo://join?ws=...&token=...
        connect_string = f"echo://join?ws={quote(ws_url, safe='')}"
        if token:
            connect_string += f"&token={quote(token, safe='')}"
        return {
            "lan_ip": ip,
            "ws_port": ws_port,
            "ws_url": ws_url,
            "token": token,
            "connect_string": connect_string,
        }

    return router
