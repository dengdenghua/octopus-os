#!/usr/bin/env bash
# 把一台 Debian(VM 或真机)装成 octopus-os 原生 shell 设备:
#   开机 → plymouth splash → 自动登录 → cage → Electron 全屏 agent 桌面。
#
# v1:从源码跑(便于在 VM 快速验证"开机进桌面");硬化成单 AppImage(electron-builder)
# 与做成可烧录 .img(debootstrap/mkosi)是后续——见 README.md。
#
# 用法(在 octopus-os 仓库里):sudo ./deploy/native-shell/setup-native-shell.sh
# 跑完 reboot。最可能要调的点见 octopus-shell.service 注释 + README 排错节。
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "请用 sudo 运行" >&2; exit 1; }

OS_DIR="${OCTOPUS_OS_DIR:-/opt/octopus-os}"
USER_NAME="${OCTOPUS_USER:-octopus}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "== 1. 装依赖(cage / plymouth / node / docker / seatd)=="
apt-get update
apt-get install -y cage plymouth plymouth-themes seatd rsync ca-certificates curl git

if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now seatd docker || true

echo "== 2. 设备用户 $USER_NAME(seat/video/input/docker 组)=="
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
for grp in video input render seat docker tty; do
  getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$USER_NAME" || true
done

echo "== 3. 同步 os 源码到 $OS_DIR 并构建前端 =="
mkdir -p "$OS_DIR"
rsync -a --delete \
  --exclude='.git' --exclude='node_modules' --exclude='data' --exclude='dist' \
  "$REPO_ROOT/" "$OS_DIR/"
chown -R "$USER_NAME:$USER_NAME" "$OS_DIR"

# 以设备用户装依赖 + 构建(pnpm 优先,回退 npm)
sudo -u "$USER_NAME" bash -lc '
  set -e
  cd "'"$OS_DIR"'/frontend"
  if command -v pnpm >/dev/null 2>&1; then pnpm install && pnpm build
  else npm install && npm run build; fi
'
[ -f "$OS_DIR/frontend/dist/index.html" ] || { echo "✗ 前端未构建出 dist/index.html" >&2; exit 1; }

echo "== 4. 安装 systemd 会话 unit + 默认进图形 =="
UNIT=/etc/systemd/system/octopus-shell.service
install -m644 "$OS_DIR/deploy/native-shell/octopus-shell.service" "$UNIT"
# 按实际 OS_DIR / 用户改写 unit
sed -i "s#/opt/octopus-os#$OS_DIR#g" "$UNIT"
sed -i "s/^User=.*/User=$USER_NAME/" "$UNIT"
chmod +x "$OS_DIR/deploy/native-shell/octopus-shell-launch.sh"
systemctl daemon-reload
systemctl set-default graphical.target
systemctl enable octopus-shell.service

echo "== 5. plymouth 开机 splash(best-effort,失败不阻断)=="
if plymouth-set-default-theme -l >/dev/null 2>&1; then
  plymouth-set-default-theme -R spinner 2>/dev/null || true
  # 需要内核 cmdline 带 quiet splash;GRUB 用户可在 /etc/default/grub 加后 update-grub。
  echo "  提示:若无 splash,确认内核 cmdline 含 'quiet splash' 并 update-initramfs -u。"
fi

cat <<EOF

✓ 原生 shell 已装好。
  后端(agent/appliance 容器)请按 deploy/appliance/README 单独起:
    ./deploy/appliance/prepare-agent-wheel.sh && ./deploy/appliance/prepare-agent-webui.sh
    (cd deploy/appliance && docker compose up -d --build)   # restart:unless-stopped 会随机自启

  然后 reboot —— 开机即:plymouth → 自动登录 → cage → Electron 全屏 agent 桌面。
  调试:journalctl -u octopus-shell -b   |   先不开机自启可:systemctl start octopus-shell
EOF
