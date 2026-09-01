#!/usr/bin/env bash
# Static and live contract checks for the target-C desktop session.
set -euo pipefail

MODE="${1:---static}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_DIR="$REPO_ROOT/frontend"
FAILURES=0

pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*" >&2; FAILURES=$((FAILURES + 1)); }

require_file() {
  if [[ -f "$1" ]]; then pass "存在 ${1#"$REPO_ROOT"/}"; else fail "缺少 $1"; fi
}

require_executable() {
  if [[ -x "$1" ]]; then pass "可执行 ${1#"$REPO_ROOT"/}"; else fail "不可执行 $1"; fi
}

echo "Echo OS desktop-session static contract"
require_file "$APP_DIR/dist/index.html"
require_file "$APP_DIR/electron/native-windows.cjs"
require_file "$APP_DIR/electron/system-notifications.cjs"
require_file "$APP_DIR/package.json"
require_file "$REPO_ROOT/deploy/desktop-session/echo-desktop.service"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-kwin-window-bridge"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-notification-service"
require_file "$REPO_ROOT/deploy/desktop-session/echo_notification_store.py"
require_file "$REPO_ROOT/deploy/desktop-session/test_echo_notification_store.py"
require_executable "$REPO_ROOT/deploy/desktop-session/test-echo-notification-service.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host"
require_file "$REPO_ROOT/deploy/desktop-session/klipperrc"
require_executable "$REPO_ROOT/deploy/desktop-session/test_echo_clipboard_host.py"
require_executable "$REPO_ROOT/deploy/desktop-session/echo-accessibility-smoke.py"
require_executable "$REPO_ROOT/deploy/desktop-session/test_echo_accessibility_smoke.py"
require_file "$REPO_ROOT/deploy/desktop-session/echo-screen-reader.desktop"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/metadata.json"
require_file "$REPO_ROOT/deploy/desktop-session/kwin-window-bridge/contents/code/main.js"
require_executable "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/smoke-wayland-session-client.sh"
require_executable "$REPO_ROOT/deploy/desktop-session/wayland-window-smoke.py"
require_file "$REPO_ROOT/deploy/oem/echo-wayland.desktop"
require_file "$REPO_ROOT/deploy/oem/kscreenlockerrc"

if grep -q '^  --drm \\$' \
     "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" && \
   grep -q 'org.freedesktop.ScreenSaver.GetActive' \
     "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" && \
   grep -q '^RequirePassword=true$' "$REPO_ROOT/deploy/oem/kscreenlockerrc"; then
  pass "Wayland candidate uses DRM, KScreenLocker and password unlock"
else
  fail "Wayland candidate production contract is incomplete"
fi

if grep -q '^export GTK_IM_MODULE=fcitx$' \
     "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" && \
   grep -q '^export QT_IM_MODULE=fcitx$' \
     "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" && \
   grep -q 'ECHO_INPUT_METHOD_READY provider=fcitx5 dbus=ready frontend=x11' \
     "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" && \
   grep -q 'ECHO_INPUT_METHOD_READY provider=fcitx5 dbus=ready frontend=wayland' \
     "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh"; then
  pass "X11 与 Wayland 会话都监管 Fcitx5 多语言输入法"
else
  fail "Fcitx5 多语言输入法会话契约不完整"
fi

if grep -q 'org.kde.plasma.private.clipboard 0.1' \
     "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" && \
   grep -q 'database must be exactly' \
     "$REPO_ROOT/deploy/desktop-session/echo-clipboard-host" && \
   grep -q '^KeepClipboardContents=false$' \
     "$REPO_ROOT/deploy/desktop-session/klipperrc" && \
   grep -q '^NoEmptyClipboard=true$' \
     "$REPO_ROOT/deploy/desktop-session/klipperrc" && \
   grep -q 'ECHO_CLIPBOARD_READY provider=klipper-qml.*session=x11' \
     "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" && \
   grep -q 'ECHO_CLIPBOARD_READY provider=klipper-qml.*session=wayland' \
     "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh"; then
  pass "X11 与 Wayland 会话都监管无窗口 Klipper，历史只写入用户 runtime"
else
  fail "系统剪贴板或其隐私边界不完整"
fi

if grep -q '^export QT_ACCESSIBILITY=1$' \
     "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" && \
   grep -q '^export QT_ACCESSIBILITY=1$' \
     "$REPO_ROOT/deploy/desktop-session/echo-wayland-session.sh" && \
   grep -q 'ECHO_ACCESSIBILITY_READY provider=at-spi2.*session=x11' \
     "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" && \
   grep -q 'ECHO_ACCESSIBILITY_READY provider=at-spi2.*session=wayland' \
     "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" && \
   grep -q -- '--force-renderer-accessibility' \
     "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" && \
   grep -q -- '--force-renderer-accessibility' \
     "$REPO_ROOT/deploy/desktop-session/echo-wayland-shell-session.sh" && \
   grep -q 'required-name "Echo OS 桌面"' \
     "$REPO_ROOT/deploy/desktop-session/echo-desktop-session.sh" && \
   grep -q '^Exec=/usr/bin/orca$' \
     "$REPO_ROOT/deploy/desktop-session/echo-screen-reader.desktop"; then
  pass "X11 与 Wayland 都要求 AT-SPI 应用树，并提供 Orca 屏幕阅读器入口"
else
  fail "AT-SPI、应用可访问树或屏幕阅读器入口不完整"
fi

if grep -q '"main": "electron/main.cjs"' "$APP_DIR/package.json" && \
   grep -q '"electron-builder"' "$APP_DIR/package.json"; then
  pass "Electron 打包入口和 builder 已配置"
else
  fail "Electron 打包入口或 builder 配置缺失"
fi

PACKAGED_BIN="$(
  find "$APP_DIR/release" -maxdepth 3 -type f -name echo-os-desktop \
    -perm -111 -print -quit 2>/dev/null || true
)"
if [[ -n "$PACKAGED_BIN" ]]; then
  pass "存在打包桌面二进制 ${PACKAGED_BIN#"$REPO_ROOT"/}"
else
  fail "未找到 release/**/echo-os-desktop 打包二进制"
fi

if [[ "$MODE" == "--static" ]]; then
  [[ "$FAILURES" -eq 0 ]] || exit 1
  echo "Static contract OK"
  exit 0
fi

if [[ "$MODE" != "--runtime" ]]; then
  echo "用法: $0 [--static|--runtime]" >&2
  exit 2
fi

echo "Echo OS desktop-session live contract"
[[ "$(uname -s)" == "Linux" ]] || fail "live contract 只能在 Linux 会话运行"
for command_name in kwin_x11 wmctrl xprop dbus-run-session; do
  if command -v "$command_name" >/dev/null 2>&1; then
    pass "命令可用: $command_name"
  else
    fail "命令缺失: $command_name"
  fi
done
if [[ -n "${DISPLAY:-}" ]]; then pass "DISPLAY=$DISPLAY"; else fail "DISPLAY 未设置"; fi

if wmctrl -m 2>/dev/null | grep -qi 'kwin'; then
  pass "KWin 正在管理当前显示会话"
else
  fail "当前 DISPLAY 的窗口管理器不是 KWin"
fi

if wmctrl -lx 2>/dev/null | grep -Eqi 'echo-shell|echo-os-desktop|Echo Desktop'; then
  pass "Echo Shell 窗口已注册到 KWin"
else
  fail "KWin 窗口列表中找不到 Echo Shell"
fi

KWIN_BRIDGE_SOCKET="${ECHO_KWIN_WINDOW_BRIDGE_SOCKET:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/echo-os/kwin-window-bridge.sock}"
if /usr/lib/echo-os/echo-kwin-window-bridge \
     --socket "$KWIN_BRIDGE_SOCKET" --probe >/dev/null 2>&1; then
  pass "KWin 脚本已通过私有 socket 发布 compositor UUID 状态"
else
  fail "KWin compositor 窗口桥未就绪"
fi

if gdbus call --session \
     --dest org.freedesktop.DBus \
     --object-path /org/freedesktop/DBus \
     --method org.freedesktop.DBus.NameHasOwner org.fcitx.Fcitx5 \
     2>/dev/null | grep -q true; then
  pass "Fcitx5 多语言输入法已取得会话 D-Bus 名称"
else
  fail "Fcitx5 多语言输入法未就绪"
fi
if gdbus call --session \
     --dest org.freedesktop.DBus \
     --object-path /org/freedesktop/DBus \
     --method org.freedesktop.DBus.NameHasOwner org.kde.klipper \
     2>/dev/null | grep -q true; then
  pass "Klipper 系统剪贴板已取得会话 D-Bus 名称"
else
  fail "Klipper 系统剪贴板未就绪"
fi
if gdbus call --session \
     --dest org.a11y.Bus \
     --object-path /org/a11y/bus \
     --method org.a11y.Bus.GetAddress >/dev/null 2>&1; then
  pass "AT-SPI 会话总线已发布辅助技术地址"
else
  fail "AT-SPI 会话总线未就绪"
fi
[[ "${GTK_IM_MODULE:-}" == fcitx && "${QT_IM_MODULE:-}" == fcitx && \
   "${XMODIFIERS:-}" == @im=fcitx && "${SDL_IM_MODULE:-}" == fcitx ]] && \
  pass "GTK、Qt、XIM 与 SDL 输入环境均指向 Fcitx5" || \
  fail "原生应用输入环境没有完整指向 Fcitx5"
[[ "${QT_ACCESSIBILITY:-}" == 1 ]] && \
  pass "Qt 原生应用无障碍桥已启用" || \
  fail "QT_ACCESSIBILITY 没有传入原生应用会话"

if ECHO_NATIVE_SHELL=1 node - "$APP_DIR" <<'NODE'
const path = require("path");
const appDir = process.argv[2];
const bridge = require(path.join(appDir, "electron", "native-windows.cjs"));
bridge.listNativeWindows({ nativeShell: true }).then((result) => {
  if (!result.ok) {
    console.error(result.error || "window provider failed");
    process.exitCode = 1;
    return;
  }
  console.log(`${result.provider}: ${result.windows.length} windows`);
});
NODE
then
  pass "Electron 窗口 provider 能读取当前窗口"
else
  fail "Electron 窗口 provider live probe 失败"
fi

[[ "$FAILURES" -eq 0 ]] || exit 1
echo "Live contract OK"
