#!/usr/bin/env bash
# fetch_chromium.sh —— 一键拉取 Chromium 源码 + 装 hooks
#
# 用法：
#   ./fetch_chromium.sh [chromium_dir]
#   缺省目录：$HOME/chromium
#
# 耗时：30 GB 拉取约 2-4 小时（视网络）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHROMIUM_DIR="${1:-$HOME/chromium}"
CHROMIUM_BRANCH="${CHROMIUM_BRANCH:-124.0.6367.78}"  # 锁版本，最后稳定 Chromium 124

echo "============================================================"
echo "🕷️ Echo Mobile · Chromium 真集成 · 源码拉取"
echo "============================================================"
echo ""
echo "目标目录：$CHROMIUM_DIR"
echo "目标版本：$CHROMIUM_BRANCH (锁版本，避免 API 漂移)"
echo ""

# ── 1. 检查依赖 ──────────────────────────────────────────
echo "🔍 检查依赖..."

for cmd in git python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "❌ 缺少依赖：$cmd"
    exit 1
  fi
done

if [ ! -d "$HOME/depot_tools" ]; then
  echo "❌ depot_tools 未安装"
  echo "   请先跑："
  echo "     git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git $HOME/depot_tools"
  echo "     echo 'export PATH=\"\$HOME/depot_tools:\$PATH\"' >> ~/.bashrc"
  echo "     source ~/.bashrc"
  exit 1
fi

export PATH="$HOME/depot_tools:$PATH"

echo "  ✓ git / python3 / depot_tools"

# ── 2. 磁盘空间检查 ──────────────────────────────────────
echo ""
echo "💾 检查磁盘空间（需要 ≥ 150 GB）..."

if [ ! -d "$CHROMIUM_DIR" ]; then
  mkdir -p "$CHROMIUM_DIR"
fi

AVAILABLE_GB=$(df -BG "$CHROMIUM_DIR" | awk 'NR==2 {print $4}' | tr -d 'G')
if [ "$AVAILABLE_GB" -lt 150 ]; then
  echo "⚠️  磁盘空间不足：当前 ${AVAILABLE_GB}GB，需要 ≥ 150GB"
  read -p "继续吗？(y/N) " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
  fi
fi

echo "  ✓ 磁盘空间：${AVAILABLE_GB}GB"

# ── 3. fetch 源码 ────────────────────────────────────────
echo ""
echo "📥 拉取 Chromium 源码（首次约 30 GB，需 2-4 小时）..."

cd "$CHROMIUM_DIR"

if [ ! -d ".git" ] && [ ! -f ".gclient_entries" ]; then
  # 第一次：初始化
  cat > .gclient <<EOF
solutions = [
  {
    "name": "src",
    "url": "https://chromium.googlesource.com/chromium/src.git@$CHROMIUM_BRANCH",
    "managed": False,
    "custom_deps": {},
    "custom_vars": {},
  },
]
EOF

  echo "  执行 fetch --nohooks android（首次可能很慢）..."
  fetch --nohooks android
else
  echo "  ✓ 源码已存在，跳过 fetch"
fi

cd src

# ── 4. 装 Android 必需的 hooks ──────────────────────────
echo ""
echo "🪝 装 Android build hooks..."

./build/install-build-deps-android.sh
gclient runhooks

# ── 5. 验证 ──────────────────────────────────────────────
echo ""
echo "✅ 验证..."

COMMIT=$(git log -1 --format=%H 2>/dev/null || echo "unknown")
echo "  HEAD commit: $COMMIT"

# 检查关键文件
for f in chrome/android_webview/BUILD.gn content/public/android/BUILD.gn; do
  if [ ! -f "$f" ]; then
    echo "❌ 关键文件缺失：$f"
    exit 1
  fi
done

echo "  ✓ 关键文件存在"

# ── 6. 总结 ──────────────────────────────────────────────
echo ""
echo "============================================================"
echo "✅ Chromium 源码就绪"
echo "============================================================"
echo ""
echo "📁 工作目录：$CHROMIUM_DIR/src"
echo "📌 当前版本：$(git describe --tags --always 2>/dev/null || echo $CHROMIUM_BRANCH)"
echo "💾 占用空间：$(du -sh . | cut -f1)"
echo ""
echo "下一步："
echo "  cd $CHROMIUM_DIR/src"
echo "  $SCRIPT_DIR/build_webview.sh"
echo ""
