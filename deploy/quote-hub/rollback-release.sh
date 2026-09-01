#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR=/opt/echo-cloud/quote-hub
RELEASES_DIR=${BASE_DIR}/releases
CURRENT_LINK=${BASE_DIR}/current
SERVICE=echo-quote-hub.service
HEALTH_URL=http://127.0.0.1:8091/readyz

die() {
    printf 'quote-hub rollback: %s\n' "$*" >&2
    exit 1
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

[[ ${EUID} -eq 0 ]] || die "run as root (sudo)"
[[ $# -eq 1 ]] || die "usage: sudo $0 RELEASE_ID"
if [[ ! $1 =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ && \
      ! $1 =~ ^[0-9]{8}-[0-9]{4}-[0-9a-f]{8,12}$ ]]; then
    die "invalid release id"
fi

exec 9>"${BASE_DIR}/deploy.lock"
flock -n 9 || die "a QuoteHub deployment or rollback is already running"

TARGET=$(realpath -e "${RELEASES_DIR}/$1")
case ${TARGET} in
    "${RELEASES_DIR}"/*) ;;
    *) die "target escaped releases directory" ;;
esac
[[ -x ${TARGET}/.venv/bin/python ]] || die "release has no usable venv: $1"

PREVIOUS_TARGET=
if [[ -L ${CURRENT_LINK} ]]; then
    PREVIOUS_TARGET=$(readlink -f "${CURRENT_LINK}")
fi
[[ ${TARGET} != "${PREVIOUS_TARGET}" ]] || die "release $1 is already active"

atomic_link "${TARGET}"
if systemctl restart "${SERVICE}" && wait_ready; then
    printf 'QuoteHub rolled back to %s.\n' "$1"
    exit 0
fi

printf 'Target release %s failed readiness; restoring prior target.\n' "$1" >&2
if [[ -n ${PREVIOUS_TARGET} && -d ${PREVIOUS_TARGET} ]]; then
    atomic_link "${PREVIOUS_TARGET}"
    systemctl restart "${SERVICE}" || true
    wait_ready || die "restore failed; inspect journalctl -u ${SERVICE}"
fi
die "rollback target failed readiness and was not activated"
