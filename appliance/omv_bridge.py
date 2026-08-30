"""Host-side OpenMediaVault storage bridge over a Unix socket.

The bridge deliberately does not proxy arbitrary OMV service/method names. It
exposes sanitized inventory plus narrowly validated desired-state controls for
creating one simple shared folder on an existing writable OMV mount, publishing
an existing shared folder through private SMB or private-network NFS, and
setting an existing filesystem user's or group's hard quota. All controls use
a deterministic plan, stale-plan rejection, verification and best-effort
rollback.
"""

from __future__ import annotations

import argparse
import secrets
import stat
import threading
from pathlib import Path

from appliance.omv_bridge_accounts import OmvAccountControlMixin
from appliance.omv_bridge_contract import (
    DEFAULT_OMV_ENGINE_SOCKET,
    DEFAULT_RPC_TIMEOUT_SECONDS,
    OMV_CONFIGOBJECT_NEW_UUID,
    MdstatReader,
    RpcRunner,
    SecretRpcRunner,
    TopologyRunner,
)
from appliance.omv_bridge_errors import (
    OmvBridgeConflict,
    OmvBridgeError,
    OmvBridgeValidationError,
)
from appliance.omv_bridge_http import (
    OmvBridgeHttpServer,
    OmvBridgeRequestHandler,
    create_server,
)
from appliance.omv_bridge_inventory import OmvInventoryMixin
from appliance.omv_bridge_quota import OmvQuotaControlMixin
from appliance.omv_bridge_runners import (
    LsblkTopologyRunner,
    OmvCommandRunner,
    OmvEngineSecretRunner,
    ProcMdstatReader,
)
from appliance.omv_bridge_sharing import OmvSharingControlMixin


class OmvReadOnlyService(
    OmvInventoryMixin,
    OmvAccountControlMixin,
    OmvSharingControlMixin,
    OmvQuotaControlMixin,
):
    def __init__(
        self,
        runner: RpcRunner,
        topology_runner: TopologyRunner | None = None,
        mdstat_reader: MdstatReader | None = None,
        secret_runner: SecretRpcRunner | None = None,
        plan_secret: bytes | None = None,
    ) -> None:
        self._runner = runner
        self._topology_runner = topology_runner
        self._mdstat_reader = mdstat_reader
        self._secret_runner = secret_runner
        self._plan_secret = plan_secret or secrets.token_bytes(32)
        if len(self._plan_secret) < 32:
            raise OmvBridgeError("OMV account plan secret is too short")
        self._control_lock = threading.RLock()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Constrained Echo storage bridge")
    parser.add_argument("--socket", type=Path, default=Path("/run/echo-omv/omv.sock"))
    parser.add_argument("--omv-rpc", type=Path, default=Path("/usr/sbin/omv-rpc"))
    parser.add_argument(
        "--omv-engine-socket",
        type=Path,
        default=Path(DEFAULT_OMV_ENGINE_SOCKET),
    )
    parser.add_argument("--lsblk", type=Path, default=Path("/usr/bin/lsblk"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_RPC_TIMEOUT_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.timeout <= 120:
        raise SystemExit("--timeout must be between 1 and 120 seconds")
    service = OmvReadOnlyService(
        OmvCommandRunner(args.omv_rpc, timeout_seconds=args.timeout),
        LsblkTopologyRunner(args.lsblk, timeout_seconds=args.timeout),
        ProcMdstatReader(),
        OmvEngineSecretRunner(args.omv_engine_socket, timeout_seconds=args.timeout),
    )
    server = create_server(args.socket, service)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            info = args.socket.lstat()
            if stat.S_ISSOCK(info.st_mode):
                args.socket.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LsblkTopologyRunner",
    "OMV_CONFIGOBJECT_NEW_UUID",
    "OmvBridgeConflict",
    "OmvBridgeError",
    "OmvBridgeHttpServer",
    "OmvBridgeRequestHandler",
    "OmvBridgeValidationError",
    "OmvCommandRunner",
    "OmvEngineSecretRunner",
    "OmvReadOnlyService",
    "ProcMdstatReader",
    "create_server",
    "main",
]
