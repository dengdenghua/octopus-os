"""Reapply the approved internal ATA/SATA HDD standby timer at boot."""

from __future__ import annotations

import json

from appliance.disk_idle_policy import apply_configured_policy


def main() -> int:
    try:
        result = apply_configured_policy()
    except (OSError, ValueError) as exc:
        print(f"disk idle policy refused to run: {exc}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("errors") == 0 else 2


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
