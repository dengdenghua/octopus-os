#!/usr/bin/env bash
# provision A 路线首次开机步骤库 —— 被 setup-base.sh `source`,不单独执行。
#
# 拆分目的(收敛 P2):把"装机步骤"从"编排"里抽出来,让 A 路线特有步骤与
# 上游收敛步骤泾渭分明:
#   - A 路线特有:step_apt / storage / docker / node / echo_src / echo_py /
#     echo_web / services(apt 源、ZFS/SMB/NFS 存储栈、nginx+appliance 服务、
#     自签证书 —— 上游 deploy/appliance 是容器模型,不覆盖这些)
#   - 上游收敛:step_shell(桌面壳,ECHO_DESKTOP 开关调用上游
#     deploy/desktop-session/setup-desktop-session.sh)、
#     step_backup_recovery(直接复用上游 deploy/backup + deploy/recovery)
#
# 依赖(由 setup-base.sh 在 source 之前设置):OS_DIR STATE_DIR LOG_TAG
# OS_REPO OS_BRANCH DEBIAN_MIRROR,以及可选覆盖 env。

log()  { echo "[$(date -Is)] $*" | tee -a "/var/log/${LOG_TAG}.log"; }
skip() { log "skip: $1 (已完成)"; }
done_mark() { mkdir -p "$STATE_DIR"; touch "$STATE_DIR/$1"; }
is_done()   { [ -f "$STATE_DIR/$1" ]; }

# ── 1/7 软件源 ──────────────────────────────────────────
step_apt() {
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
}

# ── 2/7 存储栈(全部用上游官方包,不自研)─────────────────
# 参考 NAS逆向结论:存储栈一律集成,一个补丁都不打。ZFS 用 OpenZFS 官方源。
step_storage() {
  log "== 2/7 安装存储栈 =="
  # 注意:lsblk 在 util-linux 里,不是独立 apt 包(VM 实测写成包名会
  # E: Unable to locate package → firstboot 卡死在 2/7 反复重试)
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    zfsutils-linux zfs-dkms \
    samba samba-common-bin smbclient \
    nfs-kernel-server acl \
    nut-client nut-server \
    smartmontools mdadm lvm2 btrfs-progs \
    parted util-linux

  # ZFS 开机自动导入池 + 挂载
  systemctl enable --now zfs-import-cache || true
  systemctl enable zfs-mount zfs-import.target || true
  done_mark storage
}

# ── 3/7 Docker ──────────────────────────────────────────
step_docker() {
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
}

# ── 4/7 Node(前端构建 + Electron)──────────────────────
step_node() {
  log "== 4/7 安装 Node 20 =="
  if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  fi
  done_mark node
}

# ── 5/7 Echo OS 源码 ───────────────────────────────────
step_echo_src() {
  log "== 5/7 拉取 Echo OS 源码 =="
  # safe.directory 必须落 /etc/gitconfig(系统级): firstboot.service 由 systemd
  # 拉起,环境无 HOME → ~/.gitconfig 不会被读;而 /opt/echo-os 可能由其他用户
  # (octopus)创建,root 跑 git 会报 dubious ownership 直接 fatal(VM 开机实测)。
  # 系统级配置任何用户/任何环境都读,幂等不重复追加。
  git config --system --get-all safe.directory 2>/dev/null | grep -qxF "$OS_DIR" \
    || git config --system --add safe.directory "$OS_DIR" 2>/dev/null \
    || git config --global --add safe.directory "$OS_DIR" 2>/dev/null || true
  mkdir -p "$OS_DIR"
  # 开机自跑(firstboot.service)时 DHCP/DNS 可能晚于 network-online.target
  # 就绪,git 网络操作报 "Could not resolve host"(VM 开机实测)。对 git 网络
  # 操作做退避重试(6 次 x10s);目标分支 fetch 不重试 —— 分支缺失是确定性
  # 失败,本来就走 os-main 回退,重试逻辑加在回退与 clone 上。
  git_retry() {
    local n
    for n in 1 2 3 4 5 6; do
      if "$@"; then return 0; fi
      log "  git 网络操作失败(第 $n/6 次,10s 后重试)"
      sleep 10
    done
    return 1
  }
  # 目标分支不存在于远程时(如 p3-provision 尚未 push),自动回退到 os-main
  # 上游基线,保证首启不 brick;仅缺失 A 路线 NAS 特性,运维可据 WARN 修复。
  clone_branch() {
    local br="$1"
    git_retry git clone --depth 1 --branch "$br" "$OS_REPO" "$OS_DIR" 2>/dev/null
  }
  if [ ! -d "$OS_DIR/.git" ]; then
    # late_command 会先投放 setup-base.sh 引导文件,目录因此非空,
    # 直接 clone 会报 "already exists"(VM 实测)。备份后重克隆。
    if [ -n "$(ls -A "$OS_DIR" 2>/dev/null)" ]; then
      mv "$OS_DIR" "$OS_DIR.pre-clone.$$"
      mkdir -p "$OS_DIR"
    fi
    if clone_branch "$OS_BRANCH"; then
      log "  已克隆目标分支 $OS_BRANCH"
    else
      log "⚠ 分支 '$OS_BRANCH' 在 $OS_REPO 不存在,回退克隆 os-main(上游基线)"
      log "  ⚠ 回退后不含 p3-provision 的 A 路线 NAS 特性(appliance/nas 路由、deploy/provision 装机路线)。"
      log "  ⚠ 修复:把 p3-provision push 到远程,或设 ECHO_OS_REPO/ECHO_OS_BRANCH 指向已发布分支后重跑本步。"
      clone_branch os-main || { log "✗ os-main 回退克隆也失败,仓库不可达"; exit 1; }
    fi
  else
    # 已有仓库时对齐 bundle/远程快照。注意:fetch 只更新 origin/$BRANCH,
    # checkout 不会让本地分支快进(bundle 场景实测 HEAD 停留在旧提交),
    # 必须 reset --hard 对齐;工作区脏文件会被覆盖,本脚本即被覆盖源。
    if ! git -C "$OS_DIR" fetch --depth 1 origin "$OS_BRANCH" 2>/dev/null \
       || ! git -C "$OS_DIR" reset --hard FETCH_HEAD 2>/dev/null; then
      log "⚠ 分支 '$OS_BRANCH' 对齐失败,回退到 os-main"
      git_retry git -C "$OS_DIR" fetch --depth 1 origin os-main \
        && git -C "$OS_DIR" reset --hard FETCH_HEAD \
        || { log "✗ os-main 回退对齐也失败"; exit 1; }
    fi
  fi
  # A 路线 overlay:安装介质/首次开机阶段若投放了 overlay 包(由 build-iso.sh
  # 生成、preseed late_command 落到 $ECHO_OVERLAY),解压覆盖到克隆树之上,
  # 等价于直接 clone p3-provision —— 无需把分支 push 到远程即可拿到完整 NAS 产品。
  # 典型内容:appliance/nas/ 路由、deploy/provision/ 装机路线、品牌重命名等。
  local OVERLAY="${ECHO_OVERLAY:-/opt/echo-os-overlay.tar.gz}"
  if [ -f "$OVERLAY" ]; then
    log "  应用 A 路线 overlay: $OVERLAY"
    tar xzf "$OVERLAY" -C "$OS_DIR"
    rm -f "$OVERLAY"
    log "  overlay 已应用(含 appliance/nas 路由 + deploy/provision 装机路线)"
  fi
  done_mark echo-src
  # clone/reset 后仓库版本(0644)会覆盖 late_command 投放的引导脚本,
  # 而 firstboot.service 的 ExecStart 直接执行它 —— 无执行位会 203/EXEC
  # (VM 实测:重启后服务起不来)。每次重拉后强制恢复执行位。
  # 注意:overlay 可能已覆盖此文件,这里确保执行位恢复。
  chmod +x "$OS_DIR/deploy/provision/base/setup-base.sh" 2>/dev/null || true
}

# ── 5b/7 Python 依赖 ───────────────────────────────────
step_echo_py() {
  log "== 5b/7 安装 Python 依赖 =="
  # 母体 echo-agent 是私有仓库,Docker/设备构建走本地 wheel;
  # 见 deploy/appliance/prepare-agent-wheel.sh。
  # 无凭据环境(如验证 VM)可用 ECHO_SKIP_AGENT=1 跳过私有 agent,
  # 仅装最小集;真实部署请配 GitHub token 或本地 wheel。
  if [ "${ECHO_SKIP_AGENT:-0}" = "1" ]; then
    log "  ECHO_SKIP_AGENT=1,跳过私有 agent,仅装 minimal"
    python3 -m venv "$OS_DIR/.venv"
    "$OS_DIR/.venv/bin/pip" install --upgrade pip
    "$OS_DIR/.venv/bin/pip" install -e "$OS_DIR[minimal]"
    # minimal 不依赖 packaging,但 echo-agent serve 运行期需要
    # (runtime/platform/plugins/marketplace_package.py: from packaging.specifiers import ...)
    # 缺失会致服务启动即崩;补齐以保证无凭据环境也能起后端。
    "$OS_DIR/.venv/bin/pip" install packaging
  elif command -v uv >/dev/null 2>&1; then
    (cd "$OS_DIR" && uv sync --extra serve --extra web --extra appliance --extra dev)
    # 与 minimal 分支同因: runtime/platform/plugins/marketplace_package.py
    # 顶层 import packaging.specifiers,pyproject 的 extras 却未声明 packaging
    # (fresh 装机无 firstboot.env、走完整分支时实测服务启动即崩)。
    "$OS_DIR/.venv/bin/pip" install packaging
  else
    python3 -m venv "$OS_DIR/.venv"
    "$OS_DIR/.venv/bin/pip" install --upgrade pip
    "$OS_DIR/.venv/bin/pip" install -e "$OS_DIR[serve,web,appliance]"
    # 同上: 完整分支同样需要 packaging(fresh 装机无 firstboot.env 实测:
    # echo-agent serve → ModuleNotFoundError: packaging.specifiers → 循环重启)
    "$OS_DIR/.venv/bin/pip" install packaging
  fi
  done_mark echo-py
}

# ── 5c/7 前端构建 ──────────────────────────────────────
step_echo_web() {
  log "== 5c/7 构建前端 =="
  # 锁文件是 pnpm-lock.yaml,必须用 pnpm 装(VM 实测:fallback 的 npm ci
  # 因无 package-lock.json 报 EUSAGE)。4/7 只装了 Node,这里补装 pnpm。
  if ! command -v pnpm >/dev/null 2>&1; then
    npm install -g pnpm@10
  fi
  # 首启由 firstboot.service 经 nohup 后台拉起 —— 进程无控制终端(TTY)。
  # 此时 pnpm install 在 lockfile/已装依赖不一致、需清理 node_modules 时会
  # 弹 "Remove the existing node_modules directory?" 确认,pnpm 检测无 TTY
  # 直接 abort(ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY),整段 firstboot
  # 在 5c/7 卡死(VM 实测)。CI=true 让 pnpm 走非交互,跳过确认直接清理继续。
  export CI=true
  # 低内存设备加固(VM 2GB RAM 实测): 主应用 vite build(1798+ 模块,
  # shiki/mermaid/three/codemirror)真实需求 >1.5GB V8 堆,而 node 默认旧生代
  # 上限按物理内存自动算,2GB 机器仅 ~1GB → OOM abort(exit 134)。两步保底:
  #   1) 确保 swap >= 2GB:不足则补 swapfile 并写入 fstab(重启后仍生效)。
  #      2GB 机器实测自带 1.1GB swap,构建时已被内核用到 391MB。
  #   2) V8 堆上限显式放到 2560MB,超出物理内存的部分由 swap 兜底(构建变慢
  #      但能完成)。只影响本步构建进程,不动系统其他部分。
  ensure_swap() {
    local need_mb=2048 have_mb add_mb sf=/var/lib/echo-os/swapfile
    have_mb=$(awk '/SwapTotal/{printf "%d", $2/1024}' /proc/meminfo)
    if [ "${have_mb:-0}" -lt "$need_mb" ]; then
      add_mb=$((need_mb - have_mb))
      if [ ! -f "$sf" ]; then
        mkdir -p "$(dirname "$sf")"
        dd if=/dev/zero of="$sf" bs=1M count="$add_mb" status=none
        chmod 600 "$sf"
        /usr/sbin/mkswap -q "$sf"
      fi
      /usr/sbin/swapon "$sf" 2>/dev/null || true
      grep -qs "^$sf " /etc/fstab || echo "$sf none swap sw 0 0" >> /etc/fstab
      log "  低内存设备: 已补充 swap ${add_mb}MB"
    fi
  }
  ensure_swap
  export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--max-old-space-size=2560"
  (cd "$OS_DIR/frontend" && pnpm install --frozen-lockfile && pnpm build)
  [ -f "$OS_DIR/frontend/dist/index.html" ] || { log "✗ 前端构建失败"; exit 1; }
  done_mark echo-web
}

# ── 6/7 桌面 shell(上游收敛)───────────────────────────
# 收敛策略:桌面壳优先用上游 KWin 通用会话(deploy/desktop-session/),
# 无 GPU / 轻量 NAS / 显式 ECHO_DESKTOP=cage 时回退 cage 极简 kiosk。
#   ECHO_DESKTOP=kwin  上游 KWin 会话(需 Xorg seat + echo.os.ci-session 凭据)
#   ECHO_DESKTOP=cage  默认,NAS 友好,不引整套 KDE/打印/扫描栈
  # ── 桌面壳跑在哪个系统账号下:探测,不写死 ──────────────
  # 历史教训: rebrand 曾把 unit 的 User=octopus 一并改成 echo,但两代装机
  # 介质建的用户不一致(vmtest preseed 建 echo; 更早的裸机/老 ISO 建 octopus),
  # 写死任一名字在另一种装机上就是 217/USER crash-loop。以机器上真实存在的
  # 账号为准:显式 ECHO_USER 优先,否则按常见名探测(echo=现行产品,
  # octopus=历史装机,admin=保守兜底)。
  probe_run_user() {
    local u
    [ -n "${ECHO_USER:-}" ] && { echo "$ECHO_USER"; return; }
    for u in echo octopus admin; do
      if id -u "$u" >/dev/null 2>&1; then echo "$u"; return; fi
    done
    echo echo
  }
  step_shell() {
  DESKTOP_MODE="${ECHO_DESKTOP:-cage}"
  RUN_USER="$(probe_run_user)"
  if [ "$DESKTOP_MODE" = "kwin" ] && [ -x "$OS_DIR/deploy/desktop-session/setup-desktop-session.sh" ]; then
    log "== 6/7 安装 KWin 通用桌面会话(上游) =="
    # 复用既有系统账号($RUN_USER),避免上游脚本默认新建 echo 用户;
    # 上游脚本读 ECHO_USER / ECHO_OS_DIR 两个 env。
    ECHO_USER="$RUN_USER" ECHO_OS_DIR="$OS_DIR" "$OS_DIR/deploy/desktop-session/setup-desktop-session.sh"
  elif [ "${ECHO_HDMI_SHELL:-auto}" = "off" ]; then
    log "== 6/7 跳过原生 shell (ECHO_HDMI_SHELL=off) =="
  elif ls /dev/dri/card* >/dev/null 2>&1 || [ "${ECHO_HDMI_SHELL:-auto}" = "on" ] || [ "$DESKTOP_MODE" = "cage" ]; then
    log "== 6/7 安装 cage 原生 shell(回退) =="
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      cage plymouth plymouth-themes seatd rsync
    # Electron 运行时依赖:精简 Debian 默认没有,不装桌面起不来
    # (VM 实测 echo-shell 循环重启,status=127,ldd 缺 libnss3/libasound2)
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      libnss3 libasound2 libgbm1 libgtk-3-0 libxss1 libxtst6 libcups2 \
      libxrandr2 libatk-bridge2.0-0 libdrm2
    systemctl enable seatd
    install -m644 "$OS_DIR/deploy/native-shell/echo-shell.service" \
      /etc/systemd/system/echo-shell.service
    # unit 里的 User= 是占位,以探测到的真实账号渲染(见顶部 probe_run_user);
    # 写死任一名字(echo 或 octopus)在另一种装机上都会 217/USER crash-loop。
    sed -i "s/^User=.*/User=${RUN_USER}/" /etc/systemd/system/echo-shell.service
    chmod +x "$OS_DIR/deploy/native-shell/echo-shell-launch.sh"
    systemctl daemon-reload
    systemctl set-default graphical.target
    systemctl enable echo-shell.service
    plymouth-set-default-theme -R spinner 2>/dev/null || true
  else
    log "== 6/7 未检测到 GPU,跳过原生 shell(纯无头模式)=="
  fi
  done_mark shell
}

# ── 6b/7 备份/恢复(上游收敛,provision 原缺)────────────────
# 复用上游 deploy/backup + deploy/recovery,不自研。两类服务都带条件:
# 缺挂载点/凭据时自动跳过,systemctl enable 不会失败。
step_backup_recovery() {
  log "== 6b/7 安装备份/恢复模块(上游) =="
  install -m755 "$OS_DIR/deploy/backup/echo-user-backup" /usr/bin/echo-os-backup
  install -m644 "$OS_DIR/deploy/backup/echo-user-backup.service" \
    /etc/systemd/system/echo-user-backup.service
  install -m644 "$OS_DIR/deploy/backup/echo-restore-transaction-health.service" \
    /etc/systemd/system/echo-restore-transaction-health.service
  install -m755 "$OS_DIR/deploy/recovery/echo-recovery" /usr/bin/echo-recovery
  install -m644 "$OS_DIR/deploy/recovery/echo-recovery.service" \
    /etc/systemd/system/echo-recovery.service
  install -d -m0755 /usr/lib/echo-os/recovery-repart.d
  install -m644 "$OS_DIR/deploy/recovery/repart.d/"*.conf \
    /usr/lib/echo-os/recovery-repart.d/
  systemctl daemon-reload
  # recovery 默认只读诊断,挂 multi-user.target,安全启用
  systemctl enable echo-recovery.service
  # backup 需 /mnt/echo-backup 挂载点 + 加密凭据,缺则 Condition 跳过,仍 enable 待命
  systemctl enable echo-user-backup.service 2>/dev/null || true
  systemctl enable echo-restore-transaction-health.service 2>/dev/null || true
  done_mark backup-recovery
}

# ── 7/7 服务:appliance 后端 + nginx 反代 ───────────────
step_services() {
  log "== 7/7 安装服务单元 =="

  # 数据根目录:统一命名空间挂在这里(参考 NAS用 /fs,我们用 /data)
  mkdir -p /data/nas /data/apps

  # ── 配置:首启从模板生成 /etc/echo-os/config.yaml ───────────────
  # echo-appliance.service 的 --config 指向它;缺失则后端起不来。
  # 仅缺失时生成,绝不覆盖运维后期的手动改动。
  mkdir -p /etc/echo-os
  if [ ! -f /etc/echo-os/config.yaml ]; then
    if [ -f "$OS_DIR/config.example.yaml" ]; then
      cp "$OS_DIR/config.example.yaml" /etc/echo-os/config.yaml
      log "  已生成 /etc/echo-os/config.yaml(模板副本,按需改)"
    else
      # 极小兜底配置:保证 echo-agent serve 能起(无 LLM key 时走 stub)
      cat >/etc/echo-os/config.yaml <<'YAML'
appliance:
  data_dir: /data
  nas_root: /data/nas
YAML
      log "  已生成 /etc/echo-os/config.yaml(极小兜底)"
    fi
  fi

  # ── 清理上游/历史遗留 octopus 标识 ───────────────────────────
  # 重命名前装机的残留会与 echo 冲突:nginx 双 default_server 让 nginx -t 失败;
  # 8000 端口被 octopus-appliance 占用会让 echo-appliance 起不来。必须清掉。
  rm -f /etc/nginx/sites-enabled/octopus /etc/nginx/sites-enabled/default
  for u in $(systemctl list-unit-files 2>/dev/null | awk '{print $1}' | grep -E '^octopus-[a-z-]*\.service$'); do
    log "  停用遗留 $u"
    systemctl disable --now "$u" 2>/dev/null || true
  done

  install -m644 "$OS_DIR/deploy/provision/base/echo-appliance.service" \
    /etc/systemd/system/echo-appliance.service
  install -m644 "$OS_DIR/deploy/appliance/systemd/echo-ups-shutdown-guard.service" \
    /etc/systemd/system/echo-ups-shutdown-guard.service
  install -m644 "$OS_DIR/deploy/appliance/systemd/echo-ups-shutdown-guard.timer" \
    /etc/systemd/system/echo-ups-shutdown-guard.timer
  install -m644 "$OS_DIR/deploy/appliance/systemd/echo-smart-self-test.service" \
    /etc/systemd/system/echo-smart-self-test.service
  install -m644 "$OS_DIR/deploy/appliance/systemd/echo-smart-self-test.timer" \
    /etc/systemd/system/echo-smart-self-test.timer
  install -m644 "$OS_DIR/deploy/appliance/systemd/echo-mdraid-check.service" \
    /etc/systemd/system/echo-mdraid-check.service
  install -m644 "$OS_DIR/deploy/appliance/systemd/echo-mdraid-check.timer" \
    /etc/systemd/system/echo-mdraid-check.timer

  # ── 首启引导服务自举 ─────────────────────────────────────────
  # echo-firstboot.service 负责"开机自动续跑 firstboot"(幂等,marks 齐 +
  # ConditionPathExists 后自动跳过)。ISO 路线由 preseed late_command 安装,
  # 但裸机/非 ISO 路线(直接跑 setup-base.sh)没有任何人装它 → 装机中断后
  # 只能手工拉起,开机自愈链断裂(VM 实测 not-found)。这里统一兜底装上。
  if [ -f "$OS_DIR/deploy/provision/echo-firstboot.service" ]; then
    install -m644 "$OS_DIR/deploy/provision/echo-firstboot.service" \
      /etc/systemd/system/echo-firstboot.service
    systemctl enable echo-firstboot.service 2>/dev/null || true
  fi

  # nginx 反代:对外只暴露 80/443,后端 appliance 只听 127.0.0.1:8000。
  # 与参考 NAS同一手法 —— 单 nginx 入口,功能模块各自 unix socket / 本地端口。
  install -m644 "$OS_DIR/deploy/provision/base/echo-nginx.conf" \
    /etc/nginx/sites-available/echo
  ln -sf /etc/nginx/sites-available/echo /etc/nginx/sites-enabled/echo
  rm -f /etc/nginx/sites-enabled/default

  # 随机生成 TLS 自签证书(每设备独立)。
  # 参考 NAS的一个隐患:nginx conf 里随包带了 server.crt/key,若全系共用则等于
  # TLS 私钥公开。这里强制每设备首启重新生成。
  if [ ! -f /etc/ssl/private/echo-selfsigned.key ]; then
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
      -subj "/CN=$(hostname)" \
      -keyout /etc/ssl/private/echo-selfsigned.key \
      -out /etc/ssl/certs/echo-selfsigned.crt 2>/dev/null || log "警告:自签证书生成失败"
  fi

  systemctl daemon-reload
  systemctl enable --now echo-ups-shutdown-guard.timer
  systemctl enable --now echo-smart-self-test.timer
  systemctl enable --now echo-mdraid-check.timer
  systemctl enable --now echo-appliance.service
  # nginx 可能已在跑(apt 安装时自启),`enable --now` 不会重载已运行进程
  # 的配置 → 80 端口仍服务旧 default 站点(VM 实测)。必须 restart。
  nginx -t && systemctl enable nginx && systemctl restart nginx
  done_mark services
}
