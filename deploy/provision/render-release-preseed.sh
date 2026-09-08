#!/bin/sh
# Render the network-independent d-i policy used by strict NAS releases.
set -eu

[ "$#" -eq 2 ] || {
  echo "usage: render-release-preseed.sh <input> <output>" >&2
  exit 2
}

input=$1
output=$2
[ -f "$input" ] && [ ! -L "$input" ] || {
  echo "release preseed input must be a regular file" >&2
  exit 1
}
[ "$input" != "$output" ] || {
  echo "release preseed output must differ from input" >&2
  exit 1
}

output_parent=$(dirname "$output")
[ -d "$output_parent" ] || {
  echo "release preseed output directory does not exist" >&2
  exit 1
}
staged=$(mktemp "$output_parent/.echo-release-preseed.XXXXXX")
cleanup() { rm -f "$staged"; }
trap cleanup EXIT HUP INT TERM

awk '
  /^tasksel tasksel\/first multiselect / {
    print "tasksel tasksel/first multiselect"
    next
  }
  /^d-i pkgsel\/include string/ {
    print "d-i pkgsel/include string"
    skipping_packages = 1
    next
  }
  skipping_packages {
    if ($0 ~ /^[[:space:]]+nginx smartmontools$/) skipping_packages = 0
    next
  }
  { print }
' "$input" >"$staged"

sed -i \
  -e '/^d-i apt-setup\/cdrom\/set-first boolean false$/a d-i apt-setup/use_mirror boolean false' \
  -e 's/^d-i clock-setup\/ntp boolean true$/d-i clock-setup\/ntp boolean false/' \
  "$staged"

[ "$(grep -c '^d-i apt-setup/use_mirror boolean false$' "$staged")" -eq 1 ]
[ "$(grep -c '^d-i clock-setup/ntp boolean false$' "$staged")" -eq 1 ]
[ "$(grep -c '^tasksel tasksel/first multiselect$' "$staged")" -eq 1 ]
[ "$(grep -c '^d-i pkgsel/include string$' "$staged")" -eq 1 ]
if grep -Eq '^[[:space:]]+(openssh-server|python3|nginx|smartmontools)([[:space:]]|$)' "$staged"; then
  echo "release preseed retained an online pkgsel package" >&2
  exit 1
fi

chmod 0644 "$staged"
mv "$staged" "$output"
staged=""
trap - EXIT HUP INT TERM
