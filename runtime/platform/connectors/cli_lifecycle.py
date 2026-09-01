"""CLI 连接器生命周期 —— 对齐 WorkBuddy ``cli.json`` 协议中我们之前漏掉的部分。

WorkBuddy 的 CLI 连接器不只是「跑一条 auth 命令」,完整协议还包括:
  - ``init``         安装命令(装 CLI 工具、配置 base_url),在 install 时执行
  - ``runtime``      运行时要求(node 等),安装前检查
  - ``versionCheck`` 版本检查(command + minVersion + versionPattern),不满足则建议升级
  - ``authUrlDomain`` 授权域名白名单,打开浏览器前必须校验(防恶意跳转)
  - ``authDeviceFlow`` 设备流登录:后台跑 auth 命令 → 解析 verification_uri /
    user_code → 自动打开浏览器;前端轮询 status 确认登录完成。

所有网络/子进程操作都带超时,失败只降级返回,绝不阻断。
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

_LOGGER = __import__("logging").getLogger(__name__)


def platform_key() -> str:
    sys = platform.system().lower()
    if sys == "darwin":
        return "darwin"
    if sys == "windows":
        return "win32"
    return "linux"


def pick_platform(cmd_map: Any) -> str | None:
    if not isinstance(cmd_map, dict):
        return None
    return cmd_map.get(platform_key()) or cmd_map.get("darwin") or cmd_map.get("linux")


def resolve_cmd(cmd_spec: Any) -> str | None:
    """命令字段可能是分平台 dict 或单条字符串,统一解析。"""
    if isinstance(cmd_spec, dict):
        return pick_platform(cmd_spec)
    return cmd_spec if cmd_spec else None


def detect_command(conn: Any) -> dict[str, Any]:
    """Resolve a declared CLI only when an install/status action asks us to.

    Connector catalog listing remains metadata-only.  This is intentionally
    separate from registry discovery so opening the plugin market never scans
    PATH or a provider-owned home directory.
    """
    raw = (conn.cli or {}).get("detect") or {}
    commands = raw.get("commands") if isinstance(raw, dict) else raw
    if isinstance(commands, dict):
        commands = commands.get(platform_key()) or commands.get("darwin") or commands.get("linux")
    if isinstance(commands, str):
        commands = [commands]
    if not isinstance(commands, list):
        commands = []
    for value in commands:
        command = str(value or "").strip()
        if not command:
            continue
        expanded = Path(command).expanduser()
        if expanded != Path(command) or "/" in command or "\\" in command:
            try:
                if expanded.is_file() and os.access(expanded, os.X_OK):
                    return {
                        "found": True,
                        "command": command,
                        "executable": str(expanded.resolve()),
                    }
            except OSError:
                continue
        executable = shutil.which(command)
        if executable:
            return {"found": True, "command": command, "executable": executable}
    return {"found": False, "command": "", "executable": ""}


def _version_tuple(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts[:4]) or (0,)


def version_ge(a: str, b: str) -> bool:
    """a >= b(数字段逐段比较,缺段补 0)。"""
    return _version_tuple(a) >= _version_tuple(b)


def check_runtime(conn: Any) -> dict[str, Any]:
    """校验 cli.json ``runtime`` 要求(node 版本等)。无要求 → ok。"""
    runtime = (conn.cli or {}).get("runtime") or {}
    rtype = str(runtime.get("type") or "").lower()
    if rtype == "node":
        try:
            r = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            node_v = (r.stdout or "").strip() or (r.stderr or "").strip()
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "runtime": "node",
                "node_version": "",
                "error": f"node 不可用: {exc}",
            }
        return {"ok": True, "runtime": "node", "node_version": node_v}
    return {"ok": True, "runtime": rtype or ""}


def check_version(conn: Any, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    """执行 ``versionCheck`` 命令并比较版本。无 versionCheck → ok。"""
    vc = (conn.cli or {}).get("versionCheck") or {}
    if not vc:
        return {"ok": True, "version": "", "min_version": ""}
    cmd = resolve_cmd(vc.get("command"))
    min_v = str(vc.get("minVersion") or "")
    pattern = str(vc.get("versionPattern") or "")
    if not cmd:
        return {"ok": True, "version": "", "min_version": min_v}
    try:
        argv = shlex.split(cmd)
        if argv:
            first = Path(argv[0]).expanduser()
            if first != Path(argv[0]) and first.is_file():
                argv[0] = str(first)
            elif shutil.which(argv[0]) is None:
                detected = detect_command(conn)
                if detected.get("found"):
                    argv[0] = str(detected["executable"])
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, **(env or {})},
        )
        output = (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "version": "", "min_version": min_v, "error": str(exc)}
    version = ""
    if pattern:
        m = re.search(pattern, output)
        if m:
            version = m.group(1) if m.groups() else m.group(0)
    ok = bool(version) and (not min_v or version_ge(version, min_v))
    return {
        "ok": ok,
        "version": version or "",
        "min_version": min_v,
        "output": output[:300],
        "error": None
        if ok
        else (f"版本过低:当前 {version or '未知'} < 需要 {min_v}" if version else "无法解析版本"),
    }


def run_init(conn: Any, *, env: dict[str, str] | None = None, timeout: int = 300) -> dict[str, Any]:
    """执行 ``init`` 安装命令。无 init → 直接 ok。失败只记录,不阻断。"""
    init_cmd = (conn.cli or {}).get("init")
    if not init_cmd:
        return {"ok": True, "skipped": True}
    before = detect_command(conn)
    if (conn.cli or {}).get("initIfMissing") and before.get("found"):
        return {"ok": True, "skipped": True, "reason": "already_installed", "detection": before}
    cmd = resolve_cmd(init_cmd)
    if not cmd:
        return {"ok": False, "error": "当前平台无 init 命令"}
    try:
        r = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )
        out = (r.stdout or "") + (r.stderr or "")
        after = detect_command(conn)
        return {
            "ok": r.returncode == 0,
            "exit_code": r.returncode,
            "output": out[:800],
            "error": None if r.returncode == 0 else f"init 退出码 {r.returncode}",
            "detection": after,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"init 超时(>{timeout}s),可在终端手动执行: {cmd[:120]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"init 执行失败: {exc}"}


def validate_auth_uri(uri: str, conn: Any) -> bool:
    """校验设备流授权 URI 的域名 == ``authUrlDomain``(或子域)。无配置 → 放行。"""
    allowed = (conn.cli or {}).get("authUrlDomain") or ""
    if not allowed:
        return True
    try:
        host = (urllib.parse.urlparse(uri).hostname or "").lower()
    except ValueError:
        return False
    allowed = allowed.lower()
    return host == allowed or host.endswith("." + allowed)


def extract_device_flow(output: str, spec: dict[str, Any]) -> dict[str, Any]:
    """从设备流输出中解析 authorization URI + user_code。返回 {"uri","code","found"}。"""
    uri_re = spec.get("uriPattern") or ""
    code_re = spec.get("codePattern") or ""
    uri, code = "", ""
    if uri_re:
        m = re.search(uri_re, output)
        if m:
            uri = m.group(1) if m.groups() else m.group(0)
    if code_re:
        m = re.search(code_re, output)
        if m:
            code = m.group(1) if m.groups() else m.group(0)
    return {"uri": uri, "code": code, "found": bool(uri or code)}


__all__ = [
    "check_runtime",
    "check_version",
    "detect_command",
    "extract_device_flow",
    "pick_platform",
    "platform_key",
    "resolve_cmd",
    "run_init",
    "validate_auth_uri",
    "version_ge",
]
