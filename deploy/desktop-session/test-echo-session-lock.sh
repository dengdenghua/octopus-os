#!/usr/bin/env bash
set -euo pipefail

SESSION_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_SERVICE="$SESSION_DIR/echo-session-lock"
SCREEN_LOCKER="$SESSION_DIR/echo-screen-locker"
FIXTURE="$SESSION_DIR/lock-command-fixture"
TEST_ROOT="$(mktemp -d)"
cleanup() { rm -rf -- "$TEST_ROOT"; }
trap cleanup EXIT INT TERM

for name in xset xss-lock xsecurelock; do
  ln -s "$FIXTURE" "$TEST_ROOT/$name"
done
TEST_LOG="$TEST_ROOT/lock.log"

if ECHO_SESSION_LOCK_TESTING=yes \
     ECHO_LOCK_XSET_BIN="$TEST_ROOT/xset" \
     ECHO_LOCK_XSS_LOCK_BIN="$TEST_ROOT/xss-lock" \
     ECHO_LOCK_SCREEN_LOCKER="$SCREEN_LOCKER" \
     ECHO_LOCK_IDLE_SECONDS=0 \
     ECHO_LOCK_DISPLAY_OFF_SECONDS=10 \
     DISPLAY=:99 \
     "$LOCK_SERVICE" >/dev/null 2>&1; then
  echo "invalid zero idle timeout was accepted" >&2
  exit 1
fi

ECHO_SESSION_LOCK_TESTING=yes \
ECHO_LOCK_XSET_BIN="$TEST_ROOT/xset" \
ECHO_LOCK_XSS_LOCK_BIN="$TEST_ROOT/xss-lock" \
ECHO_LOCK_SCREEN_LOCKER="$SCREEN_LOCKER" \
ECHO_LOCK_IDLE_SECONDS=2 \
ECHO_LOCK_DISPLAY_OFF_SECONDS=3 \
ECHO_LOCK_TEST_LOG="$TEST_LOG" \
DISPLAY=:99 \
  "$LOCK_SERVICE"

grep -qx 'xset <s> <2> <2> pam= display=:99' "$TEST_LOG"
grep -qx 'xset <+dpms> pam= display=:99' "$TEST_LOG"
grep -qx 'xset <dpms> <0> <0> <3> pam= display=:99' "$TEST_LOG"
grep -qx "xss-lock <--transfer-sleep-lock> <--> <$SCREEN_LOCKER> pam=echo-lock display=:99" "$TEST_LOG"

ECHO_SCREEN_LOCKER_TESTING=yes \
ECHO_XSECURELOCK_BIN="$TEST_ROOT/xsecurelock" \
ECHO_LOCK_TEST_LOG="$TEST_LOG" \
XSECURELOCK_PAM_SERVICE=echo-lock \
DISPLAY=:99 \
  "$SCREEN_LOCKER" >"$TEST_ROOT/screen-locker.log"
grep -qx 'ECHO_LOCK_SCREEN_LAUNCHED provider=xsecurelock pam=echo-lock' \
  "$TEST_ROOT/screen-locker.log"
grep -qx 'xsecurelock pam=echo-lock display=:99' "$TEST_LOG"

# xss-lock exports a logind delay-lock descriptor before suspend. The adapter
# must keep its parent copy only during startup and prevent XSecureLock itself
# from inheriting it, otherwise suspend can remain blocked until unlock.
if (( BASH_VERSINFO[0] >= 4 )); then
  exec 9<>"$TEST_ROOT/sleep-lock"
  ECHO_SCREEN_LOCKER_TESTING=yes \
  ECHO_XSECURELOCK_BIN="$TEST_ROOT/xsecurelock" \
  ECHO_SLEEP_LOCK_DELAY_SECONDS=0.05 \
  ECHO_LOCK_TEST_LOG="$TEST_LOG" \
  XSECURELOCK_PAM_SERVICE=echo-lock \
  XSS_SLEEP_LOCK_FD=9 \
  DISPLAY=:99 \
    "$SCREEN_LOCKER" >"$TEST_ROOT/sleep-locker.log"
  exec 9>&-
  grep -qx 'ECHO_LOCK_SCREEN_LAUNCHED provider=xsecurelock pam=echo-lock' \
    "$TEST_ROOT/sleep-locker.log"
  grep -qx 'sleep-lock-fd=closed' "$TEST_LOG"
else
  echo "  - sleep-lock FD runtime case requires Bash 4+; CI/Debian exercises it"
fi

echo "Echo OS X11 session-lock policy tests OK"
