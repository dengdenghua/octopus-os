#!/usr/bin/env bash
set -euo pipefail

STATE_FILE=/var/lib/echo-os/machine-id
MACHINE_ID_FILE=/etc/machine-id
APP_SPECIFIC_ID=2a7eb9f7114e4fb6b188d8b04b17ed45

[[ -f "$STATE_FILE" && -f "$MACHINE_ID_FILE" ]] || {
  echo "persistent or active machine-id file is missing" >&2
  exit 1
}
persistent_id="$(tr -d '\n' <"$STATE_FILE")"
active_id="$(tr -d '\n' <"$MACHINE_ID_FILE")"
[[ "$persistent_id" =~ ^[0-9a-f]{32}$ && \
   "$persistent_id" != 00000000000000000000000000000000 ]] || {
  echo "persistent machine-id is invalid" >&2
  exit 1
}
[[ "$active_id" == "$persistent_id" ]] || {
  echo "active machine-id does not match persistent device identity" >&2
  exit 1
}

derived_id="$(systemd-id128 machine-id --app-specific="$APP_SPECIFIC_ID")"
[[ "$derived_id" =~ ^[0-9a-f]{32}$ ]] || {
  echo "unable to derive a non-reversible machine identity" >&2
  exit 1
}
echo "ECHO_MACHINE_ID_READY derived=$derived_id source=persistent-var"
