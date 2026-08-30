#!/usr/bin/env bash
# Watchdog for the echo backend on :8000.
#
# The backend has been killed by external tooling four times (15:45 / 23:36 /
# 02:07...). This loop health-checks :8000 every 30s and restarts the server
# when it is down. Logs to /tmp/echo-watchdog.log.
#
# Usage:  bash scripts/watchdog_backend.sh   (keep it running in the background)
# For a boot-persistent guard use the launchd agent:
#   launchctl load ~/Library/LaunchAgents/com.echo.backend.plist
set -u

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HEALTH_URL="http://127.0.0.1:8000/"
CHECK_INTERVAL=30
# Grace window for SIGTERM drain (registry dispose, journal flush, in-flight
# job settlement) before escalating to SIGKILL.
TERM_GRACE_SECONDS=30
LOG=/tmp/echo-watchdog.log

echo "$(date '+%F %T') watchdog started" >> "$LOG"

while true; do
  if ! curl -s -o /dev/null --max-time 5 "$HEALTH_URL"; then
    echo "$(date '+%F %T') backend DOWN — restarting" >> "$LOG"
    stale=$(lsof -nP -iTCP:8000 -sTCP:LISTEN -t 2>/dev/null)
    if [ -n "$stale" ]; then
      kill "$stale" 2>/dev/null
      # Wait out the graceful-shutdown budget before escalating. The
      # backend's teardown is async (awaits job settlement + journal
      # flush) and needs far more than the previous fixed 1s - killing
      # at 1s orphaned every running background job with no journal
      # trace of the interruption.
      for _ in $(seq 1 "$TERM_GRACE_SECONDS"); do
        if ! kill -0 "$stale" 2>/dev/null; then
          break
        fi
        sleep 1
      done
      if kill -0 "$stale" 2>/dev/null; then
        echo "$(date '+%F %T') backend ignored SIGTERM for ${TERM_GRACE_SECONDS}s - SIGKILL" >> "$LOG"
        kill -9 "$stale" 2>/dev/null
        sleep 1
      fi
    fi
    (
      cd "$REPO_ROOT" || exit 1
      exec ./.venv/bin/python -m runtime serve \
        --config config.local.yaml \
        --port 8000
    ) >> /tmp/echo-backend.log 2>&1 &
    echo "$(date '+%F %T') backend restarted (pid $!)" >> "$LOG"
  fi
  sleep "$CHECK_INTERVAL"
done


