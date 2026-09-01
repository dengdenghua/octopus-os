#!/bin/sh
set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
certificate_path=${1:-"$script_directory/echo.crt"}
private_key_path=${2:-"$script_directory/echo.key"}
expected_host=${3:-${ECHO_TLS_HOST:-}}

fail() {
  echo "TLS preflight failed: $*" >&2
  exit 1
}

command -v openssl >/dev/null 2>&1 || fail "openssl is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
[ -n "$expected_host" ] || fail "ECHO_TLS_HOST is required"
[ -f "$certificate_path" ] && [ ! -L "$certificate_path" ] || fail "certificate must be a regular non-symlink file"
[ -f "$private_key_path" ] && [ ! -L "$private_key_path" ] || fail "private key must be a regular non-symlink file"

if key_mode=$(stat -f '%Lp' "$private_key_path" 2>/dev/null); then
  :
else
  key_mode=$(stat -c '%a' "$private_key_path" 2>/dev/null) || fail "cannot read private key permissions"
fi
case "$key_mode" in
  400|600) ;;
  *) fail "private key permissions must be 0400 or 0600 (observed $key_mode)" ;;
esac

openssl x509 -in "$certificate_path" -noout >/dev/null 2>&1 || fail "certificate is not valid PEM X.509"
openssl pkey -in "$private_key_path" -passin pass: -noout >/dev/null 2>&1 || fail "private key is invalid or requires a passphrase"
openssl x509 -in "$certificate_path" -checkend 604800 -noout >/dev/null 2>&1 || fail "certificate expires in less than 7 days"

certificate_public_key=$(openssl x509 -in "$certificate_path" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | openssl dgst -sha256)
private_public_key=$(openssl pkey -in "$private_key_path" -passin pass: -pubout -outform DER 2>/dev/null | openssl dgst -sha256)
[ "$certificate_public_key" = "$private_public_key" ] || fail "certificate and private key do not match"

if openssl x509 -help 2>&1 | grep -q -- '-checkhost'; then
  if python3 - "$expected_host" <<'PY'
import ipaddress
import sys

try:
    ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
PY
  then
    hostname_check=-checkip
  else
    hostname_check=-checkhost
  fi
  openssl x509 -in "$certificate_path" "$hostname_check" "$expected_host" -noout >/dev/null 2>&1 || fail "certificate SAN does not match ECHO_TLS_HOST"
else
  # LibreSSL lacks OpenSSL's public -checkhost/-checkip flags. Keep the
  # portable fallback strict and SAN-only instead of accepting legacy CN.
  python3 - "$certificate_path" "$expected_host" <<'PY'
import ipaddress
import ssl
import sys

certificate = ssl._ssl._test_decode_cert(sys.argv[1])
expected = sys.argv[2].rstrip(".")
subject_alt_names = certificate.get("subjectAltName", ())
try:
    expected_ip = ipaddress.ip_address(expected)
except ValueError:
    expected_ip = None

matched = False
for kind, value in subject_alt_names:
    if expected_ip is not None and kind == "IP Address":
        try:
            matched = ipaddress.ip_address(value) == expected_ip
        except ValueError:
            matched = False
    elif expected_ip is None and kind == "DNS":
        wanted = expected.encode("idna").decode("ascii").casefold()
        candidate = value.rstrip(".").encode("idna").decode("ascii").casefold()
        if candidate.startswith("*."):
            suffix = candidate[2:]
            matched = wanted.endswith(f".{suffix}") and wanted.count(".") == suffix.count(".") + 1
        else:
            matched = wanted == candidate
    if matched:
        break

if not matched:
    raise SystemExit("TLS preflight failed: certificate SAN does not match ECHO_TLS_HOST")
PY
fi

echo "TLS certificate preflight passed for $expected_host"
