#!/usr/bin/env bash
# 把一台刚装好的 Debian 13 (trixie) 变成 Echo OS。
#
# 由 echo-firstboot.service 在**首次开机、网络就绪后**执行 —— 刻意不在
# debian-installer 里跑:ZFS 编译 / Docker 安装 / 前端构建都要联网且耗时,
# 放在装机阶段失败会让整台机器装不起来;放首次开机则可重试、可查日志。
#
# 幂等:每个阶段有哨兵文件,重跑会跳过已完成的部分。
# 日志:journalctl -u echo-firstboot
#
# 步骤实现在 provision-lib.sh(被本脚本 source),本文件只做编排 + 变量声明。
set -euo pipefail

OS_DIR=/opt/echo-os
STATE_DIR=/var/lib/echo-os/firstboot
LOG_TAG="echo-firstboot"

# 可选覆盖文件:管理员/测试可在此改仓库源与镜像源(如指向宿主机 bundle/
# git daemon,或国内 git 镜像),不必改本脚本。必须在默认值赋值**之前** source,
# 否则 ${VAR:-default} 已定死,覆盖不生效(VM 实测踩坑)
[ -r /etc/echo-os/firstboot.env ] && . /etc/echo-os/firstboot.env

# 由 build-iso.sh 或环境变量注入
OS_REPO="${ECHO_OS_REPO:-https://github.com/dengdenghua/octopus-os.git}"
OS_BRANCH="${ECHO_OS_BRANCH:-p3-provision}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-https://deb.debian.org/debian}"

# 引入 A 路线步骤库(桌面/备份恢复等上游收敛步骤亦在其中)
# shellcheck source=provision-lib.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 自愈: 装机介质的 late_command 名单若漏投 provision-lib.sh(常见漏项 ——
# 它随 deploy/provision/base/ 一起在 overlay 里,却不在引导文件清单中),
# 首次开机 source 会直接失败、整台机器装不起来。overlay 包本身是
# late_command 必投项,这里从包里单独取回本文件,兼容已流出的装机介质。
if [ ! -r "$SCRIPT_DIR/provision-lib.sh" ] && [ -f /opt/echo-os-overlay.tar.gz ]; then
  tar xzf /opt/echo-os-overlay.tar.gz -C /opt/echo-os \
    deploy/provision/base/provision-lib.sh 2>/dev/null || true
fi
source "$SCRIPT_DIR/provision-lib.sh"

[ "$(id -u)" -eq 0 ] || { echo "请用 root 运行" >&2; exit 1; }

mkdir -p "$STATE_DIR"

system_deb_status=0
prepare_system_deb_repo || system_deb_status=$?
[ "$system_deb_status" -ne 2 ] || exit 1

# ── 编排:每阶段带哨兵,幂等可重入 ─────────────────────
if ! is_done apt;             then step_apt;             else skip apt; fi
if ! is_done storage;         then step_storage;         else skip storage; fi
if ! is_done docker;          then step_docker;          else skip docker; fi
if ! is_done node;            then step_node;            else skip node; fi
if ! is_done echo-src;        then step_echo_src;        else skip echo-src; fi
if ! is_done echo-py;         then step_echo_py;         else skip echo-py; fi
if ! is_done echo-web;        then step_echo_web;        else skip echo-web; fi
if ! is_done shell;           then step_shell;           else skip shell; fi
if ! is_done backup-recovery; then step_backup_recovery; else skip backup-recovery; fi
if ! is_done services;        then step_services;        else skip services; fi

log "✓ Echo OS 基础系统就绪"
log "  后端:systemctl status echo-appliance"
log "  Web :http://$(hostname).local  (或本机 IP)"
if systemctl is-enabled echo-shell.service >/dev/null 2>&1; then
  log "  HDMI:已启用原生 shell,reboot 后开机进桌面"
fi
