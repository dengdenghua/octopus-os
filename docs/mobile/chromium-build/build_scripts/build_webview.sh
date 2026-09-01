#!/usr/bin/env bash
# build_webview.sh —— 编译 Chromium WebView 子集（Android target）
#
# 前置：fetch_chromium.sh 已跑完
#
# 用法：
#   ./build_webview.sh [chromium_src_dir] [target_cpu]
#   target_cpu: arm64 (默认) / x86 / x64 / arm
#
# 耗时：首次 4-8 小时，增量 5-15 分钟

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHROMIUM_SRC="${1:-$HOME/chromium/src}"
TARGET_CPU="${2:-arm64}"
TARGET_OS="${TARGET_OS:-android}"
BUILD_TYPE="${BUILD_TYPE:-Release}"

echo "============================================================"
echo "🔨 Echo Mobile · Chromium WebView 编译"
echo "============================================================"
echo ""
echo "源码目录：$CHROMIUM_SRC"
echo "目标架构：$TARGET_CPU"
echo "目标 OS：$TARGET_OS"
echo "构建类型：$BUILD_TYPE"
echo ""

if [ ! -d "$CHROMIUM_SRC" ]; then
  echo "❌ 源码目录不存在：$CHROMIUM_SRC"
  echo "   请先跑 fetch_chromium.sh"
  exit 1
fi

cd "$CHROMIUM_SRC"

# ── 1. 检查 NDK ─────────────────────────────────────────
echo "🔍 检查 NDK..."

if [ -z "${ANDROID_NDK_ROOT:-}" ]; then
  for candidate in \
    "$HOME/android-ndk-r26b" \
    "$HOME/Android/Sdk/ndk/26.3.11579264" \
    "/opt/android-ndk-r26b"
  do
    if [ -d "$candidate" ]; then
      export ANDROID_NDK_ROOT="$candidate"
      break
    fi
  done
fi

if [ -z "${ANDROID_NDK_ROOT:-}" ] || [ ! -d "$ANDROID_NDK_ROOT" ]; then
  echo "❌ ANDROID_NDK_ROOT 未设置或目录不存在"
  echo "   下载 NDK r26b："
  echo "     https://developer.android.com/ndk/downloads"
  echo "   设置："
  echo "     export ANDROID_NDK_ROOT=\$HOME/android-ndk-r26b"
  exit 1
fi

echo "  ✓ NDK：$ANDROID_NDK_ROOT"

# ── 2. 配 GN args ──────────────────────────────────────
echo ""
echo "⚙️ 配 GN args（target_os=android, target_cpu=$TARGET_CPU）..."

OUT_DIR="out/EchoWebView_$TARGET_CPU"
mkdir -p "$OUT_DIR"

# 关键：只编 webview + content，节省 80% 时间
cat > "$OUT_DIR/args.gn" <<EOF
# Echo Mobile WebView Build Config
target_os = "$TARGET_OS"
target_cpu = "$TARGET_CPU"
is_debug = $([ "$BUILD_TYPE" = "Debug" ] && echo "true" || echo "false")
is_official_build = true
is_chrome_branded = false
is_component_build = false

# 关键 target
android_webview_use_incremental_install = true

# 性能优化
symbol_level = 1
strip_debug_info = true

# 编译优化
use_jumbo_build = true
use_thin_lto = $([ "$TARGET_CPU" = "arm64" ] && echo "true" || echo "false")

# 关闭不需要的子系统
enable_background_mode = false
enable_service_discovery = false
enable_pdf = false
enable_print_preview = false
enable_websockets = true
enable_webrtc = true

# 必装的 hooks
import("//build/config/android/rules.gni")
EOF

echo "  args.gn 写入：$OUT_DIR/args.gn"

# ── 3. gn gen ─────────────────────────────────────────
echo ""
echo "📋 生成 ninja 文件（首次约 1-2 分钟）..."

gn gen "$OUT_DIR" --check

# ── 4. 内存检查 ────────────────────────────────────────
echo ""
echo "🧠 编译内存检查..."

TOTAL_MEM_GB=$(free -g | awk '/^Mem:/ {print $2}')
NINJA_JOBS=$(( TOTAL_MEM_GB / 4 ))  # 每核 4 GB
if [ "$NINJA_JOBS" -lt 4 ]; then NINJA_JOBS=4; fi
if [ "$NINJA_JOBS" -gt 16 ]; then NINJA_JOBS=16; fi

echo "  物理内存：${TOTAL_MEM_GB} GB → ninja jobs：$NINJA_JOBS"

# ── 5. 编译 webview ────────────────────────────────
echo ""
echo "🚀 编译 webview target（首次 4-8 小时，请耐心等待）..."
echo "   日志：$OUT_DIR/build.log"
echo "   后台跑：nohup ninja -C $OUT_DIR -j$NINJA_JOBS webview > $OUT_DIR/build.log 2>&1 &"
echo ""

time ninja -C "$OUT_DIR" -j"$NINJA_JOBS" webview

# ── 6. 验证产物 ────────────────────────────────────────
echo ""
echo "✅ 验证产物..."

SO_FILES=$(find "$OUT_DIR/lib.unstripped" -name "*.so" 2>/dev/null | head -20 || true)
if [ -z "$SO_FILES" ]; then
  echo "❌ 未找到 .so 产物"
  echo "   查看日志：$OUT_DIR/build.log"
  exit 1
fi

echo "  找到的 .so 库："
for f in $SO_FILES; do
  size=$(du -h "$f" | cut -f1)
  echo "    $(basename $f) ($size)"
done

# 关键：webviewchromium_plat_support 必须有
if [ ! -f "$OUT_DIR/lib.unstripped/libwebviewchromium_plat_support.so" ]; then
  echo "⚠️  缺少 libwebviewchromium_plat_support.so"
fi

# ── 7. 总结 ─────────────────────────────────────────
echo ""
echo "============================================================"
echo "✅ WebView 编译完成"
echo "============================================================"
echo ""
echo "📁 产物目录：$OUT_DIR/lib.unstripped/"
echo ""
echo "下一步："
echo "  cd $CHROMIUM_SRC"
echo "  $SCRIPT_DIR/build_aar.sh $OUT_DIR"
echo ""
