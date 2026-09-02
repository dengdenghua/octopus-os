#!/usr/bin/env bash
# echo-install 冒烟测试 —— 打桩 whiptail 与 debconf-set-selections,真跑装机流程。
#
# 为什么需要它:装机脚本平时只能在 d-i initrd 里跑,改一行就得烧 ISO 上 VM 验证,
# 反馈周期以十分钟计。这里把交互层打桩,几秒钟就能验证 TUI 逻辑与产物正确性,
# **任何环境都能跑**(包括开发机),不依赖 d-i、不需要 Linux。
#
# 用法:./selftest.sh       退出码 0 = 全过
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/echo-install"

PASS=0
FAIL=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head2() { printf '\n\033[1m%s\033[0m\n' "$1"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
mkdir -p "$BIN"

# ── 打桩 whiptail ─────────────────────────────────────────
# 按对话框类型返回预设答案;--passwordbox 用计数器支持"两次输入不一致"场景。
#
# 关键:答案必须写 **stderr**。真实 whiptail 把用户选择输出到 stderr(UI 走
# stdout),调用方靠 `3>&1 1>&2 2>&3` 把它接进命令替换。桩若写到 stdout 会被
# 重定向到终端,捕获到空串 —— 这个坑反过来验证了装机脚本那套重定向是对的。
cat >"$BIN/whiptail" <<'STUB'
#!/bin/sh
case "$*" in
  *--radiolist*)
    [ -n "${FAKE_CANCEL_RADIOLIST:-}" ] && exit 1
    echo "${FAKE_DISK:-sdb}" >&2
    ;;
  *--inputbox*)
    echo "${FAKE_HOST:-testnas}" >&2
    ;;
  *--passwordbox*)
    n=$(cat "$CNT_FILE" 2>/dev/null || echo 0)
    n=$((n + 1))
    echo "$n" > "$CNT_FILE"
    if [ "$n" -le 1 ]; then echo "${FAKE_PW1:-supersecret123}" >&2
    else echo "${FAKE_PW2:-supersecret123}" >&2; fi
    ;;
esac
exit 0
STUB

# ── 打桩 debconf-set-selections:把答案抄出来供断言 ────────
cat >"$BIN/debconf-set-selections" <<'STUB'
#!/bin/sh
cp "$1" "$CAPTURE_FILE"
STUB

chmod +x "$BIN"/*
export PATH="$BIN:$PATH"

# 跑一次装机脚本。env 变量通过参数传入。
run_installer() {
  local capture="$WORK/captured.$$.$RANDOM"
  rm -f "$capture"
  # 注意用 ${VAR-default} 而非 ${VAR:-default}:空串是无盘场景的合法注入,
  # :- 会把空串也回落成默认盘列表,导致无盘用例失效。
  # (注释必须放在赋值块外 —— 续行中间插注释会截断 env 前缀链。)
  CAPTURE_FILE="$capture" \
  CNT_FILE="$WORK/cnt.$RANDOM" \
  ECHO_TEST_DISKS="${TEST_DISKS-sda sdb nvme0n1}" \
  FAKE_DISK="${FAKE_DISK:-sdb}" \
  FAKE_HOST="${FAKE_HOST:-testnas}" \
  FAKE_PW1="${FAKE_PW1:-supersecret123}" \
  FAKE_PW2="${FAKE_PW2:-supersecret123}" \
  FAKE_CANCEL_RADIOLIST="${FAKE_CANCEL_RADIOLIST:-}" \
    timeout 20 bash "$INSTALLER" >"$WORK/stdout.log" 2>"$WORK/stderr.log"
  INSTALL_RC=$?
  CAPTURED="$capture"
}

assert_rc() {
  [ "$INSTALL_RC" -eq "$1" ] && ok "$2" || bad "$2 (期望 rc=$1,实际 $INSTALL_RC)"
}
assert_has() {
  grep -qF -- "$1" "$CAPTURED" 2>/dev/null \
    && ok "$2" || bad "$2 (期望包含: $1)"
}
assert_not_has() {
  grep -qF -- "$1" "$CAPTURED" 2>/dev/null \
    && bad "$2 (不应包含: $1)" || ok "$2"
}

echo "echo-install 冒烟测试"
echo "脚本: $INSTALLER"

# ── 1. 正常流程 ───────────────────────────────────────────
head2 "1. 正常流程(选 sdb / 主机名 testnas / 密码 16 位)"
run_installer
assert_rc 0 "退出码为 0"
assert_has "d-i partman-auto/disk string /dev/sdb" "系统盘写的是 /dev/sdb"
assert_not_has "/dev/sda" "没有被默认选中项覆盖成 sda"
assert_has "d-i netcfg/get_hostname string testnas" "主机名写入正确"
assert_has "d-i netcfg/get_domain string local" "域名写入正确"
assert_has "d-i passwd/username string echo" "管理员用户名正确"
assert_has "d-i passwd/user-password password supersecret123" "密码写入正确"
assert_has "d-i passwd/user-password-again password supersecret123" "密码确认项一致"
assert_has "d-i partman/confirm boolean true" "分区确认项为 true"

# ── 2. 密码两次不一致 → 应重试并以第二次为准 ─────────────
head2 "2. 密码两次不一致(重试后以第二次为准)"
FAKE_PW1="abc12345" FAKE_PW2="xyz78901" run_installer
assert_rc 0 "重试后仍能完成"
assert_has "d-i passwd/user-password password xyz78901" "最终采用第二次输入的密码"
assert_not_has "password abc12345" "未误用第一次的短密码"

# ── 3. 取消选盘 → 应中止 ──────────────────────────────────
head2 "3. 用户在选盘界面取消"
FAKE_CANCEL_RADIOLIST=1 run_installer
assert_rc 1 "取消后非 0 退出"
[ ! -s "$CAPTURED" ] && ok "未产出任何 debconf 预置" || bad "取消后不应写入预置"

# ── 4. 没有可用磁盘 → 应报错中止 ──────────────────────────
head2 "4. 没有可用磁盘"
TEST_DISKS="" run_installer
assert_rc 1 "无盘时非 0 退出"

# ── 5. 注入防护:设备名不应被拼进其他字段 ─────────────────
head2 "5. 产物结构检查"
run_installer
# 先确认产物存在 —— 否则 grep 打不开文件也返回非 0,会误判成通过。
if [ ! -f "$CAPTURED" ]; then
  bad "产物文件不存在(装机脚本未完成)"
else
  lines=$(wc -l < "$CAPTURED")
  # 14 行 = partman 7 + netcfg 2 + passwd 5;增删预置项必须同步改这里。
  [ "$lines" -eq 14 ] && ok "debconf 预置共 14 行(结构完整)" \
    || bad "debconf 预置行数异常: $lines"
  if grep -qv '^d-i ' "$CAPTURED"; then
    bad "存在非 d-i 开头的行"
  else
    ok "所有行均为 d-i 预置格式"
  fi
fi

echo
echo "────────────────────────"
printf '通过 %d,失败 %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || { echo "✗ 存在失败用例"; exit 1; }
echo "✓ 全部通过"
