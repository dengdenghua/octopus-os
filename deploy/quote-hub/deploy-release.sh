#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR=/opt/echo-cloud/quote-hub
RELEASES_DIR=${BASE_DIR}/releases
CURRENT_LINK=${BASE_DIR}/current
ENV_FILE=/etc/echo/quote-hub.env
SECRET_FILE=/etc/echo/quote-hub-secret.json
SERVICE=echo-quote-hub.service
SERVICE_USER=echo-quote
SERVICE_GROUP=echo-quote
HEALTH_URL=http://127.0.0.1:8091/readyz

die() {
    printf 'quote-hub deploy: %s\n' "$*" >&2
    exit 1
}

require_root() {
    [[ ${EUID} -eq 0 ]] || die "run as root (sudo)"
}

require_secret_file() {
    local path=$1
    [[ -f ${path} ]] || die "missing ${path}; copy the example and fill it first"
    local mode owner
    mode=$(stat -c '%a' "${path}")
    owner=$(stat -c '%U:%G' "${path}")
    [[ ${mode} == 600 ]] || die "${path} must have mode 0600 (found ${mode})"
    [[ ${owner} == "${SERVICE_USER}:${SERVICE_GROUP}" ]] || \
        die "${path} must be owned by ${SERVICE_USER}:${SERVICE_GROUP} (found ${owner})"
}

wait_ready() {
    local attempt
    for attempt in $(seq 1 30); do
        if curl --fail --silent --show-error --max-time 10 "${HEALTH_URL}" >/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

atomic_link() {
    local target=$1
    local next=${BASE_DIR}/.current.next.$$
    ln -s "${target}" "${next}"
    mv -Tf "${next}" "${CURRENT_LINK}"
}

require_root
[[ $# -eq 1 ]] || die "usage: sudo $0 /absolute/path/to/quote-hub-artifact"
ARTIFACT_DIR=$(realpath -e "$1")
[[ -d ${ARTIFACT_DIR} ]] || die "artifact path is not a directory: ${ARTIFACT_DIR}"

command -v curl >/dev/null || die "curl is required"
command -v systemctl >/dev/null || die "systemctl is required"
command -v flock >/dev/null || die "flock is required"
command -v sha256sum >/dev/null || die "sha256sum is required"
UV_BIN=${UV_BIN:-$(command -v uv || true)}
if [[ -z ${UV_BIN} && -x /root/.local/bin/uv ]]; then
    UV_BIN=/root/.local/bin/uv
fi
[[ -x ${UV_BIN:-} ]] || die "uv is required for a locked release install"
id "${SERVICE_USER}" >/dev/null 2>&1 || die "missing service user ${SERVICE_USER}; follow README bootstrap"
getent group "${SERVICE_GROUP}" >/dev/null 2>&1 || die "missing service group ${SERVICE_GROUP}"
require_secret_file "${ENV_FILE}"
require_secret_file "${SECRET_FILE}"
if grep -Eq '(<[A-Z0-9_ -]+>|CHANGE_ME)' "${ENV_FILE}" "${SECRET_FILE}"; then
    die "environment or secret file still contains a placeholder"
fi

install -d -o root -g root -m 0755 "${BASE_DIR}" "${RELEASES_DIR}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0700 /var/lib/echo/quote-hub

exec 9>"${BASE_DIR}/deploy.lock"
flock -n 9 || die "another QuoteHub deployment is already running"

for required in MANIFEST README.md requirements.lock SHA256SUMS; do
    [[ -f ${ARTIFACT_DIR}/${required} ]] || die "artifact is missing ${required}"
done

if find "${ARTIFACT_DIR}" -mindepth 1 -maxdepth 1 ! -type f -print -quit | grep -q .; then
    die "artifact may only contain regular files"
fi

shopt -s nullglob
wheels=("${ARTIFACT_DIR}"/echo_agent_runtime-*.whl)
shopt -u nullglob
[[ ${#wheels[@]} -eq 1 ]] || die "artifact must contain exactly one runtime wheel"
WHEEL=${wheels[0]}
WHEEL_NAME=$(basename -- "${WHEEL}")

mapfile -t artifact_files < <(find "${ARTIFACT_DIR}" -mindepth 1 -maxdepth 1 -type f -printf '%f\n' | sort)
[[ ${#artifact_files[@]} -eq 5 ]] || die "artifact contains unexpected files"
for name in "${artifact_files[@]}"; do
    case ${name} in
        MANIFEST|README.md|SHA256SUMS|requirements.lock|"${WHEEL_NAME}") ;;
        *) die "artifact contains an unexpected file: ${name}" ;;
    esac
done

mapfile -t checksum_files < <(awk '{print $2}' "${ARTIFACT_DIR}/SHA256SUMS")
[[ ${#checksum_files[@]} -eq 4 ]] || die "SHA256SUMS must contain exactly four files"
for name in "${checksum_files[@]}"; do
    case ${name} in
        MANIFEST|README.md|requirements.lock|"${WHEEL_NAME}") ;;
        *) die "SHA256SUMS contains an unexpected path: ${name}" ;;
    esac
done
(cd "${ARTIFACT_DIR}" && sha256sum --strict --check SHA256SUMS) || \
    die "artifact checksum verification failed"

WHEEL_SHA=$(sha256sum "${WHEEL}" | awk '{print $1}')
grep -Fxq "wheel=${WHEEL_NAME}" "${ARTIFACT_DIR}/MANIFEST" || \
    die "manifest wheel name does not match artifact"
grep -Fxq "wheel_sha256=${WHEEL_SHA}" "${ARTIFACT_DIR}/MANIFEST" || \
    die "manifest wheel hash does not match artifact"

RELEASE_ID=$(date -u +%Y%m%d-%H%M)-${WHEEL_SHA:0:12}
RELEASE_DIR=${RELEASES_DIR}/${RELEASE_ID}
[[ ! -e ${RELEASE_DIR} ]] || die "release already exists: ${RELEASE_ID}"
install -d -o root -g root -m 0755 "${RELEASE_DIR}"

install -o root -g root -m 0644 "${ARTIFACT_DIR}/MANIFEST" "${RELEASE_DIR}/MANIFEST"
install -o root -g root -m 0644 "${ARTIFACT_DIR}/README.md" "${RELEASE_DIR}/README.md"
install -o root -g root -m 0644 "${ARTIFACT_DIR}/requirements.lock" "${RELEASE_DIR}/requirements.lock"
install -o root -g root -m 0644 "${ARTIFACT_DIR}/SHA256SUMS" "${RELEASE_DIR}/SHA256SUMS"
install -o root -g root -m 0644 "${WHEEL}" "${RELEASE_DIR}/${WHEEL_NAME}"

# Only the checksum-verified wheel and its hash-locked dependencies enter the
# immutable release. The local worktree, caches and credentials are never copied.
UV_PYTHON_DOWNLOADS=never "${UV_BIN}" venv \
    --no-project \
    --python /usr/bin/python3 \
    "${RELEASE_DIR}/.venv"
"${UV_BIN}" pip sync \
    --python "${RELEASE_DIR}/.venv/bin/python" \
    --require-hashes \
    --strict \
    "${RELEASE_DIR}/requirements.lock"
"${UV_BIN}" pip install \
    --python "${RELEASE_DIR}/.venv/bin/python" \
    --no-deps \
    --no-index \
    "${RELEASE_DIR}/${WHEEL_NAME}"

# This check avoids touching the running symlink when the release cannot even
# locate the ASGI entrypoint. Runtime configuration is validated after restart.
"${RELEASE_DIR}/.venv/bin/python" - <<'PY'
from runtime.platform.plugins.bundled.paper_trading.quote_service import app

if app is None:
    raise SystemExit("QuoteHub ASGI app is missing")
PY

chown -R root:root "${RELEASE_DIR}"
chmod -R go-w "${RELEASE_DIR}"

PREVIOUS_TARGET=
if [[ -L ${CURRENT_LINK} ]]; then
    PREVIOUS_TARGET=$(readlink -f "${CURRENT_LINK}")
fi

atomic_link "${RELEASE_DIR}"
if systemctl restart "${SERVICE}" && wait_ready; then
    printf 'QuoteHub release %s is active and ready.\n' "${RELEASE_ID}"
    printf 'Previous release: %s\n' "${PREVIOUS_TARGET:-none}"
    exit 0
fi

printf 'QuoteHub release %s failed readiness; reverting.\n' "${RELEASE_ID}" >&2
if [[ -n ${PREVIOUS_TARGET} && -d ${PREVIOUS_TARGET} ]]; then
    atomic_link "${PREVIOUS_TARGET}"
    systemctl restart "${SERVICE}" || true
    wait_ready || die "automatic rollback also failed; inspect journalctl -u ${SERVICE}"
    die "deployment failed and was rolled back to ${PREVIOUS_TARGET}"
fi
die "first deployment failed and no previous release exists; inspect journalctl -u ${SERVICE}"
