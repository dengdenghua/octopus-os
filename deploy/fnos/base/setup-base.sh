#!/usr/bin/env bash
# 把一台刚装好的 Debian 13 (trixie) 变成 Octopus OS。
#
# 由 octopus-firstboot.service 在**首次开机、网络就绪后**执行 —— 刻意不在
# debian-installer 里跑:ZFS 编译 / Docker 安装 / 前端构建都要联网且耗时,
# 放在装机阶段失败会让整台机器装不起来;放首次开机则可重试、可查日志。
#
# 幂等:每个阶段有哨兵文件,重跑会跳过已完成的部分。
# 日志:journalctl -u octopus-firstboot
set -euo pipefail

OS_DIR=/opt/octopus-os
STATE_DIR=/var/lib/octopus-os/firstboot
LOG_TAG="octopus-firstboot"

# 可选覆盖文件:管理员/测试可在此改仓库源与镜像源(如指向宿主机 bundle/
# git daemon,或国内 git 镜像),不必改本脚本。必须在默认值赋值**之前** source,
# 否则 ${VAR:-default} 已定死,覆盖不生效(VM 实测踩坑)
[ -r /etc/octopus/firstboot.env ] && . /etc/octopus/firstboot.env

# 由 build-iso.sh 或环境变量注入
OS_REPO="${OCTOPUS_OS_REPO:-https://github.com/dengdenghua/octopus-os.git}"
OS_BRANCH="${OCTOPUS_OS_BRANCH:-p3-fnos}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-https://deb.debian.org/debian}"

log()  { echo "[$(date -Is)] $*" | tee -a "/var/log/${LOG_TAG}.log"; }
skip() { log "skip: $1 (已完成)"; }
done_mark() { mkdir -p "$STATE_DIR"; touch "$STATE_DIR/$1"; }
is_done()   { [ -f "$STATE_DIR/$1" ]; }

[ "$(id -u)" -eq 0 ] || { echo "请用 root 运行" >&2; exit 1; }

mkdir -p "$STATE_DIR"

# ── 1. 软件源 ─────────────────────────────────────────────
if ! is_done apt; then
  log "== 1/7 配置软件源 =="
  cat >/etc/apt/sources.list.d/debian.sources <<EOF
Types: deb
URIs: ${DEBIAN_MIRROR}
Suites: trixie trixie-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release
  done_mark apt
else skip apt; fi

# ── 2. 存储栈(全部用上游官方包,不自研)─────────────────
# 飞牛逆向结论:存储栈一律集成,一个补丁都不打。ZFS 用 OpenZFS 官方源。
if ! is_done storage; then
  log "== 2/7 安装存储栈 =="
  # 注意:lsblk 在 util-linux 里,不是独立 apt 包(VM 实测写成包名会
  # E: Unable to locate package → firstboot 卡死在 2/7 反复重试)
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    zfsutils-linux zfs-dkms \
    samba samba-common-bin smbclient \
    nfs-kernel-server \
    smartmontools mdadm lvm2 btrfs-progs \
    parted util-linux

  # ZFS 开机自动导入池 + 挂载
  systemctl enable --now zfs-import-cache || true
  systemctl enable zfs-mount zfs-import.target || true
  done_mark storage
else skip storage; fi

# ── 3. Docker ─────────────────────────────────────────────
if ! is_done docker; then
  log "== 3/7 安装 Docker =="
  install -m0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: trixie
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  done_mark docker
else skip docker; fi

# ── 4. Node(前端构建 + Electron)────────────────────────
if ! is_done node; then
  log "== 4/7 安装 Node 20 =="
  if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  fi
  done_mark node
else skip node; fi

# ── 5. Octopus OS 源码 + Python 依赖 ─────────────────────
if ! is_done octopus-src; then
  log "== 5/7 拉取 Octopus OS ($OS_BRANCH) =="
  mkdir -p "$OS_DIR"
  if [ ! -d "$OS_DIR/.git" ]; then
    # late_command 会先投放 setup-base.sh 引导文件,目录因此非空,
    # 直接 clone 会报 "already exists"(VM 实测)。备份后重克隆。
    if [ -n "$(ls -A "$OS_DIR" 2>/dev/null)" ]; then
      mv "$OS_DIR" "$OS_DIR.pre-clone.$$"
      mkdir -p "$OS_DIR"
    fi
    git clone --depth 1 --branch "$OS_BRANCH" "$OS_REPO" "$OS_DIR"
  else
    # 已有仓库时对齐 bundle/远程快照。注意:fetch 只更新 origin/$BRANCH,
    # checkout 不会让本地分支快进(bundle 场景实测 HEAD 停留在旧提交),
    # 必须 reset --hard 对齐;工作区脏文件会被覆盖,本脚本即被覆盖源。
    git -C "$OS_DIR" fetch --depth 1 origin "$OS_BRANCH"
    git -C "$OS_DIR" reset --hard FETCH_HEAD
  fi
  done_mark octopus-src
  # clone/reset 后仓库版本(0644)会覆盖 late_command 投放的引导脚本,
  # 而 firstboot.service 的 ExecStart 直接执行它 —— 无执行位会 203/EXEC
  # (VM 实测:重启后服务起不来)。每次重拉后强制恢复执行位。
  chmod +x "$OS_DIR/deploy/fnos/base/setup-base.sh" 2>/dev/null || true
else skip octopus-src; fi

if ! is_done octopus-py; then
  log "== 5b/7 安装 Python 依赖 =="
  # 母体 octopus-agent 是私有仓库,Docker/设备构建走本地 wheel;
  # 见 deploy/appliance/prepare-agent-wheel.sh。
  # 无凭据环境(如验证 VM)可用 OCTOPUS_SKIP_AGENT=1 跳过私有 agent,
  # 仅装最小集;真实部署请配 GitHub token 或本地 wheel。
  if [ "${OCTOPUS_SKIP_AGENT:-0}" = "1" ]; then
    log "  OCTOPUS_SKIP_AGENT=1,跳过私有 agent,仅装 minimal"
    python3 -m venv "$OS_DIR/.venv"
    "$OS_DIR/.venv/bin/pip" install --upgrade pip
    "$OS_DIR/.venv/bin/pip" install -e "$OS_DIR[minimal]"
  elif command -v uv >/dev/null 2>&1; then
    (cd "$OS_DIR" && uv sync --extra serve --extra web --extra appliance --extra dev)
  else
    python3 -m venv "$OS_DIR/.venv"
    "$OS_DIR/.venv/bin/pip" install --upgrade pip
    "$OS_DIR/.venv/bin/pip" install -e "$OS_DIR[serve,web,appliance]"
  fi
  done_mark octopus-py
else skip octopus-py; fi

if ! is_done octopus-web; then
  log "== 5c/7 构建前端 =="
  # 锁文件是 pnpm-lock.yaml,必须用 pnpm 装(VM 实测:fallback 的 npm ci
  # 因无 package-lock.json 报 EUSAGE)。4/7 只装了 Node,这里补装 pnpm。
  if ! command -v pnpm >/dev/null 2>&1; then
    npm install -g pnpm@9
  fi
  (cd "$OS_DIR/frontend" && pnpm install --frozen-lockfile && pnpm build)
  [ -f "$OS_DIR/frontend/dist/index.html" ] || { log "✗ 前端构建失败"; exit 1; }
  done_mark octopus-web
else skip octopus-web; fi

# ── 6. 原生 shell(cage + plymouth + Electron)────────────
# 仅当检测到显示输出时才装;纯无头 NAS 跳过这步,省几百 MB。
if ! is_done shell; then
  if [ "${OCTOPUS_HDMI_SHELL:-auto}" = "off" ]; then
    log "== 6/7 跳过原生 shell (OCTOPUS_HDMI_SHELL=off) =="
  elif ls /dev/dri/card* >/dev/null 2>&1 || [ "${OCTOPUS_HDMI_SHELL:-auto}" = "on" ]; then
    log "== 6/7 安装原生 shell =="
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      cage plymouth plymouth-themes seatd rsync
    # Electron 运行时依赖:精简 Debian 默认没有,不装桌面起不来
    # (VM 实测 octopus-shell 循环重启,status=127,ldd 缺 libnss3/libasound2)
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      libnss3 libasound2 libgbm1 libgtk-3-0 libxss1 libxtst6 libcups2 \
      libxrandr2 libatk-bridge2.0-0 libdrm2
    systemctl enable seatd
    install -m644 "$OS_DIR/deploy/native-shell/octopus-shell.service" \
      /etc/systemd/system/octopus-shell.service
    chmod +x "$OS_DIR/deploy/native-shell/octopus-shell-launch.sh"
    systemctl daemon-reload
    systemctl set-default graphical.target
    systemctl enable octopus-shell.service
    plymouth-set-default-theme -R spinner 2>/dev/null || true
  else
    log "== 6/7 未检测到 GPU,跳过原生 shell(纯无头模式)=="
  fi
  done_mark shell
else skip shell; fi

# ── 7. 服务:appliance 后端 + nginx 反代 ─────────────────
if ! is_done services; then
  log "== 7/7 安装服务单元 =="

  # 数据根目录:统一命名空间挂在这里(飞牛用 /fs,我们用 /data)
  mkdir -p /data/nas /data/apps

  install -m644 "$OS_DIR/deploy/fnos/base/octopus-appliance.service" \
    /etc/systemd/system/octopus-appliance.service

  # nginx 反代:对外只暴露 80/443,后端 appliance 只听 127.0.0.1:8000。
  # 与飞牛同一手法 —— 单 nginx 入口,功能模块各自 unix socket / 本地端口。
  install -m644 "$OS_DIR/deploy/fnos/base/octopus-nginx.conf" \
    /etc/nginx/sites-available/octopus
  ln -sf /etc/nginx/sites-available/octopus /etc/nginx/sites-enabled/octopus
  rm -f /etc/nginx/sites-enabled/default

  # 随机生成 TLS 自签证书(每设备独立)。
  # 飞牛的一个隐患:nginx conf 里随包带了 server.crt/key,若全系共用则等于
  # TLS 私钥公开。这里强制每设备首启重新生成。
  if [ ! -f /etc/ssl/private/octopus-selfsigned.key ]; then
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
      -subj "/CN=$(hostname)" \
      -keyout /etc/ssl/private/octopus-selfsigned.key \
      -out /etc/ssl/certs/octopus-selfsigned.crt 2>/dev/null || log "警告:自签证书生成失败"
  fi

  systemctl daemon-reload
  systemctl enable --now octopus-appliance.service
  # nginx 可能已在跑(apt 安装时自启),`enable --now` 不会重载已运行进程
  # 的配置 → 80 端口仍服务旧 default 站点(VM 实测)。必须 restart。
  nginx -t && systemctl enable nginx && systemctl restart nginx
  done_mark services
else skip services; fi

log "✓ Octopus OS 基础系统就绪"
log "  后端:systemctl status octopus-appliance"
log "  Web :http://$(hostname).local  (或本机 IP)"
if systemctl is-enabled octopus-shell.service >/dev/null 2>&1; then
  log "  HDMI:已启用原生 shell,reboot 后开机进桌面"
fi
