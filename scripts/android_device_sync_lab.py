#!/usr/bin/env python3
"""Run an isolated Echo device-sync endpoint for Android acceptance testing.

This is deliberately a developer lab, not an appliance bootstrap path.  It
uses the production DeviceLinkService, DeviceSyncService, FileManager and HTTP
router while keeping every byte below an explicitly supplied temporary state
directory.  The one-time pairing credential is written to a caller-selected
mode-0600 file and is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import uvicorn
from fastapi import FastAPI

from appliance.approval import HighRiskApprovalService
from appliance.audit import ApplianceAudit
from appliance.device_link import DeviceLinkService
from appliance.files import FileManager
from appliance.sync import DeviceSyncService, create_device_sync_router


class _EmptyPool:
    def all(self) -> list[object]:
        return []


class _LabWebSocketServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self.auth_token = ""
        self._server: object | None = None
        self._connections: dict[str, object] = {}

    def _check_auth(self, _message: object) -> bool:
        return False


class _LabCoordinator:
    def __init__(self, port: int) -> None:
        self.ws_server = _LabWebSocketServer(port)
        self.pool = _EmptyPool()

    async def start(self) -> None:
        self.ws_server._server = object()

    async def stop(self) -> None:
        self.ws_server._server = None


def _safe_empty_state_dir(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_symlink():
        raise ValueError("lab state path must not be a symbolic link")
    candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = candidate.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("lab state path must be a real directory")
    state_dir = candidate.resolve()
    if any(state_dir.iterdir()):
        raise ValueError("lab state directory must be empty")
    with contextlib.suppress(OSError):
        state_dir.chmod(0o700)
    return state_dir


def _write_pairing_file(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True)
            output.write("\n")
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--pairing-output", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--public-host", default="10.0.2.2")
    parser.add_argument("--ws-port", type=int, default=8765)
    return parser.parse_args(argv)


def build_lab(
    *,
    state_dir: Path,
    device_id: str,
    public_host: str,
    port: int,
    ws_port: int,
) -> tuple[FastAPI, DeviceLinkService, dict[str, Any]]:
    secret = secrets.token_urlsafe(48)
    coordinator = _LabCoordinator(ws_port)
    link = DeviceLinkService(
        data_dir=state_dir / "device-link",
        jwt_secret=secret,
        coordinator_factory=lambda: coordinator,
        ws_port=ws_port,
        device_sync_port=port,
        public_host=public_host,
        allow_host_resolver_fallback=False,
    )
    asyncio.run(link.enable())
    invitation = link.create_pairing_invitation()
    connect_string = str(invitation["connectString"])
    token = parse_qs(urlparse(connect_string).query)["token"][0]
    paired = coordinator.ws_server._check_auth(
        {
            "params": {
                "tentacle_id": device_id,
                "auth_token": token,
                "platform": "android",
                "model": "acceptance-lab",
            }
        }
    )
    if not paired:
        raise RuntimeError("could not bind pairing invitation to Android device")

    files = FileManager(state_dir / "nas", upload_reserve_bytes=0)
    sync = DeviceSyncService(
        data_dir=state_dir / "device-sync",
        files=files,
        device_link=link,
    )
    sync.set_scope(device_id, "files", enabled=True)
    sync.set_scope(device_id, "photos", enabled=True)

    audit = ApplianceAudit.from_data_dir(state_dir / "audit", jwt_secret=secret)
    approval = HighRiskApprovalService(
        password_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        jwt_secret=secret,
        audit=audit,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await link.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.include_router(
        create_device_sync_router(
            sync,
            jwt_secret=secret,
            approval=approval,
            audit=audit,
        )
    )
    return app, link, invitation


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not 1 <= args.port <= 65535 or not 1 <= args.ws_port <= 65535:
        raise ValueError("ports must be between 1 and 65535")
    state_dir = _safe_empty_state_dir(args.state_dir)
    app, _link, invitation = build_lab(
        state_dir=state_dir,
        device_id=args.device_id,
        public_host=args.public_host,
        port=args.port,
        ws_port=args.ws_port,
    )
    _write_pairing_file(
        Path(args.pairing_output),
        {
            "schema": "echo.android-device-sync-lab.v1",
            "deviceId": args.device_id,
            "connectString": invitation["connectString"],
            "stateDir": str(state_dir),
        },
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
