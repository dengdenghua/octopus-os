#!/usr/bin/env bash
# Build the Python runtime wheelhouse for offline firstboot installation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/dist/python-wheelhouse}"

die() { printf '✗ %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "缺少 git"
command -v python3 >/dev/null 2>&1 || die "缺少 python3"
command -v sha256sum >/dev/null 2>&1 || die "缺少 sha256sum"
[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] \
  || die "Python 发布载荷只能从干净 Git 工作树构建"

SOURCE_TREE="$(git -C "$REPO_ROOT" rev-parse 'HEAD^{tree}')"
PYTHON_RUNTIME="$(python3 -c \
  'import platform,sys; print(f"{sys.implementation.cache_tag} {platform.system().lower()} {platform.machine().lower()}")')"
case "$PYTHON_RUNTIME" in
  cpython-313\ linux\ x86_64) ;;
  *) die "正式 Debian 13 amd64 ISO 只接受 CPython 3.13 wheelhouse: $PYTHON_RUNTIME" ;;
esac

OUT_PARENT="$(dirname "$OUT_DIR")"
mkdir -p "$OUT_PARENT"
OUT_PARENT="$(cd "$OUT_PARENT" && pwd -P)"
OUT_DIR="$OUT_PARENT/$(basename "$OUT_DIR")"
STAGED="$(mktemp -d "$OUT_PARENT/.python-wheelhouse.XXXXXX")"
BUILD_VENV="$(mktemp -d "${TMPDIR:-/tmp}/echo-wheel-build.XXXXXX")"
BACKUP=""
cleanup() {
  rm -rf "$BUILD_VENV" "$STAGED"
  if [ -n "$BACKUP" ] && [ -e "$BACKUP" ] && [ ! -e "$OUT_DIR" ]; then
    mv "$BACKUP" "$OUT_DIR"
  fi
}
trap cleanup EXIT

python3 -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --only-binary=:all: "uv==0.11.25"
LOCKED_REQUIREMENTS="$BUILD_VENV/runtime-requirements.txt"
"$BUILD_VENV/bin/uv" export --quiet \
  --frozen --no-dev --no-emit-project \
  --extra serve --extra web --extra appliance --extra minimal \
  --output-file "$LOCKED_REQUIREMENTS"
"$BUILD_VENV/bin/python" -m pip wheel \
  --require-hashes \
  --only-binary=:all: \
  --wheel-dir "$STAGED" \
  --requirement "$LOCKED_REQUIREMENTS"
# packaging is a current runtime import but is not yet declared by the project
# extras. Keep it on the exact uv.lock version until that separate metadata fix
# lands; the generated SHA256SUMS still binds the downloaded wheel bytes.
PACKAGING_VERSION="$("$BUILD_VENV/bin/python" -c '
import pathlib, sys, tomllib
data = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())
print(next(p["version"] for p in data["package"] if p["name"] == "packaging"))
' "$REPO_ROOT/uv.lock")"
"$BUILD_VENV/bin/python" -m pip wheel \
  --only-binary=:all: --wheel-dir "$STAGED" "packaging==$PACKAGING_VERSION"
"$BUILD_VENV/bin/python" -m pip wheel \
  --no-deps --wheel-dir "$STAGED" "$REPO_ROOT"

mapfile -t ECHO_WHEELS < <(find "$STAGED" -maxdepth 1 -type f -name 'echo_os-*.whl' -printf '%f\n' | sort)
[ "${#ECHO_WHEELS[@]}" -eq 1 ] \
  || die "wheelhouse 必须且只能包含一个 echo_os wheel"
find "$STAGED" -mindepth 1 -maxdepth 1 ! -type f -print -quit | grep -q . \
  && die "wheelhouse 只能包含顶层常规文件"

printf '%s\n' "$SOURCE_TREE" >"$STAGED/.echo-source-tree"
printf '%s\n' "$PYTHON_RUNTIME" >"$STAGED/.echo-python-runtime"
(
  cd "$STAGED"
  find . -maxdepth 1 -type f -name '*.whl' -printf '%P\0' \
    | sort -z | xargs -0 sha256sum >SHA256SUMS
  sha256sum -c SHA256SUMS
)

[ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] \
  || die "Python wheel 构建修改了受版本控制的源码"

if [ -e "$OUT_DIR" ]; then
  BACKUP="$OUT_PARENT/.python-wheelhouse.backup.$$"
  [ ! -e "$BACKUP" ] || die "临时备份路径已存在: $BACKUP"
  mv "$OUT_DIR" "$BACKUP"
fi
mv "$STAGED" "$OUT_DIR"
STAGED=""
if [ -n "$BACKUP" ]; then
  rm -rf "$BACKUP"
  BACKUP=""
fi
printf 'python-wheelhouse=%s\nsource-tree=%s\nruntime=%s\n' \
  "$OUT_DIR" "$SOURCE_TREE" "$PYTHON_RUNTIME"
