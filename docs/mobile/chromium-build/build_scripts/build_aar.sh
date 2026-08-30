#!/usr/bin/env bash
# build_aar.sh —— 把 Chromium WebView 编译产物打成 AAR
#
# AAR 格式：Android Archive，含 .so 库 + AndroidManifest.xml + resources
# ApkClaw 集成时通过 implementation(files("libs/echo-webview.aar")) 引入
#
# 用法：
#   ./build_aar.sh [out_dir]
#   out_dir 默认：$HOME/chromium/src/out/EchoWebView_arm64

set -euo pipefail

OUT_DIR="${1:-$HOME/chromium/src/out/EchoWebView_arm64}"
AAR_NAME="echo-webview-${TARGET_CPU:-arm64}-v8a-1.0.0"

echo "============================================================"
echo "📦 Echo Mobile · Chromium WebView AAR 打包"
echo "============================================================"
echo ""
echo "编译目录：$OUT_DIR"
echo "AAR 名称：$AAR_NAME.aar"
echo ""

if [ ! -d "$OUT_DIR" ]; then
  echo "❌ 编译目录不存在：$OUT_DIR"
  echo "   请先跑 build_webview.sh"
  exit 1
fi

WORK_DIR="$OUT_DIR/aar_work"
DIST_DIR="$OUT_DIR/../../dist"
mkdir -p "$WORK_DIR" "$DIST_DIR"

# ── 1. 清理 ────────────────────────────────────────
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR/jni/arm64-v8a"
mkdir -p "$WORK_DIR/res/values"

# ── 2. 复制 .so 库 ──────────────────────────────────
echo "📋 复制 .so 库到 jni/arm64-v8a/..."

if [ ! -d "$OUT_DIR/lib.unstripped" ]; then
  echo "❌ 找不到 lib.unstripped/，编译没成功？"
  exit 1
fi

SHIPPED_LIBS=0
for so in "$OUT_DIR/lib.unstripped"/*.so; do
  # strip 掉调试符号（AAR 不应带）
  STRIPPED="$WORK_DIR/jni/arm64-v8a/$(basename $so)"
  if command -v llvm-strip >/dev/null 2>&1; then
    llvm-strip --strip-unneeded "$so" -o "$STRIPPED" 2>/dev/null || cp "$so" "$STRIPPED"
  else
    cp "$so" "$STRIPPED"
  fi
  SHIPPED_LIBS=$((SHIPPED_LIBS + 1))
done

echo "  ✓ 复制了 $SHIPPED_LIBS 个 .so 库"

# 关键库
for lib in libwebviewchromium.so libwebviewchromium_plat_support.so libchromium_android_linker.so; do
  if [ ! -f "$WORK_DIR/jni/arm64-v8a/$lib" ]; then
    echo "⚠️  关键库缺失：$lib（功能可能不完整）"
  fi
done

# ── 3. 写 AndroidManifest.xml（空 stub） ────────────
cat > "$WORK_DIR/AndroidManifest.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.echo.webview"
    android:versionCode="1"
    android:versionName="1.0.0">
    <uses-sdk
        android:minSdkVersion="26"
        android:targetSdkVersion="34" />
</manifest>
EOF

# ── 4. 写 proguard 配置 ───────────────────────────
cat > "$WORK_DIR/proguard.txt" <<EOF
# Chromium WebView proguard 规则
-keep class org.chromium.** { *; }
-keep class com.echo.webview.** { *; }
-dontwarn org.chromium.**
EOF

# ── 5. 打 zip（=AAR） ──────────────────────────────
cd "$WORK_DIR"
AAR_FILE="$DIST_DIR/${AAR_NAME}.aar"
zip -r "$AAR_FILE" AndroidManifest.xml proguard.txt jni/ res/

AAR_SIZE=$(du -h "$AAR_FILE" | cut -f1)
echo ""
echo "✅ AAR 打包完成：$AAR_FILE ($AAR_SIZE)"

# ── 6. 验证 AAR ───────────────────────────────────
echo ""
echo "🔍 验证 AAR 内容..."

unzip -l "$AAR_FILE" | head -20

# ── 7. 集成步骤 ───────────────────────────────────
echo ""
echo "============================================================"
echo "📲 集成到 ApkClaw"
echo "============================================================"
echo ""
echo "1. 复制 AAR 到 ApkClaw："
echo "     cp $AAR_FILE $HOME/echo-agent/ApkClaw/app/libs/"
echo ""
echo "2. 改 ApkClaw/app/build.gradle.kts："
echo "     dependencies {"
echo "         implementation(files(\"libs/${AAR_NAME}.aar\"))"
echo "     }"
echo ""
echo "3. 在 ClawApplication.kt 加："
echo "     val engine: BrowserEngine = if (KiwiAarLoader.isAvailable()) {"
echo "         KiwiWebViewEngine()"
echo "     } else {"
echo "         SystemWebViewEngine()"
echo "     }"
echo ""
echo "详细集成见：docs/mobile/chromium-build/BUILD.md § Phase 4"
