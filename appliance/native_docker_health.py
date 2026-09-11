"""Boot gate for native Docker control and its exact managed firewall state."""

from __future__ import annotations

import sys
from typing import Any

from appliance import native_firewall
from appliance.app_registry.docker_client import DockerClient, DockerUnavailable

CONTROL_ORIGIN = "http://127.0.0.1:2375"


def verify() -> dict[str, Any]:
    client = DockerClient(base_url=CONTROL_ORIGIN, allow_direct_socket=False)
    if not client.ping():
        raise DockerUnavailable("native Docker control did not answer its authenticated ping")
    firewall = native_firewall.verify()
    forwards = firewall["state"]["hubForwards"]
    return {
        "control": "ready",
        "hubForwards": len(forwards),
        "managedRules": len(firewall["rules"]),
    }


def main() -> int:
    try:
        result = verify()
    except (DockerUnavailable, OSError, ValueError) as exc:
        print(f"native Docker control health failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_NATIVE_DOCKER_READY "
        f"control={result['control']} "
        f"hub-forwards={result['hubForwards']} "
        f"managed-rules={result['managedRules']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CONTROL_ORIGIN", "verify"]
