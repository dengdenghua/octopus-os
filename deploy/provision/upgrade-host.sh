#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "请用 root 运行" >&2; exit 1; }
[[ -x /opt/echo-os/.venv/bin/python ]] || {
  echo "Echo OS Python 环境不存在" >&2
  exit 1
}
cd /opt/echo-os
exec /opt/echo-os/.venv/bin/python -m deploy.provision.host_migration "$@"
