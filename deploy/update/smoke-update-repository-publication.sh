#!/usr/bin/env bash
# Exercise the repository publisher with a real ephemeral OpenPGP identity.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PUBLISHER="$HERE/publish_update_repository.py"
TEST_ROOT="$(mktemp -d)"
cleanup() { rm -rf -- "$TEST_ROOT"; }
trap cleanup EXIT INT TERM

for command_name in gpg gpgv python3 sha256sum readlink cmp; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "repository publication smoke dependency missing: $command_name" >&2
    exit 1
  }
done
[[ -x "$PUBLISHER" && -x /usr/bin/gpgv ]] || {
  echo "repository publisher and fixed /usr/bin/gpgv are required" >&2
  exit 1
}

export GNUPGHOME="$TEST_ROOT/gnupg"
mkdir -m 0700 -- "$GNUPGHOME"
gpg --batch --passphrase '' --quick-generate-key \
  'Echo OS repository publication smoke <repository-smoke@example.invalid>' \
  ed25519 sign 1d >/dev/null 2>&1
FINGERPRINT="$(
  gpg --batch --with-colons --list-secret-keys |
    awk -F: '$1 == "fpr" { print $10; exit }'
)"
[[ "$FINGERPRINT" =~ ^[0-9A-Fa-f]{40,64}$ ]] || {
  echo "repository smoke did not create one full signing fingerprint" >&2
  exit 1
}
KEYRING="$TEST_ROOT/update-keyring.gpg"
gpg --batch --export "$FINGERPRINT" >"$KEYRING"
[[ -s "$KEYRING" ]] || {
  echo "repository smoke public keyring is empty" >&2
  exit 1
}

make_bundle() {
  local version="$1"
  local commit="$2"
  local bundle="$TEST_ROOT/bundle-$version"
  mkdir -m 0700 -- "$bundle"
  python3 - "$bundle" "$version" "$commit" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

bundle = Path(sys.argv[1])
version = sys.argv[2]
commit = sys.argv[3]
source = (
    json.dumps(
        {
            "schema": 1,
            "kind": "echo-os-source-identity",
            "repository": "https://github.com/dengdenghua/echo-os.git",
            "commit": commit,
            "tree": "b" * 40,
            "commit_time": "2026-08-26T00:00:00+00:00",
            "source_date_epoch": 1787702400,
            "dirty": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n"
).encode()
payloads = {
    f"echo-os_{version}.root.11111111-2222-3333-4444-555555555555.raw.zst": b"root-" + version.encode(),
    f"echo-os_{version}.root-verity.66666666-7777-8888-9999-aaaaaaaaaaaa.raw.zst": b"verity-" + version.encode(),
    f"echo-os_{version}.root-verity-sig.bbbbbbbb-cccc-dddd-eeee-ffffffffffff.raw.zst": b"verity-signature-" + version.encode(),
    f"echo-os_{version}.efi": b"uki-" + version.encode(),
    "OS-SOURCE-IDENTITY.json": source,
}
for name, contents in payloads.items():
    (bundle / name).write_bytes(contents)
manifest = "".join(
    f"{hashlib.sha256(contents).hexdigest()}  {name}\n"
    for name, contents in payloads.items()
).encode()
(bundle / "SHA256SUMS").write_bytes(manifest)
PY
  gpg --batch --yes --local-user "$FINGERPRINT" --detach-sign \
    --output "$bundle/SHA256SUMS.gpg" "$bundle/SHA256SUMS"
  printf '%s\n' "$bundle"
}

BUNDLE_ONE="$(make_bundle 0.2.1 "$(printf 'a%.0s' {1..40})")"
BUNDLE_TWO="$(make_bundle 0.2.2 "$(printf 'c%.0s' {1..40})")"
REPOSITORY="$TEST_ROOT/repository"
mkdir -m 0700 -- "$REPOSITORY"

FIRST_OUTPUT="$TEST_ROOT/publish-one.log"
SECOND_OUTPUT="$TEST_ROOT/publish-two.log"
VERIFY_OUTPUT="$TEST_ROOT/verify-current.log"
python3 "$PUBLISHER" publish \
  --bundle "$BUNDLE_ONE" \
  --keyring "$KEYRING" \
  --repository-root "$REPOSITORY" \
  --sequence 1 >"$FIRST_OUTPUT"
grep -q '^ECHO_UPDATE_REPOSITORY_PUBLISHED sequence=1 version=0.2.1 ' "$FIRST_OUTPUT"

python3 "$PUBLISHER" publish \
  --bundle "$BUNDLE_TWO" \
  --keyring "$KEYRING" \
  --repository-root "$REPOSITORY" \
  --sequence 2 >"$SECOND_OUTPUT"
grep -q '^ECHO_UPDATE_REPOSITORY_PUBLISHED sequence=2 version=0.2.2 ' "$SECOND_OUTPUT"

python3 "$PUBLISHER" verify-current \
  --keyring "$KEYRING" \
  --repository-root "$REPOSITORY" >"$VERIFY_OUTPUT"
grep -q '^ECHO_UPDATE_REPOSITORY_VERIFIED sequence=2 version=0.2.2 ' "$VERIFY_OUTPUT"

CHANNEL="$REPOSITORY/stable/x86-64"
[[ "$(readlink "$CHANNEL")" == \
   '../releases/x86-64/00000000000000000002-0.2.2' ]]
cmp -- "$CHANNEL/SHA256SUMS" "$BUNDLE_TWO/SHA256SUMS"
gpgv --keyring "$KEYRING" \
  "$CHANNEL/SHA256SUMS.gpg" "$CHANNEL/SHA256SUMS" >/dev/null 2>&1

if python3 "$PUBLISHER" publish \
  --bundle "$BUNDLE_ONE" \
  --keyring "$KEYRING" \
  --repository-root "$REPOSITORY" \
  --sequence 1 >"$TEST_ROOT/downgrade.log" 2>&1; then
  echo "repository publisher accepted a sequence rollback" >&2
  exit 1
fi
[[ "$(readlink "$CHANNEL")" == \
   '../releases/x86-64/00000000000000000002-0.2.2' ]]

printf 'ECHO_UPDATE_REPOSITORY_GPG_OK sequence=2 version=0.2.2 rollback=rejected\n'
