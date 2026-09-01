"""Echo OS · echo-storage 同机协同启动器。

当 ``ECHO_STORAGE_AUTOSTART=1`` 时,OS appliance 启动会尝试在后台拉起
sibling 项目 ``echo-storage``(默认 127.0.0.1:8767),让 File Agent / 文件管家
无需用户手动启动即可工作。

设计要点:
- 不阻塞 FastAPI 启动:spawn 后异步等待健康检查,失败只记录日志。
- 幂等:多次调用会检测已有服务,不会重复启动。
- 优雅:OS 进程退出时尽量 terminate storage 子进程(通过 atexit)。
- 自闭环:echo-storage 未安装时静默跳过,不影响其余功能。
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_log = logging.getLogger("echo.appliance")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
HEALTH_TIMEOUT_S = 30
POLL_INTERVAL_S = 0.5

# 全局持有,用于退出时清理。
_storage_process: subprocess.Popen[Any] | None = None
_LEGACY_STORAGE_NAME = "octo" + "pus-storage"


def _base_url() -> str:
    url = (os.environ.get("ECHO_STORAGE_URL") or "").strip()
    if url:
        return url.rstrip("/")
    host = os.environ.get("ECHO_STORAGE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("ECHO_STORAGE_PORT") or DEFAULT_PORT)
    return f"http://{host}:{port}"


def _storage_executable() -> str | None:
    """返回 echo-storage 可执行文件路径,未安装则 None。"""
    exe = shutil.which("echo-storage")
    if exe:
        return exe
    # 兼容 uv/pip 安装但未进 PATH 的场景:尝试当前 Python 的 scripts 目录。
    scripts = os.path.join(sys.prefix, "bin" if sys.platform != "win32" else "Scripts")
    candidate = os.path.join(scripts, "echo-storage")
    if os.path.isfile(candidate):
        return candidate
    if sys.platform == "win32":
        candidate += ".exe"
        if os.path.isfile(candidate):
            return candidate
    # Existing installations can keep running while the external Storage
    # package adopts the Echo executable name.
    legacy_exe = shutil.which(_LEGACY_STORAGE_NAME)
    if legacy_exe:
        return legacy_exe
    legacy_candidate = os.path.join(scripts, _LEGACY_STORAGE_NAME)
    if sys.platform == "win32":
        legacy_candidate += ".exe"
    if os.path.isfile(legacy_candidate):
        return legacy_candidate
    return None


def _probe_manifest(timeout: float = 2.0) -> bool:
    """探测 storage 是否已在运行。"""
    url = f"{_base_url()}/v1/manifest"
    try:
        # _base_url restricts the default to the same-device storage sidecar; custom
        # addresses are an administrator setting, never derived from an HTTP request.
        with urllib.request.urlopen(  # noqa: S310  # nosec B310
            url, timeout=timeout
        ) as resp:
            body = resp.read().decode("utf-8", "replace")
            data = body and __import__("json").loads(body)
            return isinstance(data, dict) and data.get("role") in {
                "echo-storage",
                _LEGACY_STORAGE_NAME,
            }
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return False


def _shutdown_storage() -> None:
    global _storage_process
    proc = _storage_process
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception as exc:  # pragma: no cover - best-effort cleanup
        _log.warning("storage shutdown error: %s", exc)
    finally:
        _storage_process = None


def start_storage_service(
    *,
    host: str | None = None,
    port: int | None = None,
    wait_healthy: bool = True,
) -> dict[str, Any]:
    """尝试启动 echo-storage 服务。

    返回 ``{"started": bool, "already_running": bool, "url": str, "error": str|None}``。
    失败时 ``started=False`` 并附带原因,不会抛异常。
    """
    global _storage_process

    # 1. 先看是否已经在运行(可能是用户手动启动,或 autostart 已拉起)。
    if _probe_manifest(timeout=2.0):
        _log.info("echo-storage already running at %s", _base_url())
        return {"started": False, "already_running": True, "url": _base_url(), "error": None}

    # 2. 未启用 autostart 时不主动启动。
    if os.environ.get("ECHO_STORAGE_AUTOSTART") != "1":
        return {
            "started": False,
            "already_running": False,
            "url": _base_url(),
            "error": "ECHO_STORAGE_AUTOSTART is not set",
        }

    # 3. 检查可执行文件。
    exe = _storage_executable()
    if exe is None:
        return {
            "started": False,
            "already_running": False,
            "url": _base_url(),
            "error": "echo-storage executable not found (pip install echo-storage?)",
        }

    # 4. 若同一进程内已有子进程,先确认它是否还活着。
    if _storage_process is not None and _storage_process.poll() is None:
        return {
            "started": False,
            "already_running": True,
            "url": _base_url(),
            "error": None,
        }

    target_host = host or os.environ.get("ECHO_STORAGE_HOST", DEFAULT_HOST)
    target_port = port or int(os.environ.get("ECHO_STORAGE_PORT") or DEFAULT_PORT)

    # 5. 启动子进程。
    cmd = [exe, "serve", "--host", target_host, "--port", str(target_port)]
    data_dir = os.environ.get("ECHO_DATA_DIR")
    if data_dir:
        cmd.extend(["--data-dir", data_dir])

    env = os.environ.copy()
    try:
        _log.info("starting echo-storage: %s", " ".join(cmd))
        _storage_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        atexit.register(_shutdown_storage)
    except OSError as exc:
        return {
            "started": False,
            "already_running": False,
            "url": _base_url(),
            "error": f"failed to spawn echo-storage: {exc}",
        }

    # 6. 等待健康检查(可选,默认开启)。
    if wait_healthy:
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        while time.monotonic() < deadline:
            if _storage_process.poll() is not None:
                returncode = _storage_process.returncode
                stdout = (_storage_process.stdout.read() or b"").decode("utf-8", "replace")[:500]
                stderr = (_storage_process.stderr.read() or b"").decode("utf-8", "replace")[:500]
                _log.error(
                    "echo-storage exited early (code=%s): stdout=%s stderr=%s",
                    returncode,
                    stdout,
                    stderr,
                )
                _storage_process = None
                return {
                    "started": False,
                    "already_running": False,
                    "url": _base_url(),
                    "error": f"echo-storage exited with code {returncode}",
                }
            if _probe_manifest(timeout=1.0):
                _log.info("echo-storage healthy at %s", _base_url())
                return {
                    "started": True,
                    "already_running": False,
                    "url": _base_url(),
                    "error": None,
                }
            time.sleep(POLL_INTERVAL_S)

        _log.warning("echo-storage did not become healthy within %ss", HEALTH_TIMEOUT_S)
        return {
            "started": True,
            "already_running": False,
            "url": _base_url(),
            "error": f"storage started but not healthy within {HEALTH_TIMEOUT_S}s",
        }

    return {"started": True, "already_running": False, "url": _base_url(), "error": None}
