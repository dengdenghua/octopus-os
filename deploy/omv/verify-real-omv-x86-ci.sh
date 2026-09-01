#!/bin/sh
set -eu

fail() {
  printf 'Echo real OMV x86 CI failed: %s\n' "$*" >&2
  exit 1
}

require_regular_file() {
  if [ ! -f "$1" ] || [ -L "$1" ]; then
    fail "expected a regular non-symlink file: $1"
  fi
}

if [ "$#" -ne 2 ]; then
  fail "usage: $0 /absolute/plugin.deb /tmp/evidence.json"
fi
if [ "${ECHO_REAL_OMV_CI:-}" != 1 ] || [ "${GITHUB_ACTIONS:-}" != true ]; then
  fail "this destructive package-lifecycle probe is restricted to GitHub Actions"
fi
if [ "$(id -u)" -ne 0 ]; then
  fail "the isolated OMV integration probe must run as root"
fi
if [ "$(uname -m)" != x86_64 ]; then
  fail "the real OMV integration probe requires an x86_64 runner"
fi
if [ "$(cat /run/systemd/container 2>/dev/null || true)" != docker ]; then
  fail "the probe must run inside its disposable systemd Docker container"
fi

plugin_package=$1
evidence_path=$2
case "$plugin_package" in
  /source/dist/openmediavault-echo-os_*_all.deb) ;;
  *) fail "the plugin package must be the read-only CI build under /source/dist" ;;
esac
case "$evidence_path" in
  /tmp/*.json) ;;
  *) fail "the evidence path must be a temporary JSON file" ;;
esac
require_regular_file "$plugin_package"
if [ -e "$evidence_path" ] || [ -L "$evidence_path" ]; then
  fail "the evidence destination already exists"
fi

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
if [ "$distribution" != debian ] || [ "$distribution_version" != 13 ]; then
  fail "the disposable integration host must be Debian 13"
fi

short_hostname=$(hostname | cut -d. -f1)
hostname_length=$(printf %s "$short_hostname" | wc -c | tr -d ' ')
case "$hostname_length" in
  ''|*[!0-9]*) fail "the CI hostname length is invalid" ;;
esac
if [ "$hostname_length" -lt 1 ] || [ "$hostname_length" -gt 15 ]; then
  fail "the CI hostname must satisfy the SMB 15-character boundary"
fi

archive_key_sha256=ffa18c6c27dccd41656b6a71ca2ba042c3028077cb099dbca05fd1fd245906a3
archive_key=$(mktemp /tmp/openmediavault-archive-key.XXXXXX)
cleanup() {
  rm -f "$archive_key"
}
trap cleanup EXIT HUP INT TERM
curl --fail --silent --show-error --location \
  https://packages.openmediavault.org/public/archive.key \
  --output "$archive_key"
printf '%s  %s\n' "$archive_key_sha256" "$archive_key" | sha256sum --check --status || \
  fail "the OMV archive key does not match the reviewed fingerprint input"
gpg --batch --yes --dearmor \
  --output /usr/share/keyrings/openmediavault-archive-keyring.gpg \
  "$archive_key"
chmod 0644 /usr/share/keyrings/openmediavault-archive-keyring.gpg
printf '%s\n' \
  'deb [signed-by=/usr/share/keyrings/openmediavault-archive-keyring.gpg] https://packages.openmediavault.org/public synchrony main' \
  > /etc/apt/sources.list.d/openmediavault.list

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --yes openmediavault

omv_version=$(dpkg-query -W -f='${Version}' openmediavault)
omv_upstream=${omv_version#*:}
case "$omv_upstream" in
  8|8.*) ;;
  *) fail "the signed Synchrony repository did not install OMV 8: $omv_version" ;;
esac
systemctl is-active --quiet openmediavault-engined.service || \
  fail "openmediavault-engined is not active"

netplan_importer=/usr/share/openmediavault/confdb/populate.d/40netplan.sh
network_model=/usr/share/openmediavault/datamodels/conf.system.network.interface.json
require_regular_file "$netplan_importer"
require_regular_file "$network_model"
upstream_files_sha256=$(sha256sum "$netplan_importer" "$network_model")
negative_netplan=/etc/netplan/99-echo-ci-negative.yaml
if [ -e "$negative_netplan" ] || [ -L "$negative_netplan" ]; then
  fail "the reserved negative Netplan fixture already exists"
fi
cat > "$negative_netplan" <<'NETPLAN'
network:
  version: 2
  ethernets:
    echo-ci:
      nameservers:
        addresses: [192.0.2.53]
NETPLAN
chmod 0600 "$negative_netplan"
if grep --fixed-strings 'obj.set("dnsservers",' "$netplan_importer" >/dev/null && \
   grep --fixed-strings '"dnsnameservers"' "$network_model" >/dev/null && \
   ! grep --fixed-strings '"dnsservers"' "$network_model" >/dev/null; then
  if /usr/bin/python3 /source/deploy/omv/platform_preflight.py --quiet \
    > /tmp/echo-omv-negative-preflight.stdout \
    2> /tmp/echo-omv-negative-preflight.stderr; then
    fail "active Netplan DNS unexpectedly passed the real OMV field-mismatch gate"
  fi
  grep --fixed-strings 'omv_netplan_dns_field_mismatch' \
    /tmp/echo-omv-negative-preflight.stderr >/dev/null || \
    fail "the real OMV field mismatch did not emit its exact issue code"
  netplan_probe_result=mismatch-blocked
else
  /usr/bin/python3 /source/deploy/omv/platform_preflight.py --quiet || \
    fail "the upstream-compatible OMV Netplan field combination did not pass"
  netplan_probe_result=upstream-compatible
fi
if [ "$(sha256sum "$netplan_importer" "$network_model")" != "$upstream_files_sha256" ]; then
  fail "the read-only preflight changed an OMV upstream file"
fi
if getent group echo-omv >/dev/null 2>&1 || \
   [ -e /usr/lib/systemd/system/echo-omv-bridge.service ]; then
  fail "the negative preflight created plugin state before installation"
fi
rm -f "$negative_netplan"

preflight_json=/tmp/echo-omv-platform-preflight.json
rm -f "$preflight_json"
/usr/bin/python3 /source/deploy/omv/platform_preflight.py > "$preflight_json"
/usr/bin/python3 - "$preflight_json" <<'PY'
import json
import pathlib
import sys

report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if report.get("ready") is not True:
    raise SystemExit("platform preflight did not report ready:true")
if report.get("supportMatrix") != "debian-13+omv-8":
    raise SystemExit("platform preflight support matrix is wrong")
if report.get("smbHostnameCompatible") is not True:
    raise SystemExit("platform preflight did not prove the SMB hostname")
if report.get("netplan", {}).get("compatible") is not True:
    raise SystemExit("platform preflight did not prove Netplan compatibility")
PY

sentinel_directory=/srv/echo-ci-preserve
sentinel_path=$sentinel_directory/nas-sentinel.txt
mkdir -p "$sentinel_directory"
printf '%s\n' 'Echo CI NAS data must survive plugin remove, purge and reinstall.' > "$sentinel_path"
sentinel_sha256=$(sha256sum "$sentinel_path" | cut -d' ' -f1)

DEBIAN_FRONTEND=noninteractive apt-get install --yes "$plugin_package"
plugin_version=$(dpkg-query -W -f='${Version}' openmediavault-echo-os)
plugin_sha256=$(sha256sum "$plugin_package" | cut -d' ' -f1)
systemctl is-active --quiet echo-omv-bridge.service || fail "Echo OMV bridge is not active"
/usr/bin/python3 /usr/lib/echo-os/omv-bridge/platform_preflight.py \
  > /tmp/echo-omv-installed-platform-preflight.json
cmp --silent "$preflight_json" /tmp/echo-omv-installed-platform-preflight.json || \
  fail "the packaged platform preflight disagrees with the reviewed source"

socket_path=/run/echo-omv/omv.sock
socket_contract=$(stat -c '%a:%U:%G:%F' "$socket_path")
if [ "$socket_contract" != '660:root:echo-omv:socket' ]; then
  fail "the bridge socket contract is unsafe: $socket_contract"
fi
curl --fail --silent --show-error --unix-socket "$socket_path" \
  http://localhost/health > /tmp/echo-omv-health.json
curl --fail --silent --show-error --unix-socket "$socket_path" \
  http://localhost/v1/capabilities > /tmp/echo-omv-capabilities.json
curl --fail --silent --show-error --unix-socket "$socket_path" \
  http://localhost/v1/filesystems > /tmp/echo-omv-filesystems.json
curl --fail --silent --show-error --unix-socket "$socket_path" \
  http://localhost/v1/sharing > /tmp/echo-omv-sharing.json
/usr/sbin/omv-rpc -u admin FileSystemMgmt enumerateMountedFilesystems \
  '{"includeroot":false}' > /tmp/echo-omv-rpc-filesystems.json

for path in \
  /usr/share/openmediavault/workbench/navigation.d/services.echo-os.yaml \
  /usr/share/openmediavault/workbench/route.d/services.echo-os.yaml \
  /usr/share/openmediavault/workbench/component.d/omv-services-echo-os-form-page.yaml \
  /var/lib/openmediavault/workbench/navigation-config.json \
  /var/lib/openmediavault/workbench/route-config.json; do
  require_regular_file "$path"
done

/usr/bin/python3 - <<'PY'
import json
import pathlib

expectations = {
    "/tmp/echo-omv-health.json": dict,
    "/tmp/echo-omv-capabilities.json": dict,
    "/tmp/echo-omv-filesystems.json": dict,
    "/tmp/echo-omv-sharing.json": dict,
    "/tmp/echo-omv-rpc-filesystems.json": list,
}
for name, expected_type in expectations.items():
    value = json.loads(pathlib.Path(name).read_text(encoding="utf-8"))
    if not isinstance(value, expected_type):
        raise SystemExit(f"unexpected JSON shape from {name}")
health = json.loads(pathlib.Path("/tmp/echo-omv-health.json").read_text())
if health != {"ok": True}:
    raise SystemExit("bridge health response is not exact")
capabilities = json.loads(pathlib.Path("/tmp/echo-omv-capabilities.json").read_text())
if sorted(capabilities.get("capabilities", [])) != [
    "account.group.create.v1",
    "account.user.create.v1",
    "account.user.password.reset.v1",
    "filesystem.quota.user-group.v1",
    "nfs.share.private-network.v1",
    "shared-folder.create.simple.v1",
    "shared-folder.privilege.simple.v1",
    "smb.share.desired.v1",
]:
    raise SystemExit("bridge capabilities are incomplete")
PY

systemctl show echo-omv-bridge.service \
  --property=User \
  --property=Group \
  --property=NoNewPrivileges \
  --property=PrivateNetwork \
  --property=ProtectSystem > /tmp/echo-omv-systemd-policy.txt
for expected_policy in \
  User=root \
  Group=echo-omv \
  NoNewPrivileges=yes \
  PrivateNetwork=yes \
  ProtectSystem=strict; do
  grep --fixed-strings --line-regexp "$expected_policy" \
    /tmp/echo-omv-systemd-policy.txt >/dev/null || \
    fail "the bridge systemd policy is missing $expected_policy"
done
systemd_policy=$(tr '\n' ',' < /tmp/echo-omv-systemd-policy.txt)

/usr/bin/python3 /source/deploy/omv/real_omv_nfs_probe.py create
require_regular_file /tmp/echo-real-omv-nfs-state.json
/usr/bin/python3 /source/deploy/omv/real_omv_account_probe.py create
require_regular_file /tmp/echo-real-omv-account-state.json

DEBIAN_FRONTEND=noninteractive apt-get purge --yes openmediavault-echo-os
if dpkg-query -W openmediavault-echo-os >/dev/null 2>&1; then
  fail "the Echo OMV plugin remained installed after purge"
fi
if [ -e /usr/lib/systemd/system/echo-omv-bridge.service ] || \
   [ -e /usr/lib/echo-os/omv-bridge/appliance/omv_bridge.py ] || \
   [ -e /usr/share/openmediavault/workbench/navigation.d/services.echo-os.yaml ]; then
  fail "plugin-managed files survived purge"
fi
if [ "$(sha256sum "$sentinel_path" | cut -d' ' -f1)" != "$sentinel_sha256" ]; then
  fail "NAS sentinel changed during plugin purge"
fi
systemctl is-active --quiet openmediavault-engined.service || \
  fail "OMV stopped after purging the Echo plugin"
/usr/bin/python3 /source/deploy/omv/real_omv_nfs_probe.py verify-purged
require_regular_file /tmp/echo-real-omv-nfs-purge.json
/usr/bin/python3 /source/deploy/omv/real_omv_account_probe.py verify-purged
require_regular_file /tmp/echo-real-omv-account-purge.json

DEBIAN_FRONTEND=noninteractive apt-get install --yes "$plugin_package"
systemctl is-active --quiet echo-omv-bridge.service || \
  fail "Echo OMV bridge did not recover after reinstall"
curl --fail --silent --show-error --unix-socket "$socket_path" \
  http://localhost/health > /dev/null
if [ "$(sha256sum "$sentinel_path" | cut -d' ' -f1)" != "$sentinel_sha256" ]; then
  fail "NAS sentinel changed during plugin reinstall"
fi
/usr/bin/python3 /source/deploy/omv/real_omv_nfs_probe.py verify-reinstalled
require_regular_file /tmp/echo-real-omv-nfs-reinstall.json
/usr/bin/python3 /source/deploy/omv/real_omv_account_probe.py verify-reinstalled
require_regular_file /tmp/echo-real-omv-account-reinstall.json

export ECHO_EVIDENCE_PATH=$evidence_path
export ECHO_OMV_VERSION=$omv_version
export ECHO_PLUGIN_VERSION=$plugin_version
export ECHO_PLUGIN_SHA256=$plugin_sha256
export ECHO_SENTINEL_SHA256=$sentinel_sha256
export ECHO_SOCKET_CONTRACT=$socket_contract
export ECHO_SYSTEMD_POLICY=$systemd_policy
export ECHO_NETPLAN_PROBE_RESULT=$netplan_probe_result
/usr/bin/python3 - <<'PY'
import json
import os
import pathlib
import platform

preflight = json.loads(
    pathlib.Path("/tmp/echo-omv-platform-preflight.json").read_text(encoding="utf-8")
)
nfs_state = json.loads(
    pathlib.Path("/tmp/echo-real-omv-nfs-state.json").read_text(encoding="utf-8")
)
nfs_purge = json.loads(
    pathlib.Path("/tmp/echo-real-omv-nfs-purge.json").read_text(encoding="utf-8")
)
nfs_reinstall = json.loads(
    pathlib.Path("/tmp/echo-real-omv-nfs-reinstall.json").read_text(encoding="utf-8")
)
account_state = json.loads(
    pathlib.Path("/tmp/echo-real-omv-account-state.json").read_text(encoding="utf-8")
)
account_purge = json.loads(
    pathlib.Path("/tmp/echo-real-omv-account-purge.json").read_text(encoding="utf-8")
)
account_reinstall = json.loads(
    pathlib.Path("/tmp/echo-real-omv-account-reinstall.json").read_text(encoding="utf-8")
)
payload = {
    "schemaVersion": 6,
    "environment": "github-actions-disposable-systemd-container",
    "architecture": platform.machine(),
    "distribution": "debian",
    "distributionVersion": "13",
    "omvVersion": os.environ["ECHO_OMV_VERSION"],
    "pluginVersion": os.environ["ECHO_PLUGIN_VERSION"],
    "pluginSha256": os.environ["ECHO_PLUGIN_SHA256"],
    "supportMatrix": "debian-13+omv-8",
    "preflight": {
        "ready": preflight["ready"],
        "smbHostnameCompatible": preflight["smbHostnameCompatible"],
        "netplan": preflight["netplan"],
        "warnings": preflight["warnings"],
    },
    "checks": {
        "realOmvPackage": True,
        "realOmvRpc": True,
        "activeNetplanBehaviorVerified": True,
        "netplanProbeResult": os.environ["ECHO_NETPLAN_PROBE_RESULT"],
        "upstreamFilesUnchangedByPreflight": True,
        "workbenchGenerated": True,
        "bridgeSystemdActive": True,
        "socketContract": os.environ["ECHO_SOCKET_CONTRACT"],
        "systemdPolicy": os.environ["ECHO_SYSTEMD_POLICY"],
        "purgePreservedNasData": True,
        "reinstallHealthy": True,
        "sentinelSha256": os.environ["ECHO_SENTINEL_SHA256"],
    },
    "nfs": {
        "clientCidr": nfs_state["clientCidr"],
        "serverIp": nfs_state["serverIp"],
        "filesystemUuid": nfs_state["filesystemUuid"],
        "sharedFolderUuid": nfs_state["sharedFolderUuid"],
        "sharedFolderPlanId": nfs_state["sharedFolderPlanId"],
        "privilegePlanId": nfs_state["privilegePlanId"],
        "shareUuid": nfs_state["shareUuid"],
        "planId": nfs_state["planId"],
        "exportPath": nfs_state["exportPath"],
        "remotePath": nfs_state["remotePath"],
        "rwWriteSha256": nfs_state["payloadSha256"],
        "preservedFileSha256": nfs_state["payloadSha256"],
        "createdByEchoBridge": True,
        "sharedFolderCreatedByEchoBridge": True,
        "sharedFolderPermissionsVerified": True,
        "sharedFolderPrivilegeCreatedByEchoBridge": True,
        "omvConfigVerified": True,
        "exportsVerified": True,
        "serverActive": True,
        "tcp2049Listening": True,
        "rwMountVerified": True,
        "readOnlyRemountVerified": True,
        "purgePreservedShare": nfs_purge["purgePreservedShare"],
        "purgePreservedPayload": nfs_purge["purgePreservedPayload"],
        "purgePreservedPrivilege": nfs_purge["purgePreservedPrivilege"],
        "reinstallReadbackVerified": nfs_reinstall["reinstallReadbackVerified"],
        "reinstallPayloadVerified": nfs_reinstall["reinstallPayloadVerified"],
        "reinstallPrivilegeReadbackVerified": nfs_reinstall[
            "reinstallPrivilegeReadbackVerified"
        ],
        "reinstallPrivilegePlanNoop": nfs_reinstall["reinstallPrivilegePlanNoop"],
    },
    "accounts": {
        "groupName": account_state["groupName"],
        "groupGid": account_state["groupGid"],
        "groupPlanId": account_state["groupPlanId"],
        "userName": account_state["userName"],
        "userUid": account_state["userUid"],
        "userGid": account_state["userGid"],
        "userPlanId": account_state["userPlanId"],
        "passwordResetPlanId": account_state["passwordResetPlanId"],
        "smbShareName": account_state["smbShareName"],
        "smbShareUuid": account_state["smbShareUuid"],
        "smbPlanId": account_state["smbPlanId"],
        "smbProtocol": account_state["smbProtocol"],
        "smbPayloadSha256": account_state["smbPayloadSha256"],
        "passwordNeverReturned": account_state["passwordNeverReturned"],
        "nologinVerified": account_state["nologinVerified"],
        "noSshKeysVerified": account_state["noSshKeysVerified"],
        "selfModificationDisabled": account_state["selfModificationDisabled"],
        "sambaAccountVerified": account_state["sambaAccountVerified"],
        "smbAuthenticationVerified": account_state["smbAuthenticationVerified"],
        "smbReadWriteVerified": account_state["smbReadWriteVerified"],
        "oldPasswordRejected": account_state["oldPasswordRejected"],
        "replacementPasswordAuthenticationVerified": account_state[
            "replacementPasswordAuthenticationVerified"
        ],
        "accountFieldsPreservedAfterPasswordReset": account_state[
            "accountFieldsPreservedAfterPasswordReset"
        ],
        "purgePreservedGroup": account_purge["purgePreservedGroup"],
        "purgePreservedUser": account_purge["purgePreservedUser"],
        "purgePreservedSambaAccount": account_purge["purgePreservedSambaAccount"],
        "purgePreservedSmbAuthentication": account_purge[
            "purgePreservedSmbAuthentication"
        ],
        "purgePreservedSmbPayload": account_purge["purgePreservedSmbPayload"],
        "purgeSmbPayloadSha256": account_purge["purgeSmbPayloadSha256"],
        "reinstallReadbackVerified": account_reinstall["reinstallReadbackVerified"],
        "existingGroupCreateRejected": account_reinstall["existingGroupCreateRejected"],
        "existingUserCreateRejected": account_reinstall["existingUserCreateRejected"],
        "reinstallPasswordNeverReturned": account_reinstall["passwordNeverReturned"],
        "reinstallSmbAuthenticationVerified": account_reinstall[
            "reinstallSmbAuthenticationVerified"
        ],
        "reinstallSmbPayloadVerified": account_reinstall[
            "reinstallSmbPayloadVerified"
        ],
        "reinstallSmbPayloadSha256": account_reinstall[
            "reinstallSmbPayloadSha256"
        ],
        "reinstallSmbPlanNoop": account_reinstall["reinstallSmbPlanNoop"],
    },
    "sourceRevision": os.environ.get("GITHUB_SHA", ""),
}
path = pathlib.Path(os.environ["ECHO_EVIDENCE_PATH"])
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
path.chmod(0o644)
PY

printf 'ECHO_REAL_OMV_X86_OK omv=%s plugin=%s sha256=%s\n' \
  "$omv_version" "$plugin_version" "$plugin_sha256"
