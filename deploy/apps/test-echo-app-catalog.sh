#!/usr/bin/env bash
set -euo pipefail

APPS_DIR="$(cd "$(dirname "$0")" && pwd)"
CATALOG="$APPS_DIR/echo-app-catalog"
REMOTE_DEFINITION="$APPS_DIR/flathub.flatpakrepo"
EXPECTED_SHA256=3371dd250e61d9e1633630073fefda153cd4426f72f4afa0c3373ae2e8fea03a

TEST_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

FAKE_FLATPAK="$TEST_ROOT/flatpak"
FAKE_STATE="$TEST_ROOT/fake-remotes"
FAKE_LOG="$TEST_ROOT/flatpak.log"
cat >"$FAKE_FLATPAK" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
command_name="${1:-}"
shift || true
printf '%s %s\n' "$command_name" "$*" >>"$FAKE_FLATPAK_LOG"
case "$command_name" in
  remotes)
    if [[ -f "$FAKE_FLATPAK_STATE" ]]; then
      cat "$FAKE_FLATPAK_STATE"
    fi
    ;;
  remote-add)
    [[ "$*" == "--system --if-not-exists --from flathub $FAKE_REMOTE_DEFINITION" ]]
    printf 'flathub\thttps://dl.flathub.org/repo/\n' >"$FAKE_FLATPAK_STATE"
    ;;
  *)
    echo "unexpected fake flatpak command: $command_name" >&2
    exit 2
    ;;
esac
EOF
chmod 0755 "$FAKE_FLATPAK"

run_catalog() {
  ECHO_APP_CATALOG_TESTING=yes \
  ECHO_APP_CATALOG_FLATPAK="$FAKE_FLATPAK" \
  ECHO_APP_CATALOG_REMOTE_DEFINITION="${1:-$REMOTE_DEFINITION}" \
  ECHO_APP_CATALOG_REMOTE_SHA256="$EXPECTED_SHA256" \
  ECHO_APP_CATALOG_STATE_DIRECTORY="$2" \
  FAKE_FLATPAK_STATE="$FAKE_STATE" \
  FAKE_FLATPAK_LOG="$FAKE_LOG" \
  FAKE_REMOTE_DEFINITION="${1:-$REMOTE_DEFINITION}" \
    "$CATALOG"
}

STATE_DIRECTORY="$TEST_ROOT/echo-state"
run_catalog "$REMOTE_DEFINITION" "$STATE_DIRECTORY"
grep -qx 'schema=1' "$STATE_DIRECTORY/app-catalog-provisioned"
grep -qx "definition_sha256=$EXPECTED_SHA256" \
  "$STATE_DIRECTORY/app-catalog-provisioned"
grep -q '^remote-add --system --if-not-exists --from flathub ' "$FAKE_LOG"
[[ "$(grep -c '^remote-add ' "$FAKE_LOG")" -eq 1 ]]

# An idempotent rerun accepts the already-pinned URL without adding it again.
run_catalog "$REMOTE_DEFINITION" "$STATE_DIRECTORY"
[[ "$(grep -c '^remote-add ' "$FAKE_LOG")" -eq 1 ]]

# A same-name remote at another URL is not overwritten or marked complete.
printf 'flathub\thttps://example.invalid/repo/\n' >"$FAKE_STATE"
CONFLICT_STATE="$TEST_ROOT/conflict-state"
if run_catalog "$REMOTE_DEFINITION" "$CONFLICT_STATE" 2>/dev/null; then
  echo "catalog unexpectedly replaced a conflicting remote" >&2
  exit 1
fi
[[ ! -e "$CONFLICT_STATE/app-catalog-provisioned" ]]

# The locally shipped trust definition is checked before it reaches Flatpak.
TAMPERED_DEFINITION="$TEST_ROOT/tampered.flatpakrepo"
cp "$REMOTE_DEFINITION" "$TAMPERED_DEFINITION"
printf '\n# modified\n' >>"$TAMPERED_DEFINITION"
rm -f -- "$FAKE_STATE"
TAMPERED_STATE="$TEST_ROOT/tampered-state"
if run_catalog "$TAMPERED_DEFINITION" "$TAMPERED_STATE" 2>/dev/null; then
  echo "catalog unexpectedly accepted a modified repository definition" >&2
  exit 1
fi
[[ ! -e "$TAMPERED_STATE/app-catalog-provisioned" ]]

echo "Echo OS application catalog policy tests OK"
