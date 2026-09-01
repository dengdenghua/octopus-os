#!/usr/bin/env bash
# Prepare a dedicated Debian/Ubuntu host before the official runner is registered.
set -euo pipefail

IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
[[ $# -eq 2 ]] || {
  echo "usage: $0 ABSOLUTE_WORK_ROOT RUNNER_USER" >&2
  exit 2
}
WORK_ROOT="$1"
RUNNER_USER="$2"

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
  echo "Echo OS image runner host must be Linux x86-64" >&2
  exit 1
}
[[ "$(id -u)" -eq 0 ]] || {
  echo "runner host configuration requires root" >&2
  exit 1
}
[[ "$WORK_ROOT" == /* && "$WORK_ROOT" != / && ! -L "$WORK_ROOT" ]] || {
  echo "work root must be an absolute non-symlink directory below /" >&2
  exit 2
}
[[ "$WORK_ROOT" == /srv/echo-os-image-runner ]] || {
  echo "work root must be the dedicated /srv/echo-os-image-runner path" >&2
  exit 2
}
if [[ -e "$WORK_ROOT" ]]; then
  [[ -d "$WORK_ROOT" && ! -L "$WORK_ROOT" ]] || {
    echo "existing work root must be a real directory" >&2
    exit 2
  }
  [[ -z "$(find "$WORK_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
    echo "work root must be empty before host configuration" >&2
    exit 2
  }
fi
[[ "$RUNNER_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || {
  echo "invalid runner service account" >&2
  exit 2
}
id "$RUNNER_USER" >/dev/null 2>&1 || {
  echo "runner service account does not exist: $RUNNER_USER" >&2
  exit 2
}
grep -Eq "^${RUNNER_USER}:" /etc/passwd || {
  echo "runner service account must be defined in the local password database" >&2
  exit 2
}
[[ "$(id -u "$RUNNER_USER")" -ne 0 ]] || {
  echo "the Actions runner must not use root as its service account" >&2
  exit 2
}

. /etc/os-release
case "${ID:-}" in
  debian|ubuntu) ;;
  *)
    echo "runner host configuration supports Debian and Ubuntu only" >&2
    exit 1
    ;;
esac

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  coreutils docker.io kmod python3 qemu-system-x86 qemu-utils udev util-linux

install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 \
  "$IMAGE_DIR/cleanup-linux-image-runner.py" \
  /usr/local/libexec/echo-os-image-runner-cleanup.py
install -o root -g root -m 0755 \
  "$IMAGE_DIR/runner-host/echo-os-image-runner-job-hook.sh" \
  /usr/local/libexec/echo-os-image-runner-job-hook.sh
install -o root -g root -m 0755 \
  "$IMAGE_DIR/verify-linux-image-runner-registration.py" \
  /usr/local/libexec/echo-os-image-runner-registration.py

RUNNER_GROUP="$(id -gn "$RUNNER_USER")"
install -d -o "$RUNNER_USER" -g "$RUNNER_GROUP" -m 0700 "$WORK_ROOT"
CANONICAL_WORK_ROOT="$(realpath "$WORK_ROOT")"
[[ "$CANONICAL_WORK_ROOT" == "$WORK_ROOT" ]] || {
  echo "work root must not contain aliases or parent-directory traversal" >&2
  exit 2
}

install -o root -g root -m 0644 \
  "$IMAGE_DIR/runner-host/echo-os-image-runner.modules.conf" \
  /etc/modules-load.d/echo-os-image-runner.conf
install -o root -g root -m 0644 \
  "$IMAGE_DIR/runner-host/echo-os-image-runner.modprobe.conf" \
  /etc/modprobe.d/echo-os-image-runner.conf

usermod -aG docker,kvm "$RUNNER_USER"
systemctl enable --now docker.service
modprobe loop max_loop=64
modprobe nbd nbds_max=16 max_part=16
udevadm settle --timeout=30

HOST_EVIDENCE="$WORK_ROOT/echo-image-runner-host.json"
[[ ! -e "$HOST_EVIDENCE" && ! -L "$HOST_EVIDENCE" ]] || {
  echo "move the previous host evidence before reconfiguration: $HOST_EVIDENCE" >&2
  exit 1
}
runuser -u "$RUNNER_USER" -- \
  "$IMAGE_DIR/verify-linux-image-runner-host.py" \
  --work-root "$WORK_ROOT" \
  --output "$HOST_EVIDENCE"

echo "Echo OS image runner host prepared."
echo "Register the official GitHub Actions runner as $RUNNER_USER with:"
echo "  work directory: $WORK_ROOT"
echo "  custom label: echo-os-image"
echo "Use only the one-time registration command shown by GitHub; this script accepts no credential."
echo "After official registration and svc.sh install, bind the cleanup hooks before service start:"
echo "  sudo $IMAGE_DIR/configure-linux-image-runner-hooks.sh ABSOLUTE_RUNNER_APPLICATION_DIR $RUNNER_USER"
