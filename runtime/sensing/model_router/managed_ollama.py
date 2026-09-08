"""Pinned, private Ollama runtime owned by Echo; no system installer or shell scripts."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from runtime.platform.io.atomic import atomic_write_json
from runtime.platform.process.paths import app_paths

VERSION = "v0.33.3"
BASE = "http://127.0.0.1:11435"
GIB = 1024**3
MAX_UNPACKED = 16 * GIB
# Official GitHub release asset digests, reviewed 2026-09-07.
ASSETS = {
    ("Windows", "amd64"): (
        "ollama-windows-amd64.zip",
        1469175900,
        "52cb36a62e7e501f61514f60212dec7117b6c098811357585e02fffe32d2fcd7",
    ),
    ("Windows", "arm64"): (
        "ollama-windows-arm64.zip",
        210648637,
        "98b9ddaab6baece0418c6d1231526eb1e4e66944985e0a8eeb7d6171bcd7b6d8",
    ),
    ("Linux", "amd64"): (
        "ollama-linux-amd64.tar.zst",
        1433825108,
        "c13cea8f3389db4145f8a6cb88d1747242a48639d7c13e3bda7c1ebdc6eebb2f",
    ),
    ("Linux", "arm64"): (
        "ollama-linux-arm64.tar.zst",
        1554076220,
        "4425a112af999ae6572c1ce211fbabeaca7bab23ed5860972acdfc0cc2358420",
    ),
}
_process: subprocess.Popen | None = None
_process_lock = threading.Lock()
_lease = None
_lease_lock = threading.Lock()


def root() -> Path:
    return app_paths().data_dir / "local-ai"


def asset() -> tuple[str, int, str]:
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    result = ASSETS.get((platform.system(), arch))
    if result is None:
        raise ValueError("此平台暂不支持托管安装，请使用已安装的本机模型服务")
    if result[0].endswith(".zst") and not shutil.which("zstd"):
        raise ValueError("系统缺少 zstd 解压组件，请安装系统软件包 zstd 后重试")
    return result


def executable() -> Path:
    relative = "ollama.exe" if platform.system() == "Windows" else "bin/ollama"
    return root() / VERSION / relative


def enabled() -> bool:
    try:
        return json.loads((root() / "enabled.json").read_text("utf-8")) == {"version": VERSION}
    except (OSError, ValueError):
        return False


def available_disk() -> int:
    candidate = root().resolve()
    while not candidate.exists():
        candidate = candidate.parent
    return shutil.disk_usage(candidate).free


def claim_runtime() -> None:
    with _lease_lock:
        _claim_runtime()


def _claim_runtime() -> None:
    """Hold a nonblocking OS lease until shutdown (also released after a crash)."""
    global _lease
    if _lease is not None:
        return
    root().mkdir(parents=True, exist_ok=True)
    handle = (root() / "runtime.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise ValueError("其他 Echo 进程正在管理本地 AI") from exc
    _lease = handle


def _download(destination: Path, info: tuple[str, int, str], progress) -> None:
    name, size, expected = info
    url = f"https://github.com/ollama/ollama/releases/download/{VERSION}/{name}"
    digest = hashlib.sha256()
    received = 0
    deadline = time.monotonic() + 3600
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as connection:
        for _ in range(5):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname
                not in {
                    "github.com",
                    "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com",
                }
                or parsed.username
                or parsed.password
            ):
                raise ValueError("运行环境下载地址不在官方允许列表中")
            with connection.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        received += len(chunk)
                        if received > size or time.monotonic() > deadline:
                            raise ValueError("运行环境下载大小或时限超出预期")
                        digest.update(chunk)
                        output.write(chunk)
                        progress(received, size)
                break
        else:
            raise ValueError("运行环境下载重定向过多")
    if received != size or digest.hexdigest() != expected:
        raise ValueError("运行环境校验失败，未执行下载文件")


def _target(folder: Path, name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError("安装包包含不安全的路径")
    target = folder.joinpath(*path.parts)
    if not target.resolve().is_relative_to(folder.resolve()):
        raise ValueError("安装包路径超出目标目录")
    return target


def _extract(archive: Path, folder: Path) -> None:
    total = 0
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                target = _target(folder, entry.filename)
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("ZIP 安装包不得包含符号链接")
                total += entry.file_size
                if total > MAX_UNPACKED:
                    raise ValueError("安装包解压大小超出预留空间")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
        return
    # A fixed argv, never a downloaded shell script. Validate members ourselves.
    with subprocess.Popen(
        [shutil.which("zstd"), "-dc", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as decoder:
        links = []
        try:
            with tarfile.open(fileobj=decoder.stdout, mode="r|") as package:
                for entry in package:
                    target = _target(folder, entry.name)
                    total += entry.size
                    if total > MAX_UNPACKED:
                        raise ValueError("安装包解压大小超出预留空间")
                    if entry.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif entry.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with package.extractfile(entry) as source, target.open("xb") as output:
                            shutil.copyfileobj(source, output)
                        target.chmod(entry.mode & 0o755)
                    elif entry.issym():
                        # Create links only after all regular files are written.
                        if (
                            PurePosixPath(entry.linkname).is_absolute()
                            or "\\" in entry.linkname
                            or ":" in entry.linkname
                        ):
                            raise ValueError("安装包包含不安全的链接")
                        link = (target.parent / entry.linkname).resolve()
                        if not link.is_relative_to(folder.resolve()):
                            raise ValueError("安装包链接超出目标目录")
                        links.append((target, link))
                    else:
                        raise ValueError("安装包包含不支持的特殊文件")
            if decoder.wait(timeout=30) != 0:
                raise ValueError("运行环境解压失败")
            while links:
                remaining = []
                for target, link in links:
                    if not link.is_file():
                        remaining.append((target, link))
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.symlink_to(os.path.relpath(link, target.parent))
                if len(remaining) == len(links):
                    raise ValueError("安装包链接目标不存在或存在循环")
                links = remaining
        finally:
            if decoder.poll() is None:
                decoder.kill()


def install(progress) -> None:
    if executable().is_file():
        return
    info = asset()
    root().mkdir(parents=True, exist_ok=True)
    # TemporaryDirectory only deletes its own newly created, resolved staging dir.
    with tempfile.TemporaryDirectory(prefix="install-", dir=root().resolve()) as staging:
        stage = Path(staging)
        archive = stage / info[0]
        _download(archive, info, progress)
        unpacked = stage / "unpacked"
        unpacked.mkdir()
        _extract(archive, unpacked)
        relative = executable().relative_to(root() / VERSION)
        if not (unpacked / relative).is_file():
            raise ValueError("运行环境安装包缺少 Ollama 可执行文件")
        unpacked.rename(root() / VERSION)


def start() -> None:
    global _process
    from .local_model_setup import client, model_catalog

    with _process_lock:
        claim_runtime()
        if _process is not None and _process.poll() is None:
            with client(timeout=2, base=BASE) as connection:
                model_catalog(connection)
            return
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 11435))
            except OSError as exc:
                raise ValueError("本机端口 11435 已被占用，未接管其他服务") from exc
        if not executable().is_file():
            raise ValueError("托管 Ollama 尚未安装")
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "TMPDIR",
                "HOME",
                "USERPROFILE",
                "LOCALAPPDATA",
                "APPDATA",
                "LANG",
                "LC_ALL",
                "LD_LIBRARY_PATH",
                "CUDA_VISIBLE_DEVICES",
                "HIP_VISIBLE_DEVICES",
                "ROCR_VISIBLE_DEVICES",
                "GGML_VK_VISIBLE_DEVICES",
            }
        }
        env.update(
            OLLAMA_HOST="127.0.0.1:11435",
            OLLAMA_MODELS=str(root().resolve() / "models"),
            OLLAMA_NO_CLOUD="1",
            OLLAMA_CONTEXT_LENGTH="4096",
            OLLAMA_NUM_PARALLEL="1",
            OLLAMA_MAX_LOADED_MODELS="1",
            OLLAMA_KEEP_ALIVE="2m",
        )
        _process = subprocess.Popen(
            [str(executable().resolve()), "serve"],
            env=env,
            cwd=root().resolve(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(60):
            if _process.poll() is not None:
                raise ValueError("本机模型服务启动失败，请检查运行库或 GPU 驱动")
            try:
                with client(timeout=1, base=BASE) as connection:
                    model_catalog(connection)
                return
            except (ValueError, httpx.HTTPError):
                time.sleep(0.5)
        _process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            _process.wait(timeout=5)
        if _process.poll() is None:
            _process.kill()
            _process.wait(timeout=5)
        _process = None
        raise ValueError("本机模型服务启动超时")


def enable() -> None:
    atomic_write_json(root() / "enabled.json", {"version": VERSION})


def shutdown() -> None:
    global _lease, _process
    with _process_lock:
        if _process is not None and _process.poll() is None:
            _process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                _process.wait(timeout=5)
            if _process.poll() is None:
                _process.kill()
        _process = None
        with _lease_lock:
            if _lease is not None:
                _lease.close()
                _lease = None
