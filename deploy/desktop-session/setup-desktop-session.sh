#!/usr/bin/env bash
# Install the target-C KWin desktop bring-up session on Debian stable.
set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "请用 sudo 运行" >&2; exit 1; }

OS_DIR="${ECHO_OS_DIR:-/opt/echo-os}"
USER_NAME="${ECHO_USER:-echo}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "== 1. 安装 Xorg / KWin / EWMH / Electron 运行依赖 =="
apt-get update
apt-get install -y \
  xserver-xorg-core xinit x11-utils x11-xserver-utils dbus-x11 \
  xss-lock xsecurelock kwin-x11 kwin-wayland libkscreenlocker6 xwayland wmctrl \
  dolphin konsole firefox-esr kate okular gwenview ark haruna kde-spectacle kcalc \
  xdg-utils desktop-file-utils 7zip bzip2 unar unzip zip \
  fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3 fcitx5-frontend-gtk4 \
  fcitx5-frontend-qt5 fcitx5-frontend-qt6 fcitx5-config-qt kde-config-fcitx5 \
  libkf6config-bin \
  plasma-workspace libqt6sql6-sqlite python3-pyqt6.qtqml xclip wl-clipboard \
  at-spi2-core python3-pyatspi orca speech-dispatcher \
  speech-dispatcher-espeak-ng espeak-ng \
  libglib2.0-bin \
  udisks2 dosfstools exfatprogs ntfs-3g e2fsprogs btrfs-progs xfsprogs \
  kio-extras libmtp-runtime eject usbutils \
  cups-daemon cups-client cups-common cups-filters cups-filters-core-drivers \
  cups-pk-helper print-manager ipp-usb avahi-daemon \
  libsane1 libsane-common sane-utils sane-airscan skanpage \
  network-manager wpasupplicant wireless-regdb \
  pipewire pipewire-pulse wireplumber alsa-utils bluez brightnessctl \
  systemsettings plasma-nm bluedevil plasma-pa kscreen powerdevil \
  upower power-profiles-daemon \
  polkitd polkit-kde-agent-1 \
  python3 python3-dbus python3-gi systemd-coredump plymouth plymouth-themes rsync \
  ca-certificates curl git \
  libgtk-3-0t64 libnss3 libasound2t64 libgbm1 libdrm2 libxss1 libxtst6 \
  libxrandr2 libxcomposite1 libxdamage1 libatk-bridge2.0-0t64 \
  libatspi2.0-0t64 libcups2t64 fonts-dejavu-core

if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker || true
if ! command -v pnpm >/dev/null 2>&1; then
  corepack enable
  corepack prepare pnpm@10.26.2 --activate
fi

echo "== 2. 创建桌面会话用户 $USER_NAME =="
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
for group_name in audio video input render scanner docker tty; do
  getent group "$group_name" >/dev/null 2>&1 && \
    usermod -aG "$group_name" "$USER_NAME" || true
done

echo "== 3. 同步源码并构建离线桌面前端 =="
mkdir -p "$OS_DIR"
rsync -a \
  --exclude='.git' --exclude='node_modules' --exclude='data' \
  --exclude='dist' --exclude='release' \
  "$REPO_ROOT/" "$OS_DIR/"
chown -R "$USER_NAME:$USER_NAME" "$OS_DIR"

# shellcheck disable=SC2016 # Variables expand in the runuser-owned login shell.
runuser -u "$USER_NAME" -- env ECHO_OS_FRONTEND_DIR="$OS_DIR/frontend" bash -lc '
  set -euo pipefail
  cd "$ECHO_OS_FRONTEND_DIR"
  case "$(dpkg --print-architecture)" in
    amd64) electron_arch=x64 ;;
    arm64) electron_arch=arm64 ;;
    *) echo "暂不支持的 Electron Linux 架构: $(dpkg --print-architecture)" >&2; exit 1 ;;
  esac
  pnpm install --frozen-lockfile
  pnpm build
  pnpm exec electron-builder --linux dir --"$electron_arch"
'
[[ -f "$OS_DIR/frontend/dist/index.html" ]] || {
  echo "前端未构建出 dist/index.html" >&2
  exit 1
}
PACKAGED_BIN="$(
  find "$OS_DIR/frontend/release" -maxdepth 3 -type f -name echo-os-desktop \
    -perm -111 -print -quit
)"
[[ -n "$PACKAGED_BIN" ]] || {
  echo "Electron 未构建出可执行的 Linux 目录包" >&2
  exit 1
}

echo "== 4. 安装 KWin 桌面 systemd 会话 =="
SESSION_SCRIPT="$OS_DIR/deploy/desktop-session/echo-desktop-session.sh"
install -d -m 0755 /usr/lib/echo-os
install -m 0755 "$OS_DIR/deploy/desktop-session/echo-session-lock" \
  /usr/lib/echo-os/echo-session-lock
install -m 0755 "$OS_DIR/deploy/desktop-session/echo-screen-locker" \
  /usr/lib/echo-os/echo-screen-locker
install -m 0644 "$OS_DIR/deploy/desktop-session/echo-lock.pam" \
  /etc/pam.d/echo-lock
install -m 0755 "$OS_DIR/deploy/desktop-session/echo-kwin-window-bridge" \
  /usr/lib/echo-os/echo-kwin-window-bridge
install -m 0755 "$OS_DIR/deploy/desktop-session/echo-notification-service" \
  /usr/lib/echo-os/echo-notification-service
install -m 0644 "$OS_DIR/deploy/desktop-session/echo_notification_store.py" \
  /usr/lib/echo-os/echo_notification_store.py
install -m 0755 "$OS_DIR/deploy/desktop-session/echo-clipboard-host" \
  /usr/lib/echo-os/echo-clipboard-host
install -m 0755 "$OS_DIR/deploy/desktop-session/echo-accessibility-smoke.py" \
  /usr/lib/echo-os/echo-accessibility-smoke.py
install -d -m 0755 /usr/local/share/applications
install -m 0644 "$OS_DIR/deploy/desktop-session/echo-screen-reader.desktop" \
  /usr/local/share/applications/echo-screen-reader.desktop
install -d -m 0755 /etc/systemd/coredump.conf.d /var/lib/systemd/coredump
install -m 0644 "$OS_DIR/deploy/system-health/echo-coredump.conf" \
  /etc/systemd/coredump.conf.d/60-echo-os.conf
install -m 0755 "$OS_DIR/deploy/system-health/echo-crash-health" \
  /usr/lib/echo-os/echo-crash-health
install -m 0644 "$OS_DIR/deploy/system-health/echo-crash-health.service" \
  /etc/systemd/system/echo-crash-health.service
install -m 0755 "$OS_DIR/deploy/removable-storage/echo-removable-storage-health" \
  /usr/lib/echo-os/echo-removable-storage-health
install -m 0644 "$OS_DIR/deploy/removable-storage/echo-removable-storage-health.service" \
  /etc/systemd/system/echo-removable-storage-health.service
install -d -m 0755 /usr/share/doc/echo-os
install -m 0644 "$OS_DIR/deploy/removable-storage/README.md" \
  /usr/share/doc/echo-os/removable-storage.md
install -d -m 0755 /etc/cups /etc/ipp-usb
install -m 0644 "$OS_DIR/deploy/printing/cupsd.conf" /etc/cups/cupsd.conf
install -m 0644 "$OS_DIR/deploy/printing/ipp-usb.conf" /etc/ipp-usb/ipp-usb.conf
install -m 0755 "$OS_DIR/deploy/printing/echo_printing_policy.py" \
  /usr/lib/echo-os/echo-printing-policy.py
install -m 0755 "$OS_DIR/deploy/printing/echo-printing-health" \
  /usr/lib/echo-os/echo-printing-health
install -m 0644 "$OS_DIR/deploy/printing/echo-printing-health.service" \
  /etc/systemd/system/echo-printing-health.service
install -m 0644 "$OS_DIR/deploy/printing/README.md" \
  /usr/share/doc/echo-os/printing.md
install -d -m 0755 /etc/sane.d
install -m 0644 "$OS_DIR/deploy/scanning/airscan.conf" /etc/sane.d/airscan.conf
install -m 0755 "$OS_DIR/deploy/scanning/echo_scanning_policy.py" \
  /usr/lib/echo-os/echo-scanning-policy.py
install -m 0755 "$OS_DIR/deploy/scanning/echo-scanning-health" \
  /usr/lib/echo-os/echo-scanning-health
install -m 0644 "$OS_DIR/deploy/scanning/echo-scanning-health.service" \
  /etc/systemd/system/echo-scanning-health.service
install -m 0644 "$OS_DIR/deploy/scanning/README.md" \
  /usr/share/doc/echo-os/scanning.md
install -d -m 0755 /etc/xdg
install -m 0644 "$OS_DIR/deploy/core-apps/mimeapps.list" /etc/xdg/mimeapps.list
install -m 0755 "$OS_DIR/deploy/core-apps/echo_core_apps_policy.py" \
  /usr/lib/echo-os/echo-core-apps-policy.py
install -m 0755 "$OS_DIR/deploy/core-apps/echo-core-apps-health" \
  /usr/lib/echo-os/echo-core-apps-health
install -m 0755 "$OS_DIR/deploy/core-apps/echo_core_apps_session_smoke.py" \
  /usr/lib/echo-os/echo-core-apps-session-smoke.py
install -m 0644 "$OS_DIR/deploy/core-apps/echo-core-apps-health.service" \
  /etc/systemd/system/echo-core-apps-health.service
install -m 0644 "$OS_DIR/deploy/core-apps/README.md" \
  /usr/share/doc/echo-os/core-apps.md
install -d -m 0755 /usr/share/kwin/scripts/org.echoos.windowbridge
cp -a "$OS_DIR/deploy/desktop-session/kwin-window-bridge/." \
  /usr/share/kwin/scripts/org.echoos.windowbridge/
chown -R root:root /usr/share/kwin/scripts/org.echoos.windowbridge
chmod 0755 "$SESSION_SCRIPT"
chmod 0755 "$OS_DIR/deploy/desktop-session/echo-wayland-session.sh"
chmod 0755 "$OS_DIR/deploy/desktop-session/echo-wayland-shell-session.sh"
chmod 0755 "$OS_DIR/deploy/desktop-session/verify-desktop-session.sh"
chmod 0755 "$OS_DIR/deploy/desktop-session/smoke-desktop-session.sh"
install -d -m 0755 /usr/share/wayland-sessions /etc/xdg
install -m 0644 "$OS_DIR/deploy/oem/echo-wayland.desktop" \
  /usr/share/wayland-sessions/echo-wayland.desktop
install -m 0644 "$OS_DIR/deploy/oem/kscreenlockerrc" \
  /etc/xdg/kscreenlockerrc
install -m 0644 "$OS_DIR/deploy/desktop-session/klipperrc" \
  /etc/xdg/klipperrc
desktop-file-validate /usr/local/share/applications/echo-screen-reader.desktop
runuser -u "$USER_NAME" -- \
  "$OS_DIR/deploy/desktop-session/verify-desktop-session.sh" --static
UNIT=/etc/systemd/system/echo-desktop.service
install -m 0644 "$OS_DIR/deploy/desktop-session/echo-desktop.service" "$UNIT"
sed -i "s#/opt/echo-os#$OS_DIR#g" "$UNIT"
sed -i "s/^User=.*/User=$USER_NAME/" "$UNIT"

# Cage kiosk and KWin desktop cannot own the same seat at once.
systemctl disable --now echo-shell.service 2>/dev/null || true
systemctl daemon-reload
systemctl set-default graphical.target
systemctl preset systemd-coredump.socket
systemctl enable echo-crash-health.service
systemctl enable echo-removable-storage-health.service
systemctl enable cups.service
systemctl enable echo-printing-health.service
systemctl disable --now saned.socket
systemctl enable echo-scanning-health.service
systemctl enable echo-core-apps-health.service
systemctl enable echo-desktop.service

echo
echo "✓ KWin 通用桌面会话已安装。"
echo "  桌面二进制: $PACKAGED_BIN"
echo "  启动: systemctl start echo-desktop"
echo "  日志: journalctl -u echo-desktop -b"
echo "  重启后链路: Plymouth -> Xorg -> KWin -> Echo Desktop"
