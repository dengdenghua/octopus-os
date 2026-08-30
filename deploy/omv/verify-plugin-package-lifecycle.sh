#!/bin/sh
set -eu

fail() {
  printf 'Echo OMV plugin lifecycle verification failed: %s\n' "$*" >&2
  exit 1
}

assert_equal() {
  if [ "$1" != "$2" ]; then
    fail "expected '$2', got '$1' ($3)"
  fi
}

assert_file() {
  if [ ! -f "$1" ] || [ -L "$1" ]; then
    fail "expected bounded regular file: $1"
  fi
}

assert_absent() {
  if [ -e "$1" ] || [ -L "$1" ]; then
    fail "package-managed path still exists: $1"
  fi
}

package_status() {
  /usr/bin/dpkg-query -W -f='${Status}|${Version}' openmediavault-echo-os 2>/dev/null
}

assert_installed_version() {
  assert_equal "$(package_status)" "install ok installed|$1" "package status"
}

assert_sentinel() {
  assert_file "$sentinel_path"
  assert_equal "$(sha256sum "$sentinel_path" | cut -d' ' -f1)" "$sentinel_sha256" \
    "NAS sentinel checksum"
}

build_omv_fixture() {
  fixture_version=$1
  fixture_destination=$2
  fixture_root=$work_directory/openmediavault-$fixture_version
  mkdir -p "$fixture_root/DEBIAN"
  mkdir -p \
    "$fixture_root/usr/share/openmediavault/confdb/populate.d" \
    "$fixture_root/usr/share/openmediavault/datamodels"
  printf '%s\n' 'obj.set("dnsnameservers", dnsnameservers)' > \
    "$fixture_root/usr/share/openmediavault/confdb/populate.d/40netplan.sh"
  printf '%s\n' '{"properties":{"dnsnameservers":{"type":"array"}}}' > \
    "$fixture_root/usr/share/openmediavault/datamodels/conf.system.network.interface.json"
  {
    printf 'Package: openmediavault\n'
    printf 'Version: %s\n' "$fixture_version"
    printf 'Section: admin\n'
    printf 'Priority: optional\n'
    printf 'Architecture: all\n'
    printf 'Maintainer: Echo lifecycle fixture <noreply@example.invalid>\n'
    printf 'Description: Minimal package identity used only for offline lifecycle verification\n'
  } > "$fixture_root/DEBIAN/control"
  dpkg-deb --root-owner-group --build "$fixture_root" "$fixture_destination" >/dev/null
}

build_plugin_variant() {
  variant_version=$1
  variant_destination=$2
  inject_failure=$3
  variant_root=$work_directory/plugin-$variant_version
  dpkg-deb --raw-extract "$source_package" "$variant_root"
  sed -i "s/^Version: .*/Version: $variant_version/" "$variant_root/DEBIAN/control"
  if [ "$inject_failure" = true ]; then
    sed -i '3i\
if [ "${ECHO_LIFECYCLE_INJECT_POSTINST_FAILURE:-0}" = 1 ] && [ "$1" = configure ]; then exit 42; fi
' "$variant_root/DEBIAN/postinst"
  fi
  dpkg-deb --root-owner-group --build "$variant_root" "$variant_destination" >/dev/null
}

install_plugin() {
  DEBIAN_FRONTEND=noninteractive PATH="$shim_directory:/usr/sbin:/usr/bin:/sbin:/bin" \
    dpkg --force-depends --install "$1" >/dev/null
}

if [ "$#" -ne 1 ]; then
  fail "usage: $0 /absolute/path/openmediavault-echo-os_VERSION_all.deb"
fi
if [ "$(id -u)" != 0 ]; then
  fail "the lifecycle verifier must run as root in an isolated Debian container"
fi

source_package=$1
case "$source_package" in
  /*) ;;
  *) fail "package path must be absolute" ;;
esac
assert_file "$source_package"

distribution=
distribution_version=
while IFS='=' read -r key value; do
  value=${value#\"}
  value=${value%\"}
  case "$key" in
    ID) distribution=$value ;;
    VERSION_ID) distribution_version=$value ;;
  esac
done < /usr/lib/os-release
assert_equal "$distribution" debian "container distribution"
assert_equal "$distribution_version" 13 "container distribution version"

assert_equal "$(dpkg-deb -f "$source_package" Package)" openmediavault-echo-os \
  "source package identity"
assert_equal "$(dpkg-deb -f "$source_package" Version)" 0.2.0-1 \
  "source package version"
assert_equal "$(dpkg-deb -f "$source_package" Architecture)" all \
  "source package architecture"

work_directory=$(mktemp -d)
shim_directory=$work_directory/shims
command_log=$work_directory/maintainer-commands.log
group_state=$work_directory/echo-omv-group
sentinel_path=$work_directory/nas-data/sentinel.bin
mkdir -p "$shim_directory" "$(dirname "$sentinel_path")"
printf 'Echo NAS lifecycle sentinel - package scripts must never alter this file.\n' > "$sentinel_path"
sentinel_sha256=$(sha256sum "$sentinel_path" | cut -d' ' -f1)

if [ ! -x /usr/bin/python3 ] && [ -x /usr/local/bin/python3 ]; then
  ln -s /usr/local/bin/python3 /usr/bin/python3
fi
if [ ! -x /usr/bin/python3 ]; then
  fail "the lifecycle image does not provide Python 3"
fi
if ! /usr/bin/getent group echo-omv >/dev/null 2>&1; then
  printf 'echo-omv:x:991:\n' >> /etc/group
fi
: > "$group_state"

mkdir -p /run/echo-omv
/usr/bin/python3 - <<'PY' &
import grp
import os
import socket

path = "/run/echo-omv/omv.sock"
try:
    os.unlink(path)
except FileNotFoundError:
    pass
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
os.chown(path, 0, grp.getgrnam("echo-omv").gr_gid)
os.chmod(path, 0o660)
server.listen(8)
while True:
    connection, _ = server.accept()
    with connection:
        connection.recv(65536)
        connection.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: 11\r\nConnection: close\r\n\r\n{\"ok\":true}"
        )
PY
health_server_pid=$!

command_shim=$shim_directory/echo-lifecycle-command
cat > "$command_shim" <<'SHIM'
#!/bin/sh
set -eu
command_name=${0##*/}
printf '%s %s\n' "$command_name" "$*" >> "$ECHO_LIFECYCLE_COMMAND_LOG"
case "$command_name" in
  getent)
    if [ "${1:-}" = group ] && [ "${2:-}" = echo-omv ] && \
       [ -f "$ECHO_LIFECYCLE_GROUP_STATE" ]; then
      printf 'echo-omv:x:991:\n'
      exit 0
    fi
    exit 2
  ;;
  addgroup)
    if [ "$*" != "--system echo-omv" ]; then
      exit 64
    fi
    : > "$ECHO_LIFECYCLE_GROUP_STATE"
  ;;
  dpkg-trigger|systemctl|deb-systemd-helper|deb-systemd-invoke)
  ;;
  *)
    exit 64
  ;;
esac
exit 0
SHIM
chmod 0755 "$command_shim"
for command_name in getent addgroup dpkg-trigger systemctl deb-systemd-helper deb-systemd-invoke; do
  ln -s echo-lifecycle-command "$shim_directory/$command_name"
done
export ECHO_LIFECYCLE_COMMAND_LOG=$command_log
export ECHO_LIFECYCLE_GROUP_STATE=$group_state

omv8_package=$work_directory/openmediavault_8.3.1-1_all.deb
omv9_package=$work_directory/openmediavault_9.0.0-1_all.deb
plugin_upgrade=$work_directory/openmediavault-echo-os_0.2.0-2_all.deb
plugin_candidate=$work_directory/openmediavault-echo-os_0.2.0-3_all.deb
plugin_failed_upgrade=$work_directory/openmediavault-echo-os_0.2.0-4_all.deb
build_omv_fixture 8.3.1-1 "$omv8_package"
build_omv_fixture 9.0.0-1 "$omv9_package"
build_plugin_variant 0.2.0-2 "$plugin_upgrade" false
build_plugin_variant 0.2.0-3 "$plugin_candidate" false
build_plugin_variant 0.2.0-4 "$plugin_failed_upgrade" true

dpkg --install "$omv8_package" >/dev/null
install_plugin "$source_package"
assert_installed_version 0.2.0-1
assert_file /usr/lib/systemd/system/echo-omv-bridge.service
assert_file /usr/lib/echo-os/omv-bridge/appliance/omv_bridge.py
assert_file /usr/lib/echo-os/omv-bridge/platform_preflight.py
assert_file /usr/share/openmediavault/workbench/navigation.d/services.echo-os.yaml
assert_file /usr/share/openmediavault/workbench/route.d/services.echo-os.yaml
assert_file /usr/share/openmediavault/workbench/component.d/omv-services-echo-os-form-page.yaml
assert_equal "$(stat -c '%a:%u:%g' /usr/lib/systemd/system/echo-omv-bridge.service)" \
  644:0:0 "systemd unit ownership"
assert_file "$group_state"
assert_sentinel

install_plugin "$plugin_upgrade"
assert_installed_version 0.2.0-2
assert_sentinel

if ECHO_LIFECYCLE_INJECT_POSTINST_FAILURE=1 install_plugin "$plugin_failed_upgrade"; then
  fail "injected postinst failure unexpectedly succeeded"
fi
assert_equal "$(package_status)" "install ok half-configured|0.2.0-4" \
  "failed upgrade state"
install_plugin "$plugin_upgrade"
assert_installed_version 0.2.0-2
assert_sentinel

mkdir -p /var/lib/echo-os/omv-host
: > /var/lib/echo-os/omv-host/install-state.json
if install_plugin "$plugin_candidate"; then
  fail "manual installer conflict unexpectedly succeeded"
fi
assert_installed_version 0.2.0-2
unlink /var/lib/echo-os/omv-host/install-state.json
assert_sentinel

dpkg --force-depends --install "$omv9_package" >/dev/null
if install_plugin "$plugin_candidate"; then
  fail "OMV 9 upgrade unexpectedly passed the preinst support gate"
fi
assert_installed_version 0.2.0-2
DEBIAN_FRONTEND=noninteractive PATH="$shim_directory:/usr/sbin:/usr/bin:/sbin:/bin" \
  dpkg --remove openmediavault-echo-os >/dev/null
assert_absent /usr/lib/systemd/system/echo-omv-bridge.service
assert_absent /usr/lib/echo-os/omv-bridge/appliance/omv_bridge.py
assert_absent /usr/share/openmediavault/workbench/navigation.d/services.echo-os.yaml
assert_absent /usr/share/openmediavault/workbench/route.d/services.echo-os.yaml
assert_absent /usr/share/openmediavault/workbench/component.d/omv-services-echo-os-form-page.yaml
assert_file "$group_state"
assert_sentinel

dpkg --force-downgrade --force-depends --install "$omv8_package" >/dev/null
install_plugin "$plugin_upgrade"
assert_installed_version 0.2.0-2
DEBIAN_FRONTEND=noninteractive PATH="$shim_directory:/usr/sbin:/usr/bin:/sbin:/bin" \
  dpkg --purge openmediavault-echo-os >/dev/null
assert_absent /usr/lib/systemd/system/echo-omv-bridge.service
assert_absent /usr/lib/echo-os/omv-bridge/appliance/omv_bridge.py
assert_absent /usr/share/openmediavault/workbench/navigation.d/services.echo-os.yaml
assert_absent /usr/share/openmediavault/workbench/route.d/services.echo-os.yaml
assert_absent /usr/share/openmediavault/workbench/component.d/omv-services-echo-os-form-page.yaml
assert_file "$group_state"
assert_sentinel

grep -F 'deb-systemd-invoke restart echo-omv-bridge.service' "$command_log" >/dev/null || \
  fail "service restart was not requested"
grep -F 'deb-systemd-invoke stop echo-omv-bridge.service' "$command_log" >/dev/null || \
  fail "service stop was not requested"
grep -F 'deb-systemd-helper purge echo-omv-bridge.service' "$command_log" >/dev/null || \
  fail "systemd purge was not requested"
grep -F 'dpkg-trigger update-workbench' "$command_log" >/dev/null || \
  fail "Workbench refresh was not requested"
kill "$health_server_pid"
wait "$health_server_pid" 2>/dev/null || true

printf '%s\n' \
  '{"schema":"echo.omv-plugin-lifecycle.v1","status":"passed","distribution":"debian","distributionVersion":"13","omvFixtureVersions":["8.3.1-1","9.0.0-1"],"install":true,"upgrade":true,"failedUpgradeRecovered":true,"manualConflictRejected":true,"offMatrixUpgradeRejected":true,"offMatrixRemove":true,"purge":true,"groupPreserved":true,"nasDataPreserved":true}'
