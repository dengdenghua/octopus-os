#!/usr/bin/env bash
# Replay one downloaded Echo delivery candidate without GitHub or repository source.
set -euo pipefail

AUDIT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
CHECKSUM_MANIFEST="$AUDIT_ROOT/echo-delivery-release-candidate.sha256"
PACKAGED_INDEX="$AUDIT_ROOT/echo-delivery-release-evidence-index.json"

required_files=(
  echo-delivery-source-preflight.json
  echo-release-candidate-preflight.json
  echo-delivery-release-evidence-index.json
  delivery_source_preflight.py
  hub_lifecycle_lab.py
  lan_discovery_functional_lab.py
  paperless_functional_lab.py
  physical_acceptance.py
  physical_acceptance_capture.py
  product_delivery_bundle.py
  release_candidate_preflight.py
  release_evidence_index.py
  verify_public_keyring.py
  verify-os-image-evidence-release.sh
  verify-release-candidate-bundle.sh
  inputs/os-image-evidence.json
  inputs/os-image-evidence.json.gpg
  inputs/os-image-keyring.gpg
  inputs/ab-update-evidence.json
  inputs/ab-update-evidence.json.gpg
  inputs/ab-update-keyring.gpg
  inputs/omv-evidence.json
  inputs/omv-verification.json
  inputs/openmediavault-echo-os.deb
  inputs/appliance-release.json
)

[[ ! -L "$0" && -f "$CHECKSUM_MANIFEST" && ! -L "$CHECKSUM_MANIFEST" ]] || {
  echo "candidate audit entrypoint or checksum manifest is unsafe" >&2
  exit 1
}
for command_name in awk cmp find mktemp python3 rm sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "candidate audit command is unavailable: $command_name" >&2
    exit 1
  }
done
for relative_name in "${required_files[@]}"; do
  candidate="$AUDIT_ROOT/$relative_name"
  [[ -f "$candidate" && ! -L "$candidate" ]] || {
    echo "candidate audit input is missing or unsafe: $relative_name" >&2
    exit 1
  }
done

expected_entries=$((${#required_files[@]} + 2))
actual_entries=0
while IFS= read -r -d '' candidate; do
  relative_name="${candidate#"$AUDIT_ROOT"/}"
  allowed=false
  if [[ "$relative_name" == inputs || \
        "$relative_name" == "$(basename "$CHECKSUM_MANIFEST")" ]]; then
    allowed=true
  else
    for required_name in "${required_files[@]}"; do
      if [[ "$relative_name" == "$required_name" ]]; then
        allowed=true
        break
      fi
    done
  fi
  [[ "$allowed" == true ]] || {
    echo "candidate audit bundle contains an unexpected path: $relative_name" >&2
    exit 1
  }
  actual_entries=$((actual_entries + 1))
done < <(find "$AUDIT_ROOT" -mindepth 1 -print0)
[[ "$actual_entries" -eq "$expected_entries" && -d "$AUDIT_ROOT/inputs" && \
   ! -L "$AUDIT_ROOT/inputs" ]] || {
  echo "candidate audit bundle inventory is incomplete or unsafe" >&2
  exit 1
}

checksum_lines="$(awk 'END { print NR + 0 }' "$CHECKSUM_MANIFEST")"
[[ "$checksum_lines" =~ ^[0-9]+$ && "$checksum_lines" -eq "${#required_files[@]}" ]] || {
  echo "candidate audit checksum inventory has the wrong size" >&2
  exit 1
}
awk 'NF != 2 || length($1) != 64 || $1 !~ /^[0-9a-f]+$/ { exit 1 }' \
  "$CHECKSUM_MANIFEST" || {
  echo "candidate audit checksum manifest is malformed" >&2
  exit 1
}
for relative_name in "${required_files[@]}"; do
  checksum_occurrences="$(
    awk -v expected="$relative_name" '$2 == expected { count += 1 } END { print count + 0 }' \
      "$CHECKSUM_MANIFEST"
  )"
  [[ "$checksum_occurrences" -eq 1 ]] || {
    echo "candidate audit checksum entry is missing or duplicated: $relative_name" >&2
    exit 1
  }
done

(
  cd "$AUDIT_ROOT"
  sha256sum --check --strict "$(basename "$CHECKSUM_MANIFEST")"
)

AUDIT_TMP_ROOT="${TMPDIR:-/tmp}"
[[ -d "$AUDIT_TMP_ROOT" ]] || {
  echo "candidate audit temporary root is unavailable" >&2
  exit 1
}
SCRATCH_DIR="$(mktemp -d "$AUDIT_TMP_ROOT/echo-candidate-audit.XXXXXX")"
cleanup() {
  rm -rf -- "$SCRATCH_DIR"
}
trap cleanup EXIT INT TERM
REPLAYED_INDEX="$SCRATCH_DIR/echo-delivery-release-evidence-index.json"

python3 "$AUDIT_ROOT/release_evidence_index.py" \
  --source-preflight "$AUDIT_ROOT/echo-delivery-source-preflight.json" \
  --candidate-preflight "$AUDIT_ROOT/echo-release-candidate-preflight.json" \
  --os-image-evidence "$AUDIT_ROOT/inputs/os-image-evidence.json" \
  --os-image-signature "$AUDIT_ROOT/inputs/os-image-evidence.json.gpg" \
  --os-image-keyring "$AUDIT_ROOT/inputs/os-image-keyring.gpg" \
  --ab-evidence "$AUDIT_ROOT/inputs/ab-update-evidence.json" \
  --ab-signature "$AUDIT_ROOT/inputs/ab-update-evidence.json.gpg" \
  --ab-keyring "$AUDIT_ROOT/inputs/ab-update-keyring.gpg" \
  --omv-verification "$AUDIT_ROOT/inputs/omv-verification.json" \
  --omv-evidence "$AUDIT_ROOT/inputs/omv-evidence.json" \
  --omv-plugin-package "$AUDIT_ROOT/inputs/openmediavault-echo-os.deb" \
  --appliance-release "$AUDIT_ROOT/inputs/appliance-release.json" \
  --output "$REPLAYED_INDEX"

cmp -s -- "$PACKAGED_INDEX" "$REPLAYED_INDEX" || {
  echo "replayed candidate index differs from the packaged decision" >&2
  exit 1
}
read -r index_sha256 _ < <(sha256sum "$PACKAGED_INDEX")
[[ "$index_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "candidate index digest is invalid" >&2
  exit 1
}
echo "ECHO_DELIVERY_CANDIDATE_OFFLINE_OK index=$index_sha256"
