#!/usr/bin/env python3
"""Exercise the storage broker's real Linux Unix-socket trust boundary."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

import appliance.native_storage_broker as broker_module
from appliance.native_storage_broker import (
    HEALTH_SCHEMA,
    OPERATION_TARGETS,
    NativeStorageBrokerClient,
    NativeStorageBrokerError,
    NativeStorageBrokerServer,
)


def _stop_server(
    server: NativeStorageBrokerServer,
    thread: threading.Thread,
    socket_path: Path,
) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)
    if thread.is_alive():
        raise RuntimeError("native storage broker test server did not stop")
    socket_path.unlink(missing_ok=True)


def _start_server(
    socket_path: Path, *, allowed_uids: frozenset[int]
) -> tuple[NativeStorageBrokerServer, threading.Thread]:
    server = NativeStorageBrokerServer(socket_path, allowed_uids=allowed_uids)
    socket_path.chmod(0o660)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def main() -> int:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("native storage broker peer test requires Linux")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError("native storage broker peer test requires root")
    if not hasattr(socket, "AF_UNIX") or not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("native storage broker peer test requires AF_UNIX and SO_PEERCRED")

    with tempfile.TemporaryDirectory(prefix="echo-storage-broker-") as temporary:
        root = Path(temporary)
        socket_path = root / "broker.sock"

        server, thread = _start_server(socket_path, allowed_uids=frozenset({os.geteuid()}))
        try:
            client = NativeStorageBrokerClient(socket_path, timeout=2.0)
            result = client.probe()
            expected = {
                "schema": HEALTH_SCHEMA,
                "status": "ok",
                "operationCount": len(OPERATION_TARGETS),
            }
            if result != expected:
                raise RuntimeError("native storage broker health contract did not match")

            original_resolver = broker_module._resolve_operation
            broker_module._resolve_operation = lambda _operation: (
                lambda desired: {
                    "operation": "create",
                    "planId": "root-plan",
                    "desired": desired,
                }
            )
            try:
                plan = client.plan("native_storage.plan_nfs", {"client": "192.0.2.0/24"})
                broker_module._resolve_operation = lambda _operation: (
                    lambda: {
                        "users": [],
                        "sharedFolders": [],
                    }
                )
                overview = client.read("native_storage.sharing_overview", {})
            finally:
                broker_module._resolve_operation = original_resolver
            if plan != {
                "operation": "create",
                "planId": "root-plan",
                "desired": {"client": "192.0.2.0/24"},
            }:
                raise RuntimeError("native storage broker plan channel did not match")
            if overview != {"users": [], "sharedFolders": []}:
                raise RuntimeError("native storage broker read channel did not match")
        finally:
            _stop_server(server, thread, socket_path)

        denied, denied_thread = _start_server(socket_path, allowed_uids=frozenset())
        try:
            try:
                NativeStorageBrokerClient(socket_path, timeout=2.0).probe()
            except NativeStorageBrokerError as exc:
                if "not authorized" not in str(exc):
                    raise RuntimeError(
                        "native storage broker returned the wrong peer denial"
                    ) from exc
            else:
                raise RuntimeError("native storage broker accepted a denied peer")
        finally:
            _stop_server(denied, denied_thread, socket_path)

    print("native storage broker Linux peer-credential contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
