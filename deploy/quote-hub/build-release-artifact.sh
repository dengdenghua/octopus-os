#!/usr/bin/env bash
set -Eeuo pipefail

die() {
    printf 'quote-hub build: %s\n' "$*" >&2
    exit 1
}

hash_file() {
    local path=$1
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${path}" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${path}" | awk '{print $1}'
    else
        die "sha256sum or shasum is required"
    fi
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SOURCE_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)

[[ $# -eq 1 ]] || die "usage: $0 /absolute/output/directory"
[[ $1 == /* ]] || die "output directory must be absolute"
OUTPUT_ROOT=$1

command -v uv >/dev/null || die "uv is required"
[[ -f ${SOURCE_ROOT}/pyproject.toml ]] || die "cannot locate project root"
[[ -f ${SOURCE_ROOT}/uv.lock ]] || die "cannot locate uv.lock"

install -d -m 0755 "${OUTPUT_ROOT}"
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
BUILD_STAMP=$(date -u +%Y%m%d-%H%M%S)
STAGING=${OUTPUT_ROOT}/.quote-hub-${BUILD_STAMP}-$$
[[ ! -e ${STAGING} ]] || die "staging directory already exists: ${STAGING}"
install -d -m 0755 "${STAGING}"

uv build --quiet --wheel --project "${SOURCE_ROOT}" --out-dir "${STAGING}" 1>&2
uv export \
    --quiet \
    --project "${SOURCE_ROOT}" \
    --format requirements.txt \
    --locked \
    --no-dev \
    --no-editable \
    --no-emit-project \
    --extra serve \
    --extra channels \
    --output-file "${STAGING}/requirements.lock" 1>&2
rm -f -- "${STAGING}/.gitignore"

shopt -s nullglob
wheels=("${STAGING}"/echo_agent_runtime-*.whl)
shopt -u nullglob
[[ ${#wheels[@]} -eq 1 ]] || die "expected exactly one runtime wheel"
WHEEL=${wheels[0]}
WHEEL_NAME=$(basename -- "${WHEEL}")
WHEEL_SHA=$(hash_file "${WHEEL}")
REQUIREMENTS_SHA=$(hash_file "${STAGING}/requirements.lock")

SOURCE_REVISION=unavailable
SOURCE_DIRTY=unknown
if command -v git >/dev/null && git -C "${SOURCE_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    SOURCE_REVISION=$(git -C "${SOURCE_ROOT}" rev-parse HEAD)
    if [[ -n $(git -C "${SOURCE_ROOT}" status --porcelain --untracked-files=normal) ]]; then
        SOURCE_DIRTY=true
    else
        SOURCE_DIRTY=false
    fi
fi

install -m 0644 "${SCRIPT_DIR}/README.md" "${STAGING}/README.md"
cat >"${STAGING}/MANIFEST" <<EOF
schema=1
built_at=${BUILD_TIME}
source_revision=${SOURCE_REVISION}
source_dirty=${SOURCE_DIRTY}
wheel=${WHEEL_NAME}
wheel_sha256=${WHEEL_SHA}
requirements_sha256=${REQUIREMENTS_SHA}
EOF

for name in MANIFEST README.md requirements.lock "${WHEEL_NAME}"; do
    printf '%s  %s\n' "$(hash_file "${STAGING}/${name}")" "${name}"
done >"${STAGING}/SHA256SUMS"

FINAL_DIR=${OUTPUT_ROOT}/quote-hub-${BUILD_STAMP}-${WHEEL_SHA:0:12}
[[ ! -e ${FINAL_DIR} ]] || die "artifact already exists: ${FINAL_DIR}"
mv "${STAGING}" "${FINAL_DIR}"
printf '%s\n' "${FINAL_DIR}"
