#!/usr/bin/env bash
# Run the unit suite in sequential shards, one pytest process per shard.
#
# Why this exists: on macOS 26 (arm64) a single long pytest process dies with
# SIGTRAP (exit 133) partway through the suite. Four crash reports showed four
# unrelated callers — the pydantic-core Rust extension, OpenSSL ASN.1 cert
# parsing, and CPython's _PyBytes_Resize — all aborting at the same place,
# libsystem_malloc's xzone freelist integrity check during realloc. Three
# different libraries do not share one bug; the allocator underneath them is
# the common factor. The crash point drifts between runs and the same command
# passes on some attempts, which rules out a specific test and matches an
# allocator-level fault rather than anything in this repo.
#
# Smaller processes keep each heap short-lived enough to finish. Shards run
# sequentially (not in parallel) so the suite's own assumptions about shared
# state and ports are unchanged.
#
# Usage:
#   scripts/test_sharded.sh [shard_size] [-- extra pytest args]
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

PYTHON="${PYTHON:-$(if [ -x .venv/bin/python ]; then printf '%s' .venv/bin/python; else printf '%s' python; fi)}"

SHARD_SIZE=170
if [ "${1:-}" != "--" ] && [ -n "${1:-}" ]; then
    SHARD_SIZE="$1"
    shift
fi
[ "${1:-}" = "--" ] && shift

# Keep scratch state inside the repo's build dir. A plain `mktemp -d` targets
# $TMPDIR, which is not always writable (sandboxed shells), and an unchecked
# mktemp yields an empty path that silently turns every later write into "/...".
WORK="${TMPDIR:-/tmp}/echo-shards-$$"
mkdir -p "$WORK" 2>/dev/null || WORK="./.pytest_cache/shards-$$"
mkdir -p "$WORK" || { echo "cannot create a scratch dir for shard lists" >&2; exit 2; }
trap 'rm -rf "$WORK"' EXIT

EXTRA_ARGS=("$@")

find tests -name 'test_*.py' | sort > "$WORK/all.txt"
TOTAL_FILES=$(wc -l < "$WORK/all.txt" | tr -d ' ')
split -l "$SHARD_SIZE" "$WORK/all.txt" "$WORK/shard_"

echo "Running $TOTAL_FILES test files in shards of $SHARD_SIZE (sequential)."
echo

FAILED_SHARDS=0
CRASHED_SHARDS=0
SHARD_NO=0

# Run one list of test files. Returns pytest's exit status; appends its
# FAILED/ERROR lines to the aggregate.
run_list() {
    local list="$1" log="$2" status
    # shellcheck disable=SC2046
    "$PYTHON" -m pytest $(tr '\n' ' ' < "$list") \
        -q -p no:randomly -m "not slow and not integration" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
        > "$log" 2>&1
    status=$?
    grep -hE '^(FAILED|ERROR) ' "$log" >> "$WORK/failures.txt" 2>/dev/null
    return $status
}

# The allocator fault is probabilistic, so a crashed shard is not a verdict on
# its tests — splitting it and retrying usually gets a clean result. Recurse
# until a single file crashes on its own, which would be a real signal.
run_with_split() {
    local list="$1" label="$2" depth="${3:-0}" n half status
    # Capture pytest's status directly. Testing it via `if run_list ...` would
    # leave $? holding the result of the `if`, not of pytest.
    run_list "$list" "$list.log"
    status=$?
    if [ "$status" -eq 0 ]; then
        echo "  $label: OK · $(grep -oE '[0-9]+ (passed|failed)[^)]*' "$list.log" | tail -1)"
        return 0
    fi
    if [ "$status" -eq 1 ]; then
        echo "  $label: FAILURES · $(grep -oE '[0-9]+ (passed|failed)[^)]*' "$list.log" | tail -1)"
        FAILED_SHARDS=$((FAILED_SHARDS + 1))
        return 1
    fi

    n=$(wc -l < "$list" | tr -d ' ')
    if [ "$n" -le 1 ] || [ "$depth" -ge 4 ]; then
        echo "  $label: CRASHED (exit $status) on $n file(s) — not recoverable by splitting"
        CRASHED_SHARDS=$((CRASHED_SHARDS + 1))
        return 1
    fi

    echo "  $label: crashed (exit $status) · splitting $n files and retrying"
    half=$((n / 2))
    head -"$half" "$list" > "$list.a"
    tail -n +$((half + 1)) "$list" > "$list.b"
    run_with_split "$list.a" "$label.a" $((depth + 1))
    run_with_split "$list.b" "$label.b" $((depth + 1))
}

for shard in "$WORK"/shard_*; do
    SHARD_NO=$((SHARD_NO + 1))
    run_with_split "$shard" "shard $SHARD_NO"
done

echo
if [ -s "$WORK/failures.txt" ]; then
    echo "Failing tests across all shards:"
    sort -u "$WORK/failures.txt" | sed 's/^/  /'
    echo
fi

echo "$SHARD_NO shard(s): $FAILED_SHARDS with failures, $CRASHED_SHARDS crashed."
[ "$FAILED_SHARDS" -eq 0 ] && [ "$CRASHED_SHARDS" -eq 0 ] && exit 0
exit 1


